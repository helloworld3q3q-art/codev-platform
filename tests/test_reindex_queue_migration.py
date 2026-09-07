"""reindex 队列 legacy 检测与精确 pending 迁移契约。"""
from __future__ import annotations

from codev_platform.reindex.file_queue import FileSpoolQueue
from codev_platform.reindex import queue_ports
from codev_platform.reindex.queue_ports import Job, JobMeta, QueueSnapshot


def test_job保留_pending_version与_owner_token兼容尾字段() -> None:
    job = Job("demo", "chroma", 1.0)

    assert job.pending_version is None
    assert job.owner_token is None


def test_公共端口暴露结构化_pending迁移契约() -> None:
    assert queue_ports.PendingMigrationOutcome.MIGRATED.value == "migrated"
    assert queue_ports.PendingMigrationResult(
        queue_ports.PendingMigrationOutcome.MIGRATED,
    ).migrated is True


def test_兼容队列入口重导出_pending迁移公共类型() -> None:
    from codev_platform.reindex.queue import PendingMigrationOutcome, PendingMigrationResult

    assert PendingMigrationOutcome.BUSY.value == "busy"
    assert PendingMigrationResult(PendingMigrationOutcome.NOT_PENDING).migrated is False


def test_legacy检测纯读取_pending_active与过期_active() -> None:
    from codev_platform.reindex.queue_migration import (
        LegacyQueueReason,
        detect_legacy_queue_entries,
    )

    snapshot = QueueSnapshot(
        pending=[Job("demo", "chroma", 1.0, meta=JobMeta(target_commit="HEAD"))],
        active=[Job("demo", "codegraph", 2.0, token="claim-1")],
        expired_active=[Job("demo", "ingest", 3.0, token="claim-2", meta=JobMeta(target_commit="abc"))],
        results=[Job("demo", "code_vec", 4.0, meta=JobMeta(target_commit="a" * 40))],
    )

    entries = detect_legacy_queue_entries(snapshot, stable_owner_token="stable-owner")

    assert [(entry.phase, entry.reason) for entry in entries] == [
        ("pending", LegacyQueueReason.SYMBOLIC_TARGET_COMMIT),
        ("active", LegacyQueueReason.MISSING_TARGET_COMMIT),
        ("active", LegacyQueueReason.MISSING_OWNER_TOKEN),
        ("expired_active", LegacyQueueReason.NONCANONICAL_TARGET_COMMIT),
        ("expired_active", LegacyQueueReason.MISSING_OWNER_TOKEN),
    ]


def test_legacy检测标识无_owner的_active以阻止无证恢复() -> None:
    from codev_platform.reindex.queue_migration import (
        LegacyQueueReason,
        detect_legacy_queue_entries,
    )

    snapshot = QueueSnapshot(
        active=[Job(
            "demo",
            "chroma",
            1.0,
            token="claim-legacy",
            meta=JobMeta(target_commit="a" * 40),
        )],
        expired_active=[Job(
            "demo",
            "codegraph",
            2.0,
            token="claim-stable",
            meta=JobMeta(target_commit="b" * 40),
            owner_token="stable-owner",
        )],
    )

    entries = detect_legacy_queue_entries(snapshot, stable_owner_token="stable-owner")

    assert [(entry.phase, entry.reason) for entry in entries] == [
        ("active", LegacyQueueReason.MISSING_OWNER_TOKEN),
    ]


def test_file快照仅给_active映射_owner_token(tmp_path) -> None:
    queue = FileSpoolQueue(tmp_path / "queue", owner_token="stable-owner")
    queue.enqueue("demo", "chroma", JobMeta(target_commit="HEAD"))
    queue.pending(limit=1)
    queue.enqueue("demo", "chroma", JobMeta(target_commit="HEAD"))

    snapshot = queue.snapshot()

    assert snapshot.active[0].owner_token == "stable-owner"
    assert snapshot.pending[0].owner_token is None


