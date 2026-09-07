"""单仓 CodeGraph stdio 后端会话与协作排空。"""
from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from contextlib import AbstractAsyncContextManager, AbstractContextManager
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from mcp.client.session import ClientSession
from mcp.client.stdio import StdioServerParameters, stdio_client

from codev_platform.codegraph import maintenance_gate
from codev_platform.codegraph.backend_inbox import (
    BackendInbox,
    CodegraphBackendDrainingError,
    wait_for_writer_intent,
)
from codev_platform.codegraph.backend_runtime import (
    codegraph_backend_arguments,
    codegraph_backend_environment,
)
from codev_platform.codegraph.backend_startup import BoundedBackendStartup
from codev_platform.codegraph.operation_lease import (
    codegraph_operation_lease,
    codegraph_writer_pending,
)


BackendPayload = tuple[str, str | None, dict | None]
BackendLogger = Callable[[str], None]
BackendOperationLease = Callable[[Path], AbstractContextManager[None]]
BackendAsyncContextFactory = Callable[..., AbstractAsyncContextManager[Any]]
BackendWriterIntentWaiter = Callable[..., Awaitable[None]]

DEFAULT_WRITER_INTENT_POLL_SEC = 0.1
DEFAULT_BACKEND_DRAIN_GRACE_SEC = 2.0
DEFAULT_BACKEND_CHILD_CANCEL_TIMEOUT_SEC = 1.0
_WRITER_INTENT_POLL_SEC = DEFAULT_WRITER_INTENT_POLL_SEC
_BACKEND_DRAIN_GRACE_SEC = DEFAULT_BACKEND_DRAIN_GRACE_SEC
_BACKEND_CHILD_CANCEL_TIMEOUT_SEC = DEFAULT_BACKEND_CHILD_CANCEL_TIMEOUT_SEC
BACKEND_DRAINING_MESSAGE = "CodeGraph 后端正在排空，请稍后重试"


@dataclass(frozen=True, slots=True)
class BackendSessionPorts:
    """聚合单仓会话的跨模块依赖；收件箱和启动协调仍由本模块持有。"""

    command: Callable[[], str]
    logger: BackendLogger
    operation_lease: BackendOperationLease
    writer_pending: Callable[[Path], bool]
    stdio_client: BackendAsyncContextFactory
    client_session: BackendAsyncContextFactory
    backend_arguments: Callable[[], list[str]]
    backend_environment: Callable[[], dict[str, str]]
    wait_for_writer_intent: BackendWriterIntentWaiter
    writer_intent_poll_sec: Callable[[], float]
    drain_grace_sec: Callable[[], float]
    child_cancel_timeout_sec: Callable[[], float]


def _default_ports(command: str, logger: BackendLogger) -> BackendSessionPorts:
    """为直接使用新模块的调用方提供默认依赖，同时保留模块替换点。"""
    return BackendSessionPorts(
        command=lambda: command,
        logger=logger,
        operation_lease=lambda repo: codegraph_operation_lease(repo),
        writer_pending=lambda repo: codegraph_writer_pending(repo),
        stdio_client=lambda params: stdio_client(params),
        client_session=lambda read, write: ClientSession(read, write),
        backend_arguments=lambda: codegraph_backend_arguments(),
        backend_environment=lambda: codegraph_backend_environment(),
        wait_for_writer_intent=lambda check, *, poll_sec: wait_for_writer_intent(
            check,
            poll_sec=poll_sec,
        ),
        writer_intent_poll_sec=lambda: _WRITER_INTENT_POLL_SEC,
        drain_grace_sec=lambda: _BACKEND_DRAIN_GRACE_SEC,
        child_cancel_timeout_sec=lambda: _BACKEND_CHILD_CANCEL_TIMEOUT_SEC,
    )


