"""Windows Job 死亡证明与公共报告的纯构造叶子。"""
from __future__ import annotations

from codev_platform.reindex.attempt_process import (
    ExecutionHandle,
    ProcessReference,
    RecoveryReport,
    RecoveryState,
    TerminationReport,
    handle_epoch_time,
)
from codev_platform.reindex.attempts import ConfirmedProcessDeath


def build_windows_death_proof(
    subject: ExecutionHandle | ProcessReference,
    *,
    confirmed_at: float,
    evidence: str,
) -> ConfirmedProcessDeath:
    """从已校验公共引用生成同身份的死亡证明。"""
    if not isinstance(subject, (ExecutionHandle, ProcessReference)):
        raise ValueError("Windows Job 死亡证明主体无效")
    resolved_at = confirmed_at
    if isinstance(subject, ExecutionHandle):
        resolved_at = handle_epoch_time(subject, confirmed_at)
    return ConfirmedProcessDeath(
        process_identity=subject.process_identity,
        containment_kind=subject.containment_kind,
        confirmed_at=resolved_at,
        evidence=evidence,
    )


def build_windows_termination_report(
    *,
    requested_at: float,
    finished_at: float,
    graceful: bool,
    forced: bool,
    proof: ConfirmedProcessDeath | None,
    note: str,
) -> TerminationReport:
    """把状态机结论收敛为公共终止报告。"""
    proof_time = proof.confirmed_at if proof is not None else 0.0
    return TerminationReport(
        requested_at=requested_at,
        finished_at=max(requested_at, finished_at, proof_time),
        graceful=graceful,
        forced=forced,
        confirmed_dead=proof is not None,
        death_proof=proof,
        note=note,
    )


def build_windows_recovery_report(
    state: RecoveryState,
    *,
    handle: ExecutionHandle | None = None,
    proof: ConfirmedProcessDeath | None = None,
    note: str | None = None,
) -> RecoveryReport:
    """集中维护四种恢复状态的规范字段组合与默认说明。"""
    default_note = {
        RecoveryState.ACTIVE: "恢复到活动 Windows Job",
        RecoveryState.CONFIRMED_DEAD: "已确认历史 Windows Job 死亡",
        RecoveryState.NEVER_STARTED: "journal 尚未启动 Windows Job",
        RecoveryState.UNCONFIRMED: "Windows Job 状态无法确认",
    }[state]
    return RecoveryReport(
        state=state,
        handle=handle,
        death_proof=proof,
        note=note or default_note,
    )


__all__ = [
    "build_windows_death_proof",
    "build_windows_recovery_report",
    "build_windows_termination_report",
]
