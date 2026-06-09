"""resolve_membership 直接回归 —— 补审计 #1 盲区: PG-优先分支此前无直接单测。

web RBAC 鉴权单一真值源是 membership.resolve_membership(PG RBAC 优先 → 缺则内存 OrgMember →
deny-by-default)。其它 web 测试 monkeypatch `_pg_rbac_store→None` 走内存路径, 使 PG 分支更无覆盖。
这里显式注入 fake PG store 钉死三条路径。
"""
from __future__ import annotations

import pytest

pytest.importorskip("fastapi")

from codev_platform.core.rbac import Membership  # noqa: E402
from codev_platform.web.security import membership as mem  # noqa: E402


class _FakePgStore:
    """记录入参 + 返回预置 Membership 的假 PG RBAC store。"""
    def __init__(self, membership: Membership):
        self._m = membership
        self.calls = []

    def fetch_membership(self, org_id, username, project_id=None):
        self.calls.append((org_id, username, project_id))
        return self._m


def test_resolve_membership_prefers_pg(monkeypatch):
    # PG store 在场 → resolve_membership 走 PG 分支(非内存), 返回它的 Membership。
    fake = _FakePgStore(Membership(org_role="admin", project_role="write"))
    monkeypatch.setattr(mem, "_pg_rbac_store", lambda: fake)
    m = mem.resolve_membership("orgA", "alice", "p1")
    assert m.org_role == "admin" and m.project_role == "write"
    assert fake.calls == [("orgA", "alice", "p1")]   # 确实查了 PG


def test_resolve_membership_memory_fallback(monkeypatch):
    # 无 PG store → 回退内存 OrgMember(org 级 role + 逐项目 role)。
    from codev_platform.web.domain.accounts import OrgMember
    from codev_platform.web.repositories.account_store import (
        get_member_store,
        reset_account_stores,
    )
    monkeypatch.setattr(mem, "_pg_rbac_store", lambda: None)
    reset_account_stores()
    get_member_store().upsert(
        OrgMember(org_id="orgA", username="alice", role="admin", project_roles={"p1": "write"}))
    try:
        m = mem.resolve_membership("orgA", "alice", "p1")
        assert m.org_role == "admin" and m.project_role == "write"
    finally:
        reset_account_stores()


def test_resolve_membership_empty_without_identity(monkeypatch):
    # 无 org / 无 user → 空 Membership(deny-by-default), 连 PG 都不查。
    fake = _FakePgStore(Membership(org_role="admin"))
    monkeypatch.setattr(mem, "_pg_rbac_store", lambda: fake)
    assert mem.resolve_membership(None, "alice", None).org_role is None
    assert mem.resolve_membership("orgA", None, None).org_role is None
    assert fake.calls == []   # 无身份不查 PG
