"""Graph 组请求/响应 schema (plan §十五 Graph + §二十)。

字段名严格对齐 Java codegraph-api 的 DTO (camelCase), 让前端 force-graph / openapi-typescript
生成的类型与旧 :18082 无缝迁移 (见 plan §20.2 接口吸收映射)。
- codegraph 组: 对齐 NodeDTO / EdgeDTO / StatsResponse / SearchResponse / ...
- 统一图谱组: UnifiedGraphNode / UnifiedGraphEdge (前端→后端→表全栈链路, 见 :129)。
"""
from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field

# ----------------------------------------------------------------------
# codegraph 组 —— 对齐 codegraph-api dto/*.java
# ----------------------------------------------------------------------


class CodegraphNode(BaseModel):
    """对齐 Java NodeDTO。"""

    id: str | None = None
    kind: str | None = None
    name: str | None = None
    qualifiedName: str | None = None
    filePath: str | None = None
    language: str | None = None
    startLine: int | None = None
    endLine: int | None = None
    startColumn: int | None = None
    endColumn: int | None = None
    docstring: str | None = None
    signature: str | None = None
    visibility: str | None = None
    isExported: bool | None = None
    isAsync: bool | None = None
    isStatic: bool | None = None
    isAbstract: bool | None = None


class CodegraphEdge(BaseModel):
    """对齐 Java EdgeDTO。"""

    id: int | None = None
    source: str | None = None
    target: str | None = None
    kind: str | None = None
    line: int | None = None
    col: int | None = None


class CodegraphFile(BaseModel):
    """对齐 Java FileDTO。"""

    path: str | None = None
    language: str | None = None
    size: int | None = None
    nodeCount: int | None = None


class CodegraphStatsResponse(BaseModel):
    """对齐 Java StatsResponse。"""

    totalFiles: int = 0
    totalNodes: int = 0
    totalEdges: int = 0
    byLanguage: dict[str, int] = Field(default_factory=dict)
    byNodeKind: dict[str, int] = Field(default_factory=dict)
    byEdgeKind: dict[str, int] = Field(default_factory=dict)


class CodegraphSearchRequest(BaseModel):
    keyword: str | None = None
    languages: list[str] | None = None
    kinds: list[str] | None = None
    limit: int | None = None


class CodegraphSearchResponse(BaseModel):
    items: list[CodegraphNode] = Field(default_factory=list)


class CodegraphNodeRequest(BaseModel):
    id: str | None = None


class CodegraphNeighborsRequest(BaseModel):
    id: str | None = None
    direction: str | None = None
    edgeKinds: list[str] | None = None
    depth: int | None = None


class CodegraphNeighborsResponse(BaseModel):
    center: CodegraphNode | None = None
    nodes: list[CodegraphNode] = Field(default_factory=list)
    edges: list[CodegraphEdge] = Field(default_factory=list)


class CodegraphFileTreeRequest(BaseModel):
    prefix: str | None = None


class CodegraphFileTreeResponse(BaseModel):
    items: list[CodegraphFile] = Field(default_factory=list)


class CodegraphGraphRequest(BaseModel):
    limit: int | None = None
    languages: list[str] | None = None
    kinds: list[str] | None = None
    edgeKinds: list[str] | None = None


class CodegraphGraphResponse(BaseModel):
    nodes: list[CodegraphNode] = Field(default_factory=list)
    edges: list[CodegraphEdge] = Field(default_factory=list)
    totalNodes: int = 0
    totalEdges: int = 0


# ----------------------------------------------------------------------
# 统一图谱组 —— 直读 graph/store.py (插件聚合落点) 的全量节点/边
# ----------------------------------------------------------------------
#
# 跨业务链路 (前端→后端→表) 现由统一图谱单页承载: 返回 store 内 **全部** GraphNode/
# GraphEdge (frontend / backend / database 各 stack 插件 + 核心 linker), 用统一
# NodeKind / EdgeKind (db_table / db_column / backend_endpoint / frontend_route /
# reads_table / writes_table / calls_api ...) 直出。cross_link 组已于 2026-06-03 退场。


class UnifiedGraphNode(BaseModel):
    id: str | None = None
    kind: str | None = None
    name: str | None = None
    filePath: str | None = None
    startLine: int | None = None
    language: str | None = None
    meta: dict[str, Any] = Field(default_factory=dict)


class UnifiedGraphEdge(BaseModel):
    source: str | None = None
    target: str | None = None
    kind: str | None = None


class UnifiedGraphResponse(BaseModel):
    nodes: list[UnifiedGraphNode] = Field(default_factory=list)
    edges: list[UnifiedGraphEdge] = Field(default_factory=list)
    nodeCount: int = 0
    edgeCount: int = 0


class UnifiedGraphStatsResponse(BaseModel):
    nodesByKind: dict[str, int] = Field(default_factory=dict)
    edgesByKind: dict[str, int] = Field(default_factory=dict)
    totalNodes: int = 0
    totalEdges: int = 0
