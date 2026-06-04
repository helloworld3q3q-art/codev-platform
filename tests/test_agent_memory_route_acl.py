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
from codev_platform.core.rbac import Membership  # noqa: E402


class _FakeStore:
    """ACL 拒绝在 store 之前发生; 放行路径写入返回固定 id。"""

    def write(self, entry):
        return "mem-1"

    def list_scope(self, scope, scope_ref, org_id=None, limit=100):
        return []


def _client(monkeypatch, *, identity, token_mode=True):
    monkeypatch.setattr(memory_route.deps, "get_memory_store", lambda: _FakeStore())
    # 隔离: org/team 走确定的 interim 路径(不依赖本机是否配了真 PG / RBAC store);
    # project 走 can_access(用下面 mock 的 cfg)。两者均不碰真库, 测试可复现。
    monkeypatch.setattr(memory_route.deps, "get_rbac_store", lambda: None)
    cfg = {"gateway": {"auth_mode": "token" if token_mode else "passthrough"}, "projects": {}}
    monkeypatch.setattr(memory_route, "load_config", lambda: cfg)

    app = FastAPI()

    @app.middleware("http")
    async def _inject_identity(request: Request, call_next):
        request.state.identity = identity
        return await call_next(request)

    app.include_router(memory_route.router)
    return TestClient(app)


def _client_without_gateway(monkeypatch):
    """dev 单机形态:未挂 AuthMiddleware,路由从 header 回退解析身份。"""
    monkeypatch.setattr(memory_route.deps, "get_memory_store", lambda: _FakeStore())
    monkeypatch.setattr(memory_route.deps, "get_rbac_store", lambda: None)
    cfg = {"gateway": {"auth_mode": "passthrough"}, "projects": {}}
    monkeypatch.setattr(memory_route, "load_config", lambda: cfg)

    app = FastAPI()
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


def test_write_personal_self_allowed_without_gateway(monkeypatch):
    c = _client_without_gateway(monkeypatch)
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


# ---- rbac-store-backed path (M5: 真实成员校验) ----
# 上面的用例 mock get_rbac_store=None, 走 interim(org/team 全 deny)。这一组挂一个
# 返回真 Membership 的 fake store, 验证路由把 fetch_membership → memory_scope_decision
# 接对了: 同 org 成员可读写 org memory; 非成员 deny; 跨 team deny。


class _FakeRbacStore:
    """fetch_membership 据 (org_id, user_id) 返回预置 Membership; 命中不到 → 空(非成员)。"""

    def __init__(self, memberships):
        self._m = memberships  # {(org_id, user_id): Membership}

    def fetch_membership(self, org_id, user_id, project_id=None):
        return self._m.get((org_id, user_id), Membership())


def _client_with_rbac(monkeypatch, *, identity, memberships):
    monkeypatch.setattr(memory_route.deps, "get_memory_store", lambda: _FakeStore())
    monkeypatch.setattr(memory_route.deps, "get_rbac_store",
                        lambda: _FakeRbacStore(memberships))
    cfg = {"gateway": {"auth_mode": "token"}, "projects": {}}
    monkeypatch.setattr(memory_route, "load_config", lambda: cfg)

    app = FastAPI()

    @app.middleware("http")
    async def _inject_identity(request: Request, call_next):
        request.state.identity = identity
        return await call_next(request)

    app.include_router(memory_route.router)
    return TestClient(app)


def test_rbac_org_member_can_read_org_memory(monkeypatch):
    c = _client_with_rbac(
        monkeypatch, identity=_ident(org_id="orgA", user_id="userA"),
        memberships={("orgA", "userA"): Membership(org_role="member")})
    r = c.get("/memory", headers=_hdrs(uid="userA", org="orgA"),
              params={"scope": "org", "scope_ref": "orgA"})
    assert r.status_code == 200


def test_rbac_org_member_can_write_org_memory(monkeypatch):
    c = _client_with_rbac(
        monkeypatch, identity=_ident(org_id="orgA", user_id="userA"),
        memberships={("orgA", "userA"): Membership(org_role="member")})
    r = c.post("/memory", headers=_hdrs(uid="userA", org="orgA"),
               json={"scope": "org", "scope_ref": "orgA", "content": "x"})
    assert r.status_code == 200


def test_rbac_org_nonmember_denied(monkeypatch):
    # userB 不在 memberships → fetch 返回空 Membership(org_role=None)→ deny。
    c = _client_with_rbac(
        monkeypatch, identity=_ident(org_id="orgA", user_id="userB"),
        memberships={("orgA", "userA"): Membership(org_role="member")})
    r = c.get("/memory", headers=_hdrs(uid="userB", org="orgA"),
              params={"scope": "org", "scope_ref": "orgA"})
    assert r.status_code == 403


def test_rbac_org_viewer_denied_both(monkeypatch):
    # 当前路由 read/write 都过 memory_scope_decision(write 口径, 保守 deny):
    # viewer 角色 < member 写门槛, 故 org viewer 读写均 403。这是有意的保守姿态
    # (宁可 deny 不误放), 非 bug; 若后续要放开 viewer 读, 需路由按 action 分流。
    c = _client_with_rbac(
        monkeypatch, identity=_ident(org_id="orgA", user_id="userV"),
        memberships={("orgA", "userV"): Membership(org_role="viewer")})
    r_read = c.get("/memory", headers=_hdrs(uid="userV", org="orgA"),
                   params={"scope": "org", "scope_ref": "orgA"})
    assert r_read.status_code == 403
    r_write = c.post("/memory", headers=_hdrs(uid="userV", org="orgA"),
                     json={"scope": "org", "scope_ref": "orgA", "content": "x"})
    assert r_write.status_code == 403


def test_redline_write_denied_for_org_member(monkeypatch):
    # 缺口 3: org member 可写 org memory, 但 is_redline=True 需 admin → 403。
    c = _client_with_rbac(
        monkeypatch, identity=_ident(org_id="orgA", user_id="userA"),
        memberships={("orgA", "userA"): Membership(org_role="member")})
    r = c.post("/memory", headers=_hdrs(uid="userA", org="orgA"),
               json={"scope": "org", "scope_ref": "orgA", "content": "x", "is_redline": True})
    assert r.status_code == 403


def test_redline_write_allowed_for_org_admin(monkeypatch):
    c = _client_with_rbac(
        monkeypatch, identity=_ident(org_id="orgA", user_id="userA"),
        memberships={("orgA", "userA"): Membership(org_role="admin")})
    r = c.post("/memory", headers=_hdrs(uid="userA", org="orgA"),
               json={"scope": "org", "scope_ref": "orgA", "content": "x", "is_redline": True})
    assert r.status_code == 200


def test_rbac_team_member_allowed_crossteam_denied(monkeypatch):
    # userA 是 teamA 成员 → 读 teamA 放行; 读 teamB(非其 team)→ deny。
    c = _client_with_rbac(
        monkeypatch, identity=_ident(org_id="orgA", user_id="userA"),
        memberships={("orgA", "userA"): Membership(teams=(("teamA", "member"),))})
    r_own = c.get("/memory", headers=_hdrs(uid="userA", org="orgA"),
                  params={"scope": "team", "scope_ref": "teamA"})
    assert r_own.status_code == 200
    r_cross = c.get("/memory", headers=_hdrs(uid="userA", org="orgA"),
                    params={"scope": "team", "scope_ref": "teamB"})
    assert r_cross.status_code == 403
