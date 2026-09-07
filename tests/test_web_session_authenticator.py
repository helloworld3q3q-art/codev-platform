"""双轨收口测试 (deep-audit-2026-06-03-review.md 决策2)。

token 模式下 web app 用 SessionAwareAuthenticator 包 gateway 认证器:
  - web 登录 session token → 过中间件 + current_session 解析 → 200;
  - 无 token → 401 (中间件 token 模式拒);
  - 乱 token → 401 (内层 gateway token 认证拒);
  - gateway 静态 token → 过中间件 (内层认证), 但非 session → current_session 403
    (证明 gateway token 通道保留, 不被 session 层挡)。
"""
from __future__ import annotations

import pytest

pytest.importorskip("fastapi")

from fastapi.testclient import TestClient  # noqa: E402

from codev_platform.core.httpkit import build_app  # noqa: E402
from codev_platform.gateway import build_authenticator  # noqa: E402
from codev_platform.gateway.auth import token_hash  # noqa: E402
from codev_platform.web.routes import auth as auth_routes  # noqa: E402
from codev_platform.web.security.session_authenticator import SessionAwareAuthenticator  # noqa: E402
from codev_platform.web.security.sessions import session_store  # noqa: E402

_GW_TOKEN = "GWTOKEN-static"
_CFG = {
    "gateway": {
        "auth_mode": "token",
        "tokens": {token_hash(_GW_TOKEN): {"user_id": "svc", "org_id": "o1", "projects": "*"}},
    }
}


@pytest.fixture(autouse=True)
def _reset():
    session_store.clear()
    yield
    session_store.clear()


@pytest.fixture
def client():
    app = build_app(
        title="t", routers=[auth_routes.router], cfg=_CFG,
        authenticator=SessionAwareAuthenticator(build_authenticator(_CFG)),
    )
    return TestClient(app)


def test_session_token_passes_in_token_mode(client):
    issued = session_store.create("alice", "acme")
    r = client.get("/api/v1/auth/session",
                   headers={"Authorization": f"Bearer {issued.access_token}"})
    assert r.status_code == 200
    body = r.json()
    assert body["result"] == 0
    assert body["data"]["username"] == "alice" and body["data"]["orgId"] == "acme"


def test_no_token_is_401(client):
    r = client.get("/api/v1/auth/session")
    assert r.status_code == 401


def test_bogus_token_is_401(client):
    r = client.get("/api/v1/auth/session", headers={"Authorization": "Bearer garbage"})
    assert r.status_code == 401


def test_gateway_static_token_passes_middleware_but_not_session(client):
    # gateway token 过中间件 (内层 TokenAuthenticator 认证), 但它不是 web session →
    # current_session 拒 → 403 (而非 401), 证明 gateway token 通道未被 session 层吞掉。
    r = client.get("/api/v1/auth/session", headers={"Authorization": f"Bearer {_GW_TOKEN}"})
    assert r.status_code == 403
