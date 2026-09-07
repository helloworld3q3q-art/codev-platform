"""数据库迁移状态机：分类、精确接管、前向升级与事务内后验。"""

from __future__ import annotations

from collections.abc import Mapping

from codev_platform.web.db.migration_contract import (
    DatabaseMigrationError,
    DatabaseMigrationPorts,
    DatabaseVerificationPorts,
    DatabaseVerificationReport,
    MigrationPlan,
    MigrationReport,
    RevisionState,
    SchemaFingerprint,
)


def migrate_database(
    plan: MigrationPlan,
    ports: DatabaseMigrationPorts,
) -> MigrationReport:
    """在单一外层事务内完成接管或升级；任一后验失败即整体回滚。"""
    if type(plan) is not MigrationPlan:
        raise DatabaseMigrationError("数据库迁移计划无效")
    if type(ports) is not DatabaseMigrationPorts:
        raise DatabaseMigrationError("数据库迁移端口无效")
    selected = ports.validate()
    try:
        with selected.transaction():
            selected.acquire_lock()
            before = _require_revision_state(selected.read_revision())
            previous = before.revision
            adopted: str | None = None
            if before.table_present:
                if before.revision not in plan.revisions:
                    raise DatabaseMigrationError("数据库 revision 不属于当前 release 迁移链")
            else:
                target = _require_fingerprint(selected.read_fingerprint())
                if target.table_count > 0:
                    references = _require_references(
                        selected.build_reference_fingerprints(plan.revisions),
                        plan.revisions,
                    )
                    matches = tuple(
                        revision
                        for revision in plan.revisions
                        if references[revision] == target
                    )
                    if len(matches) != 1:
                        raise DatabaseMigrationError("存量无版本数据库无法唯一匹配已知 schema")
                    adopted = matches[0]
                    selected.stamp_revision(adopted)
            selected.upgrade_revision(plan.head)
            after = _require_revision_state(selected.read_revision())
            if not after.table_present or after.revision != plan.head:
                raise DatabaseMigrationError("数据库迁移后 revision 未到当前 head")
            head_reference = _require_references(
                selected.build_reference_fingerprints((plan.head,)),
                (plan.head,),
            )[plan.head]
            if _require_fingerprint(selected.read_fingerprint()) != head_reference:
                raise DatabaseMigrationError("数据库迁移后 schema 与当前 head 不一致")
            selected.verify_runtime_contract()
            return MigrationReport(
                previous_revision=previous,
                adopted_revision=adopted,
                head_revision=plan.head,
                legacy_adopted=adopted is not None,
            )
    except (KeyboardInterrupt, SystemExit, MemoryError):
        raise
    except DatabaseMigrationError:
        raise
    except Exception:
        raise DatabaseMigrationError("数据库迁移事务失败") from None


def verify_database_current(
    plan: MigrationPlan,
    ports: DatabaseVerificationPorts,
    *,
    expected_table_count: int,
) -> DatabaseVerificationReport:
    """在只读事务内独立证明当前 revision、受管表集合与运行契约。"""
    if type(plan) is not MigrationPlan:
        raise DatabaseMigrationError("数据库迁移计划无效")
    if type(expected_table_count) is not int or expected_table_count <= 0:
        raise DatabaseMigrationError("数据库验收表数量无效")
    selected = ports.validate()
    try:
        with selected.readonly_transaction():
            revision = _require_revision_state(selected.read_revision())
            if not revision.table_present or revision.revision != plan.head:
                raise DatabaseMigrationError("数据库 revision 未到当前 head")
            fingerprint = _require_fingerprint(selected.read_fingerprint())
            if fingerprint.table_count != expected_table_count:
                raise DatabaseMigrationError("数据库受管表集合不完整")
            selected.verify_runtime_contract()
            return DatabaseVerificationReport(
                head_revision=plan.head,
                schema_sha256=fingerprint.digest,
                table_count=fingerprint.table_count,
            )
    except (KeyboardInterrupt, SystemExit, MemoryError):
        raise
    except DatabaseMigrationError:
        raise
    except Exception:
        raise DatabaseMigrationError("数据库只读验收失败") from None


def _require_revision_state(value: object) -> RevisionState:
    if type(value) is not RevisionState:
        raise DatabaseMigrationError("数据库版本状态无效")
    return value


def _require_fingerprint(value: object) -> SchemaFingerprint:
    if type(value) is not SchemaFingerprint:
        raise DatabaseMigrationError("数据库 schema 指纹无效")
    return value


def _require_references(
    value: object,
    revisions: tuple[str, ...],
) -> dict[str, SchemaFingerprint]:
    if not isinstance(value, Mapping) or set(value) != set(revisions):
        raise DatabaseMigrationError("数据库参考 schema 集合无效")
    references = dict(value)
    if any(type(key) is not str or type(item) is not SchemaFingerprint for key, item in references.items()):
        raise DatabaseMigrationError("数据库参考 schema 集合无效")
    return references


__all__ = ["migrate_database", "verify_database_current"]
