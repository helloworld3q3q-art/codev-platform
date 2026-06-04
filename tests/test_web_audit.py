"""Audit 只读路由测试 (B1) —— filter/分页 + org_admin 限本 org + 普通用户 403。

审计日志 (access.jsonl) 用临时文件造; 通过 monkeypatch repo 的 audit_log_path 指过去。
授权 deps.load_config (platform_admins) monkeypatch; 登录态用 session_store.create。
本 venv 未装 fastapi → importorskip 自动 skip。
"""
from __future__ import annotations

import json

import pytest

pytest.importorskip("fastapi")

from fastapi.testclient import TestClient  # noqa: E402

import codev_platform.web.repositories.audit_read_repo as audit_repo  # noqa: E402
import codev_platform.web.routes.audit as waudit  # noqa: E402
import codev_platform.web.security.deps as wdeps  # noqa: E402
from codev_platform.core.httpkit import build_app  # noqa: E402
from codev_platform.web.domain.accounts import OrgMember  # noqa: E402
from codev_platform.web.repositories.account_store import (  # noqa: E402
    member_store,
    reset_account_stores,
)
from codev_platform.web.routes import audit  # noqa: E402
from codev_platform.web.security.sessions import session_store  # noqa: E402

_CFG = {"gateway": {"auth_mode": "passthrough"}, "projects": {}}
_ADMINS = {"platform_admins": ["super"]}


def _rec(**kw) -> dict:
    base = {
        "ts": "2026-06-01T00:00:00+00:00", "service": "codev-web",
        "user_id": "u", "org_id": "acme", "via": "token",
        "project_id": "p1", "allowed": True, "reason": "ok",
    }
    base.update(kw)
    return base


@pytest.fixture(autouse=True)
def _isolate(monkeypatch, tmp_path):
    reset_account_stores()
    session_store.clear()
    monkeypatch.setattr(wdeps, "load_config", lambda: _ADMINS)
    monkeypatch.setattr(waudit, "load_config", lambda: _ADMINS)
    log = tmp_path / "access.jsonl"
    monkeypatch.setattr(audit_repo, "audit_log_path", lambda: log)
    yield log
    reset_account_stores()
    session_store.clear()


def _write(log, recs):
    with log.open("w", encoding="utf-8") as f:
        for r in recs:
            f.write(json.dumps(r) + "\n")


@pytest.fixture
def client():
    return TestClient(build_app(title="t", routers=[audit.router], cfg=_CFG))


def _auth(username: str, org_id: str) -> dict:
    t = session_store.create(username, org_id)
    return {"Authorization": f"Bearer {t.access_token}"}


def test_normal_user_denied(client):
    # 无成员角色 → role_allows(None, admin) 拒
    h = _auth("alice", "acme")
    r = client.post("/api/v1/audit/list", json={}, headers=h)
    assert r.status_code == 403
    assert r.json()["errors"][0]["errorCode"] == "access_denied"


def test_org_admin_scoped_to_own_org(_isolate, client):
    log = _isolate
    _write(log, [
        _rec(org_id="acme", reason="a1"),
        _rec(org_id="beta", reason="b1"),
        _rec(org_id="acme", reason="a2"),
    ])
    member_store.upsert(OrgMember(org_id="acme", username="bob", role="admin"))
    h = _auth("bob", "acme")
    # 即使传 orgId=beta 也被强制覆盖为本 org acme
    r = client.post("/api/v1/audit/list", json={"orgId": "beta"}, headers=h)
    assert r.status_code == 200
    body = r.json()
    assert body["total"] == 2
    assert {x["orgId"] for x in body["data"]} == {"acme"}


def test_platform_admin_cross_org_and_filter(_isolate, client):
    log = _isolate
    _write(log, [
        _rec(org_id="acme", service="codev-web", allowed=True),
        _rec(org_id="beta", service="codev-agent", allowed=False),
        _rec(org_id="acme", service="codev-agent", allowed=False),
    ])
    h = _auth("super", "")  # platform_admin
    # 跨 org + 按 service+allowed 过滤
    r = client.post("/api/v1/audit/list",
                    json={"service": "codev-agent", "allowed": False}, headers=h)
    assert r.status_code == 200
    body = r.json()
    assert body["total"] == 2
    assert all(x["service"] == "codev-agent" and x["allowed"] is False for x in body["data"])
    # 指定 orgId 跨 org 查
    r2 = client.post("/api/v1/audit/list", json={"orgId": "beta"}, headers=h)
    assert r2.json()["total"] == 1 and r2.json()["data"][0]["orgId"] == "beta"


def test_pagination_newest_first(_isolate, client):
    log = _isolate
    _write(log, [_rec(org_id="acme", reason=f"r{i}", ts=f"2026-06-01T00:00:0{i}+00:00")
                 for i in range(5)])
    member_store.upsert(OrgMember(org_id="acme", username="bob", role="admin"))
    h = _auth("bob", "acme")
    r = client.post("/api/v1/audit/list", json={"pageSize": 2, "pageNumber": 1}, headers=h)
    body = r.json()
    assert body["total"] == 5 and body["pageSize"] == 2 and body["totalPage"] == 3
    assert len(body["data"]) == 2
    # 最新在前 (ts 最大的 r4)
    assert body["data"][0]["reason"] == "r4"


def test_missing_log_file_empty(client):
    member_store.upsert(OrgMember(org_id="acme", username="bob", role="admin"))
    h = _auth("bob", "acme")
    r = client.post("/api/v1/audit/list", json={}, headers=h)
    assert r.status_code == 200 and r.json()["total"] == 0
