"""gateway 认证策略 + 统一拦截中间件测试。"""
from __future__ import annotations

import pytest

from codev_platform.gateway.auth import (
    PassthroughAuthenticator,
    TokenAuthenticator,
    Unauthorized,
    build_authenticator,
)


# ---- authenticators(纯逻辑)----

def test_passthrough_resolves_headers():
    idt = PassthroughAuthenticator().authenticate({"X-User-Id": "alice", "X-Org-Id": "acme"})
    assert idt.user_id == "alice" and idt.org_id == "acme" and idt.via == "passthrough"


def test_passthrough_defaults_when_no_headers():
    idt = PassthroughAuthenticator().authenticate({})
    assert idt.user_id == "local" and idt.org_id == "default"


def test_passthrough_rejects_illegal_header():
    with pytest.raises(Unauthorized):
        PassthroughAuthenticator().authenticate({"X-User-Id": "bad id!"})


def test_token_valid():
    a = TokenAuthenticator({"tok-1": {"user_id": "bob", "org_id": "acme"}})
    idt = a.authenticate({"Authorization": "Bearer tok-1"})
    assert idt.user_id == "bob" and idt.org_id == "acme" and idt.via == "token"


def test_token_missing_rejected():
    with pytest.raises(Unauthorized):
        TokenAuthenticator({"t": {"user_id": "u"}}).authenticate({})


def test_token_invalid_rejected():
    with pytest.raises(Unauthorized):
        TokenAuthenticator({"t": {"user_id": "u"}}).authenticate({"Authorization": "Bearer nope"})


def test_build_authenticator_default_passthrough():
    assert isinstance(build_authenticator({}), PassthroughAuthenticator)
    assert isinstance(build_authenticator(None), PassthroughAuthenticator)


def test_build_authenticator_token_mode():
    cfg = {"gateway": {"auth_mode": "token", "tokens": {"t": {"user_id": "u"}}}}
    assert isinstance(build_authenticator(cfg), TokenAuthenticator)


# ---- middleware 拦截(直驱 ASGI,不经 httpx TestClient——本机 httpx 版本与
#       starlette.testclient 不兼容,会对带 Request 参数的路由误报 422;直驱更稳)----

def _run_asgi(app, path: str, headers: dict[str, str]) -> int:
    """最小 ASGI GET 调用,返回响应状态码(避开 httpx/TestClient)。"""
    import asyncio

    async def _go() -> int:
        scope = {
            "type": "http", "http_version": "1.1", "method": "GET", "path": path,
            "raw_path": path.encode(), "query_string": b"", "root_path": "", "scheme": "http",
            "headers": [(k.lower().encode(), v.encode()) for k, v in headers.items()],
            "client": ("127.0.0.1", 0), "server": ("127.0.0.1", 80), "state": {},
        }
        sent: list = []

        async def receive():
            return {"type": "http.request", "body": b"", "more_body": False}

        async def send(m):
            sent.append(m)

        await app(scope, receive, send)
        return next(m["status"] for m in sent if m["type"] == "http.response.start")

    return asyncio.run(_go())


def _starlette_app(cfg):
    from starlette.applications import Starlette
    from starlette.responses import JSONResponse
    from starlette.routing import Route
    from codev_platform.gateway import AuthMiddleware, build_authenticator

    captured: dict = {}

    async def health(_req):
        return JSONResponse({"ok": True})

    async def who(req):
        captured["identity"] = getattr(req.state, "identity", None)
        return JSONResponse({"ok": True})

    app = Starlette(routes=[Route("/health", health), Route("/who", who)])
    app.add_middleware(AuthMiddleware, authenticator=build_authenticator(cfg), public_paths={"/health"})
    return app, captured


def test_middleware_public_and_passthrough():
    app, cap = _starlette_app({})
    assert _run_asgi(app, "/health", {}) == 200                                  # public 放行
    assert _run_asgi(app, "/who", {"X-User-Id": "alice", "X-Org-Id": "acme"}) == 200
    assert cap["identity"] is not None and cap["identity"].user_id == "alice"    # 身份挂上 request.state


def test_middleware_token_mode_gates():
    cfg = {"gateway": {"auth_mode": "token", "tokens": {"good": {"user_id": "bob", "org_id": "acme"}}}}
    app, cap = _starlette_app(cfg)
    assert _run_asgi(app, "/health", {}) == 200                                  # public 放行
    assert _run_asgi(app, "/who", {}) == 401                                     # 无 token → 401
    assert _run_asgi(app, "/who", {"Authorization": "Bearer good"}) == 200
    assert cap["identity"].user_id == "bob" and cap["identity"].via == "token"
