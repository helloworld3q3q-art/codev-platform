"""File pending 精确 CAS 迁移的窄写侧实现。"""
from __future__ import annotations

from collections.abc import Callable
from contextlib import AbstractContextManager
from dataclasses import replace

from .file_queue_codec import pending_payload
from .file_queue_store import FileQueueStore, OperationDeadline, PENDING
from .queue_ports import (
    Job,
    JobMeta,
    PendingMigrationOutcome,
    PendingMigrationResult,
    QueueOperationTimeout,
    validate_job_identity,
    validate_target_commit,
)

KeyLocker = Callable[[str, OperationDeadline], AbstractContextManager[None]]


def migrate_pending_locked(
    store: FileQueueStore,
    expected: Job,
    new_meta: JobMeta,
    deadline: OperationDeadline,
) -> PendingMigrationResult:
    """在持有单 key 锁时，仅覆盖版本完全一致的 pending 负载。"""
    if type(expected) is not Job:
        raise ValueError("expected 必须是 Job")
    if type(new_meta) is not JobMeta:
        raise ValueError("new_meta 必须是 JobMeta")
    validate_job_identity(expected.project_id, expected.kind)
    validate_target_commit(new_meta.target_commit)
    if expected.token is not None or expected.owner_token is not None:
        return PendingMigrationResult(PendingMigrationOutcome.NOT_PENDING)
    if expected.pending_version is None:
        return PendingMigrationResult(PendingMigrationOutcome.VERSION_CONFLICT)
    deadline.check()
    current = store.read_phase(PENDING, expected.key)
    if current is None:
        return PendingMigrationResult(PendingMigrationOutcome.NOT_PENDING)
    if not current.pending_has_no_claim_or_owner:
        return PendingMigrationResult(PendingMigrationOutcome.NOT_PENDING)
    if current.pending_version is None:
        return PendingMigrationResult(PendingMigrationOutcome.NOT_PENDING)
    if current.pending_version != expected.pending_version:
        return PendingMigrationResult(PendingMigrationOutcome.VERSION_CONFLICT)
    if current.pending_version.startswith("pt:") and current.strict_pending_meta is None:
        return PendingMigrationResult(PendingMigrationOutcome.NOT_PENDING)
    if current.pending_version.startswith("pt:") and (
        current.enqueued_at != expected.enqueued_at
        or current.strict_pending_meta != expected.meta
    ):
        return PendingMigrationResult(PendingMigrationOutcome.VERSION_CONFLICT)
    path = store.phase_path(PENDING, expected.key)
    if path is None:
        raise ValueError("pending 队列 key 无效")
    deadline.check()
    store.write_json_atomic(path, pending_payload(replace(current, meta=new_meta)))
    return PendingMigrationResult(PendingMigrationOutcome.MIGRATED)


def migrate_pending(
    store: FileQueueStore,
    lock: KeyLocker,
    expected: Job,
    new_meta: JobMeta,
    *,
    timeout_sec: float,
) -> PendingMigrationResult:
    """把 File 锁竞争规范化为结构化 BUSY 结果。"""
    deadline = OperationDeadline.start(timeout_sec)
    try:
        with lock(expected.key, deadline):
            return migrate_pending_locked(store, expected, new_meta, deadline)
    except QueueOperationTimeout:
        return PendingMigrationResult(PendingMigrationOutcome.BUSY)


__all__ = ["migrate_pending", "migrate_pending_locked"]
