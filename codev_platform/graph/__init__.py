"""codev_platform.graph — 统一图谱模型 (模块化核心的 graph 模块).

Phase 1 只含 schema (中性类型 GraphNode / GraphEdge / Evidence / Finding /
AnalyzerResult)。后续:统一图谱 sqlite 存储 + 查询、graph/codegraph 适配器
(把现有输出转成本模型) 在此模块扩展。

所有 analyzer 插件都必须输出 AnalyzerResult,核心据此存储、查询、给 Agent 使用。
"""
from __future__ import annotations

from codev_platform.graph.schema import (
    CERTAIN_PROV_SOURCES,
    PROV_KEY,
    AnalyzerResult,
    EdgeKind,
    Evidence,
    Finding,
    GraphEdge,
    GraphNode,
    NodeKind,
    ProvSource,
    edge_provenance,
    stamp_provenance,
)

__all__ = [
    "AnalyzerResult",
    "CERTAIN_PROV_SOURCES",
    "EdgeKind",
    "Evidence",
    "Finding",
    "GraphEdge",
    "GraphNode",
    "NodeKind",
    "PROV_KEY",
    "ProvSource",
    "edge_provenance",
    "stamp_provenance",
]
