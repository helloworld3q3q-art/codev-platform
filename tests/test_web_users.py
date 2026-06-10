"""Users 垂直片测试 (plan §十五 Users) —— profile/list/create/status/password/roles 等。

覆盖: profile 当前用户 / create 密码不明文 (落库是 hash) / list 分页 / status 禁用后
revoke_user (会话失效) / 重复用户名报错 / password reset。
本 venv 未装 fastapi → importorskip 自动 skip。

授权地基: passthrough auth_mode; 用 session_store.create 造登录态; 经
member_store.upsert(OrgMember(role="admin")) 赋 org admin。无 platform_admin (走 org admin 路径)。
"""
from __future__ import annotations

import pytest

pytest.importorskip("fastapi")

from fastapi.testclient import TestClient  # noqa: E402

import codev_platform.web.routes.users as users_routes  # noqa: E402
import codev_platform.web.security.deps as wdeps  # noqa: E402
from codev_platform.core.httpkit import build_app  # noqa: E402
from codev_platform.web.domain.accounts import STATUS_DISABLED, OrgMember, User  # noqa: E402
from codev_platform.web.repositories.account_store import (  # noqa: E402
    member_store,
    reset_account_stores,
    user_store,
)
from codev_platform.web.routes import users  # noqa: E402
from codev_platform.web.security.passwords import hash_password  # noqa: E402
from codev_platform.web.security.sessions import session_store  # noqa: E402

_CFG = {"gateway": {"auth_mode": "passthrough"}, "projects": {}}


@pytest.fixture(autouse=True)
def _isolate(monkeypatch):
    reset_account_stores()
    session_store.clear()
    # 隔离真实 config: 无 platform_admin → 走 org admin 授权路径
    monkeypatch.setattr(wdeps, "load_config", lambda: {"platform_admins": []})
    monkeypatch.setattr(users_routes, "load_config", lambda: {"platform_admins": []})
    yield
    reset_account_stores()
    session_store.clear()


@pytest.fixture
def client():
    return TestClient(build_app(title="t", routers=[users.router], cfg=_CFG))


def _admin_session(org="orgA", username="boss"):
    """造一个 org admin 登录态, 返回 auth header。"""
    user_store.upsert(User(username=username, password_hash=hash_password("pw123456"),
                           org_id=org, display_name="Boss"))
    member_store.upsert(OrgMember(org_id=org, username=username, role="admin"))
    t = session_store.create(username, org)
    return {"Authorization": f"Bearer {t.access_token}"}


# ---- profile ----

def test_profile_returns_current_user(client):
    auth = _admin_session(username="boss")
    r = client.get("/api/v1/users/profile", headers=auth)
    assert r.status_code == 200
    body = r.json()
    assert body["result"] == 0
    assert body["data"]["username"] == "boss"
    assert body["data"]["orgId"] == "orgA"
    # response 绝不含密码 / hash
    assert "password" not in body["data"] and "passwordHash" not in body["data"]


def test_profile_requires_login(client):
    assert client.get("/api/v1/users/profile").status_code == 403


# ---- create: 密码不落明文 (存的是 hash) ----

def test_create_user_stores_hash_not_plaintext(client):
    auth = _admin_session()
    r = client.post("/api/v1/users/create", headers=auth, json={
        "username": "alice", "password": "secret-pw", "orgId": "orgA",
        "displayName": "Alice", "role": "member",
    })
    assert r.status_code == 200, r.text
    assert r.json()["data"]["username"] == "alice"
    stored = user_store.get("alice")
    assert stored is not None
    assert stored.password_hash != "secret-pw"
    assert stored.password_hash.startswith("pbkdf2_sha256$")
    # 角色已写入 member 表
    assert member_store.get("orgA", "alice").role == "member"
    # 创建动作不应回显密码
    assert "password" not in r.json()["data"]


def test_create_duplicate_username_is_error(client):
    auth = _admin_session()
    payload = {"username": "dup", "password": "pw123456", "orgId": "orgA"}
    assert client.post("/api/v1/users/create", headers=auth, json=payload).status_code == 200
    r = client.post("/api/v1/users/create", headers=auth, json=payload)
    assert r.status_code == 400
    body = r.json()
    assert body["result"] == 1
    assert body["errors"][0]["errorCode"] == "invalid_params"


