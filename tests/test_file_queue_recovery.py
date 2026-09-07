"""File 队列锁回收、隔离复核与损坏输入的确定性回归。"""

from __future__ import annotations

import os
import threading
import time

import pytest

import codev_platform.reindex.file_advisory_lock as advisory_module
import codev_platform.reindex.file_durability as durability_module
import codev_platform.reindex.file_queue_store as store_module
from codev_platform.reindex.attempts import ConfirmedProcessDeath
from codev_platform.reindex.file_queue import FileSpoolQueue
from codev_platform.reindex.file_queue_store import OperationDeadline, _fsync_directory
from codev_platform.reindex.queue_ports import JobMeta, QueueOperationTimeout

_OID_A = "a" * 40


class _CrossingDeadline:
    """模拟锁竞争后恰好跨过 deadline 的确定性时钟。"""

    def __init__(self) -> None:
        self.checks = 0

    def check(self) -> None:
        self.checks += 1
        if self.checks > 1:
            raise QueueOperationTimeout("测试预算已耗尽")

    def remaining(self) -> float:
        return -0.001


class _CrossingReleaseDeadline:
    """模拟释放重试两次读取之间预算跨零。"""

    def __init__(self) -> None:
        self._remaining = iter((0.001, -0.001))

    @classmethod
    def start(cls, _timeout_sec: float):
        return cls()

    def check(self) -> None:
        return None

    def remaining(self) -> float:
        return next(self._remaining, -0.001)


def _claim(queue: FileSpoolQueue, *, owner: str = "worker-1"):
    queue.enqueue("demo", "chroma", JobMeta(source="test", target_commit=_OID_A))
    return queue.claim(
        owner_token=owner,
        projects=None,
        limit=1,
        timeout_sec=0.3,
    )[0]


def test_advisory_wait_crossing_deadline_raises_queue_timeout(
    tmp_path,
    monkeypatch,
) -> None:
    deadline = _CrossingDeadline()
    monkeypatch.setattr(advisory_module, "_try_lock", lambda _stream: False)

    with pytest.raises(QueueOperationTimeout, match="测试预算已耗尽"):
        with advisory_module.advisory_lock(
            tmp_path / "queue.gate",
            deadline,
            blocking=True,
        ):
            pytest.fail("预算耗尽后不得进入临界区")

    assert deadline.checks == 2


def test_advisory取得锁后才初始化首字节(tmp_path, monkeypatch) -> None:
    path = tmp_path / "queue.gate"
    observed_sizes: list[int] = []

    def acquire_empty_file(stream) -> bool:
        stream.seek(0, os.SEEK_END)
        observed_sizes.append(stream.tell())
        return True

    monkeypatch.setattr(advisory_module, "_try_lock", acquire_empty_file)
    monkeypatch.setattr(advisory_module, "_unlock", lambda _stream: None)

    with advisory_module.advisory_lock(
        path,
        OperationDeadline.start(0.3),
        blocking=True,
    ) as acquired:
        assert acquired is True

    assert observed_sizes == [0]
    assert path.read_bytes() == b"\0"


def test_stale_recovery_gate_allows_only_one_key_entrant(tmp_path) -> None:
    root = tmp_path / "queue"
    first = FileSpoolQueue(root)
    second = FileSpoolQueue(root)
    lock_path = root / "locks" / "demo__chroma.lock"
    lock_path.mkdir()
    (lock_path / "owner.json").write_text('{"pid":"损坏"}', encoding="utf-8")
    stale_at = time.time() - 2.0
    os.utime(lock_path, (stale_at, stale_at))
    barrier = threading.Barrier(2)
    counter_lock = threading.Lock()
    active = 0
    maximum = 0
    entrants = 0

    def _enter(queue: FileSpoolQueue) -> None:
        nonlocal active, maximum, entrants
        barrier.wait(timeout=1.0)
        with queue._key_lock("demo__chroma"):
            with counter_lock:
                active += 1
                entrants += 1
                maximum = max(maximum, active)
            time.sleep(0.05)
            with counter_lock:
                active -= 1

    threads = [threading.Thread(target=_enter, args=(queue,)) for queue in (first, second)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=2.0)

    assert all(not thread.is_alive() for thread in threads)
    assert entrants == 2
    assert maximum == 1


def test_lock_release_refuses_to_delete_different_acquisition(tmp_path) -> None:
    queue = FileSpoolQueue(tmp_path / "queue")
    store = queue._store
    key = "demo__chroma"
    path = store.lock_path(key)
    assert path is not None

    with pytest.raises(RuntimeError, match="锁目录释放失败"):
        with store.key_lock(key, OperationDeadline.start(0.3)) as acquired:
            assert acquired is True
            payload = store.read_json(path / "owner.json")
            payload["lock_id"] = "different-acquisition"
            store.write_json_atomic(path / "owner.json", payload)

    assert path.exists()


