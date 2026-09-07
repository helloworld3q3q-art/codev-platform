"""reindex 队列新端口与 File 后端的公共行为契约。"""
from __future__ import annotations

import dataclasses
import json
import math
import os
import socket
import threading
import time

import pytest

import codev_platform.reindex.file_queue_store as file_store_module
from codev_platform.reindex.attempts import ConfirmedProcessDeath
from codev_platform.reindex.file_queue import FileSpoolQueue
from codev_platform.reindex.queue_ports import (
    ClaimedJob,
    Job,
    JobMeta,
    QuarantineRecord,
    QueueClaimLost,
    QueueOperationTimeout,
)


_OID_A = "a" * 40
_OID_B = "b" * 40
_TIMEOUT = 0.3


def _enqueue(queue: FileSpoolQueue, *, project: str = "demo", kind: str = "chroma",
             revision: str = _OID_A, source: str = "test") -> None:
    queue.enqueue(
        project,
        kind,
        JobMeta(source=source, pull_policy="never", target_commit=revision),
    )


def _claim(queue: FileSpoolQueue, *, owner: str = "worker-1",
           projects: set[str] | None = None) -> ClaimedJob:
    claims = queue.claim(
        owner_token=owner,
        projects=projects,
        limit=1,
        timeout_sec=_TIMEOUT,
    )
    assert len(claims) == 1
    return claims[0]


def _quarantine(queue: FileSpoolQueue, claim: ClaimedJob) -> QuarantineRecord:
    return queue.quarantine(
        claim,
        attempt_id="attempt-1",
        fence="fence-1",
        process_identity="pid:10:start:20",
        containment_kind="test",
        native_ref="fixture:10",
        reason="进程树死亡尚未确认",
        timeout_sec=_TIMEOUT,
    )


def _death(record: QuarantineRecord, *, identity: str | None = None,
           containment: str | None = None, confirmed_at: float | None = None) -> ConfirmedProcessDeath:
    return ConfirmedProcessDeath(
        process_identity=identity or record.process_identity,
        containment_kind=containment or record.containment_kind,
        confirmed_at=record.quarantined_at + 1.0 if confirmed_at is None else confirmed_at,
        evidence="测试进程树已退出",
    )


def _hold_key_lock(queue: FileSpoolQueue, key: str) -> tuple[threading.Event, threading.Event, threading.Thread]:
    started = threading.Event()
    release = threading.Event()

    def _hold() -> None:
        with queue._key_lock(key):
            started.set()
            assert release.wait(timeout=2.0)

    thread = threading.Thread(target=_hold, daemon=True)
    thread.start()
    assert started.wait(timeout=1.0)
    return started, release, thread


def test_new_models_are_frozen_slotted_and_validate_identity() -> None:
    job = Job("demo", "chroma", 1.0, meta=JobMeta(target_commit=_OID_A))
    claim = ClaimedJob(job, "claim-1", "worker-1", 2.0)
    record = QuarantineRecord(
        "demo", "chroma", "claim-1", "attempt-1", "fence-1",
        "pid:10:start:20", "test", "fixture:10", "原因", 3.0,
    )

    assert "__slots__" in ClaimedJob.__dict__
    assert "__slots__" in QuarantineRecord.__dict__
    with pytest.raises(dataclasses.FrozenInstanceError):
        claim.owner_token = "worker-2"  # type: ignore[misc]
    assert record.quarantined_at == 3.0
    with pytest.raises(ValueError):
        ClaimedJob(job, "", "worker-1", 2.0)
    with pytest.raises(ValueError):
        QuarantineRecord(
            "demo", "chroma", "claim-1", "", "fence-1",
            "pid:10:start:20", "test", "fixture:10", "原因", 3.0,
        )


def test_claim_binds_worker_incarnation_and_non_optional_token(tmp_path) -> None:
    queue = FileSpoolQueue(tmp_path / "queue", lease_ttl_sec=10.0)
    _enqueue(queue)

    claim = _claim(queue, owner="worker-incarnation-1")
    recovered = queue.recover_owned(
        owner_token="worker-incarnation-1",
        timeout_sec=_TIMEOUT,
    )

    assert claim.claim_token
    assert claim.owner_token == "worker-incarnation-1"
    assert claim.job.token is None
    assert recovered == [claim]
    assert recovered[0].lease_expires_at == claim.lease_expires_at