# ---- list 分页 ----

def test_list_users_pagination(client):
    auth = _admin_session()
    for i in range(3):
        client.post("/api/v1/users/create", headers=auth,
                    json={"username": f"u{i}", "password": "pw123456", "orgId": "orgA"})
    r = client.post("/api/v1/users/list", json={"pageNumber": 1, "pageSize": 2}, headers=auth)
    assert r.status_code == 200
    body = r.json()
    assert body["currentPage"] == 1 and body["pageSize"] == 2
    # boss + u0/u1/u2 = 4 用户, totalPage = 2
    assert body["total"] == 4 and body["totalPage"] == 2
    assert len(body["data"]) == 2


def test_list_and_detail_include_role(client):
    auth = _admin_session(username="boss")  # boss 在 orgA 是 admin
    client.post("/api/v1/users/create", headers=auth,
                json={"username": "alice", "password": "pw123456", "orgId": "orgA", "role": "member"})
    # list 每行带 role
    r = client.post("/api/v1/users/list", headers=auth)
    by_name = {u["username"]: u for u in r.json()["data"]}
    assert by_name["boss"]["role"] == "admin"
    assert by_name["alice"]["role"] == "member"
    # detail 带 role
    d = client.get("/api/v1/users/detail", headers=auth, params={"username": "alice"})
    assert d.json()["data"]["role"] == "member"
    # 无 member 记录 → role=None (不报错)
    user_store.upsert(User(username="nomem", password_hash=hash_password("pw123456"), org_id="orgA"))
    d2 = client.get("/api/v1/users/detail", headers=auth, params={"username": "nomem"})
    assert d2.json()["data"]["role"] is None


# ---- status 禁用 → revoke_user ----

def test_disable_user_revokes_sessions(client):
    auth = _admin_session(username="boss")
    # 造一个目标用户 + 它自己的活跃会话
    user_store.upsert(User(username="victim", password_hash=hash_password("pw123456"),
                           org_id="orgA"))
    vt = session_store.create("victim", "orgA")
    assert session_store.resolve(vt.access_token) is not None
    r = client.post("/api/v1/users/status", headers=auth,
                    json={"username": "victim", "status": STATUS_DISABLED})
    assert r.status_code == 200
    assert r.json()["data"]["status"] == STATUS_DISABLED
    assert user_store.get("victim").status == STATUS_DISABLED
    # 会话已被撤销
    assert session_store.resolve(vt.access_token) is None


# ---- password reset ----

def test_password_reset_changes_hash(client):
    auth = _admin_session()
    user_store.upsert(User(username="pw", password_hash=hash_password("old-pw"),
                           org_id="orgA"))
    old_hash = user_store.get("pw").password_hash
    r = client.post("/api/v1/users/password/reset", headers=auth,
                    json={"username": "pw", "newPassword": "brand-new-pw"})
    assert r.status_code == 200
    new_hash = user_store.get("pw").password_hash
    assert new_hash != old_hash
    assert new_hash.startswith("pbkdf2_sha256$")
    assert new_hash != "brand-new-pw"


# ---- roles ----

def test_set_roles_updates_member(client):
    auth = _admin_session()
    user_store.upsert(User(username="r", password_hash=hash_password("pw123456"),
                           org_id="orgA"))
    r = client.post("/api/v1/users/roles", headers=auth,
                    json={"username": "r", "orgId": "orgA", "role": "admin"})
    assert r.status_code == 200
    assert member_store.get("orgA", "r").role == "admin"


def test_set_roles_rejected_for_other_org_user(client):
    # P0-2: orgA org_admin 不能给 orgB 用户改角色 (即便请求 orgId 传 orgA)。防把外组用户写进本组成员表。
    auth = _admin_session(org="orgA", username="boss")
    user_store.upsert(User(username="outsider", password_hash=hash_password("pw123456"),
                           org_id="orgB"))
    r = client.post("/api/v1/users/roles", headers=auth,
                    json={"username": "outsider", "orgId": "orgA", "role": "admin"})
    assert r.status_code == 403
    assert r.json()["errors"][0]["errorCode"] == "access_denied"
    assert member_store.get("orgA", "outsider") is None