def test_lock_release_retry_crossing_deadline_returns_failure(
    tmp_path,
    monkeypatch,
) -> None:
    queue = FileSpoolQueue(tmp_path / "queue")
    store = queue._store
    path = store.lock_path("demo__chroma")
    assert path is not None
    path.mkdir()
    store.write_json_atomic(
        path / "owner.json",
        store._lock_payload("lock-1"),
    )
    monkeypatch.setattr(store_module, "OperationDeadline", _CrossingReleaseDeadline)
    monkeypatch.setattr(store, "_clear_lock_dir", lambda _path: False)

    assert store._release_lock(path, "lock-1") is False


def test_recovery_rechecks_quarantine_inside_key_lock(tmp_path, monkeypatch) -> None:
    queue = FileSpoolQueue(tmp_path / "queue", owner_token="worker-1")
    claim = _claim(queue)
    queue.quarantine(
        claim,
        attempt_id="attempt-1",
        fence="fence-1",
        process_identity="pid:10:start:20",
        containment_kind="test",
        native_ref="fixture:10",
        reason="死亡未确认",
        timeout_sec=0.3,
    )
    monkeypatch.setattr(queue, "_quarantine_map", lambda: {})

    assert queue.recover_owned(owner_token="worker-1", timeout_sec=0.3) == []
    assert queue.reclaim_stale_own() == 0
    assert queue._store.read_quarantine(claim.job.key) is not None


def test_recover_owned_cannot_return_after_read_deadline(tmp_path, monkeypatch) -> None:
    queue = FileSpoolQueue(tmp_path / "queue", owner_token="worker-1")
    claim = _claim(queue)
    original_read = queue._store.read_phase

    def _slow_active_read(phase, key):
        record = original_read(phase, key)
        if phase == "active":
            time.sleep(0.03)
        return record

    monkeypatch.setattr(queue._store, "read_phase", _slow_active_read)
    with pytest.raises(QueueOperationTimeout):
        queue.recover_owned(owner_token="worker-1", timeout_sec=0.01)

    assert queue._store.read_phase("active", claim.job.key) is not None


@pytest.mark.parametrize(
    ("failure_stage", "expected_active", "expected_pending"),
    [
        ("restore_pending", True, False),
        ("delete_active", True, True),
        ("delete_marker", False, True),
    ],
)
def test_clear_quarantine_can_resume_after_each_persistence_failure(
    tmp_path,
    monkeypatch,
    failure_stage,
    expected_active,
    expected_pending,
) -> None:
    queue = FileSpoolQueue(tmp_path / "queue", owner_token="worker-1")
    claim = _claim(queue)
    record = queue.quarantine(
        claim,
        attempt_id="attempt-1",
        fence="fence-1",
        process_identity="pid:10:start:20",
        containment_kind="test",
        native_ref="fixture:10",
        reason="死亡未确认",
        timeout_sec=0.3,
    )
    proof = ConfirmedProcessDeath(
        process_identity=record.process_identity,
        containment_kind=record.containment_kind,
        confirmed_at=record.quarantined_at + 1.0,
        evidence="确定性测试死亡证据",
    )
    original_write = queue._store.write_json_atomic
    original_unlink = queue._store.unlink
    failed = False

    def _write(path, payload):
        nonlocal failed
        if not failed and failure_stage == "restore_pending" and path.parent.name == "pending":
            failed = True
            raise OSError("注入 pending 恢复失败")
        return original_write(path, payload)

    def _unlink(path):
        nonlocal failed
        phase = path.parent.name
        target = {
            "delete_active": "active",
            "delete_marker": "quarantined",
        }.get(failure_stage)
        if not failed and phase == target:
            failed = True
            raise OSError(f"注入 {phase} 删除失败")
        return original_unlink(path)

    monkeypatch.setattr(queue._store, "write_json_atomic", _write)
    monkeypatch.setattr(queue._store, "unlink", _unlink)
    with pytest.raises(OSError, match="注入"):
        queue.clear_quarantine(record, death_proof=proof, timeout_sec=0.3)

    assert failed is True
    assert queue._store.read_quarantine(claim.job.key) == record
    assert (queue._store.read_phase("active", claim.job.key) is not None) is expected_active
    assert (queue._store.read_phase("pending", claim.job.key) is not None) is expected_pending

    monkeypatch.setattr(queue._store, "write_json_atomic", original_write)
    monkeypatch.setattr(queue._store, "unlink", original_unlink)
    assert queue.clear_quarantine(record, death_proof=proof, timeout_sec=0.3) is True
    assert queue._store.read_quarantine(claim.job.key) is None
    assert queue._store.read_phase("active", claim.job.key) is None
    assert queue._store.read_phase("pending", claim.job.key) is not None


def test_posix_unknown_pid_probe_error_fails_closed(monkeypatch) -> None:
    monkeypatch.setattr(store_module.os, "name", "posix")
    monkeypatch.setattr(
        store_module.os,
        "kill",
        lambda _pid, _signal: (_ for _ in ()).throw(OSError("未知探测失败")),
    )

    assert store_module.FileQueueStore._pid_alive(17) is True