def test_claim_rejects_empty_owner(tmp_path) -> None:
    queue = FileSpoolQueue(tmp_path / "queue")
    _enqueue(queue)

    with pytest.raises(ValueError):
        queue.claim(owner_token=" ", projects=None, limit=1, timeout_sec=_TIMEOUT)


@pytest.mark.parametrize("timeout", [True, 0.0, 0.009, math.nan, math.inf])
def test_bounded_operations_reject_invalid_timeout(tmp_path, timeout) -> None:
    queue = FileSpoolQueue(tmp_path / "queue")

    with pytest.raises(ValueError):
        queue.claim(owner_token="worker-1", projects=None, limit=1, timeout_sec=timeout)


def test_recover_owned_lists_active_claim_without_mutation(tmp_path) -> None:
    queue = FileSpoolQueue(tmp_path / "queue", lease_ttl_sec=10.0)
    _enqueue(queue)
    claim = _claim(queue)

    first = queue.recover_owned(owner_token="worker-1", timeout_sec=_TIMEOUT)
    second = queue.recover_owned(owner_token="worker-1", timeout_sec=_TIMEOUT)

    assert first == second == [claim]
    assert queue.recover_owned(owner_token="worker-2", timeout_sec=_TIMEOUT) == []
    assert queue.snapshot().active[0].key == claim.job.key


def test_renew_and_retry_require_matching_claim_token(tmp_path) -> None:
    queue = FileSpoolQueue(tmp_path / "queue", lease_ttl_sec=10.0)
    _enqueue(queue)
    claim = _claim(queue)
    stale = dataclasses.replace(claim, claim_token="stale-token")
    wrong_owner = dataclasses.replace(claim, owner_token="worker-2")

    assert queue.renew(stale, ttl_sec=20.0, timeout_sec=_TIMEOUT) is False
    assert queue.retry(wrong_owner, reason="测试重试", timeout_sec=_TIMEOUT) is False
    assert queue.renew(claim, ttl_sec=20.0, timeout_sec=_TIMEOUT) is True
    renewed = queue.recover_owned(owner_token="worker-1", timeout_sec=_TIMEOUT)[0]
    assert renewed.lease_expires_at > claim.lease_expires_at
    assert queue.retry(renewed, reason="测试重试", timeout_sec=_TIMEOUT) is True
    assert [job.key for job in queue.peek()] == [claim.job.key]


def test_quarantine_remains_non_claimable_after_lease_expiry(tmp_path) -> None:
    root = tmp_path / "queue"
    queue = FileSpoolQueue(root, lease_ttl_sec=0.02)
    _enqueue(queue)
    claim = _claim(queue)
    record = _quarantine(queue, claim)

    time.sleep(0.04)
    restarted = FileSpoolQueue(root, lease_ttl_sec=0.02)
    _enqueue(restarted, revision=_OID_B, source="webhook")

    assert restarted.claim(
        owner_token="worker-2", projects=None, limit=1, timeout_sec=_TIMEOUT,
    ) == []
    snapshot = restarted.snapshot()
    assert snapshot.quarantined == [record]
    assert snapshot.pending == []
    assert snapshot.active == []
    assert snapshot.expired_active == []


def test_repeated_quarantine_is_idempotent_but_different_identity_fails_closed(tmp_path) -> None:
    queue = FileSpoolQueue(tmp_path / "queue")
    _enqueue(queue)
    claim = _claim(queue)

    first = _quarantine(queue, claim)
    second = _quarantine(queue, claim)

    assert second == first
    with pytest.raises(QueueClaimLost):
        queue.quarantine(
            claim,
            attempt_id="attempt-2",
            fence="fence-1",
            process_identity="pid:10:start:20",
            containment_kind="test",
            native_ref="fixture:10",
            reason="进程树死亡尚未确认",
            timeout_sec=_TIMEOUT,
        )
    assert queue.snapshot().quarantined == [first]


def test_quarantine_rejects_stale_token_and_wrong_owner(tmp_path) -> None:
    queue = FileSpoolQueue(tmp_path / "queue")
    _enqueue(queue)
    claim = _claim(queue)

    for invalid in (
        dataclasses.replace(claim, claim_token="stale-token"),
        dataclasses.replace(claim, owner_token="worker-2"),
    ):
        with pytest.raises(QueueClaimLost):
            _quarantine(queue, invalid)

    assert queue.snapshot().quarantined == []
    assert _quarantine(queue, claim).claim_token == claim.claim_token


