"""FileSpoolQueue v2 边车文件与租约生命周期。"""
from __future__ import annotations

import json
import socket
import threading
import time

import codev_platform.reindex.file_queue as file_queue_module
from codev_platform.reindex.queue import FileSpoolQueue, Job, JobMeta, QueueSnapshot


def _phase_path(root, phase: str, key: str):
    return root / phase / f"{key}.json"


def _read_json(path):
    return json.loads(path.read_text(encoding="utf-8"))


def _write_json(path, data):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data), encoding="utf-8")


def _set_enqueued_at(root, phase: str, key: str, value: float):
    path = _phase_path(root, phase, key)
    data = _read_json(path)
    data["enqueued_at"] = value
    _write_json(path, data)


def test_job_legacy_constructor_sets_legacy_meta():
    job = Job("demo", "chroma", 1.0)
    assert job.meta.source == "legacy"


def test_enqueue_creates_pending_json_sidecar(tmp_path):
    q = FileSpoolQueue(tmp_path)
    meta = JobMeta(source="manual", pull_policy="never", target_commit="abc123")

    q.enqueue("demo-proj", "chroma", meta=meta)

    sidecar = _phase_path(tmp_path, "pending", "demo-proj__chroma")
    assert sidecar.exists()
    data = _read_json(sidecar)
    assert data["project_id"] == "demo-proj"
    assert data["kind"] == "chroma"
    assert data["meta"] == {"source": "manual", "pull_policy": "never", "target_commit": "abc123"}
    assert q.peek()[0].meta == meta


def test_pending_claim_moves_pending_to_active_and_hides_from_peek(tmp_path):
    q = FileSpoolQueue(tmp_path, lease_ttl_sec=30, owner_token="worker-1")
    q.enqueue("demo-proj", "chroma")

    jobs = q.pending()

    assert len(jobs) == 1
    job = jobs[0]
    assert job.token
    assert not _phase_path(tmp_path, "pending", job.key).exists()
    active = _phase_path(tmp_path, "active", job.key)
    assert active.exists()
    data = _read_json(active)
    assert data["claim_token"] == job.token
    assert data["owner_token"] == "worker-1"
    assert data["lease_expires_at"] > data["enqueued_at"]
    assert q.peek() == []


def test_pending_limit_claims_one_job_per_call(tmp_path):
    q = FileSpoolQueue(tmp_path, lease_ttl_sec=30, owner_token="worker-1")
    q.enqueue("demo-proj", "codegraph")
    q.enqueue("demo-proj", "chroma")

    first = q.pending(limit=1)
    second = q.pending(limit=1)

    assert [job.kind for job in first] == ["codegraph"]
    assert [job.kind for job in second] == ["chroma"]


def test_enqueue_during_active_creates_dirty_pending_without_touching_active(tmp_path):
    q = FileSpoolQueue(tmp_path, lease_ttl_sec=30, owner_token="worker-1")
    q.enqueue("demo-proj", "chroma")
    active_job = q.pending()[0]
    active_before = _read_json(_phase_path(tmp_path, "active", active_job.key))

    q.enqueue("demo-proj", "chroma", meta=JobMeta(source="webhook"))

    assert _read_json(_phase_path(tmp_path, "active", active_job.key))["claim_token"] == active_before["claim_token"]
    pending = _phase_path(tmp_path, "pending", active_job.key)
    assert pending.exists()
    assert _read_json(pending)["meta"]["source"] == "webhook"


def test_complete_with_dirty_pending_returns_false_and_keeps_pending(tmp_path):
    q = FileSpoolQueue(tmp_path, lease_ttl_sec=30, owner_token="worker-1")
    q.enqueue("demo-proj", "chroma")
    job = q.pending()[0]
    q.enqueue("demo-proj", "chroma", meta=JobMeta(source="webhook"))

    assert q.complete(job) is False
    assert not _phase_path(tmp_path, "active", job.key).exists()
    assert _phase_path(tmp_path, "pending", job.key).exists()
    assert [pending.key for pending in q.peek()] == [job.key]