def test_corrupt_pending_supersedes_publish_without_deleting_pending(tmp_path) -> None:
    queue = FileSpoolQueue(tmp_path / "queue")
    claim = _claim(queue)
    pending = queue.location / "pending" / f"{claim.job.key}.json"
    pending.write_bytes(b"{broken")

    with queue.begin_publish(
        claim,
        desired_revision=_OID_A,
        timeout_sec=0.3,
    ) as permit:
        assert permit.superseded is True
        assert permit.ack() is True

    assert pending.read_bytes() == b"{broken"
    assert queue.snapshot().active == []


def test_corrupt_quarantine_marker_still_blocks_snapshot_state(tmp_path) -> None:
    queue = FileSpoolQueue(tmp_path / "queue")
    claim = _claim(queue)
    queue.enqueue("demo", "chroma", JobMeta(source="test", target_commit=_OID_A))
    marker = queue.location / "quarantined" / f"{claim.job.key}.json"
    marker.write_bytes(b"{broken")

    snapshot = queue.snapshot()

    assert snapshot.pending == []
    assert snapshot.active == []
    assert snapshot.expired_active == []
    assert snapshot.quarantined == []
    assert queue.peek() == []


def test_empty_scan_cannot_return_success_after_deadline(tmp_path, monkeypatch) -> None:
    queue = FileSpoolQueue(tmp_path / "queue")

    def _slow_empty(_phase):
        time.sleep(0.03)
        return []

    monkeypatch.setattr(queue._store, "phase_records", _slow_empty)
    with pytest.raises(QueueOperationTimeout):
        queue.claim(
            owner_token="worker-1",
            projects=None,
            limit=1,
            timeout_sec=0.01,
        )


def test_busy_nonblocking_key_cannot_return_empty_after_deadline(
    tmp_path,
    monkeypatch,
) -> None:
    queue = FileSpoolQueue(tmp_path / "queue")
    queue.enqueue("demo", "chroma", JobMeta(source="test", target_commit=_OID_A))

    def _slow_busy(_stream):
        time.sleep(0.03)
        return False

    monkeypatch.setattr(advisory_module, "_try_lock", _slow_busy)
    with pytest.raises(QueueOperationTimeout):
        queue.claim(
            owner_token="worker-1",
            projects=None,
            limit=1,
            timeout_sec=0.01,
        )


def test_empty_project_filter_still_validates_timeout(tmp_path) -> None:
    queue = FileSpoolQueue(tmp_path / "queue")

    with pytest.raises(ValueError):
        queue.claim(
            owner_token="worker-1",
            projects=set(),
            limit=1,
            timeout_sec=0.0,
        )


def test_mismatched_payload_identity_does_not_block_healthy_claim(tmp_path) -> None:
    queue = FileSpoolQueue(tmp_path / "queue")
    bad_path = queue.location / "pending" / "bad__chroma.json"
    queue._store.write_json_atomic(
        bad_path,
        {
            "project_id": "../escape",
            "kind": "chroma",
            "enqueued_at": 1.0,
            "meta": {"source": "test", "pull_policy": None, "target_commit": _OID_A},
        },
    )
    queue.enqueue("healthy", "chroma", JobMeta(source="test", target_commit=_OID_A))

    claims = queue.claim(
        owner_token="worker-1",
        projects=None,
        limit=2,
        timeout_sec=0.3,
    )

    assert [claim.job.project_id for claim in claims] == ["healthy"]


@pytest.mark.skipif(os.name == "nt", reason="Windows 状态变更使用 write-through rename")
def test_posix_fsync_directory_uses_directory_handle(tmp_path, monkeypatch) -> None:
    calls: list[tuple[str, object]] = []

    monkeypatch.setattr(os, "open", lambda path, flags: calls.append(("open", (path, flags))) or 71)
    monkeypatch.setattr(os, "fsync", lambda fd: calls.append(("fsync", fd)))
    monkeypatch.setattr(os, "close", lambda fd: calls.append(("close", fd)))

    _fsync_directory(tmp_path)

    assert calls[0][0] == "open"
    assert calls[1:] == [("fsync", 71), ("close", 71)]


@pytest.mark.skipif(os.name != "nt", reason="仅验证 Windows write-through 策略")
def test_windows_state_changes_use_write_through_move(tmp_path, monkeypatch) -> None:
    calls: list[tuple[object, object]] = []

    def _move(source, target):
        calls.append((source, target))
        os.replace(source, target)

    monkeypatch.setattr(durability_module, "_move_file_write_through", _move)
    source = tmp_path / "source.json"
    target = tmp_path / "target.json"
    source.write_text("new", encoding="utf-8")
    target.write_text("old", encoding="utf-8")

    durability_module.durable_replace(source, target)
    assert target.read_text(encoding="utf-8") == "new"
    assert calls[-1] == (source, target)

    assert durability_module.durable_unlink(target) is True
    assert target.exists() is False
    assert len(calls) == 2
