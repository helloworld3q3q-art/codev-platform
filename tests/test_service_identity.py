"""B1 共享身份基建: core.service_identity 签发/验签 + gateway X-Identity 接入。"""
from __future__ import annotations

import time

from codev_platform.core.service_identity import sign_identity, verify_identity


# ----------------------------- sign / verify -----------------------------

def test_sign_verify_roundtrip():
    claims = {"user_id": "alice", "org_id": "acme", "projects": ["p1"], "all_projects": False}
    tok = sign_identity(claims, "s3cret")
    got = verify_identity(tok, "s3cret")
    assert got is not None
    assert got["user_id"] == "alice" and got["org_id"] == "acme"
    assert got["projects"] == ["p1"] and got["all_projects"] is False
    assert "exp" in got


def test_verify_wrong_secret_returns_none():
    tok = sign_identity({"user_id": "u"}, "right")
    assert verify_identity(tok, "wrong") is None


def test_verify_tampered_payload_returns_none():
    tok = sign_identity({"user_id": "u", "org_id": "a"}, "s")
    body, sig = tok.split(".", 1)
    # 篡改 body (换成别的合法 b64url) → 签名不再匹配
    forged = sign_identity({"user_id": "evil", "org_id": "a"}, "other").split(".", 1)[0]
    assert verify_identity(f"{forged}.{sig}", "s") is None


def test_verify_expired_returns_none():
    tok = sign_identity({"user_id": "u"}, "s", ttl_sec=-1)  # 已过期
    assert verify_identity(tok, "s") is None


def test_verify_not_yet_expired_ok():
    tok = sign_identity({"user_id": "u"}, "s", ttl_sec=120)
    got = verify_identity(tok, "s")
    assert got is not None and got["exp"] > time.time()


def test_verify_garbage_returns_none():
    assert verify_identity("", "s") is None
    assert verify_identity("not-a-token", "s") is None
    assert verify_identity("a.b.c", "s") is None
    assert verify_identity("x.y", "") is None  # 空 secret


def test_sign_empty_secret_raises():
    import pytest
    with pytest.raises(ValueError):
        sign_identity({"user_id": "u"}, "")


# ---------------------- claims -> Identity (gateway) ----------------------

def test_identity_from_internal_claims():
    from codev_platform.gateway.auth import identity_from_internal_claims
    idt = identity_from_internal_claims(
        {"user_id": "bob", "org_id": "acme", "projects": ["p1", "p2"], "all_projects": False})
    assert idt.user_id == "bob" and idt.org_id == "acme" and idt.via == "internal"
    assert idt.projects == frozenset({"p1", "p2"}) and idt.all_projects is False


def test_identity_from_internal_claims_all_projects():
    from codev_platform.gateway.auth import identity_from_internal_claims
    idt = identity_from_internal_claims({"user_id": "u", "all_projects": True})
    assert idt.all_projects is True and idt.projects == frozenset()


# ----------------- middleware X-Identity 通道 (ASGI 直驱) -----------------

def _run_asgi(app, path: str, headers: dict[str, str]) -> int:
    import asyncio

    async def _go():
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


def _app(captured, *, internal_secret=None):
    from starlette.applications import Starlette
    from starlette.responses import JSONResponse
    from starlette.routing import Route
    from codev_platform.gateway import AuthMiddleware, build_authenticator

    async def who(req):
        captured["identity"] = getattr(req.state, "identity", None)
        return JSONResponse({"ok": True})

    # token 模式 (无合法 token 默认 401), 用来证明 X-Identity 旁路放行
    cfg = {"gateway": {"auth_mode": "token", "tokens": {}}}
    app = Starlette(routes=[Route("/who", who)])
    app.add_middleware(
        AuthMiddleware, authenticator=build_authenticator(cfg),
        public_paths={"/health"}, internal_secret=internal_secret,
    )
    return app


def test_middleware_internal_identity_accepted():
    cap: dict = {}
    app = _app(cap, internal_secret="shared")
    tok = sign_identity({"user_id": "alice", "org_id": "acme", "projects": ["p1"]}, "shared")
    status = _run_asgi(app, "/who", {"X-Identity": tok})
    assert status == 200
    assert cap["identity"] is not None
    assert cap["identity"].user_id == "alice" and cap["identity"].via == "internal"


def test_middleware_internal_bad_token_falls_back_to_auth():
    # 验签失败 → 不采信, 回退原 token 认证 (无合法 token → 401), 不破坏原行为
    cap: dict = {}
    app = _app(cap, internal_secret="shared")
    assert _run_asgi(app, "/who", {"X-Identity": "garbage"}) == 401


def test_middleware_no_internal_secret_ignores_xidentity():
    # 未配 secret → X-Identity 通道关闭, 完全维持原 token 行为 (401)
    cap: dict = {}
    app = _app(cap, internal_secret=None)
    tok = sign_identity({"user_id": "alice"}, "shared")
    assert _run_asgi(app, "/who", {"X-Identity": tok}) == 401
