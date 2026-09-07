"""隔离 reindex worker 的启动顺序与单并发控制循环。"""
from __future__ import annotations

import asyncio
import math
from collections.abc import Callable, Iterable
from contextlib import AbstractContextManager, contextmanager
from typing import Protocol

from .attempt_process import Deadline
from .health_refresh import HealthRefreshReport


class IsolatedWorkerLoopError(RuntimeError):
    """隔离控制循环的启动前置条件或运行预算无效。"""


class _OrchestratorPort(Protocol):
    def recover_incomplete(self) -> None: ...

    def drain_once(self) -> int: ...


class _HealthRefreshPort(Protocol):
    def request(self, project_id: str) -> None: ...

    def flush(self, deadline: Deadline) -> object: ...


class _MonotonicClock(Protocol):
    def monotonic(self) -> float: ...


MaintenanceWritePermit = Callable[[], AbstractContextManager[bool]]


def _positive_finite(value: object, field: str) -> float:
    if type(value) not in (int, float):
        raise ValueError(f"{field} 必须是有限正数")
    resolved = float(value)
    if not math.isfinite(resolved) or resolved <= 0:
        raise ValueError(f"{field} 必须是有限正数")
    return resolved


def _projects(values: Iterable[str]) -> tuple[str, ...]:
    if isinstance(values, str | bytes):
        raise ValueError("projects 不能是裸字符串")
    try:
        resolved = tuple(sorted(set(values)))
    except TypeError as error:
        raise ValueError("projects 必须是字符串可迭代对象") from error
    if any(type(project_id) is not str or not project_id.strip() for project_id in resolved):
        raise ValueError("projects 只能包含非空 project_id")
    return resolved


class IsolatedWorkerLoop:
    """只编排恢复、启动 health、每轮迁移门禁与一次 queue drain 的先后顺序。"""

    def __init__(
        self,
        *,
        orchestrator: _OrchestratorPort,
        health_refresh: _HealthRefreshPort,
        projects: Iterable[str],
        clock: _MonotonicClock,
        health_timeout_sec: float,
        legacy_audit: Callable[[], None],
        maintenance_write_permit: MaintenanceWritePermit | None = None,
    ) -> None:
        if not callable(getattr(orchestrator, "recover_incomplete", None)):
            raise ValueError("orchestrator 必须支持 recover_incomplete")
        if not callable(getattr(orchestrator, "drain_once", None)):
            raise ValueError("orchestrator 必须支持 drain_once")
        if not callable(getattr(health_refresh, "request", None)):
            raise ValueError("health_refresh 必须支持 request")
        if not callable(getattr(health_refresh, "flush", None)):
            raise ValueError("health_refresh 必须支持 flush")
        if not callable(getattr(clock, "monotonic", None)):
            raise ValueError("clock 必须支持 monotonic")
        if not callable(legacy_audit):
            raise ValueError("legacy_audit 必须可调用")
        permit = (
            _default_maintenance_write_permit
            if maintenance_write_permit is None
            else maintenance_write_permit
        )
        if not callable(permit):
            raise ValueError("maintenance_write_permit 必须可调用")
        self._orchestrator = orchestrator
        self._health = health_refresh
        self._projects = _projects(projects)
        self._clock = clock
        self._health_timeout_sec = _positive_finite(
            health_timeout_sec,
            "health_timeout_sec",
        )
        self._legacy_audit = legacy_audit
        self._maintenance_write_permit = permit
        self._started = False

    def startup(self) -> None:
        """完成可恢复状态收口和首次 health 刷新，不在此处领取任务。"""
        with self._write_permit():
            self._startup_permitted()

    def _startup_permitted(self) -> None:
        """只在本轮写许可仍持有时执行恢复与首次 health。"""
        if self._started:
            return
        self._orchestrator.recover_incomplete()
        for project_id in self._projects:
            self._health.request(project_id)
        report = self._health.flush(
            Deadline.start(
                self._health_timeout_sec,
                now=self._clock.monotonic(),
            )
        )
        self._require_initial_health_report(report)
        self._started = True

    def drain_once(self) -> int:
        """每次 claim 前重新审计 legacy 队列，避免启动后的陈旧快照失效。"""
        with self._write_permit():
            self._startup_permitted()
            self._legacy_audit()
            count = self._orchestrator.drain_once()
            if type(count) is not int or count < 0:
                raise IsolatedWorkerLoopError("orchestrator drain_once 返回无效计数")
            return count

    @contextmanager
    def _write_permit(self):
        """把单次恢复/claim/执行阶段包在可撤销的维护共享许可内。"""
        try:
            context = self._maintenance_write_permit()
        except MemoryError:
            raise
        except Exception as error:
            raise IsolatedWorkerLoopError("维护写许可无法取得") from error
        if not isinstance(context, AbstractContextManager):
            raise IsolatedWorkerLoopError("维护写许可上下文无效")
        with context as permitted:
            if permitted is not True:
                raise IsolatedWorkerLoopError("reindex 维护窗口已启用，worker 写阶段被拒绝")
            yield

    async def run_forever(self, *, poll_sec: float) -> None:
        """以有限单调轮询运行，不创建 heartbeat 或 lease 后台线程。"""
        interval = _positive_finite(poll_sec, "poll_sec")
        while True:
            self.drain_once()
            await asyncio.sleep(interval)

    async def run_until_idle(self, *, idle_exit_sec: float, poll_sec: float) -> None:
        """短驻模式：连续空闲达到预算后退出。"""
        idle_budget = _positive_finite(idle_exit_sec, "idle_exit_sec")
        interval = _positive_finite(poll_sec, "poll_sec")
        idle_since = self._clock.monotonic()
        while True:
            if self.drain_once() > 0:
                idle_since = self._clock.monotonic()
            remaining = idle_budget - (self._clock.monotonic() - idle_since)
            if remaining <= 0:
                return
            await asyncio.sleep(min(interval, remaining))

    def _require_initial_health_report(self, report: object) -> None:
        """只阻断未覆盖或 containment 未证死；普通 health 失败保持可观测但不中断消费。"""
        if type(report) is not HealthRefreshReport:
            raise IsolatedWorkerLoopError("启动 health 刷新未返回严格报告")
        if not report.containment_confirmed_dead:
            raise IsolatedWorkerLoopError("启动 health containment 死亡无法确认")
        attempted = set(report.attempted_projects)
        if not set(self._projects).issubset(attempted):
            raise IsolatedWorkerLoopError("启动 health 刷新未覆盖全部受管项目")


def _default_maintenance_write_permit() -> AbstractContextManager[bool]:
    """延迟接线维护门禁，避免控制循环依赖 CLI 或 systemd 编排层。"""
    from .maintenance_gate import maintenance_reindex_operation_permit

    return maintenance_reindex_operation_permit()


__all__ = ["IsolatedWorkerLoop", "IsolatedWorkerLoopError"]
