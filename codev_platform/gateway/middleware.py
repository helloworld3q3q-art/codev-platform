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

import hmac
import logging
import time
from collections.abc import Iterable

from starlette.datastructures import Headers
from starlette.responses import JSONResponse

from codev_platform.core.ratelimit import SlidingWindowLimiter, default_key_from_scope
from codev_platform.core.service_identity import verify_identity
from codev_platform.gateway.auth import Authenticator, Unauthorized, identity_from_internal_claims

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


def _client_is_loopback(scope) -> bool:
    """请求的真实 TCP 对端是否本机 loopback。

    取 ASGI scope["client"] = (host, port) 的实际对端 (不信任 X-Forwarded-For 等可伪造头),
    故远程经代理转发也只会看到代理的真实地址。用于"纯算力接口本机免 token"判定。"""
    client = scope.get("client")
    if not client:
        return False
    host = client[0] or ""
    return host == "::1" or host == "localhost" or host.startswith("127.")


class AuthMiddleware:
    """纯 ASGI 认证拦截。挂载方式同普通中间件;对 SSE/流式安全,高并发轻量。"""

    def __init__(self, app, authenticator: Authenticator, public_paths: Iterable[str] = (),
                 internal_secret: str | None = None,
                 loopback_exempt_paths: Iterable[str] = ()) -> None:
        self.app = app
        self._auth = authenticator
        # 服务间信物密钥 (web 前门 → agent 后端)。配了才启 X-Identity 通道; 空=维持原行为。
        self._internal_secret = internal_secret or None
        # 规整 public 前缀: 统一去尾斜杠, 按 path 段边界匹配 (防 startswith 子串/穿越绕过)
        self._public_exact = _normalize_public_paths(public_paths)
        # 纯算力接口 (/embed /rerank): 无租户数据, **仅本机 loopback** 调用免 token (内部索引/
        # 记忆写复用 daemon 的 GPU 模型); 远程访问仍按正常鉴权 (防外部白嫖 GPU)。
        self._loopback_exempt = _normalize_public_paths(loopback_exempt_paths)

    def _is_public(self, path: str) -> bool:
        return _path_is_public(path, self._public_exact)

    def _is_loopback_exempt(self, path: str) -> bool:
        return bool(self._loopback_exempt) and _path_is_public(path, self._loopback_exempt)

    def _internal_identity(self, headers: Headers):
        """X-Identity 验签通过 → Identity; 无 header / 未配 secret / 验签失败 → None (回退原认证)。"""
        if not self._internal_secret:
            return None
        token = headers.get("x-identity")
        if not token:
            return None
        claims = verify_identity(token, self._internal_secret)
        return identity_from_internal_claims(claims) if claims is not None else None

    def _loopback_call_authorized(self, headers: Headers) -> bool:
        """loopback 豁免的附加闸: 未配 internal_secret → 纯 loopback 可信 (单机 passthrough, 原行为);
        配了 → 必须带匹配的 X-Internal-Call 信物。同机反代转发的远程请求带不出 secret (它只在本机内部
        调用方 config 里, 不过网络) → 拿不到豁免, 落正常鉴权 (防白嫖 GPU)。"""
        if not self._internal_secret:
            return True
        provided = headers.get("x-internal-call") or ""
        return hmac.compare_digest(provided, self._internal_secret)

    async def __call__(self, scope, receive, send) -> None:
        if scope["type"] != "http" or self._is_public(scope.get("path", "")):
            await self.app(scope, receive, send)
            return
        headers = Headers(scope=scope)
        # 纯算力接口本机免 token: 路径在豁免集 + 真实对端 loopback + (未配 secret 或带对的内部信物)。
        # 带 secret 时, 不带/带错信物的 loopback 请求 (同机反代转发的远程) 落到下方正常鉴权。
        if (self._is_loopback_exempt(scope.get("path", "")) and _client_is_loopback(scope)
                and self._loopback_call_authorized(headers)):
            await self.app(scope, receive, send)
            return
        try:
            # 服务间信物优先: web 前门已认证身份经 X-Identity 直接采信, 不破坏 passthrough/token 回退。
            identity = self._internal_identity(headers)
            if identity is None:
                # query 传给认证器: token 模式下 SSE/MCP 客户端无法设 header 时走 ?token= 兜底。
                query = scope.get("query_string", b"").decode("latin-1")
                identity = self._auth.authenticate(headers, query)
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
