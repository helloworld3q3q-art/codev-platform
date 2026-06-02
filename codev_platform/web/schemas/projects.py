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


class ProjectLoadRequest(BaseModel):
    code: str = Field(..., min_length=1, max_length=64, description="项目编码")


class ProjectListItem(BaseModel):
    """项目列表 / 详情项 (plan §七 通用字段)。"""

    code: str
    name: str
    repoPath: str | None = None
    description: str | None = None
    status: str = "ACTIVE"
    loaded: bool = False


class ProjectActionResult(BaseModel):
    """load/unload/register 写操作回执。"""

    code: str
    loaded: bool
    status: str = "ACTIVE"
