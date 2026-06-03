"""Reports 组 schema (Track A5) —— 影响分析报告 + 跨层查询的请求/响应。

影响分析结果天然嵌套 (按层分组 + 变长层),故响应用 dict 透传 (与 graph 组 force-graph
data 同范式),不为每层硬编字段。请求是简单的节点引用 (id 或 name)。
"""
from __future__ import annotations

from pydantic import BaseModel, Field


class ImpactRequest(BaseModel):
    nodeRef: str  # 节点 id 或 name (表名 / 端点名 / 函数名 / 前端节点)


class TableUsageRequest(BaseModel):
    table: str  # 表名 (大小写不敏感)


class PageDepsRequest(BaseModel):
    pageRef: str  # 前端节点 id 或 name


class ApiCallersRequest(BaseModel):
    endpointRef: str  # 端点 id 或 name


class ImpactReportResponse(BaseModel):
    found: bool = False
    target: dict | None = None
    impact: dict | None = None          # {byLayer, counts, total}
    risk: str | None = None             # high / medium / low
    layersAffected: list[str] = Field(default_factory=list)
    total: int = 0
    summary: str = ""                   # 人类可读 markdown


class GraphQueryResponse(BaseModel):
    """table-usage / page-deps / api-callers 通用容器 (结构随查询不同, data 透传)。"""

    found: bool = False
    data: dict | None = None
