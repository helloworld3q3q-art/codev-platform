"""Pg 队列 retry/reject 的单事务状态转换。"""

from __future__ import annotations

import math

from codev_platform.reindex.pg_queue_sql import (
    reject_sql,
    reject_unowned_legacy_active_sql,
    retry_sql,
    tail_lock_sql,
)
from codev_platform.reindex.pg_queue_tx import (
    PgOperationBudget,
    bounded_connection,
    execute_with_budget,
)
from codev_platform.reindex.queue_ports import ClaimedJob, Job


def retry_claim(
    pool,
    table: str,
    budget: PgOperationBudget,
    claim: ClaimedJob,
    *,
    require_owner: bool = True,
    require_expired: bool = False,
    expected_lease_expires_at: float | None = None,
) -> bool:
    """在全局排序锁内生成严格队尾值，并于提交前解释结果。"""
    params: tuple[object, ...] = (
        claim.job.project_id,
        claim.job.kind,
        claim.claim_token,
    )
    if require_owner:
        params += (claim.owner_token,)
    if expected_lease_expires_at is not None:
        params += (expected_lease_expires_at,)
    with bounded_connection(pool, budget) as conn:
        execute_with_budget(conn, budget, tail_lock_sql(table))
        cursor = execute_with_budget(
            conn,
            budget,
            retry_sql(
                table,
                require_owner=require_owner,
                require_expired=require_expired,
                require_exact_lease=expected_lease_expires_at is not None,
            ),
            params,
        )
        matched = cursor.rowcount == 1
    return matched


def reject_claim(
    pool,
    table: str,
    budget: PgOperationBudget,
    claim: ClaimedJob,
    *,
    reason: str,
    require_expired: bool = False,
    expected_lease_expires_at: float | None = None,
) -> bool:
    """以 token/owner 围栏退休 active，并原子写入失败审计。"""
    with bounded_connection(pool, budget) as conn:
        cursor = execute_with_budget(
            conn,
            budget,
            reject_sql(
                table,
                require_expired=require_expired,
                require_exact_lease=expected_lease_expires_at is not None,
            ),
            (
                reason,
                claim.job.project_id,
                claim.job.kind,
                claim.claim_token,
                claim.owner_token,
                *((expected_lease_expires_at,) if expected_lease_expires_at is not None else ()),
            ),
        )
        matched = cursor.rowcount == 1
    return matched


def reject_unowned_legacy_active(
    pool,
    table: str,
    budget: PgOperationBudget,
    expected: Job,
    *,
    reason: str,
) -> bool:
    """以 claim token 与 owner 为空的组合围栏退休旧 active。"""
    lease = _snapshot_lease(expected)
    if not expected.token or expected.owner_token is not None or lease is None:
        return False
    with bounded_connection(pool, budget) as conn:
        cursor = execute_with_budget(
            conn,
            budget,
            reject_unowned_legacy_active_sql(table, require_exact_lease=True),
            (reason, expected.project_id, expected.kind, expected.token, lease),
        )
        matched = cursor.rowcount == 1
    return matched


def settle_expired_legacy_active(
    pool,
    table: str,
    budget: PgOperationBudget,
    expected: Job,
    *,
    action: str,
    reason: str,
) -> bool:
    """在同一 PG 事务内复核当前租约、claim 与 owner 后收口 legacy active。"""
    lease = _snapshot_lease(expected)
    if (
        action not in {"retry", "reject"}
        or not expected.token
        or not expected.owner_token
        or lease is None
    ):
        return False
    claim = ClaimedJob(
        expected,
        expected.token,
        expected.owner_token,
        max(lease, 0.000001),
    )
    if action == "retry":
        return retry_claim(
            pool,
            table,
            budget,
            claim,
            require_expired=True,
            expected_lease_expires_at=lease,
        )
    return reject_claim(
        pool,
        table,
        budget,
        claim,
        reason=reason,
        require_expired=True,
        expected_lease_expires_at=lease,
    )


def _snapshot_lease(expected: Job) -> float | None:
    """只接受快照中可精确回写到 PG double precision 的租约值。"""
    value = expected.lease_expires_at
    if type(value) not in (int, float):
        return None
    lease = float(value)
    return lease if math.isfinite(lease) else None


__all__ = [
    "reject_claim",
    "reject_unowned_legacy_active",
    "retry_claim",
    "settle_expired_legacy_active",
]