def test_pending_claim_does_not_drop_reenqueue_during_claim(tmp_path, monkeypatch):
    q = FileSpoolQueue(tmp_path, lease_ttl_sec=30, owner_token="worker-1")
    q.enqueue("demo-proj", "chroma")
    key = "demo-proj__chroma"
    active_path = _phase_path(tmp_path, "active", key)
    allow_reenqueue = threading.Event()
    reenqueued_payload: dict[str, object] = {}
    original_write = q._store.write_json_atomic

    def _producer():
        assert allow_reenqueue.wait(timeout=1.0)
        q.enqueue("demo-proj", "chroma", meta=JobMeta(source="webhook", pull_policy="never"))
        reenqueued_payload.update(_read_json(_phase_path(tmp_path, "pending", key)))

    producer = threading.Thread(target=_producer)
    producer.start()

    def _write_json_atomic(path, data):
        original_write(path, data)
        if path == active_path and data.get("claim_token"):
            allow_reenqueue.set()
            time.sleep(0.1)

    monkeypatch.setattr(q._store, "write_json_atomic", _write_json_atomic)

    jobs = q.pending()

    producer.join(timeout=1.0)
    assert not producer.is_alive()
    assert len(jobs) == 1
    pending_path = _phase_path(tmp_path, "pending", key)
    assert pending_path.exists()
    assert _read_json(pending_path) == reenqueued_payload


def test_complete_without_dirty_pending_writes_result_sidecar(tmp_path):
    q = FileSpoolQueue(tmp_path, lease_ttl_sec=30, owner_token="worker-1")
    q.enqueue("demo-proj", "chroma", meta=JobMeta(source="manual"))
    job = q.pending()[0]

    assert q.complete(job) is True
    snap = q.snapshot()

    assert isinstance(snap, QueueSnapshot)
    assert snap.pending == []
    assert snap.active == []
    assert [result.key for result in snap.results] == [job.key]
    assert snap.results[0].meta == JobMeta(source="manual")


def test_stale_complete_token_cannot_delete_newer_active_or_pending(tmp_path):
    q = FileSpoolQueue(tmp_path, lease_ttl_sec=30, owner_token="worker-1")
    q.enqueue("demo-proj", "chroma")
    stale = q.pending()[0]
    assert q.break_lease(stale) is True
    current = q.pending()[0]
    q.enqueue("demo-proj", "chroma", meta=JobMeta(source="webhook"))

    assert q.complete(stale) is False
    assert _read_json(_phase_path(tmp_path, "active", current.key))["claim_token"] == current.token
    assert _phase_path(tmp_path, "pending", current.key).exists()


def test_legacy_root_marker_migrates_lazily_to_pending_json(tmp_path):
    q = FileSpoolQueue(tmp_path)
    legacy = tmp_path / "demo-proj__chroma"
    legacy.write_text("", encoding="utf-8")
    legacy.touch()

    jobs = q.peek()

    sidecar = _phase_path(tmp_path, "pending", "demo-proj__chroma")
    assert [job.key for job in jobs] == ["demo-proj__chroma"]
    assert not legacy.exists()
    assert sidecar.exists()
    assert jobs[0].meta == JobMeta()


def test_unknown_legacy_kind_remains_clearable(tmp_path):
    q = FileSpoolQueue(tmp_path, lease_ttl_sec=30, owner_token="worker-1")
    legacy = tmp_path / "demo-proj__oldkind"
    legacy.write_text("", encoding="utf-8")

    job = q.pending()[0]

    assert job.kind == "oldkind"
    assert q.complete(job) is True
    assert not legacy.exists()
    assert not _phase_path(tmp_path, "active", job.key).exists()


def test_release_matching_active_restores_pending_for_retry(tmp_path):
    q = FileSpoolQueue(tmp_path, lease_ttl_sec=30, owner_token="worker-1")
    q.enqueue("demo-proj", "chroma")
    job = q.pending()[0]

    assert q.release(job) is True
    assert not _phase_path(tmp_path, "active", job.key).exists()
    assert _phase_path(tmp_path, "pending", job.key).exists()
    assert [pending.key for pending in q.peek()] == [job.key]


def test_renew_extends_only_matching_active_lease(tmp_path):
    q = FileSpoolQueue(tmp_path, lease_ttl_sec=30, owner_token="worker-1")
    q.enqueue("demo-proj", "chroma")
    job = q.pending()[0]
    active = _phase_path(tmp_path, "active", job.key)
    lease_before = _read_json(active)["lease_expires_at"]

    assert q.renew(job) is True
    assert _read_json(active)["lease_expires_at"] > lease_before
    assert q.renew(Job(job.project_id, job.kind, job.enqueued_at, token="stale-token")) is False


def test_reclaim_stale_own_restores_owned_active_to_pending(tmp_path):
    q = FileSpoolQueue(tmp_path, lease_ttl_sec=30, owner_token="worker-1")
    q.enqueue("demo-proj", "chroma")
    job = q.pending()[0]

    assert q.reclaim_stale_own() == 1
    assert not _phase_path(tmp_path, "active", job.key).exists()
    assert _phase_path(tmp_path, "pending", job.key).exists()


