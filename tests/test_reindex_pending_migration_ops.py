"""严格 JSON pending 映射与受控 CAS 迁移测试。"""
from __future__ import annotations

import pytest

from codev_platform.reindex.queue_ports import (
    Job,
    JobMeta,
    PendingMigrationOutcome,
    PendingMigrationResult,
    QueueSnapshot,
)


class _Queue:
    def __init__(self, pending: list[Job], *, outcome: PendingMigrationOutcome) -> None:
        self.snapshot_value = QueueSnapshot(pending=pending)
        self.outcome = outcome
        self.calls: list[tuple[Job, JobMeta, float]] = []
        self.snapshot_calls = 0

    def snapshot(self) -> QueueSnapshot:
        self.snapshot_calls += 1
        return self.snapshot_value

    def migrate_pending(self, expected: Job, new_meta: JobMeta, *, timeout_sec: float) -> PendingMigrationResult:
        self.calls.append((expected, new_meta, timeout_sec))
        if self.outcome is PendingMigrationOutcome.MIGRATED:
            self.snapshot_value = QueueSnapshot()
        return PendingMigrationResult(self.outcome)


def _legacy_pending(version: str = "pt:pending-token") -> Job:
    return Job(
        "demo",
        "chroma",
        1.0,
        meta=JobMeta(target_commit="HEAD"),
        pending_version=version,
    )


def _mapping(*, version: str = "pt:pending-token", target: str = "a" * 40) -> bytes:
    return (
        '{"schema_version":1,"items":[{'
        '"project_id":"demo","kind":"chroma",'
        f'"pending_version":"{version}","target_commit":"{target}"'
        "}]}"
    ).encode()


def test_严格JSON映射拒绝重复键与额外字段() -> None:
    from codev_platform.ops.reindex_migration import (
        PendingMigrationMappingError,
        parse_pending_migration_mapping,
    )

    duplicate = b'{"schema_version":1,"schema_version":1,"items":[]}'
    extra = b'{"schema_version":1,"items":[],"extra":true}'

    for raw in (duplicate, extra):
        with pytest.raises(PendingMigrationMappingError):
            parse_pending_migration_mapping(raw)


def test_严格JSON映射拒绝重复身份与HEAD目标() -> None:
    from codev_platform.ops.reindex_migration import (
        PendingMigrationMappingError,
        parse_pending_migration_mapping,
    )

    duplicate_identity = (
        b'{"schema_version":1,"items":['
        b'{"project_id":"demo","kind":"chroma","pending_version":"pt:a","target_commit":"'
        + b"a" * 40
        + b'"},{"project_id":"demo","kind":"chroma","pending_version":"pt:b","target_commit":"'
        + b"b" * 40
        + b'"}]}'
    )

    for raw in (duplicate_identity, _mapping(target="HEAD")):
        with pytest.raises(PendingMigrationMappingError):
            parse_pending_migration_mapping(raw)


def test_pending映射dry_run只输出计划且绝不解析HEAD或写队列() -> None:
    from codev_platform.ops.reindex_migration import (
        parse_pending_migration_mapping,
        run_pending_queue_migration,
    )

    queue = _Queue([_legacy_pending()], outcome=PendingMigrationOutcome.MIGRATED)

    report = run_pending_queue_migration(
        queue,
        parse_pending_migration_mapping(_mapping()),
        confirmed=False,
        run_lock_acquired=False,
        legacy_worker_stopped=False,
        timeout_sec=0.3,
    )

    assert report.executed is False
    assert [item.action for item in report.items] == ["planned"]
    assert queue.calls == []


@pytest.mark.parametrize(
    ("run_lock_acquired", "legacy_worker_stopped"),
    [(False, True), (True, False)],
)
def test_pending确认迁移必须持有run_lock且持有旧worker停止证明(run_lock_acquired, legacy_worker_stopped) -> None:
    from codev_platform.ops.reindex_migration import (
        LegacyMigrationPreconditionError,
        parse_pending_migration_mapping,
        run_pending_queue_migration,
    )

    queue = _Queue([_legacy_pending()], outcome=PendingMigrationOutcome.MIGRATED)

    with pytest.raises(LegacyMigrationPreconditionError):
        run_pending_queue_migration(
            queue,
            parse_pending_migration_mapping(_mapping()),
            confirmed=True,
            run_lock_acquired=run_lock_acquired,
            legacy_worker_stopped=legacy_worker_stopped,
            timeout_sec=0.3,
        )
    assert queue.calls == []


