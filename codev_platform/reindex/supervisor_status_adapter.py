"""把隔离编排的窄状态事件映射到既有 supervisor 状态面。"""
from __future__ import annotations

from collections.abc import Callable

_PHASES = {
    "queue_scan": "queue_scan",
    "executing": "runner",
    "idle": "idle",
}


class SupervisorStatusAdapter:
    """不保存 attempt 机密，只传递允许展示的阶段和 job 身份。"""

    def __init__(
        self,
        instance_token: str,
        *,
        record_phase: Callable[..., None] | None = None,
        record_job_event: Callable[..., None] | None = None,
    ) -> None:
        if type(instance_token) is not str or not instance_token.strip():
            raise ValueError("instance_token 必须是非空字符串")
        if record_phase is not None and not callable(record_phase):
            raise ValueError("record_phase 必须可调用或 None")
        if record_job_event is not None and not callable(record_job_event):
            raise ValueError("record_job_event 必须可调用或 None")
        if record_phase is None or record_job_event is None:
            from . import supervisor

            record_phase = supervisor.record_phase if record_phase is None else record_phase
            record_job_event = (
                supervisor.record_job_event
                if record_job_event is None
                else record_job_event
            )
        self._instance_token = instance_token
        self._record_phase = record_phase
        self._record_job_event = record_job_event

    def heartbeat(self, phase: str, claim: object | None = None) -> None:
        """仅接受固定编排阶段，claim 只降级为 job 后传给 supervisor。"""
        mapped = _PHASES.get(phase)
        if mapped is None:
            raise ValueError("未知隔离编排阶段")
        job = None if claim is None else getattr(claim, "job", None)
        self._record_phase(self._instance_token, mapped, job=job)

    def set_health_failed(self, *, failed: bool) -> None:
        """health 端口只写固定失败事件或健康刷新阶段。"""
        if type(failed) is not bool:
            raise ValueError("failed 必须是 bool")
        if failed:
            self._record_job_event(
                self._instance_token,
                None,
                "health_refresh_failed",
            )
            return
        self._record_phase(self._instance_token, "health_refresh")


__all__ = ["SupervisorStatusAdapter"]
