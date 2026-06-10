"""Auth 垂直片测试 (plan §十五 Auth) —— login/logout/token-refresh/session 全链路。

passthrough 模式建 app (dev 信任, AuthMiddleware 放行); 登录态由 session_store 会话 token 提供。
autouse fixture 每例清空账户表 + 会话表, 保证隔离。用 hash_password 造用户塞 user_store。
"""
from __future__ import annotations

import pytest

pytest.importorskip("fastapi")

from fastapi.testclient import TestClient  # noqa: E402

from codev_platform.core.httpkit import build_app  # noqa: E402
from codev_platform.web.domain.accounts import STATUS_DISABLED, User  # noqa: E402
from codev_platform.web.repositories.account_store import (  # noqa: E402
    reset_account_stores,
    user_store,
)
from codev_platform.web.routes import auth  # noqa: E402
from codev_platform.web.security.passwords import hash_password  # noqa: E402
from codev_platform.web.security.sessions import session_store  # noqa: E402

_CFG = {"gateway": {"auth_mode": "passthrough"}, "projects": {}}


@pytest.fixture(autouse=True)
def _isolate(monkeypatch):
    # 本套是 dev/passthrough 内存路径测试 (角色写进内存 member_store)。但在配了 memory.pg_dsn
    # 的环境 (如 WSL) get_rbac_store 会返回真 PG store, resolve_membership 优先查它 (空) 而越过
    # 内存 store → 角色空/403。强制 _pg_rbac_store→None, 让本套确定走内存路径 (PG RBAC 路径由
    # test_web_db_stores_sqlite / test_rbac_wire 等专测)。
    monkeypatch.setattr("codev_platform.web.security.membership._pg_rbac_store", lambda: None)
    reset_account_stores()
    session_store.clear()
    yield
    reset_account_stores()
    session_store.clear()


@pytest.fixture
def client():
    app = build_app(title="t", routers=[auth.router], cfg=_CFG)
    return TestClient(app)


def _seed_user(username="alice", password="s3cret", org_id="orgA", status="ACTIVE"):
    user_store.create(User(
        username=username, password_hash=hash_password(password),
        org_id=org_id, status=status,
    ))


def _login(client, username="alice", password="s3cret"):
    return client.post("/api/v1/auth/login", json={"username": username, "password": password})


def test_login_success_returns_tokens(client):
    _seed_user()
    r = _login(client)
    assert r.status_code == 200
    data = r.json()["data"]
    assert data["accessToken"] and data["refreshToken"]
    assert data["accessToken"] != data["refreshToken"]


def test_login_wrong_password_403(client):
    _seed_user()
    r = _login(client, password="nope")
    assert r.status_code == 403 and r.json()["result"] == 1


def test_login_unknown_user_403(client):
    r = _login(client, username="ghost")
    assert r.status_code == 403 and r.json()["result"] == 1


def test_login_disabled_user_403(client):
    _seed_user(status=STATUS_DISABLED)
    r = _login(client)
    assert r.status_code == 403 and r.json()["result"] == 1


def test_refresh_rotates_token(client):
    _seed_user()
    refresh = _login(client).json()["data"]["refreshToken"]
    r = client.post("/api/v1/auth/token/refresh", json={"refreshToken": refresh})
    assert r.status_code == 200
    new_refresh = r.json()["data"]["refreshToken"]
    assert new_refresh != refresh
    # 旧 refresh 已轮换失效
    r2 = client.post("/api/v1/auth/token/refresh", json={"refreshToken": refresh})
    assert r2.status_code == 403


def test_logout_invalidates_refresh(client):
    _seed_user()
    refresh = _login(client).json()["data"]["refreshToken"]
    assert client.post("/api/v1/auth/logout", json={"refreshToken": refresh}).status_code == 200
    r = client.post("/api/v1/auth/token/refresh", json={"refreshToken": refresh})
    assert r.status_code == 403


def test_session_returns_current(client):
    _seed_user()
    access = _login(client).json()["data"]["accessToken"]
    r = client.get("/api/v1/auth/session", headers={"Authorization": f"Bearer {access}"})
    assert r.status_code == 200
    data = r.json()["data"]
    assert data["username"] == "alice" and data["orgId"] == "orgA"


def test_session_without_login_403(client):
    assert client.get("/api/v1/auth/session").status_code == 403


