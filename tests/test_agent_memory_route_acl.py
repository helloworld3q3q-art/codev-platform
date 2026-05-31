"""memory 路由 ACL 统一闸 (审计 #2 interim) —— 路由层收敛到 memory_scope_access。

token 模式下: org/team scope → 403; project 越权 → 403; personal 他人读 → 403。
fastapi 缺失则跳过 (纯路由集成测试, ACL 纯逻辑已在 test_acl 覆盖)。
"""
from __future__ import annotations

from types import SimpleNamespace

import pytest

pytest.importorskip("fastapi")
from fastapi import FastAPI, Request  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

from codev_platform.agent.routes import memory as memory_route  # noqa: E402


class _FakeStore:
    """ACL 拒绝在 store 之前发生; 放行路径写入返回固定 id。"""

    def write(self, entry):
        return "mem-1"

    def list_scope(self, scope, scope_ref, org_id=None, limit=100):
        return []


def _client(monkeypatch, *, identity, token_mode=True):
    monkeypatch.setattr(memory_route.deps, "get_memory_store", lambda: _FakeStore())
    cfg = {"gateway": {"auth_mode": "token" if token_mode else "passthrough"}, "projects": {}}
    monkeypatch.setattr(memory_route, "load_config", lambda: cfg)

    app = FastAPI()

    @app.middleware("http")
    async def _inject_identity(request: Request, call_next):
        request.state.identity = identity
        return await call_next(request)

    app.include_router(memory_route.router)
    return TestClient(app)


def _ident(*, via="token", org_id="orgA", user_id="userA",
           projects=frozenset(), all_projects=False):
    return SimpleNamespace(via=via, org_id=org_id, user_id=user_id,
                           projects=projects, all_projects=all_projects)


def _hdrs(uid="userA", org="orgA"):
    return {"X-User-Id": uid, "X-Org-Id": org}


# ---- write ----

def test_write_org_scope_token_denied(monkeypatch):
    c = _client(monkeypatch, identity=_ident())
    r = c.post("/memory", headers=_hdrs(),
               json={"scope": "org", "scope_ref": "orgA", "content": "x"})
    assert r.status_code == 403


def test_write_team_scope_token_denied(monkeypatch):
    c = _client(monkeypatch, identity=_ident())
    r = c.post("/memory", headers=_hdrs(),
               json={"scope": "team", "scope_ref": "teamA", "content": "x"})
    assert r.status_code == 403


def test_write_project_not_in_allowlist_denied(monkeypatch):
    c = _client(monkeypatch, identity=_ident(projects=frozenset({"other"})))
    r = c.post("/memory", headers=_hdrs(),
               json={"scope": "project", "scope_ref": "openclaw-stock", "content": "x"})
    assert r.status_code == 403


def test_write_personal_self_allowed(monkeypatch):
    c = _client(monkeypatch, identity=_ident(user_id="userA"))
    r = c.post("/memory", headers=_hdrs(uid="userA"),
               json={"scope": "personal", "scope_ref": "userA", "content": "x"})
    assert r.status_code == 200


def test_write_project_in_allowlist_allowed(monkeypatch):
    c = _client(monkeypatch, identity=_ident(projects=frozenset({"openclaw-stock"})))
    r = c.post("/memory", headers=_hdrs(),
               json={"scope": "project", "scope_ref": "openclaw-stock", "content": "x"})
    assert r.status_code == 200


# ---- list ----

def test_list_personal_other_denied(monkeypatch):
    c = _client(monkeypatch, identity=_ident(user_id="userA"))
    r = c.get("/memory", headers=_hdrs(uid="userA"),
              params={"scope": "personal", "scope_ref": "userB"})
    assert r.status_code == 403


def test_list_project_not_in_allowlist_denied(monkeypatch):
    c = _client(monkeypatch, identity=_ident(projects=frozenset({"other"})))
    r = c.get("/memory", headers=_hdrs(),
              params={"scope": "project", "scope_ref": "openclaw-stock"})
    assert r.status_code == 403


def test_list_org_scope_token_denied(monkeypatch):
    c = _client(monkeypatch, identity=_ident())
    r = c.get("/memory", headers=_hdrs(),
              params={"scope": "org", "scope_ref": "orgA"})
    assert r.status_code == 403
