"""Pg 队列 pending 迁移与 owner 视图契约。"""
from __future__ import annotations

import time

import pytest

from codev_platform.reindex.pg_queue import PgJobQueue
from codev_platform.reindex.queue_ports import Job, JobMeta, PendingMigrationOutcome
from tests.pg_queue_fakes import FakeCursor, unit_queue


def _active_row(*, lease_expires_at: float, owner: str = "stable-owner") -> tuple[object, ...]:
    return (
        "demo", "chroma", 1.0,
        None, None, "running",
        1.0, '{"source":"worker","pull_policy":"never","target_commit":null}',
        "claim-1", lease_expires_at,
        None, None, None, time.time(), owner,
        None, None, None, None, None, None, None,
    )


def test_pg_active与_expired视图映射_claimed_by_owner() -> None:
    active = PgJobQueue._active_job(_active_row(lease_expires_at=time.time() + 60))
    expired = PgJobQueue._expired_active_job(_active_row(lease_expires_at=time.time() - 60))

    assert active is not None and active.owner_token == "stable-owner"
    assert expired is not None and expired.owner_token == "stable-owner"


def test_pg无owner旧active拒绝只按claim_token与claimed_by为空围栏() -> None:
    queue = unit_queue(FakeCursor(rowcount=1))
    expected = Job(
        "demo",
        "chroma",
        1.0,
        token="legacy-claim",
        lease_expires_at=1.0,
        meta=JobMeta(target_commit="HEAD"),
    )

    assert queue.reject_unowned_legacy_active(
        expected,
        reason="受控淘汰无 owner legacy active",
        timeout_sec=0.3,
    ) is True

    sql, params = queue._pool._conn.calls[0]
    assert "claim_token = %s" in sql
    assert "claimed_by IS NULL" in sql
    assert "claimed_by = %s" not in sql
    assert "lease_expires_at = %s" in sql
    assert "lease_expires_at < EXTRACT(EPOCH FROM clock_timestamp())" in sql
    assert "result_status = 'failed'" in sql
    assert params == (
        "受控淘汰无 owner legacy active",
        "demo",
        "chroma",
        "legacy-claim",
        1.0,
    )


def test_pg有owner旧active在同一事务内以当前租约和围栏收口() -> None:
    queue = unit_queue(FakeCursor(rowcount=1))
    expected = Job(
        "demo",
        "chroma",
        1.0,
        token="legacy-claim",
        owner_token="old-owner",
        lease_expires_at=1.0,
        meta=JobMeta(target_commit="HEAD"),
    )

    assert queue.settle_expired_legacy_active(
        expected,
        action="reject",
        reason="受控淘汰 legacy active",
        timeout_sec=0.3,
    ) is True

    sql, params = queue._pool._conn.calls[0]
    assert "claim_token = %s" in sql and "claimed_by = %s" in sql
    assert "lease_expires_at = %s" in sql
    assert "lease_expires_at < EXTRACT(EPOCH FROM clock_timestamp())" in sql
    assert params == (
        "受控淘汰 legacy active",
        "demo",
        "chroma",
        "legacy-claim",
        "old-owner",
        1.0,
    )


def test_pg有owner旧active重试也必须以快照租约做精确CAS() -> None:
    queue = unit_queue(FakeCursor(), FakeCursor(rowcount=1))
    expected = Job(
        "demo",
        "chroma",
        1.0,
        token="legacy-claim",
        owner_token="old-owner",
        lease_expires_at=1.0,
        meta=JobMeta(target_commit="HEAD"),
    )

    assert queue.settle_expired_legacy_active(
        expected,
        action="retry",
        reason="受控恢复 legacy active",
        timeout_sec=0.3,
    ) is True

    sql, params = queue._pool._conn.calls[1]
    assert "lease_expires_at = %s" in sql
    assert "lease_expires_at < EXTRACT(EPOCH FROM clock_timestamp())" in sql
    assert params == ("demo", "chroma", "legacy-claim", "old-owner", 1.0)


