"""分页参数依赖 —— 解析 pageNumber/pageSize, 给 PageResult 组装 (plan §七)。

FastAPI 依赖: 路由 `page: PageParams = Depends(page_params)` 拿规整后的分页参数。
纯解析 + 边界钳制, 不碰 identity (identity 走 request.state.identity)。
"""
from __future__ import annotations

from dataclasses import dataclass

from fastapi import Query
from pydantic import BaseModel, Field

_MAX_PAGE_SIZE = 200


@dataclass(frozen=True)
class PageParams:
    page_number: int
    page_size: int

    @property
    def offset(self) -> int:
        return (self.page_number - 1) * self.page_size


def page_params(
    pageNumber: int = Query(1, ge=1, description="页码, 从 1 起"),
    pageSize: int = Query(20, ge=1, le=_MAX_PAGE_SIZE, description="每页数量"),
) -> PageParams:
    """分页参数依赖 (query 版)。pageSize 上限 200 (防一次拉全表)。"""
    return PageParams(page_number=pageNumber, page_size=pageSize)


class PageBody(BaseModel):
    # POST 列表请求体的分页基类。前端 post() 一律发 body, 故分页从 body 收(不是 query);
    # list 过滤请求体继承本类追加过滤字段。
    # (用 # 注释非 docstring: 多行 docstring 会进 OpenAPI description → 前端 swagger 生成器
    #  原样塞进 typings.d.ts `//` 注释, 换行破坏 .d.ts 解析。同 AuditListRequest 的处理。)
    pageNumber: int = Field(1, ge=1, description="页码, 从 1 起")
    pageSize: int = Field(20, ge=1, le=_MAX_PAGE_SIZE, description="每页数量 (上限 200)")

    @property
    def offset(self) -> int:
        return (self.pageNumber - 1) * self.pageSize
