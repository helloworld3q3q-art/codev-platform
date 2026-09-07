"""Users 模块 request/response 模型 (plan §十五 Users / §七 字段规范)。

字段统一 camelCase (schema 层做第一层校验, §八)。username 唯一键 (plan §十五 规则)。
密码字段只在 request 出现, response 绝不含密码/hash (security.md)。
status 值对齐 web/domain/enums.py UserStatusEnum; role 对齐 MemberRoleEnum。
"""
from __future__ import annotations

from pydantic import BaseModel, Field


class UserCreateRequest(BaseModel):
    """创建用户 (org admin)。密码明文仅入参, service 立即 hash, 绝不落库 (security.md)。"""

    username: str = Field(..., min_length=1, max_length=64, description="用户名 (唯一键)")
    password: str = Field(..., min_length=6, max_length=200, description="初始密码 (明文仅入参)")
    orgId: str = Field(..., min_length=1, max_length=64, description="归属组织")
    displayName: str | None = Field(None, max_length=200, description="显示名")
    email: str | None = Field(None, max_length=200, description="邮箱")
    role: str | None = Field(None, max_length=32, description="组织级角色 (viewer|member|admin)")


class UserUpdateRequest(BaseModel):
    """更新用户资料 (不含密码 / 状态 / 角色, 各走专用端点)。"""

    username: str = Field(..., min_length=1, max_length=64, description="用户名 (唯一键)")
    displayName: str | None = Field(None, max_length=200, description="显示名")
    email: str | None = Field(None, max_length=200, description="邮箱")


class UserStatusRequest(BaseModel):
    """启用 / 禁用用户。禁用 → service 撤销其所有会话 (plan §十五 规则)。"""

    username: str = Field(..., min_length=1, max_length=64, description="用户名")
    status: str = Field(..., min_length=1, max_length=32, description="ACTIVE | DISABLED")


class UserPasswordResetRequest(BaseModel):
    """重置 / 生成初始密码。新明文仅入参, service hash 后落库。"""

    username: str = Field(..., min_length=1, max_length=64, description="用户名")
    newPassword: str = Field(..., min_length=6, max_length=200, description="新密码 (明文仅入参)")


class UserRolesRequest(BaseModel):
    """变更用户在某 org 的成员角色 (须审计, plan §十五 规则)。"""

    username: str = Field(..., min_length=1, max_length=64, description="用户名")
    orgId: str = Field(..., min_length=1, max_length=64, description="组织")
    role: str = Field(..., min_length=1, max_length=32, description="viewer | member | admin")


class UserItem(BaseModel):
    """用户列表 / 详情 / profile 项。绝不含 password / password_hash (security.md)。"""

    username: str
    orgId: str
    displayName: str | None = None
    email: str | None = None
    status: str = "ACTIVE"
    role: str | None = None    # 该用户在 orgId 的成员角色 (viewer|member|admin); 无成员记录=None
    orgs: list[str] = Field(default_factory=list, description="该用户所属全部组织 code (成员关系, 多对多)")


class UserActionResult(BaseModel):
    """create / update / status / password / roles 写操作回执 (不含敏感字段)。"""

    username: str
    status: str = "ACTIVE"


class UserSelectionItem(BaseModel):
    """用户选择器项 (label/value, 前端下拉)。"""

    label: str
    value: str
