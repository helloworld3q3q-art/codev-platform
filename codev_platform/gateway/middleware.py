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

import logging
import time
from collections.abc import Iterable

from starlette.datastructures import Headers
from starlette.responses import JSONResponse

from codev_platform.core.ratelimit import SlidingWindowLimiter, default_key_from_scope
from codev_platform.gateway.auth import Authenticator, Unauthorized

_log = logging.getLogger("codev_platform.gateway")


def _normalize_public_paths(public_paths: Iterable[str]) -> set[str]:
    """统一去尾斜杠 (空 -> "/"), 给 _path_is_public 做段边界匹配。"""
    return {p.rstrip("/") or "/" for p in public_paths}


def _path_is_public(path: str, public_exact: set[str]) -> bool:
    """按 path 段边界判 public (Auth / RateLimit 共用, 防 startswith 子串/穿越绕过)。"""
    # 拒绝未规整路径 (含 .. 段) —— 防 /health/../chat 之类穿越绕过
    norm = path.rstrip("/") or "/"
    if ".." in norm.split("/"):
        return False
    if norm in public_exact:
        return True
    # 段边界前缀放行: /health 放行 /health/live, 但不放行 /healthcheck-evil
    return any(norm == p or norm.startswith(p + "/") for p in public_exact)


class AuthMiddleware:
    """纯 ASGI 认证拦截。挂载方式同普通中间件;对 SSE/流式安全,高并发轻量。"""

    def __init__(self, app, authenticator: Authenticator, public_paths: Iterable[str] = ()) -> None:
        self.app = app
        self._auth = authenticator
        # 规整 public 前缀: 统一去尾斜杠, 按 path 段边界匹配 (防 startswith 子串/穿越绕过)
        self._public_exact = _normalize_public_paths(public_paths)

    def _is_public(self, path: str) -> bool:
        return _path_is_public(path, self._public_exact)

    async def __call__(self, scope, receive, send) -> None:
        if scope["type"] != "http" or self._is_public(scope.get("path", "")):
            await self.app(scope, receive, send)
            return
        try:
            identity = self._auth.authenticate(Headers(scope=scope))
        except Unauthorized as exc:
            # 静态 401 body —— 不回显异常内文 (避免反射用户输入 / 泄漏内部校验规则);详情只落服务端日志
            _log.info("[gateway] 401 path=%s: %s", scope.get("path"), exc)
            await JSONResponse({"error": "unauthorized"}, status_code=401)(scope, receive, send)
            return
        except Exception as exc:  # noqa: BLE001 - 认证器内部异常不得泄漏栈/不得崩 worker
            _log.exception("[gateway] authenticator error path=%s: %r", scope.get("path"), exc)
            await JSONResponse({"error": "internal auth error"}, status_code=500)(scope, receive, send)
            return
        # 挂到 scope state，下游 request.state.identity 可读(不再各路由自解析头)。
        # scope["state"] 可能已被上游建为非 dict → 不是 dict 就重置, 防 setdefault 取回对象后赋值报错。
        state = scope.get("state")
        if not isinstance(state, dict):
            state = {}
            scope["state"] = state
        state["identity"] = identity
        await self.app(scope, receive, send)


# 健康探针豁免 (与四套 HTTP 服务的 AuthMiddleware public_paths 对齐, 不被限流误伤)
_RL_PUBLIC_PATHS = {"/healthz", "/health"}


def maybe_rate_limit_middleware(cfg: dict):
    """工厂: 按 config gateway.rate_limit 决定是否挂 RateLimitMiddleware。

    gateway.rate_limit.{enabled(默认 false), per_minute(默认 120)}。
    enabled=false (dev 默认) → 返回 None (调用方不挂, 不破现状)。
    enabled=true → 返回 starlette Middleware(RateLimitMiddleware, ...), 健康探针豁免。
    """
    from starlette.middleware import Middleware
    from codev_platform.core.config import get as _cfg_get

    if not bool(_cfg_get(cfg, "gateway.rate_limit.enabled", False)):
        return None
    per_minute = int(_cfg_get(cfg, "gateway.rate_limit.per_minute", 120))
    limiter = SlidingWindowLimiter(per_minute, 60.0)
    return Middleware(
        RateLimitMiddleware,
        limiter=limiter,
        public_paths=_RL_PUBLIC_PATHS,
    )


class RateLimitMiddleware:
    """纯 ASGI 限流拦截 (仿 AuthMiddleware: SSE 安全 + 高并发, 不缓冲)。

    挂在 AuthMiddleware **之后** (内层): Auth 外层先设 scope.state.identity,
    本层 key_fn 优先按 user_id 限, 退 client IP。健康探针经 public_paths 豁免。
    限流判定全在纯 SlidingWindowLimiter (now 由本层用 monotonic clock 注入)。
    """

    def __init__(self, app, limiter: SlidingWindowLimiter, public_paths: Iterable[str] = (),
                 key_fn=default_key_from_scope) -> None:
        self.app = app
        self._limiter = limiter
        self._public_exact = _normalize_public_paths(public_paths)
        self._key_fn = key_fn

    def _is_public(self, path: str) -> bool:
        return _path_is_public(path, self._public_exact)

    async def __call__(self, scope, receive, send) -> None:
        if scope["type"] != "http" or self._is_public(scope.get("path", "")):
            await self.app(scope, receive, send)
            return
        key = self._key_fn(scope)
        now = time.monotonic()
        if not self._limiter.allow(key, now):
            # 静态 body —— 不回显 key/限额 (避免反射用户输入 / 泄漏限流参数)
            _log.info("[gateway] 429 path=%s key=%s", scope.get("path"), key)
            await JSONResponse({"error": "rate limited"}, status_code=429)(scope, receive, send)
            return
        await self.app(scope, receive, send)