def test_pending映射缺失额外或版本不一致时失败关闭不写队列() -> None:
    from codev_platform.ops.reindex_migration import (
        PendingMigrationMappingError,
        parse_pending_migration_mapping,
        run_pending_queue_migration,
    )

    queue = _Queue([_legacy_pending()], outcome=PendingMigrationOutcome.MIGRATED)
    missing = parse_pending_migration_mapping(b'{"schema_version":1,"items":[]}')

    with pytest.raises(PendingMigrationMappingError):
        run_pending_queue_migration(
            queue,
            missing,
            confirmed=False,
            run_lock_acquired=False,
            legacy_worker_stopped=False,
            timeout_sec=0.3,
        )

    conflict = run_pending_queue_migration(
        queue,
        parse_pending_migration_mapping(_mapping(version="pt:other-token")),
        confirmed=True,
        run_lock_acquired=True,
        legacy_worker_stopped=True,
        timeout_sec=0.3,
    )

    assert [item.action for item in conflict.items] == ["version_conflict"]
    assert queue.calls == []


def test_pending确认迁移将精确快照与显式OID交给后端CAS() -> None:
    from codev_platform.ops.reindex_migration import (
        parse_pending_migration_mapping,
        run_pending_queue_migration,
    )

    pending = _legacy_pending()
    queue = _Queue([pending], outcome=PendingMigrationOutcome.MIGRATED)

    report = run_pending_queue_migration(
        queue,
        parse_pending_migration_mapping(_mapping()),
        confirmed=True,
        run_lock_acquired=True,
        legacy_worker_stopped=True,
        timeout_sec=0.3,
    )

    assert report.executed is True
    assert report.completed is True
    assert report.remaining_count == 0
    assert [item.action for item in report.items] == ["migrated"]
    assert queue.calls == [(pending, JobMeta(target_commit="a" * 40), 0.3)]
    assert queue.snapshot_calls == 2


def test_组合迁移先完成完整pending预检再在同一窗口写入并复审() -> None:
    from codev_platform.ops.reindex_migration import (
        parse_pending_migration_mapping,
        run_queue_migration_window,
    )

    pending = _legacy_pending()
    stale_active = Job(
        "demo",
        "codegraph",
        2.0,
        token="stale-active",
        meta=JobMeta(target_commit="HEAD"),
        lease_expires_at=1.0,
    )
    fresh_active = Job(
        "demo",
        "codegraph",
        3.0,
        token="fresh-active",
        meta=JobMeta(target_commit="HEAD"),
        lease_expires_at=2.0,
    )

    class _CombinedQueue(_Queue):
        def __init__(self) -> None:
            super().__init__([pending], outcome=PendingMigrationOutcome.MIGRATED)
            self.snapshot_value = QueueSnapshot(pending=[pending], expired_active=[stale_active])
            self.events: list[str] = []

        def migrate_pending(self, expected: Job, new_meta: JobMeta, *, timeout_sec: float) -> PendingMigrationResult:
            self.events.append("pending")
            self.calls.append((expected, new_meta, timeout_sec))
            self.snapshot_value = QueueSnapshot(expired_active=[fresh_active])
            return PendingMigrationResult(PendingMigrationOutcome.MIGRATED)

        def reject_unowned_legacy_active(self, expected: Job, *, reason: str, timeout_sec: float) -> bool:
            assert expected == fresh_active
            assert reason == "受控淘汰无 owner legacy active"
            self.events.append("active")
            self.snapshot_value = QueueSnapshot()
            return True

    queue = _CombinedQueue()

    report = run_queue_migration_window(
        queue,
        pending_mapping=parse_pending_migration_mapping(_mapping()),
        confirmed=True,
        run_lock_acquired=True,
        legacy_worker_stopped=True,
        bootstrap=True,
        timeout_sec=0.3,
    )

    assert queue.events == ["pending", "active"]
    assert queue.snapshot_calls == 3
    assert report.completed is True
    assert report.pending.completed is True
    assert report.active.completed is True


