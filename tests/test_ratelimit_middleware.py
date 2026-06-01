"""RateLimitMiddleware + maybe_rate_limit_middleware 工厂行为测试。

- 同身份第 N+1 次请求 → 429(滑窗满)
- /healthz 豁免(打多次不 429)
- 不同 user(X-User-Id 不同)互不影响(各自独立窗口)
- enabled=false → 工厂返回 None(dev 默认不挂, 不破现状)
- enabled=true → 工厂返回 starlette Middleware
"""
from __future__ import annotations

from starlette.applications import Starlette
from starlette.middleware import Middleware
from starlette.responses import JSONResponse
from starlette.routing import Route
from starlette.testclient import TestClient

from codev_platform.core.ratelimit import SlidingWindowLimiter
from codev_platform.gateway.middleware import (
    RateLimitMiddleware,
    maybe_rate_limit_middleware,
)


class _PassthroughAuth:
    """最小 ASGI 中间件: 把 X-User-Id 头写进 scope.state.identity(供 RateLimit key_fn 读)。"""

    def __init__(self, app) -> None:
        self.app = app

    async def __call__(self, scope, receive, send) -> None:
        if scope["type"] == "http":
            uid = None
            for k, v in scope.get("headers", []):
                if k == b"x-user-id":
                    uid = v.decode()
                    break
            state = scope.get("state")
            if not isinstance(state, dict):
                state = {}
                scope["state"] = state
            state["identity"] = type("Id", (), {"user_id": uid})()
        await self.app(scope, receive, send)


def _build_client(max_events: int) -> TestClient:
    async def ok(_request):
        return JSONResponse({"ok": True})

    app = Starlette(
        routes=[
            Route("/chat", ok, methods=["GET"]),
            Route("/healthz", ok, methods=["GET"]),
        ],
        # [Auth, RateLimit]: Auth 外层先设 identity, RateLimit 内层读
        middleware=[
            Middleware(_PassthroughAuth),
            Middleware(
                RateLimitMiddleware,
                limiter=SlidingWindowLimiter(max_events, 10_000.0),  # 大窗口 → 仅按计数判
                public_paths={"/healthz", "/health"},
            ),
        ],
    )
    return TestClient(app)


def test_third_request_same_identity_rate_limited() -> None:
    client = _build_client(max_events=2)
    h = {"X-User-Id": "alice"}
    assert client.get("/chat", headers=h).status_code == 200
    assert client.get("/chat", headers=h).status_code == 200
    r = client.get("/chat", headers=h)
    assert r.status_code == 429
    assert r.json() == {"error": "rate limited"}


def test_healthz_exempt_from_rate_limit() -> None:
    client = _build_client(max_events=1)
    for _ in range(5):
        assert client.get("/healthz").status_code == 200


def test_distinct_users_have_independent_windows() -> None:
    client = _build_client(max_events=1)
    # alice 用满
    assert client.get("/chat", headers={"X-User-Id": "alice"}).status_code == 200
    assert client.get("/chat", headers={"X-User-Id": "alice"}).status_code == 429
    # bob 不受影响
    assert client.get("/chat", headers={"X-User-Id": "bob"}).status_code == 200


def test_factory_disabled_returns_none() -> None:
    assert maybe_rate_limit_middleware({}) is None
    assert maybe_rate_limit_middleware({"gateway": {"rate_limit": {"enabled": False}}}) is None


def test_factory_enabled_returns_middleware() -> None:
    mw = maybe_rate_limit_middleware(
        {"gateway": {"rate_limit": {"enabled": True, "per_minute": 5}}}
    )
    assert mw is not None
    assert mw.cls is RateLimitMiddleware
    limiter = mw.kwargs["limiter"]
    assert isinstance(limiter, SlidingWindowLimiter)