def test_clear_quarantine_requires_matching_confirmed_process_death(tmp_path) -> None:
    queue = FileSpoolQueue(tmp_path / "queue")
    _enqueue(queue)
    claim = _claim(queue)
    record = _quarantine(queue, claim)
    _enqueue(queue, revision=_OID_B, source="webhook")

    assert queue.clear_quarantine(
        record,
        death_proof=_death(record, identity="pid:11:start:20"),
        timeout_sec=_TIMEOUT,
    ) is False
    assert queue.clear_quarantine(
        record,
        death_proof=_death(record, confirmed_at=record.quarantined_at - 0.01),
        timeout_sec=_TIMEOUT,
    ) is False
    assert queue.clear_quarantine(
        dataclasses.replace(record, reason="伪造原因"),
        death_proof=_death(record),
        timeout_sec=_TIMEOUT,
    ) is False
    assert queue.clear_quarantine(
        record,
        death_proof=_death(record),
        timeout_sec=_TIMEOUT,
    ) is True

    snapshot = queue.snapshot()
    assert snapshot.quarantined == []
    assert snapshot.active == []
    assert [job.meta.target_commit for job in snapshot.pending] == [_OID_B]


def test_break_lease_cannot_bypass_quarantine(tmp_path) -> None:
    queue = FileSpoolQueue(tmp_path / "queue")
    _enqueue(queue)
    claim = _claim(queue)
    _quarantine(queue, claim)
    legacy = dataclasses.replace(claim.job, token=claim.claim_token)

    assert queue.break_lease(legacy) is False
    assert len(queue.snapshot().quarantined) == 1


def test_dirty_pending_is_superseded_inside_publish_guard(tmp_path) -> None:
    queue = FileSpoolQueue(tmp_path / "queue")
    _enqueue(queue, revision=_OID_A)
    claim = _claim(queue)
    _enqueue(queue, revision=_OID_B, source="webhook")

    with queue.begin_publish(
        claim, desired_revision=_OID_A, timeout_sec=_TIMEOUT,
    ) as permit:
        assert permit.superseded is True
        assert permit.ack() is True
        assert permit.ack() is False

    with pytest.raises(RuntimeError):
        permit.ack()
    assert [job.meta.target_commit for job in queue.snapshot().pending] == [_OID_B]


def test_same_revision_pending_is_not_superseded_and_is_preserved(tmp_path) -> None:
    queue = FileSpoolQueue(tmp_path / "queue")
    _enqueue(queue, revision=_OID_A)
    claim = _claim(queue)
    _enqueue(queue, revision=_OID_A, source="webhook")

    with queue.begin_publish(
        claim, desired_revision=_OID_A, timeout_sec=_TIMEOUT,
    ) as permit:
        assert permit.superseded is False
        assert permit.ack() is True

    assert [job.meta.target_commit for job in queue.snapshot().pending] == [_OID_A]


def test_publish_guard_requires_current_owner_token_and_revision(tmp_path) -> None:
    queue = FileSpoolQueue(tmp_path / "queue")
    _enqueue(queue)
    claim = _claim(queue)

    for invalid in (
        dataclasses.replace(claim, claim_token="stale-token"),
        dataclasses.replace(claim, owner_token="worker-2"),
    ):
        with pytest.raises(QueueClaimLost):
            with queue.begin_publish(
                invalid, desired_revision=_OID_A, timeout_sec=_TIMEOUT,
            ):
                pytest.fail("失权 claim 不得进入发布围栏")
    with pytest.raises(QueueClaimLost):
        with queue.begin_publish(
            claim, desired_revision=_OID_B, timeout_sec=_TIMEOUT,
        ):
            pytest.fail("错误期望版本不得进入发布围栏")


def test_unacked_or_failed_publish_guard_preserves_active(tmp_path) -> None:
    queue = FileSpoolQueue(tmp_path / "queue")
    _enqueue(queue)
    claim = _claim(queue)

    with queue.begin_publish(claim, desired_revision=_OID_A, timeout_sec=_TIMEOUT):
        pass
    assert queue.recover_owned(owner_token="worker-1", timeout_sec=_TIMEOUT) == [claim]

    with pytest.raises(RuntimeError, match="发布失败"):
        with queue.begin_publish(
            claim, desired_revision=_OID_A, timeout_sec=_TIMEOUT,
        ):
            raise RuntimeError("发布失败")
    assert queue.recover_owned(owner_token="worker-1", timeout_sec=_TIMEOUT) == [claim]