def test_file快照保留缺_owner的旧过期_active供受控处置(tmp_path) -> None:
    import time

    queue = FileSpoolQueue(tmp_path / "queue")
    path = queue.location / "active" / "demo__chroma.json"
    queue._store.write_json_atomic(path, {
        "project_id": "demo",
        "kind": "chroma",
        "enqueued_at": 1.0,
        "meta": {
            "source": "legacy",
            "pull_policy": None,
            "target_commit": "HEAD",
        },
        "claim_token": "legacy-claim",
        "lease_expires_at": time.time() - 1.0,
    })

    snapshot = queue.snapshot()

    assert len(snapshot.expired_active) == 1
    assert snapshot.expired_active[0].token == "legacy-claim"
    assert snapshot.expired_active[0].owner_token is None


def test_file_pending迁移以版本CAS更新元数据并轮换版本(tmp_path) -> None:
    from codev_platform.reindex.queue_ports import PendingMigrationOutcome

    queue = FileSpoolQueue(tmp_path / "queue")
    queue.enqueue("demo", "chroma", JobMeta(target_commit="HEAD"))
    expected = queue.snapshot().pending[0]

    result = queue.migrate_pending(
        expected,
        JobMeta(source="migration", pull_policy="never", target_commit="a" * 40),
        timeout_sec=0.3,
    )
    migrated = queue.snapshot().pending[0]

    assert expected.pending_version is not None
    assert result.outcome is PendingMigrationOutcome.MIGRATED
    assert migrated.meta.target_commit == "a" * 40
    assert migrated.pending_version is not None
    assert migrated.pending_version != expected.pending_version


def test_file_pending迁移不触碰扫描后出现的legacy根标记(tmp_path) -> None:
    from codev_platform.reindex.queue_ports import PendingMigrationOutcome

    queue = FileSpoolQueue(tmp_path / "queue")
    queue.enqueue("demo", "chroma", JobMeta(target_commit="HEAD"))
    expected = queue.snapshot().pending[0]
    legacy = queue.location / "demo__chroma"
    legacy.write_bytes(b'{"target_commit":"HEAD"}')
    before = legacy.read_bytes()

    result = queue.migrate_pending(
        expected,
        JobMeta(target_commit="a" * 40),
        timeout_sec=0.3,
    )

    assert result.outcome is PendingMigrationOutcome.MIGRATED
    assert legacy.read_bytes() == before


def test_file_pending迁移仅替换dirty_pending而不改active负载(tmp_path) -> None:
    queue = FileSpoolQueue(tmp_path / "queue", owner_token="stable-owner")
    queue.enqueue("demo", "chroma", JobMeta(target_commit="HEAD"))
    queue.claim(owner_token="stable-owner", projects=None, limit=1, timeout_sec=0.3)
    queue.enqueue("demo", "chroma", JobMeta(target_commit="HEAD"))
    expected = queue.snapshot().pending[0]
    active = queue.location / "active" / "demo__chroma.json"
    before = active.read_bytes()

    queue.migrate_pending(
        expected,
        JobMeta(target_commit="a" * 40),
        timeout_sec=0.3,
    )

    assert active.read_bytes() == before
    assert queue.snapshot().pending[0].meta.target_commit == "a" * 40


def test_file_pending耐久写入后不因迟到预算误报busy(tmp_path) -> None:
    from codev_platform.reindex.file_queue_migration import migrate_pending_locked
    from codev_platform.reindex.queue_ports import PendingMigrationOutcome, QueueOperationTimeout

    class _Deadline:
        def __init__(self) -> None:
            self.checks = 0

        def check(self) -> None:
            self.checks += 1
            if self.checks >= 3:
                raise QueueOperationTimeout("写入后预算耗尽")

    queue = FileSpoolQueue(tmp_path / "queue")
    queue.enqueue("demo", "chroma", JobMeta(target_commit="HEAD"))
    expected = queue.snapshot().pending[0]

    result = migrate_pending_locked(
        queue._store,
        expected,
        JobMeta(target_commit="a" * 40),
        _Deadline(),
    )

    assert result.outcome is PendingMigrationOutcome.MIGRATED
    assert queue.snapshot().pending[0].meta.target_commit == "a" * 40
