"""账户域模型 —— Org / User / OrgMember (plan §五 / §十五)。

不可变 dataclass, 纯数据。值字面量对齐 web/domain/enums.py 的
OrgStatusEnum / UserStatusEnum / MemberRoleEnum (cross-layer-enum-consistency)。
密码只存 hash (password_hash), 明文绝不入域模型 (security.md)。
"""
from __future__ import annotations

from dataclasses import dataclass, field

# 角色等级对齐 core.rbac.ROLE_RANK (viewer<member<admin); MemberRoleEnum 同值。
ROLE_VIEWER = "viewer"
ROLE_MEMBER = "member"
ROLE_ADMIN = "admin"

STATUS_ACTIVE = "ACTIVE"
STATUS_DISABLED = "DISABLED"


@dataclass(frozen=True)
class Org:
    code: str
    name: str
    status: str = STATUS_ACTIVE
    description: str = ""


@dataclass(frozen=True)
class User:
    username: str            # 唯一键 (后续可接 email/SSO)
    password_hash: str       # 仅 hash, 明文不入库
    org_id: str              # 归属 org
    status: str = STATUS_ACTIVE
    display_name: str = ""
    email: str = ""


@dataclass(frozen=True)
class OrgMember:
    org_id: str
    username: str
    role: str = ROLE_MEMBER          # org 级角色 (viewer|member|admin)
    project_roles: dict = field(default_factory=dict)  # {project_id: role}