def test_break_lease_only_clears_active_and_keeps_pending(tmp_path):
    q = FileSpoolQueue(tmp_path, lease_ttl_sec=30, owner_token="worker-1")
    q.enqueue("demo-proj", "chroma")
    job = q.pending()[0]
    q.enqueue("demo-proj", "chroma")

    assert q.break_lease(job) is True
    assert not _phase_path(tmp_path, "active", job.key).exists()
    assert _phase_path(tmp_path, "pending", job.key).exists()


def test_break_lease_stale_token_does_not_clear_current_active_or_pending(tmp_path):
    q = FileSpoolQueue(tmp_path, lease_ttl_sec=30, owner_token="worker-1")
    q.enqueue("demo-proj", "chroma")
    stale = q.pending()[0]

    assert q.release(stale) is True

    current = q.pending()[0]
    q.enqueue("demo-proj", "chroma", meta=JobMeta(source="webhook"))
    active_path = _phase_path(tmp_path, "active", current.key)
    pending_path = _phase_path(tmp_path, "pending", current.key)
    pending_before = _read_json(pending_path)

    assert q.break_lease(stale) is False
    assert _read_json(active_path)["claim_token"] == current.token
    assert _read_json(pending_path) == pending_before


def test_enqueue_cleans_stale_empty_lock_dir(tmp_path):
    q = FileSpoolQueue(tmp_path)
    lock_dir = tmp_path / "locks" / "demo-proj__chroma.lock"
    lock_dir.mkdir(parents=True, exist_ok=True)
    owner = lock_dir / "owner.json"
    owner.write_text(
        json.dumps(
            {
                "pid": 999999,
                "host": socket.gethostname(),
                "owner_token": "dead-worker",
                "created_at": time.time() - 600,
            }
        ),
        encoding="utf-8",
    )
    (lock_dir / ".owner.json.dead.tmp").write_text("{}", encoding="utf-8")
    stale_ts = time.time() - 600
    lock_dir.touch()
    import os
    os.utime(lock_dir, (stale_ts, stale_ts))

    q.enqueue("demo-proj", "chroma")

    assert not lock_dir.exists()
    assert _phase_path(tmp_path, "pending", "demo-proj__chroma").exists()


def test_pending_cleans_stale_empty_lock_dir(tmp_path):
    q = FileSpoolQueue(tmp_path, lease_ttl_sec=30, owner_token="worker-1")
    q.enqueue("demo-proj", "chroma")
    lock_dir = tmp_path / "locks" / "demo-proj__chroma.lock"
    lock_dir.mkdir(parents=True, exist_ok=True)
    owner = lock_dir / "owner.json"
    owner.write_text(
        json.dumps(
            {
                "pid": 999999,
                "host": socket.gethostname(),
                "owner_token": "dead-worker",
                "created_at": time.time() - 600,
            }
        ),
        encoding="utf-8",
    )
    (lock_dir / ".owner.json.dead.tmp").write_text("{}", encoding="utf-8")
    stale_ts = time.time() - 600
    lock_dir.touch()
    import os
    os.utime(lock_dir, (stale_ts, stale_ts))

    jobs = q.pending(limit=1)

    assert [job.key for job in jobs] == ["demo-proj__chroma"]
    assert not lock_dir.exists()


def test_key_lock_does_not_delete_live_lock_owned_by_current_process(tmp_path):
    q = FileSpoolQueue(tmp_path)
    key = "demo-proj__chroma"
    started = threading.Event()
    finished = threading.Event()
    worker_error: list[str] = []

    def _enqueue():
        started.set()
        try:
            q.enqueue("demo-proj", "chroma")
        except Exception as exc:  # noqa: BLE001
            worker_error.append(str(exc))
        finally:
            finished.set()

    with q._key_lock(key):
        lock_dir = tmp_path / "locks" / f"{key}.lock"
        stale_ts = time.time() - 600
        import os
        os.utime(lock_dir, (stale_ts, stale_ts))
        t = threading.Thread(target=_enqueue)
        t.start()
        assert started.wait(timeout=1.0)
        time.sleep(0.05)
        assert finished.is_set() is False
        assert lock_dir.exists()
    t.join(timeout=1.0)
    assert t.is_alive() is False
    assert worker_error == []
    assert _phase_path(tmp_path, "pending", key).exists()


