"""Orgs 模块 request/response 模型 (plan §十五 Orgs / §七 字段规范)。

字段统一 camelCase, schema 层做基本校验 (§八 第一层)。org code 即 org_id (租户根键),
小写 slug 约束在 service 层校验。状态/角色值对齐 web/domain/enums.py
(OrgStatusEnum / MemberRoleEnum), 前端经 useModel('enum') 取 options, 禁本地造
(cross-layer-enum-consistency)。
"""
from __future__ import annotations

from pydantic import BaseModel, Field


class OrgCreateRequest(BaseModel):
    """创建组织 (平台超管, 幂等: 重复 code 报错)。"""

    code: str = Field(..., min_length=1, max_length=64, description="组织编码 (org_id slug)")
    name: str = Field(..., min_length=1, max_length=200, description="组织名称")
    description: str | None = Field(None, max_length=2000, description="描述")


class OrgUpdateRequest(BaseModel):
    """更新组织 (名称 / 描述; code 不可改)。"""

    code: str = Field(..., min_length=1, max_length=64, description="组织编码")
    name: str | None = Field(None, min_length=1, max_length=200, description="组织名称")
    description: str | None = Field(None, max_length=2000, description="描述")


class OrgStatusRequest(BaseModel):
    """启用 / 禁用组织 (第一版只置状态, 不物理删)。"""

    code: str = Field(..., min_length=1, max_length=64, description="组织编码")
    status: str = Field(..., description="目标状态 (OrgStatusEnum: ACTIVE|DISABLED)")


class OrgItem(BaseModel):
    """组织列表 / 详情项。"""

    code: str
    name: str
    status: str = "ACTIVE"
    description: str | None = None


class OrgSelectionItem(BaseModel):
    """下拉选择项 (code + 中文 label)。"""

    code: str
    name: str
    status: str = "ACTIVE"


class MemberListRequest(BaseModel):
    """成员列表请求 (POST body)。前端 post() 走 body, 故 code/分页全收 body, 不用 query。"""

    code: str = Field(..., min_length=1, max_length=64, description="组织编码")
    pageNumber: int = Field(1, ge=1, description="页码, 从 1 起")
    pageSize: int = Field(20, ge=1, le=200, description="每页数量 (上限 200)")


class MemberAddRequest(BaseModel):
    """加成员 (幂等 upsert; org_admin 管本 org)。role 对齐 MemberRoleEnum。"""

    code: str = Field(..., min_length=1, max_length=64, description="组织编码")
    username: str = Field(..., min_length=1, max_length=128, description="成员用户名")
    role: str = Field("member", description="成员角色 (MemberRoleEnum: viewer|member|admin)")


class MemberRemoveRequest(BaseModel):
    code: str = Field(..., min_length=1, max_length=64, description="组织编码")
    username: str = Field(..., min_length=1, max_length=128, description="成员用户名")


class MemberRoleRequest(BaseModel):
    """改成员角色 (org_admin 管本 org)。"""

    code: str = Field(..., min_length=1, max_length=64, description="组织编码")
    username: str = Field(..., min_length=1, max_length=128, description="成员用户名")
    role: str = Field(..., description="新角色 (MemberRoleEnum: viewer|member|admin)")


class MemberItem(BaseModel):
    """成员列表项。"""

    orgId: str
    username: str
    role: str = "member"


class OrgActionResult(BaseModel):
    """create/update/status 写操作回执。"""

    code: str
    status: str = "ACTIVE"


class MemberActionResult(BaseModel):
    """add/remove/roles 写操作回执。"""

    orgId: str
    username: str
    role: str | None = None
    removed: bool = False
