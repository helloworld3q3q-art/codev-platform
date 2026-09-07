"""Recall 组 schema (Phase 6) —— 跨 lane 代码召回请求/响应。"""
from __future__ import annotations

from pydantic import BaseModel, Field


class RecallCodeRequest(BaseModel):
    query: str = Field(..., description="检索词")
    limit: int = Field(20, ge=1, le=100, description="返回上限")
    weights: dict[str, float] | None = Field(
        None, description="lane→权重(symbol 类偏 codegraph / 架构类偏 graph); 缺省等权")


class RecallCodeHit(BaseModel):
    ref: str = Field(..., description="候选标识(node_id / symbol_id)")
    score: float = Field(..., description="融合得分(加权 RRF + boost)")
    name: str = Field("", description="名称")
    kind: str = Field("", description="类型(backend_function / db_table / method ...)")
    file: str | None = Field(None, description="源文件")
    lanes: list[str] = Field(default_factory=list, description="命中它的 lane(可解释)")


class RecallCodeResponse(BaseModel):
    """跨 lane 融合代码召回结果: 统一排名 + 实际参与 lane(可解释/可观测)。"""

    hits: list[RecallCodeHit] = Field(default_factory=list)
    count: int = Field(0, description="结果数")
    lanes: list[str] = Field(default_factory=list, description="实际有贡献的 lane(某 lane 缺失则不在内)")
