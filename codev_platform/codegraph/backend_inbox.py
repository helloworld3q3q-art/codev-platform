"""CodeGraph 后端请求收件箱与写意图轮询。"""
from __future__ import annotations

import asyncio
import inspect
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Generic, TypeVar, cast


_RequestT = TypeVar("_RequestT")
_ResultT = TypeVar("_ResultT")
_CLOSED = object()
_DRAINING_MESSAGE = "CodeGraph 后端正在排空，暂不接收请求"


class CodegraphBackendDrainingError(RuntimeError):
    """后端正让出操作租约，请求可在稍后重试。"""


@dataclass(slots=True)
class _QueuedRequest(Generic[_RequestT, _ResultT]):
    payload: _RequestT
    result: asyncio.Future[_ResultT]


class BackendInbox(Generic[_RequestT, _ResultT]):
    """管理单消费者后端的请求队列与排空生命周期。"""

    def __init__(self) -> None:
        self._state_lock = asyncio.Lock()
        self._queue = self._new_queue()
        self._accepting = False

    async def reset(self) -> None:
        """拒绝当前代的排队请求，并回到全新关闭状态。"""
        async with self._state_lock:
            if self._accepting:
                self._close_current_generation()
            self._queue = self._new_queue()

    async def open(self) -> None:
        """从关闭状态开放一个全新队列；已开放时保持幂等。"""
        async with self._state_lock:
            if self._accepting:
                return
            self._queue = self._new_queue()
            self._accepting = True

    async def submit(self, payload: _RequestT) -> _ResultT:
        """提交请求并等待单消费者通过结果凭据完成它。"""
        result = asyncio.get_running_loop().create_future()
        async with self._state_lock:
            if not self._accepting:
                raise self._draining_error()
            self._queue.put_nowait(_QueuedRequest(payload, result))
        return await result

    async def next(self) -> tuple[_RequestT, asyncio.Future[_ResultT]]:
        """取得下一项；关闭信号会唤醒空闲消费者并结束本代。"""
        async with self._state_lock:
            if not self._accepting:
                raise self._draining_error()
            queue = self._queue
        item = await queue.get()
        if item is _CLOSED:
            raise self._draining_error()
        request = cast(_QueuedRequest[_RequestT, _ResultT], item)
        return request.payload, request.result

    async def close(self) -> None:
        """停止接收新请求，并以统一暂态异常拒绝全部排队请求。"""
        async with self._state_lock:
            if not self._accepting:
                return
            self._close_current_generation()

    def _close_current_generation(self) -> None:
        self._accepting = False
        while True:
            try:
                item = self._queue.get_nowait()
            except asyncio.QueueEmpty:
                break
            if item is _CLOSED:
                continue
            request = cast(_QueuedRequest[_RequestT, _ResultT], item)
            if not request.result.done():
                request.result.set_exception(self._draining_error())
        self._queue.put_nowait(_CLOSED)

    @staticmethod
    def _new_queue() -> asyncio.Queue[object]:
        return asyncio.Queue()

    @staticmethod
    def _draining_error() -> CodegraphBackendDrainingError:
        return CodegraphBackendDrainingError(_DRAINING_MESSAGE)


WriterIntentCheck = Callable[[], bool | Awaitable[bool]]


async def wait_for_writer_intent(
    check: WriterIntentCheck,
    *,
    poll_sec: float,
) -> None:
    """轮询同步或异步探针，直到观察到写意图。"""
    while True:
        pending = check()
        if inspect.isawaitable(pending):
            pending = await pending
        if pending:
            return
        await asyncio.sleep(poll_sec)


__all__ = [
    "BackendInbox",
    "CodegraphBackendDrainingError",
    "wait_for_writer_intent",
]
