"""legacy 队列运维迁移的前置门禁与脱敏报告测试。"""
from __future__ import annotations

import pytest

from codev_platform.reindex.queue_ports import Job, JobMeta, QueueSnapshot


class _Queue:
    def __init__(self, snapshot: QueueSnapshot) -> None:
        self.snapshot_value = snapshot
        self.unowned_rejected: list[Job] = []

    def snapshot(self) -> QueueSnapshot:
        return self.snapshot_value

    def recover_owned(self, *, owner_token: str, timeout_sec: float):
        raise AssertionError("本测试不应恢复有 owner 任务")

    def retry(self, claim, *, reason: str, timeout_sec: float) -> bool:
        raise AssertionError("本测试不应重试任务")

    def reject(self, claim, *, reason: str, timeout_sec: float) -> bool:
        raise AssertionError("本测试不应拒绝有 owner 任务")

    def reject_unowned_legacy_active(self, expected: Job, *, reason: str, timeout_sec: float) -> bool:
        self.unowned_rejected.append(expected)
        return True


def _invalid_unowned_expired() -> Job:
    return Job(
        "demo",
        "chroma",
        1.0,
        token="legacy-claim",
        meta=JobMeta(target_commit="HEAD"),
        lease_expires_at=2.0,
    )


def test_dry_run仅生成脱敏报告且不要求停worker或run_lock() -> None:
    from codev_platform.ops.reindex_migration import run_legacy_queue_migration

    queue = _Queue(QueueSnapshot(expired_active=[_invalid_unowned_expired()]))

    report = run_legacy_queue_migration(
        queue,
        confirmed=False,
        run_lock_acquired=False,
        legacy_worker_stopped=False,
        timeout_sec=0.3,
    )

    assert report.executed is False
    assert [item.key for item in report.items] == ["demo__chroma"]
    assert queue.unowned_rejected == []
    assert "legacy-claim" not in repr(report)


@pytest.mark.parametrize(
    ("run_lock_acquired", "legacy_worker_stopped", "message"),
    [
        (False, True, "run lock"),
        (True, False, "停止证明"),
    ],
)
def test_confirmed处置必须持有run_lock且worker停止(
    run_lock_acquired,
    legacy_worker_stopped,
    message,
) -> None:
    from codev_platform.ops.reindex_migration import (
        LegacyMigrationPreconditionError,
        run_legacy_queue_migration,
    )

    queue = _Queue(QueueSnapshot(expired_active=[_invalid_unowned_expired()]))

    with pytest.raises(LegacyMigrationPreconditionError, match=message):
        run_legacy_queue_migration(
            queue,
            confirmed=True,
            run_lock_acquired=run_lock_acquired,
            legacy_worker_stopped=legacy_worker_stopped,
            bootstrap=True,
            timeout_sec=0.3,
        )
    assert queue.unowned_rejected == []


def test_confirmed仅处置过期legacy_active并返回脱敏执行结果() -> None:
    from codev_platform.ops.reindex_migration import run_legacy_queue_migration

    queue = _Queue(QueueSnapshot(expired_active=[_invalid_unowned_expired()]))

    report = run_legacy_queue_migration(
        queue,
        confirmed=True,
        run_lock_acquired=True,
        legacy_worker_stopped=True,
        bootstrap=True,
        timeout_sec=0.3,
    )

    assert report.executed is True
    assert report.completed is False
    assert queue.unowned_rejected == [_invalid_unowned_expired()]
    assert [item.action for item in report.items] == ["rejected"]


def test_pending只报告精确版本与原因而不解析或写入HEAD() -> None:
    from codev_platform.ops.reindex_migration import (
        LegacyMigrationItem,
        run_legacy_queue_migration,
    )

    pending = Job(
        "demo",
        "chroma",
        1.0,
        meta=JobMeta(target_commit="HEAD"),
        pending_version="pt:pending-token",
    )
    queue = _Queue(QueueSnapshot(pending=[pending]))

    report = run_legacy_queue_migration(
        queue,
        confirmed=False,
        run_lock_acquired=False,
        legacy_worker_stopped=False,
        timeout_sec=0.3,
    )

    assert report.pending_unmigrated_count == 1
    assert report.items == (
        LegacyMigrationItem(
            "pending",
            "demo__chroma",
            ("symbolic_target_commit",),
            "pending_mapping_required",
            "pt:pending-token",
        ),
    )


def test_confirmed后端异常不向运维面泄露连接细节() -> None:
    from codev_platform.ops.reindex_migration import (
        LegacyMigrationPreconditionError,
        run_legacy_queue_migration,
    )

    class _BrokenQueue(_Queue):
        def reject_unowned_legacy_active(self, expected: Job, *, reason: str, timeout_sec: float) -> bool:
            raise RuntimeError("postgresql://user:secret@db.example/reindex")

    queue = _BrokenQueue(QueueSnapshot(expired_active=[_invalid_unowned_expired()]))

    with pytest.raises(LegacyMigrationPreconditionError) as raised:
        run_legacy_queue_migration(
            queue,
            confirmed=True,
            run_lock_acquired=True,
            legacy_worker_stopped=True,
            bootstrap=True,
            timeout_sec=0.3,
        )
    assert "secret" not in str(raised.value)
