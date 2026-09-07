"""项目级访问控制 — 身份能否访问某 project_id 的唯一真值源。

3 MCP(platform-docs / graph / codegraph)+ agent + memory 五处共用本判定,
不另造一套模型(与 memory §3.4 同源: org 是租户根, project 挂 org, 身份带 project 白名单)。

纯函数, 无 IO: 只读传入的 cfg dict 与 identity 对象。mode 判定收敛一处, 闸逻辑线性。
passthrough(dev) 恒放行(advisory, 非真授权, 审计区分用);token(prod) 硬校验两道闸。
"""
from __future__ import annotations

from dataclasses import dataclass

from codev_platform.core.config import get as _cfg_get


@dataclass(frozen=True)
class AccessDecision:
    allowed: bool
    reason: str
    advisory: bool = False   # True = passthrough(dev) 放行, 非真授权(审计区分用)


def can_access(cfg: dict | None, identity, project_id: str | None) -> AccessDecision:
    """身份能否访问 project_id。3 MCP + agent + memory 五处共用的唯一真值源。
    passthrough(dev) 恒放行(advisory);token(prod) 硬校验两道闸。"""
    # 服务间内部信物(via=internal): web 前门已认证并经 require_project_access 鉴权后, 用
    # HMAC internal_secret 签发、被 agent 中间件验签通过才写入。agent 无 web 的 RBAC 成员数据,
    # 不能独立复算逐项目授权 → 信任已验签的 web 授权。纵深 = HMAC 验签 + web 前置鉴权 + 内网 127.0.0.1。
    # 任何 auth_mode 下都成立(internal 身份本身即"已授权"凭证)。
    if getattr(identity, "via", None) == "internal":
        return AccessDecision(True, "web-vouched internal identity")
    mode = _cfg_get(cfg or {}, "gateway.auth_mode", "passthrough")
    if mode != "token":
        return AccessDecision(True, "passthrough(dev): advisory allow", advisory=True)
    if identity is None or getattr(identity, "via", None) != "token":
        return AccessDecision(False, "token mode requires authenticated identity")
    if not project_id:
        # token(prod): 无显式 project_id 不允许 cwd 回退(防越权扫盲)
        return AccessDecision(False, "token mode requires explicit project_id")
    # 闸1 org: project 的 org_id 缺省=公开;否则须 == 身份 org
    proj = (_cfg_get(cfg or {}, "projects", {}) or {}).get(project_id) or {}
    proj_org = proj.get("org_id")
    if proj_org and proj_org != getattr(identity, "org_id", None):
        return AccessDecision(False, f"org mismatch: identity={getattr(identity,'org_id',None)} project={proj_org}")
    # 闸2 白名单: all_projects 或 project_id ∈ projects
    if getattr(identity, "all_projects", False):
        return AccessDecision(True, "token: all projects")
    if project_id in getattr(identity, "projects", frozenset()):
        return AccessDecision(True, "token: project in allowlist")
    return AccessDecision(False, f"project {project_id} not in token allowlist")


def memory_scope_access(cfg: dict | None, identity, scope: str, scope_ref: str | None) -> AccessDecision:
    """memory 作用域访问判定 —— **纯函数兜底分支**(无 RBAC store 时), 与 memory plan §3.4 对齐:
    - personal: scope_ref 必须 == 自己 user_id 否则 deny;
    - project:  委托 can_access(cfg, identity, scope_ref);
    - org/team:  本纯函数无成员数据 → passthrough(dev)放行 advisory / token(prod)兜底 deny。
      ⚠️ **org/team 真实校验已闭环**(不是待做): memory 路由 _scope_decision 优先走 RbacStore
      (org_members/team_members 表)+ core/rbac.py:memory_scope_decision(按 org_role/team role
      判权), 仅在无 PG RbacStore 时才回退到本函数兜底。详见 agent/routes/memory.py:_scope_decision。
    - 其它 scope: deny。
    """
    if scope == "personal":
        uid = getattr(identity, "user_id", None)
        if scope_ref and uid and scope_ref == uid:
            return AccessDecision(True, "personal: self")
        return AccessDecision(False, f"personal scope: ref={scope_ref} != self={uid}")
    if scope == "project":
        return can_access(cfg, identity, scope_ref)
    if scope in ("org", "team"):
        mode = _cfg_get(cfg or {}, "gateway.auth_mode", "passthrough")
        if mode != "token":
            return AccessDecision(True, f"passthrough(dev): {scope} advisory allow", advisory=True)
        return AccessDecision(False, f"no RBAC store: {scope} 兜底 deny (真实校验走 RbacStore, 见 memory 路由 _scope_decision)")
    return AccessDecision(False, f"unknown memory scope: {scope}")
