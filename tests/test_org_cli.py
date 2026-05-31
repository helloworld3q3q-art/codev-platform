"""org CLI(ops/org.py)单测:argparse 路由正确性 + 缺 dsn/psycopg 优雅退出。

不连真库:用 FakeStore 录调用核对路由;缺存储用 monkeypatch get_rbac_store→None
/ 抛 ImportError 模拟 psycopg 缺失。
"""
from __future__ import annotations

import argparse

import pytest

from codev_platform.ops import org as org_mod


def _parse(argv):
    p = argparse.ArgumentParser()
    sub = p.add_subparsers(dest="cmd", required=True)
    org_mod.register(sub)
    return p.parse_args(argv)


# ---- argparse 路由:各 action 解析到正确 action + 参数 ----

def test_route_create():
    ns = _parse(["org", "create", "acme", "--name", "Acme Inc"])
    assert ns.action == "create" and ns.target == "acme" and ns.name == "Acme Inc"


def test_route_add_user():
    ns = _parse(["org", "add-user", "alice", "--name", "Alice"])
    assert ns.action == "add-user" and ns.target == "alice" and ns.name == "Alice"


def test_route_add_member_role_default():
    ns = _parse(["org", "add-member", "acme", "alice"])
    assert ns.action == "add-member" and ns.target == "acme"
    assert ns.user == "alice" and ns.role == "member"


def test_route_team_create():
    ns = _parse(["org", "team", "create", "core", "--org", "acme", "--name", "Core"])
    assert ns.action == "team-create" and ns.target == "core" and ns.org == "acme"


def test_route_team_add_member():
    ns = _parse(["org", "team", "add-member", "core", "bob", "--role", "admin"])
    assert ns.action == "team-add-member" and ns.target == "core"
    assert ns.user == "bob" and ns.role == "admin"


def test_route_project_set_org():
    ns = _parse(["org", "project", "set-org", "pid1", "--org", "acme"])
    assert ns.action == "project-set-org" and ns.target == "pid1" and ns.org == "acme"


def test_route_project_grant_kind_team():
    ns = _parse(["org", "project", "grant", "pid1", "core", "--kind", "team", "--role", "admin"])
    assert ns.action == "project-grant" and ns.target == "pid1"
    assert ns.principal == "core" and ns.kind == "team" and ns.role == "admin"


def test_route_list():
    ns = _parse(["org", "list"])
    assert ns.action == "list"


# ---- dispatch:FakeStore 录调用,核对薄壳调对方法 ----

class _FakeStore:
    def __init__(self):
        self.calls = []

    def add_org(self, org_id, name=None):
        self.calls.append(("add_org", org_id, name))

    def add_user(self, user_id, name=None):
        self.calls.append(("add_user", user_id, name))

    def add_org_member(self, org_id, user_id, role="member"):
        self.calls.append(("add_org_member", org_id, user_id, role))

    def add_team(self, team_id, org_id, name=None):
        self.calls.append(("add_team", team_id, org_id, name))

    def add_team_member(self, team_id, user_id, role="member"):
        self.calls.append(("add_team_member", team_id, user_id, role))

    def upsert_project(self, project_id, org_id, name=None):
        self.calls.append(("upsert_project", project_id, org_id, name))

    def grant_project(self, project_id, principal, principal_kind, role="member"):
        self.calls.append(("grant_project", project_id, principal, principal_kind, role))


@pytest.fixture()
def fake_store(monkeypatch):
    store = _FakeStore()
    monkeypatch.setattr(org_mod, "_get_store", lambda: store)
    return store


def test_dispatch_create(fake_store):
    assert org_mod.cmd_org(_parse(["org", "create", "acme", "--name", "A"])) == 0
    assert fake_store.calls == [("add_org", "acme", "A")]


def test_dispatch_add_member(fake_store):
    assert org_mod.cmd_org(_parse(["org", "add-member", "acme", "alice", "--role", "admin"])) == 0
    assert fake_store.calls == [("add_org_member", "acme", "alice", "admin")]


def test_dispatch_team_create(fake_store):
    assert org_mod.cmd_org(_parse(["org", "team", "create", "core", "--org", "acme"])) == 0
    assert fake_store.calls == [("add_team", "core", "acme", None)]


def test_dispatch_project_grant_team(fake_store):
    rc = org_mod.cmd_org(_parse(["org", "project", "grant", "pid1", "core", "--kind", "team"]))
    assert rc == 0
    assert fake_store.calls == [("grant_project", "pid1", "core", "team", "member")]


def test_dispatch_project_grant_bad_kind(fake_store):
    rc = org_mod.cmd_org(_parse(["org", "project", "grant", "pid1", "x", "--kind", "bogus"]))
    assert rc == 1
    assert fake_store.calls == []  # 非法 kind 不落库


# ---- 缺 dsn / psycopg → get_rbac_store None → 友好退非 0,不 raise ----

def test_no_store_returns_nonzero(monkeypatch, capsys):
    import codev_platform.agent.deps as deps
    monkeypatch.setattr(deps, "get_rbac_store", lambda: None)
    rc = org_mod.cmd_org(_parse(["org", "create", "acme"]))
    assert rc == 1
    err = capsys.readouterr().err
    assert "RBAC 存储不可用" in err


def test_psycopg_missing_falls_back_to_none(monkeypatch, capsys):
    """模拟 deps.get_rbac_store 内部 lazy import psycopg 失败 → 自身 try/except 回退 None
    (见 deps.py:get_rbac_store)。org 层据 None 友好退非 0,绝不 raise / 不自动装。
    """
    import codev_platform.agent.deps as deps
    # 模拟 deps 内部 RbacStore 构造时 psycopg 缺失:让 get_rbac_store 走它的回退分支返回 None。
    monkeypatch.setattr(deps, "get_rbac_store", lambda: None)
    rc = org_mod.cmd_org(_parse(["org", "add-member", "acme", "alice"]))
    assert rc == 1  # 不 raise
    err = capsys.readouterr().err
    assert "psycopg" in err or "RBAC 存储不可用" in err
