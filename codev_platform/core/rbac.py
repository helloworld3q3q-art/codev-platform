"""作用域 RBAC 纯权限逻辑 — 角色 × 作用域 × 动作判定的唯一真值源。

纯函数 / 纯数据, **无 psycopg / 无任何 IO**: 只读传入的 Membership。
PG 取数(fetch_membership)归 agent/rbac_store_pg.py, 本模块只做计算 → 全可单测。

与 memory plan §3.4/§3.5 对齐(org 是租户根, 作用域链 org>team>project>personal,
scoped RBAC: allowed(person, scope_ref, action) = role(person, scope_ref) 覆盖 action)。
安全默认: 缺角色 / 未知作用域 = 拒。
"""
from __future__ import annotations

from dataclasses import dataclass, field

from codev_platform.core.acl import AccessDecision

# 角色等级与动作门槛(矩阵 admin > member > viewer)。
# action ∈ read | recall | write | admin;读类(read/recall)只需 viewer, 写需 member, admin 需 admin。
ROLE_RANK: dict[str, int] = {"viewer": 1, "member": 2, "admin": 3}
ACTION_MIN: dict[str, int] = {"read": 1, "recall": 1, "write": 2, "admin": 3}


@dataclass(frozen=True)
class Membership:
    """某 (org, user) 在一次请求上下文里的角色快照(由 rbac_store_pg.fetch_membership 填)。

    org_role:     org 级角色(admin|member|viewer) 或 None(非成员)
    teams:        该 user 在本 org 的团队列表 [(team_id, role), ...]
    project_role: 当前 project 上的角色 或 None(无 access)
    """
    org_role: str | None = None
    teams: tuple[tuple[str, str], ...] = field(default_factory=tuple)
    project_role: str | None = None


def role_allows(role: str | None, action: str) -> bool:
    """role 是否覆盖 action。role None(无角色) → 恒 False(安全默认)。"""
    if role is None:
        return False
    rank = ROLE_RANK.get(role)
    need = ACTION_MIN.get(action)
    if rank is None or need is None:
        return False
    return rank >= need


def compute_visible_scopes(
    org_id: str | None,
    user_id: str | None,
    project_id: str | None,
    m: Membership,
) -> list[tuple[str, str]]:
    """该 (org, user, project) 可见作用域列表 [(scope, scope_ref), ...], 去重保序。

    org_role 非 None        → ("org", "org")
    每个 (team_id, _)       → ("team", team_id)
    project_id 且 project_role 非 None → ("project", project_id)
    user_id                 → ("personal", user_id)
    """
    scopes: list[tuple[str, str]] = []
    seen: set[tuple[str, str]] = set()

    def _add(scope: str, ref: str) -> None:
        key = (scope, ref)
        if key not in seen:
            seen.add(key)
            scopes.append(key)

    if m.org_role is not None:
        _add("org", "org")
    for team_id, _role in m.teams:
        _add("team", team_id)
    if project_id and m.project_role is not None:
        _add("project", project_id)
    if user_id:
        _add("personal", user_id)
    return scopes


def memory_scope_decision(
    scope: str,
    scope_ref: str | None,
    user_id: str | None,
    m: Membership,
) -> AccessDecision:
    """memory 作用域写访问判定(真实 Membership 版, M5 ACL 落地后替代 acl.memory_scope_access 的 interim)。

    复用 core.acl.AccessDecision, 不重复定义。从严按 write 判角色(读写都要求 write 角色; 调用方
    若要区分 read/recall 可改用 role_allows 直接判, 这里给写护栏的保守口径)。
    - personal: scope_ref == user_id 才 allow, 否则 deny(隐私: 不能写他人个人记忆)
    - project:  role_allows(project_role, "write")
    - org:      role_allows(org_role, "write")
    - team:     scope_ref 命中某 team_id 且该 team role_allows(role, "write")
    - 其它/缺角色: deny(安全默认)
    """
    if scope == "personal":
        if scope_ref and user_id and scope_ref == user_id:
            return AccessDecision(True, "personal: self")
        return AccessDecision(False, f"personal scope: ref={scope_ref} != self={user_id}")
    if scope == "project":
        if role_allows(m.project_role, "write"):
            return AccessDecision(True, f"project: role={m.project_role} allows write")
        return AccessDecision(False, f"project scope: role={m.project_role} cannot write")
    if scope == "org":
        if role_allows(m.org_role, "write"):
            return AccessDecision(True, f"org: role={m.org_role} allows write")
        return AccessDecision(False, f"org scope: role={m.org_role} cannot write")
    if scope == "team":
        for team_id, role in m.teams:
            if team_id == scope_ref:
                if role_allows(role, "write"):
                    return AccessDecision(True, f"team {team_id}: role={role} allows write")
                return AccessDecision(False, f"team {team_id}: role={role} cannot write")
        return AccessDecision(False, f"team scope: not a member of {scope_ref}")
    return AccessDecision(False, f"unknown memory scope: {scope}")
