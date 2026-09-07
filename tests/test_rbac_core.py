"""core/rbac.py 纯权限逻辑单测(无 psycopg / 无 IO)。

覆盖 role_allows 矩阵 / compute_visible_scopes / memory_scope_decision 三组,
对齐 memory plan §3.4/§3.5 + COMMON 接口契约。
"""
from __future__ import annotations

from codev_platform.core.rbac import (
    Membership,
    compute_visible_scopes,
    memory_scope_decision,
    role_allows,
)


# --- role_allows 矩阵 ---

def test_role_allows_viewer_reads_not_writes():
    assert role_allows("viewer", "read") is True
    assert role_allows("viewer", "recall") is True
    assert role_allows("viewer", "write") is False
    assert role_allows("viewer", "admin") is False


def test_role_allows_member_writes_not_admin():
    assert role_allows("member", "read") is True
    assert role_allows("member", "write") is True
    assert role_allows("member", "admin") is False


def test_role_allows_admin_all():
    for action in ("read", "recall", "write", "admin"):
        assert role_allows("admin", action) is True


def test_role_allows_none_denies_all():
    for action in ("read", "recall", "write", "admin"):
        assert role_allows(None, action) is False


def test_role_allows_unknown_role_or_action_denies():
    assert role_allows("superuser", "read") is False
    assert role_allows("admin", "delete") is False


# --- compute_visible_scopes ---

def test_visible_scopes_org_only_yields_org_and_personal():
    m = Membership(org_role="member")
    scopes = compute_visible_scopes("org", "u1", None, m)
    assert ("org", "org") in scopes
    assert ("personal", "u1") in scopes
    assert not any(s == "team" for s, _ in scopes)
    assert not any(s == "project" for s, _ in scopes)


def test_visible_scopes_includes_teams():
    m = Membership(org_role="member", teams=(("t1", "member"), ("t2", "viewer")))
    scopes = compute_visible_scopes("org", "u1", None, m)
    assert ("team", "t1") in scopes
    assert ("team", "t2") in scopes


def test_visible_scopes_includes_project_when_role_present():
    m = Membership(org_role="member", project_role="member")
    scopes = compute_visible_scopes("org", "u1", "proj-x", m)
    assert ("project", "proj-x") in scopes


def test_visible_scopes_no_roles_only_personal():
    m = Membership()  # 无任何角色
    scopes = compute_visible_scopes("org", "u1", "proj-x", m)
    assert scopes == [("personal", "u1")]


def test_visible_scopes_dedup_and_order():
    m = Membership(org_role="admin", teams=(("t1", "admin"), ("t1", "member")))
    scopes = compute_visible_scopes("org", "u1", None, m)
    # 去重: t1 只出现一次; 保序: org 在 team 前, personal 最后
    assert scopes == [("org", "org"), ("team", "t1"), ("personal", "u1")]


# --- memory_scope_decision ---

def test_personal_self_allow_other_deny():
    m = Membership()
    assert memory_scope_decision("personal", "u1", "u1", m).allowed is True
    assert memory_scope_decision("personal", "u2", "u1", m).allowed is False


def test_project_member_allow_viewer_deny():
    member = Membership(project_role="member")
    viewer = Membership(project_role="viewer")
    assert memory_scope_decision("project", "proj-x", "u1", member).allowed is True
    assert memory_scope_decision("project", "proj-x", "u1", viewer).allowed is False


def test_org_admin_allow_member_deny():
    admin = Membership(org_role="admin")
    member = Membership(org_role="member")
    assert memory_scope_decision("org", "org", "u1", admin).allowed is True
    assert memory_scope_decision("org", "org", "u1", member).allowed is True  # member 可 write


def test_org_viewer_write_deny():
    viewer = Membership(org_role="viewer")
    assert memory_scope_decision("org", "org", "u1", viewer).allowed is False


def test_team_member_allow_viewer_deny_nonmember_deny():
    m = Membership(teams=(("t1", "member"), ("t2", "viewer")))
    assert memory_scope_decision("team", "t1", "u1", m).allowed is True
    assert memory_scope_decision("team", "t2", "u1", m).allowed is False
    assert memory_scope_decision("team", "t9", "u1", m).allowed is False


def test_no_role_denies():
    m = Membership()
    assert memory_scope_decision("project", "proj-x", "u1", m).allowed is False
    assert memory_scope_decision("org", "org", "u1", m).allowed is False


def test_unknown_scope_denies():
    m = Membership(org_role="admin")
    assert memory_scope_decision("galaxy", "g1", "u1", m).allowed is False
