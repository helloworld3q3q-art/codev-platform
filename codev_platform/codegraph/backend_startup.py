"""CodeGraph stdio 后端的有界启动协调器。"""
from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable


_BACKEND_STARTUP_TIMEOUT_SEC = 20.0
_BACKEND_CANCEL_TIMEOUT_SEC = 2.0

StartupFactory = Callable[[], Awaitable[None]]
StartupAdmission = Callable[[], Awaitable[None]]


class CodegraphBackendStartupError(RuntimeError):
    """CodeGraph 后端未能在受控时间内进入可服务状态。"""


class BoundedBackendStartup:
    """复用单一启动任务，并在超时后确认取消结果再允许重试。"""

    def __init__(
        self,
        start: StartupFactory,
        *,
        startup_timeout_sec: float | None = None,
        cancel_timeout_sec: float | None = None,
    ) -> None:
        self._start = start
        self._startup_timeout_sec = (
            _BACKEND_STARTUP_TIMEOUT_SEC
            if startup_timeout_sec is None
            else startup_timeout_sec
        )
        self._cancel_timeout_sec = (
            _BACKEND_CANCEL_TIMEOUT_SEC
            if cancel_timeout_sec is None
            else cancel_timeout_sec
        )
        if self._startup_timeout_sec <= 0 or self._cancel_timeout_sec <= 0:
            raise ValueError("CodeGraph 后端启动与取消超时必须为正数")
        self._task: asyncio.Task[None] | None = None
        self._ready = asyncio.Event()
        self._error: Exception | None = None
        self._lock = asyncio.Lock()

    @property
    def task(self) -> asyncio.Task[None] | None:
        """当前启动或服务任务；超时取消未结束时必须保留它。"""
        return self._task

    @property
    def alive(self) -> bool:
        task = self._task
        return task is not None and not task.done()

    @property
    def ready(self) -> asyncio.Event:
        """供后端工作任务发布初始化完成或失败。"""
        return self._ready

    @property
    def error(self) -> Exception | None:
        """当前启动任务已发布的失败原因。"""
        return self._error

    def mark_ready(self) -> None:
        """只在 stdio 会话完成 initialize 后发布可服务状态。"""
        self._ready.set()

    def mark_failed(self, error: Exception) -> None:
        """把后端初始化错误同步给所有等待同一任务的调用方。"""
        if self._error is None:
            self._error = error
        self._ready.set()

    async def ensure(self, *, admit: StartupAdmission) -> None:
        """完成一次有界启动；并发调用共享同一任务和同一结果。"""
        deadline = asyncio.get_running_loop().time() + self._startup_timeout_sec
        task = await self._create_or_reuse(admit, deadline)
        await self._wait_until_ready(task, deadline)

    async def _create_or_reuse(
        self,
        admit: StartupAdmission,
        deadline: float,
    ) -> asyncio.Task[None]:
        try:
            await asyncio.wait_for(self._lock.acquire(), timeout=self._remaining(deadline))
        except asyncio.TimeoutError as error:
            raise CodegraphBackendStartupError("CodeGraph 后端启动超时，未取得启动协调锁") from error
        try:
            task = self._task
            if task is not None and not task.done():
                if self._error is not None:
                    raise self._error
                return task
            await self._admit_within_budget(admit, deadline)
            self._ready = asyncio.Event()
            self._error = None
            task = asyncio.create_task(self._start())
            self._task = task
            task.add_done_callback(self._record_task_completion)
            return task
        finally:
            self._lock.release()

    async def _admit_within_budget(self, admit: StartupAdmission, deadline: float) -> None:
        try:
            await asyncio.wait_for(admit(), timeout=self._remaining(deadline))
        except asyncio.TimeoutError as error:
            raise CodegraphBackendStartupError("CodeGraph 后端启动准入超时，未创建任务") from error

    async def _wait_until_ready(self, task: asyncio.Task[None], deadline: float) -> None:
        try:
            await asyncio.wait_for(self._ready.wait(), timeout=self._remaining(deadline))
        except asyncio.TimeoutError as error:
            failure = await self._cancel_timed_out_task(task)
            raise failure from error
        if self._error is not None:
            raise self._error
        if task.done():
            self._record_task_completion(task)
            if self._error is not None:
                raise self._error
            raise CodegraphBackendStartupError("CodeGraph 后端在就绪前异常结束")

    @staticmethod
    def _remaining(deadline: float) -> float:
        return max(deadline - asyncio.get_running_loop().time(), 0.0)

    async def _cancel_timed_out_task(
        self,
        task: asyncio.Task[None],
    ) -> CodegraphBackendStartupError:
        failure = CodegraphBackendStartupError("CodeGraph 后端启动超时，已请求取消")
        async with self._lock:
            if task is self._task:
                self.mark_failed(failure)
                if not task.done():
                    task.cancel()
        if not task.done():
            try:
                await asyncio.wait_for(
                    asyncio.shield(task),
                    timeout=self._cancel_timeout_sec,
                )
            except (asyncio.CancelledError, asyncio.TimeoutError, Exception):
                pass
        return failure

    def _record_task_completion(self, task: asyncio.Task[None]) -> None:
        if task is not self._task:
            return
        if task.cancelled():
            self.mark_failed(CodegraphBackendStartupError("CodeGraph 后端启动任务已取消"))
            return
        error = task.exception()
        if error is None:
            self.mark_failed(CodegraphBackendStartupError("CodeGraph 后端工作任务意外结束"))
            return
        if isinstance(error, Exception):
            self.mark_failed(error)
            return
        self.mark_failed(CodegraphBackendStartupError("CodeGraph 后端工作任务被异常中断"))


__all__ = ["BoundedBackendStartup", "CodegraphBackendStartupError"]
