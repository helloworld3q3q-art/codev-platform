"""分页参数依赖 —— 解析 pageNumber/pageSize, 给 PageResult 组装 (plan §七)。

FastAPI 依赖: 路由 `page: PageParams = Depends(page_params)` 拿规整后的分页参数。
纯解析 + 边界钳制, 不碰 identity (identity 走 request.state.identity)。
"""
from __future__ import annotations

from dataclasses import dataclass

from fastapi import Query

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
    """分页参数依赖。pageSize 上限 200 (防一次拉全表)。"""
    return PageParams(page_number=pageNumber, page_size=pageSize)
