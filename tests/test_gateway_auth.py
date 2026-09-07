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


def test_token_via_query_param():
    # SSE/MCP 客户端无法设 header 时, ?token= 兜底(header 优先, 这里只给 query)。
    a = TokenAuthenticator({token_hash("tok-q"): {"user_id": "bob", "org_id": "acme"}})
    idt = a.authenticate({}, query="project_id=p1&token=tok-q")
    assert idt.user_id == "bob" and idt.org_id == "acme" and idt.via == "token"


def test_token_header_preferred_over_query():
    # header 与 query 都给时, header(Bearer)优先(更安全, 不进 URL)。
    a = TokenAuthenticator({token_hash("hdr"): {"user_id": "h", "org_id": "o"}})
    idt = a.authenticate({"Authorization": "Bearer hdr"}, query="token=ignored-bad")
    assert idt.user_id == "h"


def test_token_query_invalid_rejected():
    with pytest.raises(Unauthorized):
        TokenAuthenticator({token_hash("t"): {"user_id": "u"}}).authenticate({}, query="token=wrong")


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

def _run_asgi(app, path: str, headers: dict[str, str], query: str = "") -> int:
    """最小 ASGI GET 调用,返回响应状态码(避开 httpx/TestClient)。query=查询串(?后部分)。"""
    import asyncio

    async def _go() -> int:
        scope = {
            "type": "http", "http_version": "1.1", "method": "GET", "path": path,
            "raw_path": path.encode(), "query_string": query.encode(), "root_path": "", "scheme": "http",
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


# ---- deploy_policy_error(prod fail-fast 纯函数)----

def test_deploy_policy_prod_passthrough_rejected():
    from codev_platform.gateway.auth import deploy_policy_error
    err = deploy_policy_error({"deployment": {"mode": "prod"}, "gateway": {"auth_mode": "passthrough"}}, "0.0.0.0")
    assert err and "auth_mode=token" in err


def test_deploy_policy_prod_token_ok():
    from codev_platform.gateway.auth import deploy_policy_error
    assert deploy_policy_error({"deployment": {"mode": "prod"}, "gateway": {"auth_mode": "token"}}, "0.0.0.0") is None


def test_deploy_policy_dev_ok():
    from codev_platform.gateway.auth import deploy_policy_error
    assert deploy_policy_error({"deployment": {"mode": "dev"}, "gateway": {"auth_mode": "passthrough"}}, "0.0.0.0") is None
    assert deploy_policy_error({}, "127.0.0.1") is None  # 空 config 默认 dev/passthrough


def test_deploy_policy_remote_url_passthrough_rejected():
    from codev_platform.gateway.auth import deploy_policy_error
    err = deploy_policy_error({"platform": {"url": "https://platform.example.com:8848"}}, "127.0.0.1")
    assert err and "auth_mode=token" in err


def test_deploy_policy_localhost_url_ok():
    from codev_platform.gateway.auth import deploy_policy_error
    assert deploy_policy_error({"platform": {"url": "http://127.0.0.1:18083"}}, "127.0.0.1") is None
    assert deploy_policy_error({"platform": {"url": "http://localhost:8848"}}, "127.0.0.1") is None


# ---- bind_policy_error(非 loopback bind 硬拒, multi-org P1.2)----

def test_bind_policy_nonloopback_passthrough_rejected():
    from codev_platform.gateway.auth import bind_policy_error
    err = bind_policy_error({"gateway": {"auth_mode": "passthrough"}}, "0.0.0.0")
    assert err and "auth_mode=token" in err


def test_bind_policy_nonloopback_token_ok():
    from codev_platform.gateway.auth import bind_policy_error
    assert bind_policy_error({"gateway": {"auth_mode": "token"}}, "0.0.0.0") is None


def test_bind_policy_loopback_passthrough_ok():
    from codev_platform.gateway.auth import bind_policy_error
    # 单机默认: loopback bind + passthrough 放行(不破单机)
    assert bind_policy_error({}, "127.0.0.1") is None
    assert bind_policy_error({}, "localhost") is None
    assert bind_policy_error({}, "::1") is None


# ---- startup_policy_error(聚合三条, 单一入口)----

def test_startup_policy_aggregates_all_three():
    from codev_platform.gateway.auth import startup_policy_error
    # 多 dev passthrough → 命中 multi_user(第一条)
    assert startup_policy_error({"gateway": {"auth_mode": "passthrough", "multi_user": True}}, "127.0.0.1")
    # prod passthrough → 命中 deploy(第二条)
    assert startup_policy_error({"deployment": {"mode": "prod"}}, "127.0.0.1")
    # 非 loopback bind passthrough → 命中 bind(第三条)
    assert startup_policy_error({}, "0.0.0.0")
    # 单机默认全放行
    assert startup_policy_error({}, "127.0.0.1") is None
    # token 模式三条全放行(对外暴露安全)
    assert startup_policy_error({"gateway": {"auth_mode": "token"}, "deployment": {"mode": "prod"}}, "0.0.0.0") is None


def test_mcp_bind_host_single_source():
    from codev_platform.mcp_serve import mcp_bind_host
    assert mcp_bind_host({}) == "127.0.0.1"                          # 默认 loopback
    assert mcp_bind_host({"mcp": {"bind_host": "0.0.0.0"}}) == "0.0.0.0"  # config 覆盖


def test_mcp_bind_host_empty_falls_back_loopback():
    # 审计 P1: 显式空串 / 纯空白 → 回落 127.0.0.1(否则 uvicorn 绑 0.0.0.0 + 闸误放行)
    from codev_platform.mcp_serve import mcp_bind_host
    assert mcp_bind_host({"mcp": {"bind_host": ""}}) == "127.0.0.1"
    assert mcp_bind_host({"mcp": {"bind_host": "   "}}) == "127.0.0.1"


def test_bind_policy_empty_host_rejected_in_passthrough():
    # 审计 P1 defense-in-depth: bind 闸直接收到空串(= INADDR_ANY)也当非 loopback 拒绝
    from codev_platform.gateway.auth import bind_policy_error
    assert bind_policy_error({}, "") is not None
    assert bind_policy_error({}, "   ") is not None
    # token 模式仍放行(已验签)
    assert bind_policy_error({"gateway": {"auth_mode": "token"}}, "") is None


def test_middleware_token_mode_gates():
    cfg = {"gateway": {"auth_mode": "token", "tokens": {token_hash("good"): {"user_id": "bob", "org_id": "acme"}}}}
    app, cap = _starlette_app(cfg)
    assert _run_asgi(app, "/health", {}) == 200                                  # public 放行
    assert _run_asgi(app, "/who", {}) == 401                                     # 无 token → 401
    assert _run_asgi(app, "/who", {"Authorization": "Bearer bad"}) == 401        # 错 token → 401
    assert _run_asgi(app, "/who", {"Authorization": "Bearer good"}) == 200       # 对 token(hash 命中)
    assert cap["identity"].user_id == "bob" and cap["identity"].via == "token"


def test_middleware_token_via_query_param_end_to_end():
    # 端到端(#2): SSE/MCP 客户端无 header 时, ?token= 经 middleware→authenticator 整链认证。
    # 验真 AuthMiddleware 把 scope query_string 传给 authenticator + token 解析 + identity 挂上。
    cfg = {"gateway": {"auth_mode": "token", "tokens": {token_hash("qgood"): {"user_id": "carol", "org_id": "acme"}}}}
    app, cap = _starlette_app(cfg)
    assert _run_asgi(app, "/who", {}, query="project_id=p1&token=qgood") == 200   # ?token= 认证通
    assert cap["identity"].user_id == "carol" and cap["identity"].via == "token"
    assert _run_asgi(app, "/who", {}, query="token=qbad") == 401                  # 错 token → 401
    assert _run_asgi(app, "/who", {}, query="project_id=p1") == 401               # 无 token → 401


# ---- #6 token projects 白名单格式校验(非法跳过, 合法保留)----

def test_token_projects_filters_illegal_keeps_valid(caplog):
    import logging
    # "bad_id" 含下划线非法, "Up Per" 含空格非法; "good-1" / "GOOD2"(归一小写)合法
    a = TokenAuthenticator({token_hash("t"): {
        "user_id": "u", "projects": ["good-1", "bad_id", "Up Per", "good2"]}})
    with caplog.at_level(logging.WARNING, logger="codev_platform.gateway"):
        idt = a.authenticate({"Authorization": "Bearer t"})
    assert idt.projects == frozenset({"good-1", "good2"})  # 非法剔除, 合法保留(归一小写)
    assert idt.all_projects is False
    msgs = " ".join(r.message for r in caplog.records)
    assert "bad_id" in msgs and "Up Per" in msgs  # 非法项各发一条 warning


def test_token_projects_star_unaffected():
    # "*" 分支不走校验, 仍 all_projects=True
    a = TokenAuthenticator({token_hash("t"): {"user_id": "u", "projects": "*"}})
    idt = a.authenticate({"Authorization": "Bearer t"})
    assert idt.all_projects is True and idt.projects == frozenset()


def test_token_projects_all_illegal_yields_empty():
    a = TokenAuthenticator({token_hash("t"): {"user_id": "u", "projects": ["a_b", "c d"]}})
    idt = a.authenticate({"Authorization": "Bearer t"})
    assert idt.projects == frozenset() and idt.all_projects is False  # 全非法 → 无权


# ---- #5 obslog logging_mode: token→prod / 显式 dev 尊重 / passthrough→dev ----

def test_logging_mode_token_defaults_prod(monkeypatch):
    from codev_platform.core.obslog import logging_mode
    monkeypatch.delenv("CODEV_PLATFORM_LOG_MODE", raising=False)
    assert logging_mode({"gateway": {"auth_mode": "token"}}) == "prod"


def test_logging_mode_token_auto_defaults_prod(monkeypatch):
    from codev_platform.core.obslog import logging_mode
    monkeypatch.delenv("CODEV_PLATFORM_LOG_MODE", raising=False)
    # 显式 "auto"(非 dev/prod)仍按 token 推断 → prod
    assert logging_mode({"gateway": {"auth_mode": "token"}, "logging": {"mode": "auto"}}) == "prod"


def test_logging_mode_explicit_dev_respected_under_token(monkeypatch):
    from codev_platform.core.obslog import logging_mode
    monkeypatch.delenv("CODEV_PLATFORM_LOG_MODE", raising=False)
    # 显式 dev 留调试逃生口, token 模式也尊重
    assert logging_mode({"gateway": {"auth_mode": "token"}, "logging": {"mode": "dev"}}) == "dev"


def test_logging_mode_passthrough_defaults_dev(monkeypatch):
    from codev_platform.core.obslog import logging_mode
    monkeypatch.delenv("CODEV_PLATFORM_LOG_MODE", raising=False)
    assert logging_mode({"gateway": {"auth_mode": "passthrough"}}) == "dev"
    assert logging_mode({}) == "dev"
    assert logging_mode(None) == "dev"
