"""attempt 父侧资源的有界释放端口与固定路由。"""
from __future__ import annotations

from collections.abc import Mapping
from types import MappingProxyType
from typing import Protocol

from codev_platform.reindex.attempt_process import Deadline
from codev_platform.reindex.attempts import (
    AttemptSpec,
    CleanupReport,
    ConfirmedProcessDeath,
)


def _require_cleanup_budget(deadline: Deadline) -> None:
    if not isinstance(deadline, Deadline):
        raise TypeError("清理截止时间必须是 Deadline")
    if deadline.expired():
        raise TimeoutError("清理预算已耗尽")


class AttemptCleanupPort(Protocol):
    """父侧资源释放端口；适配器必须用同一截止时间约束全部操作。"""

    def release(
        self,
        spec: AttemptSpec,
        death: ConfirmedProcessDeath,
        deadline: Deadline,
    ) -> CleanupReport:
        """有死亡证明时执行有界释放。"""
        raise NotImplementedError("清理适配器方法")

    def release_unstarted(
        self,
        spec: AttemptSpec,
        deadline: Deadline,
    ) -> CleanupReport:
        """目标从未启动时执行有界释放，不伪造死亡证明。"""
        raise NotImplementedError("清理适配器方法")


class ConfiguredAttemptCleanup:
    """configured 输入不持有父侧资源，因此两条释放路径均幂等。"""

    def release(
        self,
        spec: AttemptSpec,
        death: ConfirmedProcessDeath,
        deadline: Deadline,
    ) -> CleanupReport:
        if not isinstance(death, ConfirmedProcessDeath):
            raise TypeError("必须提供已确认的进程死亡证明")
        return self._release(spec, deadline)

    def release_unstarted(
        self,
        spec: AttemptSpec,
        deadline: Deadline,
    ) -> CleanupReport:
        return self._release(spec, deadline)

    @staticmethod
    def _release(spec: AttemptSpec, deadline: Deadline) -> CleanupReport:
        _require_cleanup_budget(deadline)
        report = CleanupReport(
            released=True,
            attempt_id=spec.attempt_id,
            note="configured 输入不持有父侧资源",
        )
        _require_cleanup_budget(deadline)
        return report


class AttemptCleanupRouter:
    """按固定输入类型委派清理，未知类型失败关闭。"""

    def __init__(self, adapters: Mapping[str, AttemptCleanupPort]) -> None:
        frozen = dict(adapters)
        if not frozen:
            raise ValueError("清理适配器映射不能为空")
        self._adapters = MappingProxyType(frozen)

    def release(
        self,
        spec: AttemptSpec,
        death: ConfirmedProcessDeath,
        deadline: Deadline,
    ) -> CleanupReport:
        if not isinstance(death, ConfirmedProcessDeath):
            raise TypeError("必须提供已确认的进程死亡证明")
        _require_cleanup_budget(deadline)
        adapter = self._adapter_for(spec.input_kind)
        report = adapter.release(spec, death, deadline)
        _require_cleanup_budget(deadline)
        return report

    def release_unstarted(
        self,
        spec: AttemptSpec,
        deadline: Deadline,
    ) -> CleanupReport:
        _require_cleanup_budget(deadline)
        adapter = self._adapter_for(spec.input_kind)
        report = adapter.release_unstarted(spec, deadline)
        _require_cleanup_budget(deadline)
        return report

    def _adapter_for(self, input_kind: str) -> AttemptCleanupPort:
        adapter = self._adapters.get(input_kind)
        if adapter is None:
            raise ValueError(f"没有适用于 {input_kind} 的清理适配器")
        return adapter


__all__ = [
    "AttemptCleanupPort",
    "AttemptCleanupRouter",
    "ConfiguredAttemptCleanup",
]