def test_pg_pending快照携带稳定pending版本() -> None:
    row = (
        "demo", "chroma", 1.0,
        1.0, '{"source":"legacy","pull_policy":null,"target_commit":"HEAD"}', "pending",
        None, None, None, None,
        None, None, None, time.time(), None,
        None, None, None, None, None, None, None,
        "pending-token", "12345",
    )

    pending = PgJobQueue._pending_job(row)

    assert pending is not None
    assert pending.pending_version == "pt:pending-token"


def test_pg_pending迁移仅更新pending列且用token版本CAS() -> None:
    row = (
        1.0,
        1.0,
        '{"source":"legacy","pull_policy":null,"target_commit":"HEAD"}',
        "pending",
        None,
        None,
        "pending-token",
        None,
        "12345",
    )
    queue = unit_queue(FakeCursor(rows=[row]), FakeCursor(rowcount=1))
    expected = Job(
        "demo",
        "chroma",
        1.0,
        meta=JobMeta(target_commit="HEAD"),
        pending_version="pt:pending-token",
    )

    result = queue.migrate_pending(
        expected,
        JobMeta(source="migration", pull_policy="never", target_commit="a" * 40),
        timeout_sec=0.3,
    )

    assert result.outcome is PendingMigrationOutcome.MIGRATED
    sql, params = queue._pool._conn.calls[-1]
    set_clause = sql.split("SET", 1)[1].split("WHERE", 1)[0]
    assert "pending_token" in set_clause
    assert "pending_meta_json" in set_clause
    assert "active_meta_json" not in set_clause
    assert "claimed_by" not in set_clause
    assert "result_status" not in set_clause
    assert params[-1] == "pending-token"


def test_pg_legacy_pending迁移以xmin版本CAS且不依赖当前HEAD() -> None:
    row = (
        1.0,
        None,
        None,
        "pending",
        None,
        None,
        None,
        None,
        "12345",
    )
    queue = unit_queue(FakeCursor(rows=[row]), FakeCursor(rowcount=1))
    expected = Job(
        "demo",
        "chroma",
        1.0,
        meta=JobMeta(target_commit="HEAD"),
        pending_version="px:12345",
    )

    result = queue.migrate_pending(
        expected,
        JobMeta(target_commit="a" * 40),
        timeout_sec=0.3,
    )

    assert result.outcome is PendingMigrationOutcome.MIGRATED
    sql, params = queue._pool._conn.calls[-1]
    assert "pending_token IS NULL" in sql
    assert "xmin::text = %s" in sql
    assert params[-1] == "12345"


def test_pg非pending预期与File一致返回not_pending且不访问数据库() -> None:
    queue = unit_queue()
    expected = Job(
        "demo",
        "chroma",
        1.0,
        token="active-claim",
        meta=JobMeta(target_commit="HEAD"),
        pending_version="pt:pending-token",
    )

    result = queue.migrate_pending(
        expected,
        JobMeta(target_commit="a" * 40),
        timeout_sec=0.3,
    )

    assert result.outcome is PendingMigrationOutcome.NOT_PENDING
    assert queue._pool._conn.calls == []


def _exception_chain_text(error: BaseException) -> str:
    """检查隐藏的 cause/context 也不会残留连接串。"""
    pending = [error]
    seen: set[int] = set()
    fragments: list[str] = []
    while pending:
        current = pending.pop()
        if id(current) in seen:
            continue
        seen.add(id(current))
        fragments.extend((str(current), repr(current)))
        if current.__cause__ is not None:
            pending.append(current.__cause__)
        if current.__context__ is not None:
            pending.append(current.__context__)
    return "\n".join(fragments)


def test_pg_pending迁移后端异常的完整异常链不含dsn() -> None:
    from codev_platform.reindex.pg_queue_migration import PgPendingMigrationError

    secret_dsn = "postgresql://user:top-secret@db.example/reindex"
    queue = unit_queue(RuntimeError(f"查询失败: {secret_dsn}"))
    expected = Job(
        "demo",
        "chroma",
        1.0,
        meta=JobMeta(target_commit="HEAD"),
        pending_version="pt:pending-token",
    )

    with pytest.raises(PgPendingMigrationError) as raised:
        queue.migrate_pending(
            expected,
            JobMeta(target_commit="a" * 40),
            timeout_sec=0.3,
        )

    assert "top-secret" not in _exception_chain_text(raised.value)
    assert "postgresql://" not in _exception_chain_text(raised.value)