def test_ack_is_committed_only_after_normal_publish_guard_exit(tmp_path) -> None:
    queue = FileSpoolQueue(tmp_path / "queue")
    _enqueue(queue)
    claim = _claim(queue)

    with pytest.raises(RuntimeError, match="ack 后失败"):
        with queue.begin_publish(
            claim, desired_revision=_OID_A, timeout_sec=_TIMEOUT,
        ) as permit:
            assert permit.ack() is True
            raise RuntimeError("ack 后失败")

    assert queue.recover_owned(owner_token="worker-1", timeout_sec=_TIMEOUT) == [claim]
    assert queue.snapshot().results == []


def test_publish_guard_ack_preserves_enqueue_after_guard(tmp_path) -> None:
    root = tmp_path / "queue"
    queue = FileSpoolQueue(root)
    producer_queue = FileSpoolQueue(root)
    _enqueue(queue, revision=_OID_A)
    claim = _claim(queue)
    producer_started = threading.Event()
    producer_finished = threading.Event()

    def _produce() -> None:
        producer_started.set()
        _enqueue(producer_queue, revision=_OID_B, source="webhook")
        producer_finished.set()

    with queue.begin_publish(
        claim, desired_revision=_OID_A, timeout_sec=1.0,
    ) as permit:
        thread = threading.Thread(target=_produce, daemon=True)
        thread.start()
        assert producer_started.wait(timeout=1.0)
        time.sleep(0.03)
        assert producer_finished.is_set() is False
        assert permit.ack() is True

    thread.join(timeout=1.0)
    assert thread.is_alive() is False
    assert producer_finished.is_set() is True
    assert [job.meta.target_commit for job in queue.snapshot().pending] == [_OID_B]


def test_queue_operation_exceeding_timeout_fails_closed(tmp_path) -> None:
    root = tmp_path / "queue"
    queue = FileSpoolQueue(root, lease_ttl_sec=10.0)
    lock_owner = FileSpoolQueue(root)
    _enqueue(queue)
    claim = _claim(queue)
    before = claim.lease_expires_at
    _started, release, thread = _hold_key_lock(lock_owner, claim.job.key)

    started_at = time.monotonic()
    try:
        with pytest.raises(QueueOperationTimeout):
            queue.renew(claim, ttl_sec=20.0, timeout_sec=0.03)
    finally:
        release.set()
        thread.join(timeout=1.0)
    elapsed = time.monotonic() - started_at

    assert elapsed < 0.3
    recovered = queue.recover_owned(owner_token="worker-1", timeout_sec=_TIMEOUT)[0]
    assert recovered.lease_expires_at == before


def test_claim_skips_busy_key_without_orphaning_earlier_claim(tmp_path) -> None:
    root = tmp_path / "queue"
    queue = FileSpoolQueue(root)
    lock_owner = FileSpoolQueue(root)
    _enqueue(queue, project="first", revision=_OID_A)
    time.sleep(0.01)
    _enqueue(queue, project="second", revision=_OID_B)
    _started, release, thread = _hold_key_lock(lock_owner, "first__chroma")

    try:
        started_at = time.monotonic()
        claims = queue.claim(
            owner_token="worker-1", projects=None, limit=2, timeout_sec=0.5,
        )
        elapsed = time.monotonic() - started_at
    finally:
        release.set()
        thread.join(timeout=1.0)

    assert elapsed < 0.25
    assert [claim.job.project_id for claim in claims] == ["second"]
    assert [job.project_id for job in queue.peek()] == ["first"]


def test_claim_returns_existing_batch_if_later_key_exhausts_budget(tmp_path, monkeypatch) -> None:
    queue = FileSpoolQueue(tmp_path / "queue")
    _enqueue(queue, project="first", revision=_OID_A)
    time.sleep(0.01)
    _enqueue(queue, project="second", revision=_OID_B)
    original = queue._claim_locked
    calls = 0

    def _claim_locked(key, owner_token, deadline):
        nonlocal calls
        calls += 1
        if calls == 2:
            raise QueueOperationTimeout("模拟后续 key 预算耗尽")
        return original(key, owner_token, deadline)

    monkeypatch.setattr(queue, "_claim_locked", _claim_locked)
    claims = queue.claim(
        owner_token="worker-1", projects=None, limit=2, timeout_sec=_TIMEOUT,
    )

    assert [claim.job.project_id for claim in claims] == ["first"]
    assert [job.project_id for job in queue.peek()] == ["second"]


