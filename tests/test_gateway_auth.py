"""gateway 认证策略 + 统一拦截中间件测试。"""
from __future__ import annotations

import pytest

from codev_platform.gateway.auth import (
    PassthroughAuthenticator,
    TokenAuthenticator,
    Unauthorized,
    build_authenticator,
    token_hash,
    warn_if_insecure,
)
from codev_platform.gateway.middleware import AuthMiddleware


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


def test_token_valid_by_hash():
    # config 存 sha256 hash,客户端发明文;hash 比对命中
    a = TokenAuthenticator({token_hash("tok-1"): {"user_id": "bob", "org_id": "acme"}})
    idt = a.authenticate({"Authorization": "Bearer tok-1"})
    assert idt.user_id == "bob" and idt.org_id == "acme" and idt.via == "token"


def test_token_plaintext_in_config_does_not_match():
    # 防回归:config 里若误存明文(非 hash),明文 token 不应认证通过(只认 hash)
    a = TokenAuthenticator({"tok-1": {"user_id": "bob"}})  # 明文当 key(错误用法)
    with pytest.raises(Unauthorized):
        a.authenticate({"Authorization": "Bearer tok-1"})


def test_token_missing_rejected():
    with pytest.raises(Unauthorized):
        TokenAuthenticator({token_hash("t"): {"user_id": "u"}}).authenticate({})


def test_token_invalid_rejected():
    with pytest.raises(Unauthorized):
        TokenAuthenticator({token_hash("t"): {"user_id": "u"}}).authenticate(
            {"Authorization": "Bearer nope"})


def test_token_hash_is_sha256_hex():
    import hashlib
    assert token_hash("abc") == hashlib.sha256(b"abc").hexdigest()
    assert len(token_hash("x")) == 64  # sha256 hex


def test_build_authenticator_default_passthrough():
    assert isinstance(build_authenticator({}), PassthroughAuthenticator)
    assert isinstance(build_authenticator(None), PassthroughAuthenticator)


def test_build_authenticator_token_mode():
    cfg = {"gateway": {"auth_mode": "token", "tokens": {token_hash("t"): {"user_id": "u"}}}}
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


def test_middleware_public_prefix_segment_boundary():
    # /health 放行其子路径, 但不放行同前缀的别的路径 (防 startswith 子串绕过)
    app, _ = _starlette_app({})
    app2 = AuthMiddleware(None, build_authenticator({}), public_paths={"/health"})
    assert app2._is_public("/health") is True
    assert app2._is_public("/health/live") is True       # 段边界子路径放行
    assert app2._is_public("/healthcheck-evil") is False  # 同前缀非段边界 → 不放行
    assert app2._is_public("/who") is False


def test_middleware_rejects_traversal_in_public_check():
    # /health/../who 不得被当 public 放行 (含 .. 段直接判非 public)
    mw = AuthMiddleware(None, build_authenticator({}), public_paths={"/health"})
    assert mw._is_public("/health/../who") is False
    assert mw._is_public("/../secret") is False


def test_warn_if_insecure_only_passthrough_nonloopback(caplog):
    import logging
    pt = PassthroughAuthenticator()
    tok = TokenAuthenticator({token_hash("x"): {"user_id": "u"}})
    with caplog.at_level(logging.WARNING, logger="codev_platform.gateway"):
        warn_if_insecure(pt, "127.0.0.1")   # loopback → 不警告
        warn_if_insecure(tok, "0.0.0.0")    # token 模式 → 不警告
    assert not caplog.records
    with caplog.at_level(logging.WARNING, logger="codev_platform.gateway"):
        warn_if_insecure(pt, "0.0.0.0")     # passthrough + 非 loopback → 警告
    assert any("passthrough" in r.message for r in caplog.records)


def test_middleware_token_mode_gates():
    cfg = {"gateway": {"auth_mode": "token", "tokens": {token_hash("good"): {"user_id": "bob", "org_id": "acme"}}}}
    app, cap = _starlette_app(cfg)
    assert _run_asgi(app, "/health", {}) == 200                                  # public 放行
    assert _run_asgi(app, "/who", {}) == 401                                     # 无 token → 401
    assert _run_asgi(app, "/who", {"Authorization": "Bearer bad"}) == 401        # 错 token → 401
    assert _run_asgi(app, "/who", {"Authorization": "Bearer good"}) == 200       # 对 token(hash 命中)
    assert cap["identity"].user_id == "bob" and cap["identity"].via == "token"
