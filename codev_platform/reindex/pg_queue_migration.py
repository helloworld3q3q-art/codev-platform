"""Pg pending 精确 CAS 迁移的独立事务实现。"""
from __future__ import annotations

from .pg_queue_codec import (
    legacy_pending_version_from_facts,
    meta_json,
    strict_meta_from_json,
)
from .pg_queue_sql import (
    migrate_pending_token_sql,
    migrate_pending_xmin_sql,
    pending_migration_read_sql,
)
from .pg_queue_tx import bounded_connection, execute_with_budget
from .queue_ports import (
    Job,
    JobMeta,
    PendingMigrationOutcome,
    PendingMigrationResult,
    QueueOperationTimeout,
    validate_job_identity,
    validate_target_commit,
)


class PgPendingMigrationError(RuntimeError):
    """Pg pending 迁移失败时屏蔽后端连接细节。"""


def _pending_version(row: tuple[object, ...]) -> tuple[str, str] | None:
    token = row[6]
    if row[1] is not None:
        return ("token", token) if type(token) is str and token.strip() else None
    version = legacy_pending_version_from_facts(
        pending_enqueued_at=row[1],
        pending_meta=row[2],
        status=row[3],
        claim_token=row[4],
        owner_token=row[5],
        pending_token=token,
        quarantine=row[7],
        xmin=row[8],
    )
    return ("xmin", version[3:]) if version is not None else None


def _has_pending(row: tuple[object, ...]) -> bool:
    pending_enqueued_at = row[1]
    pending_meta = row[2]
    quarantine = row[7]
    if quarantine is not None:
        return False
    if pending_enqueued_at is not None:
        return _pending_version(row) is not None and strict_meta_from_json(pending_meta) is not None
    return _pending_version(row) is not None


def _same_v2_pending(expected: Job, row: tuple[object, ...]) -> bool:
    """同一 pending token 仍须对应同一入队时间与元数据事实。"""
    current_meta = strict_meta_from_json(row[2])
    return (
        current_meta is not None
        and expected.enqueued_at == row[1]
        and expected.meta == current_meta
    )


def _expected_version(expected: Job) -> tuple[str, str] | None:
    version = expected.pending_version
    if type(version) is not str:
        return None
    if version.startswith("pt:") and version[3:]:
        return ("token", version[3:])
    if version.startswith("px:") and version[3:]:
        return ("xmin", version[3:])
    return None


def migrate_pending(queue, expected: Job, new_meta: JobMeta, *, timeout_sec: float) -> PendingMigrationResult:
    """在同一事务内锁定并仅更新 pending 字段，任何竞争均返回确定结果。"""
    if type(expected) is not Job:
        raise ValueError("expected 必须是 Job")
    if type(new_meta) is not JobMeta:
        raise ValueError("new_meta 必须是 JobMeta")
    validate_job_identity(expected.project_id, expected.kind)
    validate_target_commit(new_meta.target_commit)
    expected_version = _expected_version(expected)
    if expected.token is not None or expected.owner_token is not None:
        return PendingMigrationResult(PendingMigrationOutcome.NOT_PENDING)
    if expected_version is None:
        return PendingMigrationResult(PendingMigrationOutcome.VERSION_CONFLICT)
    try:
        budget = queue._ready(timeout_sec)
        with bounded_connection(queue._pool, budget) as conn:
            cursor = execute_with_budget(
                conn,
                budget,
                pending_migration_read_sql(queue._t),
                (expected.project_id, expected.kind),
            )
            row = cursor.fetchone()
            if row is None:
                return PendingMigrationResult(PendingMigrationOutcome.NOT_PENDING)
            if type(row) is not tuple or len(row) != 9:
                raise PgPendingMigrationError("Pg pending 迁移读取结果无效")
            if not _has_pending(row):
                return PendingMigrationResult(PendingMigrationOutcome.NOT_PENDING)
            current_version = _pending_version(row)
            if current_version != expected_version:
                return PendingMigrationResult(PendingMigrationOutcome.VERSION_CONFLICT)
            if expected_version[0] == "token" and not _same_v2_pending(expected, row):
                return PendingMigrationResult(PendingMigrationOutcome.VERSION_CONFLICT)
            sql = (
                migrate_pending_token_sql(queue._t)
                if expected_version[0] == "token"
                else migrate_pending_xmin_sql(queue._t)
            )
            updated = execute_with_budget(
                conn,
                budget,
                sql,
                (
                    meta_json(new_meta),
                    expected.project_id,
                    expected.kind,
                    expected_version[1],
                ),
            )
            outcome = (
                PendingMigrationOutcome.MIGRATED
                if updated.rowcount == 1
                else PendingMigrationOutcome.VERSION_CONFLICT
            )
            return PendingMigrationResult(outcome)
    except QueueOperationTimeout:
        return PendingMigrationResult(PendingMigrationOutcome.BUSY)
    except PgPendingMigrationError:
        raise
    except Exception:  # noqa: BLE001 - 禁止把 DSN 等后端细节暴露给运维面
        pass
    raise PgPendingMigrationError("Pg pending 迁移失败") from None


__all__ = ["PgPendingMigrationError", "migrate_pending"]
