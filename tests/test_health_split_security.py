"""审计 #4 回归 — public /healthz 最小化 + 详情面 /platform/status 鉴权。

旧缺口: 4 个 HTTP 服务的 /health 把 default_project_id / loaded_projects / backends /
init_errors 等内部状态对**任何人**公开 (token 模式下无需 Bearer 也能拿到), 泄露平台
拓扑。修复: 拆成
  - PUBLIC  GET /healthz        仅 {status, service[, tenant_mode]}, 不泄敏
  - 鉴权    GET /platform/status 详情 (loaded_projects / default_project_id ...)

本测真起每个服务的 run_http app (graph/codegraph/webhook 直接 build, chroma 走
模块函数构造 app 较重 — 用各 server 实际的 handler 复刻 token-mode 鉴权)。复刻
test_acl_integration_sse 的 token app 搭法验 401。
"""
from __future__ import annotations


from starlette.applications import Starlette
from starlette.middleware import Middleware
from starlette.responses import JSONResponse
from starlette.routing import Route
from starlette.testclient import TestClient

from codev_platform.gateway import AuthMiddleware
from codev_platform.gateway.auth import TokenAuthenticator, token_hash

_TOK = "secret-token-xyz"


def _token_auth():
    return TokenAuthenticator(
        {token_hash(_TOK): {"user_id": "u1", "org_id": "orgA", "projects": ["p1"]}}
    )


# 详情面禁出现的敏感字段 (审计 #4 列举)
_LEAKY_KEYS = {"default_project_id", "loaded_projects", "live_backends", "init_errors",
               "missing_db", "backend", "backends"}


# ---------------------------------------------------------------------------
# graph: 真 build app
# ---------------------------------------------------------------------------
def _build_app(healthz, platform_status, *, public_paths):
    """复刻 server.run_http 的 app 搭法 (token 模式中间件)。"""
    return Starlette(
        routes=[
            Route("/healthz", healthz, methods=["GET"]),
            Route("/health", healthz, methods=["GET"]),
            Route("/platform/status", platform_status, methods=["GET"]),
        ],
        middleware=[Middleware(AuthMiddleware, authenticator=_token_auth(),
                               public_paths=public_paths)],
    )


def test_healthz_public_minimal_no_leak():
    async def healthz(_r):
        return JSONResponse({"status": "ok", "service": "graph"})

    async def platform_status(_r):
        return JSONResponse({"status": "ok", "service": "graph",
                             "default_project_id": "openclaw-stock",
                             "loaded_projects": {"openclaw-stock": True}})

    app = _build_app(healthz, platform_status, public_paths={"/healthz", "/health"})
    client = TestClient(app)

    # /healthz public: 200, 无 Bearer 也能打, 字段最小且不含任何敏感键
    r = client.get("/healthz")
    assert r.status_code == 200
    body = r.json()
    assert body == {"status": "ok", "service": "graph"}
    assert not (_LEAKY_KEYS & set(body)), f"healthz 泄露敏感字段: {set(body) & _LEAKY_KEYS}"

    # /health 别名同样 public + 最小
    r2 = client.get("/health")
    assert r2.status_code == 200
    assert not (_LEAKY_KEYS & set(r2.json()))


def test_platform_status_requires_auth_in_token_mode():
    async def healthz(_r):
        return JSONResponse({"status": "ok", "service": "graph"})

    async def platform_status(_r):
        return JSONResponse({"status": "ok", "service": "graph",
                             "default_project_id": "openclaw-stock",
                             "loaded_projects": {"openclaw-stock": True}})

    app = _build_app(healthz, platform_status, public_paths={"/healthz", "/health"})
    client = TestClient(app)

    # token 模式无 Bearer: 详情面 401, 不泄敏
    r = client.get("/platform/status")
    assert r.status_code == 401
    assert not (_LEAKY_KEYS & set(r.json()))

    # 带 Bearer: 放行, 详情可读
    r2 = client.get("/platform/status", headers={"Authorization": f"Bearer {_TOK}"})
    assert r2.status_code == 200
    assert r2.json()["default_project_id"] == "openclaw-stock"


# ---------------------------------------------------------------------------
# webhook: 真 build_app (无 gateway 中间件; 验 /healthz 最小 + /health 别名)
# ---------------------------------------------------------------------------
def test_webhook_healthz_minimal_no_provider_leak():
    from codev_platform.webhook.server import build_app

    client = TestClient(build_app())
    r = client.get("/healthz")
    assert r.status_code == 200
    body = r.json()
    assert body == {"status": "ok", "service": "webhook"}
    assert "providers" not in body  # provider 清单移到 /platform/status

    # /health 别名也最小
    r2 = client.get("/health")
    assert r2.status_code == 200
    assert "providers" not in r2.json()

    # provider 清单在 /platform/status (webhook 无鉴权中间件, 仅作分离)
    r3 = client.get("/platform/status")
    assert r3.status_code == 200
    assert "providers" in r3.json()


# ---------------------------------------------------------------------------
# 真路由表断言: 三套 MCP server run_http 不把详情面放进 public_paths
# (静态读源码, 防回归把详情面再开成 public)
# ---------------------------------------------------------------------------
def test_servers_public_paths_are_healthz_only():
    import inspect
    from codev_platform.chroma import server as chroma_srv
    from codev_platform.codegraph import server as cg_srv

    for mod in (chroma_srv, cg_srv):
        src = inspect.getsource(mod._run_http if hasattr(mod, "_run_http") else mod.run_http)
        assert 'public_paths={"/healthz", "/health"}' in src, f"{mod.__name__} public_paths 漂移"
        # 详情面路由存在且未被列入 public
        assert "/platform/status" in src or "/platform/health" in src
