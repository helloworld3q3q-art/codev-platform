"""Projects 模块 request/response 模型 (plan §十五 Projects / §七 字段规范)。

字段统一走 camelCase (createdAt 等), schema 层做校验 (§八 第一层)。
project code 复用 core.project_id 的 slug 约束 (小写字母/数字/连字符), 在 service 层校验。
"""
from __future__ import annotations

from pydantic import BaseModel, Field


class ProjectRegisterRequest(BaseModel):
    """注册项目请求 (plan §八 示例)。code 唯一, register 幂等 (重复 code 报错)。"""

    code: str = Field(..., min_length=1, max_length=64, description="项目编码 (project_id slug)")
    name: str = Field(..., min_length=1, max_length=200, description="项目名称")
    repoPath: str | None = Field(None, max_length=500, description="项目仓路径")
    description: str | None = Field(None, max_length=2000, description="描述")
    orgId: str | None = Field(None, max_length=64, description="所属组织 (缺省=当前请求 org)")


class ProjectListRequest(BaseModel):
    """项目列表查询 (POST body, 与前端 ResizableTable 一致)。分页 + 组织/关键词过滤。"""

    pageNumber: int = Field(1, ge=1, description="页码, 从 1 起")
    pageSize: int = Field(20, ge=1, le=200, description="每页数量")
    orgId: str | None = Field(None, max_length=64, description="按组织过滤 (缺省=当前 org 可见全部)")
    keyword: str | None = Field(None, max_length=200, description="按项目编码/名称模糊匹配")


class ProjectLoadRequest(BaseModel):
    code: str = Field(..., min_length=1, max_length=64, description="项目编码")


class ProjectListItem(BaseModel):
    """项目列表 / 详情项 (plan §七 通用字段)。"""

    code: str
    name: str
    repoPath: str | None = None
    description: str | None = None
    orgId: str | None = None
    status: str = "ACTIVE"
    loaded: bool = False


class ProjectActionResult(BaseModel):
    """load/unload/register 写操作回执。"""

    code: str
    loaded: bool
    status: str = "ACTIVE"
