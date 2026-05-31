"""项目级访问控制 — 身份能否访问某 project_id 的唯一真值源。

3 MCP(platform-docs / cross-link / codegraph)+ agent + memory 五处共用本判定,
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
    mode = _cfg_get(cfg or {}, "gateway.auth_mode", "passthrough")
    if mode != "token":
        return AccessDecision(True, "passthrough(dev): advisory allow", advisory=True)
    if identity is None or getattr(identity, "via", None) != "token":
        return AccessDecision(False, "token mode requires authenticated identity")
    if not project_id:
        return AccessDecision(True, "no project scope (cwd fallback)")
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
