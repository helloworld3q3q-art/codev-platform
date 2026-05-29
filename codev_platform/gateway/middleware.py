"""统一请求拦截中间件 —— **纯 ASGI**(不是 BaseHTTPMiddleware)。

为什么纯 ASGI 而非 BaseHTTPMiddleware:
- **SSE / 流式安全**:BaseHTTPMiddleware 会缓冲响应体,破坏 SSE(MCP 的 daemon /sse 长连接);
  纯 ASGI 不碰响应流,直接放行,SSE / Streamable HTTP 不受影响。
- **高并发轻量**:无 BaseHTTPMiddleware 的额外 task/anyio 包装开销,每请求只做一次认证 + 透传。
- **无共享可变状态**:认证器只读,中间件无锁,天然并发安全。

每个 HTTP 入口(agent / chroma daemon / 将来的 MCP-SSE)mount 同一份:认证 → 把 Identity
写进 scope["state"](下游 request.state.identity 可读)→ 失败 401 → public_paths 放行。

用法:app.add_middleware(AuthMiddleware, authenticator=build_authenticator(cfg), public_paths={"/health"})
"""
from __future__ import annotations

from collections.abc import Iterable

from starlette.datastructures import Headers
from starlette.responses import JSONResponse

from codev_platform.gateway.auth import Authenticator, Unauthorized


class AuthMiddleware:
    """纯 ASGI 认证拦截。挂载方式同普通中间件;对 SSE/流式安全,高并发轻量。"""

    def __init__(self, app, authenticator: Authenticator, public_paths: Iterable[str] = ()) -> None:
        self.app = app
        self._auth = authenticator
        self._public = set(public_paths)

    def _is_public(self, path: str) -> bool:
        if path in self._public:
            return True
        # public_paths 里以 / 结尾的项按前缀放行
        return any(p.endswith("/") and path.startswith(p) for p in self._public)

    async def __call__(self, scope, receive, send) -> None:
        if scope["type"] != "http" or self._is_public(scope.get("path", "")):
            await self.app(scope, receive, send)
            return
        try:
            identity = self._auth.authenticate(Headers(scope=scope))
        except Unauthorized as exc:
            await JSONResponse({"error": f"unauthorized: {exc}"}, status_code=401)(scope, receive, send)
            return
        # 挂到 scope state，下游 request.state.identity 可读(不再各路由自解析头)
        scope.setdefault("state", {})["identity"] = identity
        await self.app(scope, receive, send)
