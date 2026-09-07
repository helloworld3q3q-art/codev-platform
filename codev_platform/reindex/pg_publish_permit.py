"""PostgreSQL 发布围栏的一次性确认能力。"""
from __future__ import annotations

from codev_platform.reindex.pg_queue_sql import publish_ack_sql
from codev_platform.reindex.pg_queue_tx import PgOperationBudget, execute_with_budget
from codev_platform.reindex.queue_ports import ClaimedJob, QueueClaimLost


def acknowledge_active(
    conn,
    budget: PgOperationBudget,
    table: str,
    *,
    project_id: str,
    kind: str,
    claim_token: str,
    owner_token: str,
    status: str,
) -> tuple[bool, bool]:
    """按 claim 围栏确认 active，并返回是否命中及队列是否清空。"""
    cursor = execute_with_budget(
        conn,
        budget,
        publish_ack_sql(table),
        (status, project_id, kind, claim_token, owner_token),
    )
    if cursor.rowcount != 1:
        return False, False
    row = cursor.fetchone()
    return True, bool(row and row[0] is None)


class PgPublishPermit:
    """复用 guard 行锁连接、且最多确认一次的发布许可。"""

    def __init__(
        self,
        *,
        conn,
        budget: PgOperationBudget,
        table: str,
        claim: ClaimedJob,
        superseded: bool,
    ) -> None:
        self._conn = conn
        self._budget = budget
        self._table = table
        self._claim = claim
        self._superseded = superseded
        self._acked = False
        self._closed = False

    @property
    def superseded(self) -> bool:
        return self._superseded

    @property
    def acked(self) -> bool:
        return self._acked

    def ack(self) -> bool:
        if self._closed:
            raise RuntimeError("publish permit 已关闭")
        if self._acked:
            return False
        self._budget.remaining()
        status = "superseded" if self._superseded else "completed"
        matched, _clean = acknowledge_active(
            self._conn,
            self._budget,
            self._table,
            project_id=self._claim.job.project_id,
            kind=self._claim.job.kind,
            claim_token=self._claim.claim_token,
            owner_token=self._claim.owner_token,
            status=status,
        )
        if not matched:
            raise QueueClaimLost("发布 ack 时 claim 已失权")
        self._acked = True
        return True

    def close(self) -> None:
        self._closed = True


__all__ = ["PgPublishPermit", "acknowledge_active"]