def test_break_lease_does_not_overwrite_new_pending_written_during_transition(tmp_path, monkeypatch):
    q = FileSpoolQueue(tmp_path, lease_ttl_sec=30, owner_token="worker-1")
    q.enqueue("demo-proj", "chroma", meta=JobMeta(source="manual"))
    job = q.pending()[0]
    allow_enqueue = threading.Event()
    fresh_pending_payload: dict[str, object] = {}
    original_restore = file_queue_module.restore_active_pending

    def _producer():
        assert allow_enqueue.wait(timeout=1.0)
        q.enqueue("demo-proj", "chroma", meta=JobMeta(source="webhook", pull_policy="never"))
        fresh_pending_payload.update(_read_json(_phase_path(tmp_path, "pending", job.key)))

    producer = threading.Thread(target=_producer)
    producer.start()

    def _restore_active_pending(store, active):
        allow_enqueue.set()
        time.sleep(0.1)
        return original_restore(store, active)

    monkeypatch.setattr(file_queue_module, "restore_active_pending", _restore_active_pending)

    assert q.break_lease(job) is True
    producer.join(timeout=1.0)
    assert not producer.is_alive()
    pending_path = _phase_path(tmp_path, "pending", job.key)
    assert pending_path.exists()
    pending = _read_json(pending_path)
    assert pending["meta"] == fresh_pending_payload["meta"]
    assert pending["enqueued_at"] == fresh_pending_payload["enqueued_at"]


def test_discard_removes_pending_job(tmp_path):
    q = FileSpoolQueue(tmp_path)
    q.enqueue("demo-proj", "chroma")
    job = q.peek()[0]

    assert q.discard(job) is True
    assert q.peek() == []


def test_discard_keeps_job_reenqueued_after_peek(tmp_path):
    q = FileSpoolQueue(tmp_path)
    q.enqueue("demo-proj", "chroma")
    job = q.peek()[0]
    q.enqueue("demo-proj", "chroma")

    assert q.discard(job) is False
    assert [queued.key for queued in q.peek()] == ["demo-proj__chroma"]


def test_snapshot_returns_pending_active_and_results(tmp_path):
    q = FileSpoolQueue(tmp_path, lease_ttl_sec=30, owner_token="worker-1")
    q.enqueue("demo-proj", "chroma")
    q.enqueue("other-proj", "chroma", meta=JobMeta(source="manual"))
    active = q.pending({"demo-proj"})[0]
    other = q.peek()[0]

    assert q.discard(other) is True
    snap = q.snapshot()

    assert isinstance(snap, QueueSnapshot)
    assert snap.pending == []
    assert [job.key for job in snap.active] == [active.key]
    assert [job.key for job in snap.results] == ["other-proj__chroma"]


def test_snapshot_exposes_expired_active_with_lease_expiry(tmp_path):
    q = FileSpoolQueue(tmp_path, lease_ttl_sec=30, owner_token="worker-1")
    q.enqueue("demo-proj", "chroma")
    job = q.pending()[0]
    active_path = _phase_path(tmp_path, "active", job.key)
    data = _read_json(active_path)
    data["lease_expires_at"] = time.time() - 1
    _write_json(active_path, data)

    snap = q.snapshot()

    assert snap.active == []
    assert [expired.key for expired in snap.expired_active] == [job.key]
    assert snap.expired_active[0].lease_expires_at is not None


def test_pending_codegraph_before_code_vec_on_same_enqueued_at(tmp_path):
    q = FileSpoolQueue(tmp_path)
    q.enqueue("demo-proj", "code_vec")
    q.enqueue("demo-proj", "codegraph")
    _set_enqueued_at(tmp_path, "pending", "demo-proj__code_vec", 1000.0)
    _set_enqueued_at(tmp_path, "pending", "demo-proj__codegraph", 1000.0)

    kinds = [job.kind for job in q.pending()]

    assert kinds.index("codegraph") < kinds.index("code_vec")


def test_pending_preserves_fifo_across_enqueued_at(tmp_path):
    q = FileSpoolQueue(tmp_path)
    q.enqueue("demo-proj", "code_vec")
    q.enqueue("demo-proj", "chroma")
    _set_enqueued_at(tmp_path, "pending", "demo-proj__code_vec", 1000.0)
    _set_enqueued_at(tmp_path, "pending", "demo-proj__chroma", 2000.0)

    kinds = [job.kind for job in q.pending()]

    assert kinds.index("code_vec") < kinds.index("chroma")


def test_pending_projects_none_returns_all(tmp_path):
    q = FileSpoolQueue(tmp_path)
    q.enqueue("proj-a", "chroma")
    q.enqueue("proj-b", "chroma")

    pids = {job.project_id for job in q.pending()}

    assert pids == {"proj-a", "proj-b"}


def test_pending_projects_filter_local(tmp_path):
    q = FileSpoolQueue(tmp_path)
    q.enqueue("proj-a", "chroma")
    q.enqueue("proj-b", "chroma")

    pids = {job.project_id for job in q.pending({"proj-a"})}

    assert pids == {"proj-a"}
    assert q.pending(set()) == []