class CodegraphBackend:
    """持有一个仓库的 stdio 会话，并串行执行与排空后端请求。"""

    def __init__(
        self,
        project_id: str,
        repo: Path,
        *,
        command: str | None = None,
        logger: BackendLogger | None = None,
        ports: BackendSessionPorts | None = None,
    ) -> None:
        if ports is None:
            if command is None or logger is None:
                raise TypeError("CodegraphBackend 需要 ports，或同时提供 command 与 logger")
            ports = _default_ports(command, logger)
        elif command is not None or logger is not None:
            raise TypeError("CodegraphBackend 的 ports 不能与 command/logger 同时提供")
        self.pid = project_id
        self.repo = repo
        self._ports = ports
        self._inbox: BackendInbox[BackendPayload, Any] = BackendInbox()
        self._startup = BoundedBackendStartup(lambda: self._run())

    async def _run(self) -> None:
        try:
            with self._ports.operation_lease(self.repo):
                await self._require_writer_absent()
                await self._run_stdio_session()
        except Exception as exc:  # noqa: BLE001 — 后端起不来或会话整体退出。
            self._startup.mark_failed(exc)
            self._ports.logger(f"[backend] pid={self.pid} worker exit: {exc!s}")
        finally:
            await self._inbox.close()

    async def _run_stdio_session(self) -> None:
        params = StdioServerParameters(
            command=self._ports.command(),
            args=self._ports.backend_arguments(),
            cwd=str(self.repo),
            env=self._ports.backend_environment(),
        )
        self._ports.logger(f"[backend] spawn codegraph pid={self.pid} cwd={self.repo}")
        self._require_backend_spawn_permitted()
        async with self._ports.stdio_client(params) as (read, write):
            async with self._ports.client_session(read, write) as session:
                await session.initialize()
                await self._require_writer_absent()
                await self._inbox.open()
                self._startup.mark_ready()
                self._ports.logger(f"[backend] ready pid={self.pid}")
                await self._serve_requests(session)

    async def _serve_requests(self, session: ClientSession) -> None:
        writer = asyncio.create_task(
            self._ports.wait_for_writer_intent(
                self._writer_pending,
                poll_sec=self._ports.writer_intent_poll_sec(),
            )
        )
        try:
            while True:
                item, draining = await self._next_request_or_drain(writer)
                if item is None:
                    await self._begin_drain(writer)
                    return
                payload, result = item
                if draining:
                    await self._begin_drain(writer, result)
                    if not result.done():
                        result.set_exception(self._draining_error())
                    return
                if await self._execute_request(session, payload, result, writer):
                    return
        finally:
            if not writer.done():
                await self._cancel_child(writer)

    async def _next_request_or_drain(
        self,
        writer: asyncio.Task[None],
    ) -> tuple[tuple[BackendPayload, asyncio.Future[Any]] | None, bool]:
        request = asyncio.create_task(self._inbox.next())
        try:
            done, _ = await asyncio.wait(
                {request, writer},
                return_when=asyncio.FIRST_COMPLETED,
            )
            if request not in done:
                return None, True
            return request.result(), writer in done
        finally:
            if not request.done():
                await self._cancel_child(request)

    async def _execute_request(
        self,
        session: ClientSession,
        payload: BackendPayload,
        result: asyncio.Future[Any],
        writer: asyncio.Task[None],
    ) -> bool:
        call = asyncio.create_task(self._invoke(session, payload))
        drain_owns_call = False
        try:
            done, _ = await asyncio.wait(
                {call, writer},
                return_when=asyncio.FIRST_COMPLETED,
            )
            if writer not in done:
                self._publish_call_result(call, result)
                return False
            await self._begin_drain(writer, result)
            drain_owns_call = True
            await self._settle_call_during_drain(call, result)
            return True
        except asyncio.CancelledError:
            if not result.done():
                result.cancel()
            raise
        finally:
            if not call.done() and not drain_owns_call:
                await self._cancel_child(call)

    async def _settle_call_during_drain(
        self,
        call: asyncio.Task[Any],
        result: asyncio.Future[Any],
    ) -> None:
        try:
            done, _ = await asyncio.wait(
                {call},
                timeout=self._ports.drain_grace_sec(),
            )
        except asyncio.CancelledError:
            await self._cancel_child(call)
            raise
        if call in done:
            self._publish_call_result(call, result)
            return
        try:
            await self._cancel_child(call)
        except Exception as exc:  # 取消失败不改变调用方看到的暂态排空语义。
            self._ports.logger(
                f"[backend] pid={self.pid} call cancel cleanup failed: {exc!s}"
            )
        finally:
            if not result.done():
                result.set_exception(self._draining_error())

    @staticmethod
    async def _invoke(session: ClientSession, payload: BackendPayload) -> Any:
        kind, name, args = payload
        if kind == "list":
            return await session.list_tools()
        return await session.call_tool(name, args or {})

    @classmethod
    def _publish_call_result(
        cls,
        call: asyncio.Task[Any],
        result: asyncio.Future[Any],
    ) -> None:
        try:
            value = call.result()
        except asyncio.CancelledError:
            if not result.done():
                result.set_exception(cls._draining_error())
            raise
        except Exception as exc:  # noqa: BLE001 — 单次请求失败不能拖垮 worker。
            if not result.done():
                result.set_exception(exc)
        else:
            if not result.done():
                result.set_result(value)

    @staticmethod
    def _confirm_writer_intent(
        writer: asyncio.Task[None],
        result: asyncio.Future[Any] | None = None,
    ) -> None:
        try:
            writer.result()
        except Exception as exc:
            if result is not None and not result.done():
                result.set_exception(exc)
            raise

    async def _begin_drain(
        self,
        writer: asyncio.Task[None],
        result: asyncio.Future[Any] | None = None,
    ) -> None:
        await self._inbox.close()
        self._confirm_writer_intent(writer, result)

    async def _cancel_child(self, task: asyncio.Task[Any]) -> None:
        if not task.done():
            task.cancel()
        try:
            await asyncio.wait_for(
                asyncio.shield(task),
                timeout=self._ports.child_cancel_timeout_sec(),
            )
        except asyncio.CancelledError:
            current = asyncio.current_task()
            if current is not None and current.cancelling():
                self._detach_cancelled_child(task)
                raise
            return
        except asyncio.TimeoutError:
            self._detach_cancelled_child(task)
            self._ports.logger(
                f"[backend] pid={self.pid} child cancel timeout; "
                "continue closing stdio session"
            )
            return
        except Exception:
            current = asyncio.current_task()
            if current is not None and current.cancelling():
                raise asyncio.CancelledError from None
            raise

    def _detach_cancelled_child(self, task: asyncio.Task[Any]) -> None:
        if not task.done():
            task.cancel()
        task.add_done_callback(self._consume_detached_child)

    def _consume_detached_child(self, task: asyncio.Task[Any]) -> None:
        try:
            task.result()
        except asyncio.CancelledError:
            return
        except Exception as exc:  # 后台只收敛已二次取消的子任务。
            self._ports.logger(f"[backend] pid={self.pid} detached child exit: {exc!s}")

    async def _writer_pending(self) -> bool:
        return await asyncio.to_thread(self._ports.writer_pending, self.repo)

    async def _require_writer_absent(self) -> None:
        if await self._writer_pending():
            raise self._draining_error()

    @staticmethod
    def _draining_error() -> CodegraphBackendDrainingError:
        return CodegraphBackendDrainingError(BACKEND_DRAINING_MESSAGE)

    async def ensure(self) -> None:
        self._require_backend_spawn_permitted()
        await self._require_writer_absent()
        await self._startup.ensure(admit=self._admit_backend_start)

    async def _admit_backend_start(self) -> None:
        """冷启动强校验在工作线程执行，并再次收紧 marker 竞态窗口。"""
        await asyncio.to_thread(self._require_service_start_permitted)
        self._require_backend_spawn_permitted()
        await self._require_writer_absent()
        await self._inbox.reset()

    @staticmethod
    def _require_backend_spawn_permitted() -> None:
        if maintenance_gate.codegraph_backend_start_permitted() is not True:
            raise maintenance_gate.CodegraphMaintenanceGateError(
                "reindex 维护门禁已激活或状态不可证明，拒绝启动 CodeGraph 后端"
            )

    @staticmethod
    def _require_service_start_permitted() -> None:
        maintenance_gate.require_codegraph_service_start_permitted()

    async def request(
        self,
        kind: str,
        name: str | None = None,
        args: dict | None = None,
    ) -> Any:
        await self.ensure()
        return await self._inbox.submit((kind, name, args))

    @property
    def alive(self) -> bool:
        return self._startup.alive


__all__ = [
    "BACKEND_DRAINING_MESSAGE",
    "BackendSessionPorts",
    "CodegraphBackend",
    "DEFAULT_BACKEND_CHILD_CANCEL_TIMEOUT_SEC",
    "DEFAULT_BACKEND_DRAIN_GRACE_SEC",
    "DEFAULT_WRITER_INTENT_POLL_SEC",
]
