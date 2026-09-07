"""cgroup 终止结果与精确死亡证据构造。"""

from __future__ import annotations

from codev_platform.reindex.attempt_process import (
    ExecutionHandle,
    TerminationReport,
)
from codev_platform.reindex.attempts import ConfirmedProcessDeath
from codev_platform.reindex.cgroup_v2 import CGROUP_CONTAINMENT_KIND


def build_cgroup_termination_report(
    *,
    handle: ExecutionHandle,
    requested_at: float,
    finished_at: float,
    graceful: bool,
    forced: bool,
    confirmed: bool,
    evidence: str,
) -> TerminationReport:
    """按实际观测生成报告，避免不同死亡路径共用错误证据。"""
    proof = None
    if confirmed:
        proof = ConfirmedProcessDeath(
            handle.process_identity,
            CGROUP_CONTAINMENT_KIND,
            finished_at,
            evidence,
        )
    note = "cgroup 子树死亡已确认" if proof else "cgroup 子树死亡无法确认"
    return TerminationReport(
        requested_at,
        finished_at,
        graceful,
        forced,
        proof is not None,
        proof,
        note,
    )


__all__ = ["build_cgroup_termination_report"]
