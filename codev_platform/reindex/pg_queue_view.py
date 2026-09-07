"""Pg 队列依赖状态的有限预算只读视图。"""
from __future__ import annotations

from codev_platform.reindex.pg_queue_codec import meta_from_json
from codev_platform.reindex.pg_queue_sql import dependency_state_sql
from codev_platform.reindex.pg_queue_tx import bounded_connection, execute_with_budget
from codev_platform.reindex.queue_ports import (
    DependencyQueueState,
    validate_job_identity,
    validate_target_commit,
)


def _target(meta_json: object) -> str | None:
    return meta_from_json(meta_json).target_commit


class PgDependencyQueueView:
    """复用 Pg 事务预算，仅执行单行 SELECT。"""

    def dependency_state(
        self,
        project_id: str,
        kind: str,
        target_commit: str,
        *,
        timeout_sec: float,
    ) -> DependencyQueueState:
        project, resolved_kind = validate_job_identity(project_id, kind)
        target = validate_target_commit(target_commit)
        budget = self._ready(timeout_sec)
        with bounded_connection(self._pool, budget) as conn:
            row = execute_with_budget(
                conn,
                budget,
                dependency_state_sql(self._t),
                (project, resolved_kind),
            ).fetchone()
            if row is None or row[3] is not None:
                return DependencyQueueState.ABSENT
            pending_target = _target(row[0])
            active_target = _target(row[1]) if row[2] is not None else None
            budget.remaining()
            if active_target == target:
                return DependencyQueueState.ACTIVE
            if pending_target == target:
                return DependencyQueueState.PENDING
            if any(value and value != target for value in (active_target, pending_target)):
                return DependencyQueueState.REPLACEMENT
            return DependencyQueueState.ABSENT


__all__ = ["PgDependencyQueueView"]
