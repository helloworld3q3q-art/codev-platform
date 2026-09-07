from __future__ import annotations

from collections.abc import Callable
from contextlib import asynccontextmanager

from mcp.server.streamable_http_manager import StreamableHTTPSessionManager
from starlette.requests import Request
from starlette.responses import Response
from starlette.types import Receive, Scope, Send

ContextReset = Callable[[], None]
ContextBinder = Callable[[Request], ContextReset | Response | None]


class ContextualStreamableHTTPASGIApp:
    """Streamable HTTP adapter that binds per-request service context."""

    def __init__(
        self,
        session_manager: StreamableHTTPSessionManager,
        bind_context: ContextBinder,
    ) -> None:
        self.session_manager = session_manager
        self.bind_context = bind_context

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        bound = self.bind_context(Request(scope))
        if isinstance(bound, Response):
            await bound(scope, receive, send)
            return
        try:
            await self.session_manager.handle_request(scope, receive, send)
        finally:
            if bound is not None:
                bound()


def streamable_lifespan(session_manager: StreamableHTTPSessionManager):
    @asynccontextmanager
    async def lifespan(_app):
        async with session_manager.run():
            yield

    return lifespan
