"""统一请求拦截中间件(ASGI / Starlette,FastAPI 通用)。

每个 HTTP 入口 mount 同一份:请求进来先认证 → 把 Identity 挂到 request.state.identity →
失败 401。公开路径(健康检查等)放行。路由从 request.state.identity 取身份,不各自再解析头。

用法(agent FastAPI / chroma daemon Starlette 都适用):
    from codev_platform.gateway import AuthMiddleware, build_authenticator
    app.add_middleware(AuthMiddleware, authenticator=build_authenticator(load_config()),
                       public_paths={"/health"})
"""
from __future__ import annotations

from collections.abc import Iterable

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import JSONResponse

from codev_platform.gateway.auth import Authenticator, Unauthorized


class AuthMiddleware(BaseHTTPMiddleware):
    """统一认证拦截。认证成功挂 request.state.identity;失败返 401;public_paths 放行。"""

    def __init__(self, app, authenticator: Authenticator, public_paths: Iterable[str] = ()) -> None:
        super().__init__(app)
        self._auth = authenticator
        self._public = set(public_paths)

    def _is_public(self, path: str) -> bool:
        # 精确匹配或前缀匹配(public_paths 里项以 / 结尾视为前缀)
        if path in self._public:
            return True
        return any(p.endswith("/") and path.startswith(p) for p in self._public)

    async def dispatch(self, request: Request, call_next):
        if self._is_public(request.url.path):
            return await call_next(request)
        try:
            request.state.identity = self._auth.authenticate(request.headers)
        except Unauthorized as exc:
            return JSONResponse({"error": f"unauthorized: {exc}"}, status_code=401)
        return await call_next(request)
