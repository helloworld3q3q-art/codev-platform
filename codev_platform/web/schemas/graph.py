"""Graph 组请求/响应 schema (plan §十五 Graph + §二十)。

字段名严格对齐 Java codegraph-api 的 DTO (camelCase), 让前端 force-graph / openapi-typescript
生成的类型与旧 :18082 无缝迁移 (见 plan §20.2 接口吸收映射)。
- codegraph 组: 对齐 NodeDTO / EdgeDTO / StatsResponse / SearchResponse / ...
- cross-link 组: 对齐 CrossLinkStatsResponse / CrossLinkTableRefsResponse / ...
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
# cross-link 组 —— 对齐 codegraph-api dto/CrossLink*.java
# ----------------------------------------------------------------------


class CrossLinkStatsResponse(BaseModel):
    lastBuildAt: str | None = None
    nodesByKind: dict[str, int] = Field(default_factory=dict)
    edgesByRel: dict[str, int] = Field(default_factory=dict)


class CrossLinkTablesResponse(BaseModel):
    tables: list[str] = Field(default_factory=list)


class CrossLinkNodeRef(BaseModel):
    name: str | None = None
    kind: str | None = None
    path: str | None = None
    line: int | None = None
    confidence: float | None = None
    evidence: str | None = None


class CrossLinkTableRefsRequest(BaseModel):
    table: str | None = None


class CrossLinkTableRefsResponse(BaseModel):
    table: str | None = None
    definers: list[CrossLinkNodeRef] = Field(default_factory=list)
    javaReaders: list[CrossLinkNodeRef] = Field(default_factory=list)
    javaWriters: list[CrossLinkNodeRef] = Field(default_factory=list)
    javaUpdaters: list[CrossLinkNodeRef] = Field(default_factory=list)
    pythonReaders: list[CrossLinkNodeRef] = Field(default_factory=list)
    pythonWriters: list[CrossLinkNodeRef] = Field(default_factory=list)
    pythonUpdaters: list[CrossLinkNodeRef] = Field(default_factory=list)


class CrossLinkEndpointTarget(BaseModel):
    name: str | None = None
    path: str | None = None
    line: int | None = None
    url: str | None = None
    confidence: float | None = None
    evidence: str | None = None


class CrossLinkEndpointLinkRequest(BaseModel):
    name: str | None = None


class CrossLinkEndpointLinkItem(BaseModel):
    node: str | None = None
    kind: str | None = None
    path: str | None = None
    line: int | None = None
    url: str | None = None
    direction: str | None = None
    targets: list[CrossLinkEndpointTarget] = Field(default_factory=list)
    callers: list[CrossLinkEndpointTarget] = Field(default_factory=list)


class CrossLinkSearchNodesRequest(BaseModel):
    query: str | None = None
    kind: str | None = None
    limit: int | None = None


class CrossLinkSearchHit(BaseModel):
    name: str | None = None
    kind: str | None = None
    path: str | None = None
    line: int | None = None
    language: str | None = None
    meta: dict[str, Any] = Field(default_factory=dict)


class CrossLinkSearchNodesResponse(BaseModel):
    query: str | None = None
    kind: str | None = None
    hits: list[CrossLinkSearchHit] = Field(default_factory=list)


class CrossLinkGraphNode(BaseModel):
    id: str | None = None
    kind: str | None = None
    name: str | None = None
    filePath: str | None = None
    startLine: int | None = None
    language: str | None = None


class CrossLinkGraphEdge(BaseModel):
    source: str | None = None
    target: str | None = None
    kind: str | None = None


class CrossLinkGraphRequest(BaseModel):
    mode: str | None = None
    kinds: list[str] | None = None
    excludeKinds: list[str] | None = None
    rels: list[str] | None = None
    excludeRels: list[str] | None = None
    limit: int | None = None


class CrossLinkGraphResponse(BaseModel):
    nodes: list[CrossLinkGraphNode] = Field(default_factory=list)
    edges: list[CrossLinkGraphEdge] = Field(default_factory=list)
    nodeCount: int = 0
    edgeCount: int = 0


# ----------------------------------------------------------------------
# 统一图谱组 —— 直读 graph/store.py (插件聚合落点) 的全量节点/边
# ----------------------------------------------------------------------
#
# 与 cross-link 组的区别:cross-link 组只筛 store 里 cross_link 适配器产出的节点
# (meta 含 cross_link_kind),而本组返回 store 内 **全部** GraphNode/GraphEdge
# (所有插件:frontend / backend / database / cross_link 等),用统一 NodeKind /
# EdgeKind (db_table / db_column / backend_endpoint / frontend_route ...) 直出,
# 让插件产出 (尤其 sql 的 db_table/db_column) 在前端完整可见。


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