def test_lock_metadata_write_failure_does_not_leave_owned_lock(tmp_path, monkeypatch) -> None:
    queue = FileSpoolQueue(tmp_path / "queue")
    original = queue._store.write_json_atomic

    def _fail_owner(path, payload):
        if path.name == "owner.json":
            raise OSError("模拟 owner metadata 写入失败")
        return original(path, payload)

    monkeypatch.setattr(queue._store, "write_json_atomic", _fail_owner)

    with pytest.raises(OSError):
        _enqueue(queue)
    assert not (queue.location / "locks" / "demo__chroma.lock").exists()


def test_windows_pid_probe_never_sends_signal(monkeypatch) -> None:
    def _unexpected_kill(_pid, _signal) -> None:
        raise AssertionError("Windows PID 探测不得调用 os.kill")

    monkeypatch.setattr(file_store_module.os, "name", "nt")
    monkeypatch.setattr(file_store_module.os, "kill", _unexpected_kill)
    monkeypatch.setattr(file_store_module, "_windows_pid_alive", lambda pid: pid == 17)

    assert file_store_module.FileQueueStore._pid_alive(17) is True


def test_damaged_lock_metadata_is_reclaimed_after_short_grace(tmp_path) -> None:
    queue = FileSpoolQueue(tmp_path / "queue")
    _enqueue(queue)
    lock_dir = queue.location / "locks" / "demo__chroma.lock"
    lock_dir.mkdir()
    (lock_dir / "owner.json").write_text('{"pid":"损坏"}', encoding="utf-8")
    stale_at = time.time() - 2.0
    os.utime(lock_dir, (stale_at, stale_at))

    claims = queue.claim(
        owner_token="worker-1", projects=None, limit=1, timeout_sec=_TIMEOUT,
    )

    assert len(claims) == 1
    assert not lock_dir.exists()


@pytest.mark.parametrize("owner", ["live-local", "remote-host"])
def test_live_or_cross_host_lock_is_never_force_removed(tmp_path, owner) -> None:
    queue = FileSpoolQueue(tmp_path / "queue")
    _enqueue(queue)
    lock_dir = queue.location / "locks" / "demo__chroma.lock"
    lock_dir.mkdir()
    payload = {
        "pid": os.getpid(),
        "host": "remote.example" if owner == "remote-host" else socket.gethostname(),
        "owner_token": owner,
        "created_at": time.time() - 10.0,
    }
    (lock_dir / "owner.json").write_text(
        json.dumps(payload), encoding="utf-8",
    )

    assert queue.claim(
        owner_token="worker-1", projects=None, limit=1, timeout_sec=_TIMEOUT,
    ) == []
    assert lock_dir.exists()


def test_state_deletion_fsyncs_deleted_entry_parent(tmp_path, monkeypatch) -> None:
    queue = FileSpoolQueue(tmp_path / "queue")
    _enqueue(queue)
    claim = _claim(queue)
    deleted_from: list[object] = []
    original_unlink = file_store_module.durable_unlink

    def _durable_unlink(path):
        deleted_from.append(path.parent)
        return original_unlink(path)

    monkeypatch.setattr(file_store_module, "durable_unlink", _durable_unlink)

    assert queue.retry(claim, reason="测试重试", timeout_sec=_TIMEOUT) is True

    assert queue.location / "active" in deleted_from


def test_peek_skips_single_corrupt_or_concurrently_disappeared_record(tmp_path, monkeypatch) -> None:
    queue = FileSpoolQueue(tmp_path / "queue")
    _enqueue(queue, project="healthy")
    corrupt = queue.location / "pending" / "corrupt__chroma.json"
    corrupt.write_text('{"enqueued_at":1e999}', encoding="utf-8")
    vanished = queue.location / "pending" / "vanished__chroma.json"
    vanished.write_text("{}", encoding="utf-8")
    original = queue._store.load_record

    def _load_record(path, phase):
        if path == vanished:
            path.unlink(missing_ok=True)
        return original(path, phase)

    monkeypatch.setattr(queue._store, "load_record", _load_record)

    assert [job.project_id for job in queue.peek()] == ["healthy"]


def test_atomic_write_does_not_delete_foreign_temp_file(tmp_path) -> None:
    queue = FileSpoolQueue(tmp_path / "queue")
    foreign = queue.location / "pending" / ".demo__chroma.json.foreign.tmp"
    foreign.write_text("外部临时文件", encoding="utf-8")

    _enqueue(queue)

    assert foreign.read_text(encoding="utf-8") == "外部临时文件"