def test_session_roles_member_default_empty(client):
    """无 membership 记录的用户 → roles 空 (安全默认, 不臆造角色)。"""
    _seed_user()
    access = _login(client).json()["data"]["accessToken"]
    r = client.get("/api/v1/auth/session", headers={"Authorization": f"Bearer {access}"})
    assert r.status_code == 200
    assert r.json()["data"]["roles"] == []


def test_session_roles_org_admin_from_membership(client):
    """org admin membership → roles 含 'admin' (后端从可信 member_store 算)。"""
    from codev_platform.web.domain.accounts import OrgMember
    from codev_platform.web.repositories.account_store import member_store

    _seed_user()
    member_store.upsert(OrgMember(org_id="orgA", username="alice", role="admin"))
    access = _login(client).json()["data"]["accessToken"]
    r = client.get("/api/v1/auth/session", headers={"Authorization": f"Bearer {access}"})
    assert r.status_code == 200
    assert "admin" in r.json()["data"]["roles"]


def test_session_roles_platform_admin_from_config(client, monkeypatch):
    """platform_admin 白名单命中 → roles 含 'platform_admin' (config/env 可信源, 不信 client)。"""
    monkeypatch.setenv("CODEV_PLATFORM_ADMINS", "alice")
    _seed_user()
    access = _login(client).json()["data"]["accessToken"]
    r = client.get("/api/v1/auth/session", headers={"Authorization": f"Bearer {access}"})
    assert r.status_code == 200
    assert "platform_admin" in r.json()["data"]["roles"]


# ---- 活动 org 切换 (多 org 成员) ----

def _add_member(org, username="alice", role="member"):
    from codev_platform.web.domain.accounts import OrgMember
    from codev_platform.web.repositories.account_store import member_store
    member_store.upsert(OrgMember(org_id=org, username=username, role=role))


def test_session_includes_member_orgs(client):
    # /session 返回本人所属全部 org(含当前活动 org), 供前端切换器列选项。
    _seed_user()  # home orgA
    _add_member("orgA")
    _add_member("orgB")
    access = _login(client).json()["data"]["accessToken"]
    r = client.get("/api/v1/auth/session", headers={"Authorization": f"Bearer {access}"})
    orgs = r.json()["data"]["orgs"]
    assert "orgA" in orgs and "orgB" in orgs


def test_switch_org_to_member_org(client):
    # alice 是 orgA+orgB 成员, 登录(orgA)后切到 orgB → 新 token 的 session orgId=orgB(#2: RBAC 随之按新 org 判)。
    _seed_user()  # home orgA
    _add_member("orgA")
    _add_member("orgB")
    access = _login(client).json()["data"]["accessToken"]
    r = client.post("/api/v1/auth/switch-org", json={"orgId": "orgB"},
                    headers={"Authorization": f"Bearer {access}"})
    assert r.status_code == 200
    new_access = r.json()["data"]["accessToken"]
    s = client.get("/api/v1/auth/session", headers={"Authorization": f"Bearer {new_access}"})
    assert s.json()["data"]["orgId"] == "orgB"


def test_switch_org_to_non_member_403(client):
    # 🔴 红线: 不是 orgB 成员 → 切换被拒。否则任意登录用户改个 orgId 就能拿别 org 会话身份 = 越权。
    _seed_user()  # home orgA
    _add_member("orgA")  # 只 orgA 成员
    access = _login(client).json()["data"]["accessToken"]
    r = client.post("/api/v1/auth/switch-org", json={"orgId": "orgB"},
                    headers={"Authorization": f"Bearer {access}"})
    assert r.status_code == 403
    assert r.json()["errors"][0]["errorCode"] == "access_denied"


def test_switch_org_platform_admin_any(client, monkeypatch):
    # platform_admin 可切任意 org(即便非成员)—— 运维特权。
    monkeypatch.setenv("CODEV_PLATFORM_ADMINS", "alice")
    _seed_user()  # home orgA, 无 orgZ 成员
    access = _login(client).json()["data"]["accessToken"]
    r = client.post("/api/v1/auth/switch-org", json={"orgId": "orgZ"},
                    headers={"Authorization": f"Bearer {access}"})
    assert r.status_code == 200
    new_access = r.json()["data"]["accessToken"]
    s = client.get("/api/v1/auth/session", headers={"Authorization": f"Bearer {new_access}"})
    assert s.json()["data"]["orgId"] == "orgZ"