def test_create_user_writes_default_member(client):
    # P0-4: 不传 role 创建用户 → 默认写 member 成员关系 (PG 模式 org 归属从成员表反推不丢)。
    auth = _admin_session(org="orgA", username="boss")
    r = client.post("/api/v1/users/create", headers=auth,
                    json={"username": "nomember", "password": "pw123456", "orgId": "orgA"})
    assert r.status_code == 200
    m = member_store.get("orgA", "nomember")
    assert m is not None and m.role == "member"


def test_create_user_respects_explicit_role(client):
    # P0-4 对照 (审计补): 显式传 role 时尊重该 role, 不被默认 member 覆盖。
    auth = _admin_session(org="orgA", username="boss")
    r = client.post("/api/v1/users/create", headers=auth,
                    json={"username": "adm", "password": "pw123456", "orgId": "orgA", "role": "admin"})
    assert r.status_code == 200
    m = member_store.get("orgA", "adm")
    assert m is not None and m.role == "admin"


def test_set_roles_rejects_mismatched_org_id(client):
    # 跨 org 隔离红线: org_admin 只能在自己 (session) org 授角色, 往其它 org 写一律拒
    # (多对多模型下跨 org 成员管理是 platform_admin 的活, 见 test_platform_admin_sets_role_across_orgs)。
    auth = _admin_session(org="orgA", username="boss")
    user_store.upsert(User(username="u2", password_hash=hash_password("pw123456"), org_id="orgA"))
    # boss 是 orgA org_admin(非 platform_admin), 传 org_id=orgB → 拒绝, orgB 成员表不留越权 membership
    r = client.post("/api/v1/users/roles", headers=auth,
                    json={"username": "u2", "orgId": "orgB", "role": "admin"})
    assert r.status_code != 200
    assert member_store.get("orgB", "u2") is None


def test_platform_admin_sets_role_across_orgs(client, monkeypatch):
    # 多对多成员模型: platform_admin 可给用户在其首 org 之外的第 2 个 org 授角色 → 用户成多 org 成员,
    # 两处成员关系并存。跨 org 隔离仍由 org_admin 路径守(见上一个测试)。
    monkeypatch.setattr(users_routes, "load_config", lambda: {"platform_admins": ["boss"]})
    monkeypatch.setattr(wdeps, "load_config", lambda: {"platform_admins": ["boss"]})
    t = session_store.create("boss", "orgA")
    auth = {"Authorization": f"Bearer {t.access_token}"}
    user_store.upsert(User(username="multi", password_hash=hash_password("pw123456"), org_id="orgA"))
    member_store.upsert(OrgMember(org_id="orgA", username="multi", role="member"))
    r = client.post("/api/v1/users/roles", headers=auth,
                    json={"username": "multi", "orgId": "orgB", "role": "admin"})
    assert r.status_code == 200
    assert member_store.get("orgB", "multi").role == "admin"   # 第 2 个 org 成员关系建立
    assert member_store.get("orgA", "multi").role == "member"  # 首 org 成员关系仍在 → 多对多并存


# ---- 授权: 非 admin 被拒 ----

def test_non_admin_cannot_list(client):
    user_store.upsert(User(username="plain", password_hash=hash_password("pw123456"),
                           org_id="orgA"))
    t = session_store.create("plain", "orgA")
    auth = {"Authorization": f"Bearer {t.access_token}"}
    assert client.post("/api/v1/users/list", headers=auth).status_code == 403


# ---- 越权: org_admin 不能管理其他 org 用户 ----

def test_org_admin_cannot_touch_other_org(client):
    auth = _admin_session(org="orgA", username="boss")
    user_store.upsert(User(username="outsider", password_hash=hash_password("pw123456"),
                           org_id="orgB"))
    r = client.get("/api/v1/users/detail", headers=auth, params={"username": "outsider"})
    assert r.status_code == 403
    assert r.json()["errors"][0]["errorCode"] == "access_denied"


# ---- selections ----

def test_selections_returns_label_value(client):
    auth = _admin_session()
    client.post("/api/v1/users/create", headers=auth,
                json={"username": "sel", "password": "pw123456", "orgId": "orgA",
                      "displayName": "Sel User"})
    r = client.get("/api/v1/users/selections", headers=auth)
    assert r.status_code == 200
    items = r.json()["data"]
    values = {it["value"] for it in items}
    assert "sel" in values and "boss" in values