def test_组合迁移缺少pending映射时不允许先改active() -> None:
    from codev_platform.ops.reindex_migration import (
        PendingMigrationMappingError,
        run_queue_migration_window,
    )

    active = Job(
        "demo",
        "codegraph",
        2.0,
        token="legacy-active",
        meta=JobMeta(target_commit="HEAD"),
        lease_expires_at=1.0,
    )

    class _CombinedQueue(_Queue):
        def __init__(self) -> None:
            super().__init__([_legacy_pending()], outcome=PendingMigrationOutcome.MIGRATED)
            self.snapshot_value = QueueSnapshot(
                pending=[_legacy_pending()], expired_active=[active],
            )
            self.active_writes = 0

        def reject_unowned_legacy_active(self, expected: Job, *, reason: str, timeout_sec: float) -> bool:
            self.active_writes += 1
            return True

    queue = _CombinedQueue()

    with pytest.raises(PendingMigrationMappingError):
        run_queue_migration_window(
            queue,
            pending_mapping=None,
            confirmed=True,
            run_lock_acquired=True,
            legacy_worker_stopped=True,
            bootstrap=True,
            timeout_sec=0.3,
        )
    assert queue.calls == []
    assert queue.active_writes == 0


def test_组合迁移pending_CAS冲突后不得继续收口active() -> None:
    from codev_platform.ops.reindex_migration import (
        parse_pending_migration_mapping,
        run_queue_migration_window,
    )

    pending = _legacy_pending()
    active = Job(
        "demo",
        "codegraph",
        2.0,
        token="legacy-active",
        meta=JobMeta(target_commit="HEAD"),
        lease_expires_at=1.0,
    )

    class _ConflictQueue(_Queue):
        def __init__(self) -> None:
            super().__init__([pending], outcome=PendingMigrationOutcome.VERSION_CONFLICT)
            self.snapshot_value = QueueSnapshot(pending=[pending], expired_active=[active])
            self.active_writes = 0

        def reject_unowned_legacy_active(self, expected: Job, *, reason: str, timeout_sec: float) -> bool:
            self.active_writes += 1
            return True

    queue = _ConflictQueue()

    report = run_queue_migration_window(
        queue,
        pending_mapping=parse_pending_migration_mapping(_mapping()),
        confirmed=True,
        run_lock_acquired=True,
        legacy_worker_stopped=True,
        bootstrap=True,
        timeout_sec=0.3,
    )

    assert queue.calls == [(pending, JobMeta(target_commit="a" * 40), 0.3)]
    assert queue.active_writes == 0
    assert report.completed is False
    assert report.active.executed is False
    assert [item.action for item in report.pending.items] == ["version_conflict"]


def test_组合迁移pending宣称成功但fresh快照仍有legacy时不得收口active() -> None:
    from codev_platform.ops.reindex_migration import (
        parse_pending_migration_mapping,
        run_queue_migration_window,
    )

    pending = _legacy_pending()
    active = Job(
        "demo",
        "codegraph",
        2.0,
        token="legacy-active",
        meta=JobMeta(target_commit="HEAD"),
        lease_expires_at=1.0,
    )

    class _ResidualQueue(_Queue):
        def __init__(self) -> None:
            super().__init__([pending], outcome=PendingMigrationOutcome.MIGRATED)
            self.snapshot_value = QueueSnapshot(pending=[pending], expired_active=[active])
            self.active_writes = 0

        def migrate_pending(self, expected: Job, new_meta: JobMeta, *, timeout_sec: float) -> PendingMigrationResult:
            self.calls.append((expected, new_meta, timeout_sec))
            return PendingMigrationResult(PendingMigrationOutcome.MIGRATED)

        def reject_unowned_legacy_active(self, expected: Job, *, reason: str, timeout_sec: float) -> bool:
            self.active_writes += 1
            return True

    queue = _ResidualQueue()

    report = run_queue_migration_window(
        queue,
        pending_mapping=parse_pending_migration_mapping(_mapping()),
        confirmed=True,
        run_lock_acquired=True,
        legacy_worker_stopped=True,
        bootstrap=True,
        timeout_sec=0.3,
    )

    assert queue.active_writes == 0
    assert queue.snapshot_calls == 2
    assert report.completed is False
    assert report.active.executed is False
    assert report.pending.remaining_count == 1
