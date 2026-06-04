"""Orgs 垂直片测试 (plan §十五 Orgs) —— 组织 CRUD/status/selections + 成员 add/roles/list。

覆盖: 创建组织需平台超管 (普通成员被拒) / list 分页 PageResult / 成员 add+roles /
status 禁用后 selections 过滤。授权依赖 deps.load_config (platform_admins) 用
monkeypatch 模拟; 登录态用 session_store.create 造。
本 venv 未装 fastapi → importorskip 自动 skip。
"""
from __future__ import annotations

import pytest

pytest.importorskip("fastapi")

from fastapi.testclient import TestClient  # noqa: E402

import codev_platform.web.security.deps as wdeps  # noqa: E402
from codev_platform.core.httpkit import build_app  # noqa: E402
from codev_platform.web.domain.accounts import Org, OrgMember  # noqa: E402
from codev_platform.web.repositories.account_store import (  # noqa: E402
    member_store,
    org_store,
    reset_account_stores,
)
from codev_platform.web.routes import orgs  # noqa: E402
from codev_platform.web.security.sessions import session_store  # noqa: E402

_CFG = {"gateway": {"auth_mode": "passthrough"}, "projects": {}}
_ADMINS = {"platform_admins": ["super"]}


@pytest.fixture(autouse=True)
def _isolate(monkeypatch):
    reset_account_stores()
    session_store.clear()
    monkeypatch.setattr(wdeps, "load_config", lambda: _ADMINS)
    yield
    reset_account_stores()
    session_store.clear()


@pytest.fixture
def client():
    return TestClient(build_app(title="t", routers=[orgs.router], cfg=_CFG))


def _auth(username: str, org_id: str) -> dict:
    t = session_store.create(username, org_id)
    return {"Authorization": f"Bearer {t.access_token}"}


def test_create_org_requires_platform_admin(client):
    super_h = _auth("super", "")
    r = client.post("/api/v1/orgs/create", json={"code": "acme", "name": "Acme"}, headers=super_h)
    assert r.status_code == 200
    assert r.json()["data"]["code"] == "acme"
    # 详情可读
    d = client.get("/api/v1/orgs/detail", params={"code": "acme"}, headers=super_h)
    assert d.json()["data"]["name"] == "Acme"


def test_create_org_non_admin_denied(client):
    h = _auth("alice", "acme")
    r = client.post("/api/v1/orgs/create", json={"code": "acme", "name": "Acme"}, headers=h)
    assert r.status_code == 403
    assert r.json()["result"] == 1
    assert r.json()["errors"][0]["errorCode"] == "access_denied"


def test_create_org_idempotent_duplicate_is_unified_error(client):
    h = _auth("super", "")
    payload = {"code": "dup", "name": "Dup"}
    assert client.post("/api/v1/orgs/create", json=payload, headers=h).status_code == 200
    r = client.post("/api/v1/orgs/create", json=payload, headers=h)
    assert r.status_code == 400
    assert r.json()["errors"][0]["errorCode"] == "invalid_params"


def test_list_orgs_pagination(client):
    for i in range(3):
        org_store.create(Org(code=f"org{i}", name=f"Org{i}"))
    h = _auth("super", "")
    r = client.post("/api/v1/orgs/list", json={"pageSize": 2, "pageNumber": 1}, headers=h)
    assert r.status_code == 200
    body = r.json()
    assert body["result"] == 0
    assert body["total"] == 3 and body["pageSize"] == 2 and body["totalPage"] == 2
    assert len(body["data"]) == 2
    assert body["requestId"]


def test_member_add_then_roles(client):
    org_store.create(Org(code="acme", name="Acme"))
    # bob 是 acme 的 org_admin
    member_store.upsert(OrgMember(org_id="acme", username="bob", role="admin"))
    admin_h = _auth("bob", "acme")
    # 加成员 (默认 member)
    a = client.post("/api/v1/orgs/members/add",
                    json={"code": "acme", "username": "carol"}, headers=admin_h)
    assert a.status_code == 200
    assert a.json()["data"]["role"] == "member"
    # 提权为 admin
    r = client.post("/api/v1/orgs/members/roles",
                    json={"code": "acme", "username": "carol", "role": "admin"}, headers=admin_h)
    assert r.status_code == 200 and r.json()["data"]["role"] == "admin"
    # 成员列表含 carol
    lst = client.post("/api/v1/orgs/members/list", json={"code": "acme"}, headers=admin_h)
    names = {m["username"] for m in lst.json()["data"]}
    assert {"bob", "carol"} <= names


def test_member_add_invalid_role_rejected(client):
    org_store.create(Org(code="acme", name="Acme"))
    member_store.upsert(OrgMember(org_id="acme", username="bob", role="admin"))
    h = _auth("bob", "acme")
    r = client.post("/api/v1/orgs/members/add",
                    json={"code": "acme", "username": "x", "role": "superuser"}, headers=h)
    assert r.status_code == 400
    assert r.json()["errors"][0]["errorCode"] == "invalid_params"


def test_status_disable_filters_selections(client):
    org_store.create(Org(code="acme", name="Acme"))
    org_store.create(Org(code="beta", name="Beta"))
    h = _auth("super", "")  # platform_admin bypass org_role
    # selections 初始含两者
    sel = client.post("/api/v1/orgs/selections", headers=h)
    assert {o["code"] for o in sel.json()["data"]} == {"acme", "beta"}
    # 禁用 acme
    s = client.post("/api/v1/orgs/status",
                    json={"code": "acme", "status": "DISABLED"}, headers=h)
    assert s.status_code == 200 and s.json()["data"]["status"] == "DISABLED"
    sel2 = client.post("/api/v1/orgs/selections", headers=h)
    assert {o["code"] for o in sel2.json()["data"]} == {"beta"}


def test_detail_unknown_org_is_project_unknown(client):
    h = _auth("super", "")
    r = client.get("/api/v1/orgs/detail", params={"code": "nope"}, headers=h)
    assert r.status_code == 404
    assert r.json()["errors"][0]["errorCode"] == "project_unknown"
