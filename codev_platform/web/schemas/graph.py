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


class UnifiedGraphRequest(BaseModel):
    # includeSoft=True(默认): 含软节点(arch_layer/business_domain)+ 软边(plays_role/
    # belongs_to_domain)。这些是"架构层/业务域"的分组标注 —— **不是依赖**, 但提供图的连通结构
    # (实测软边占全图 ~77% 连接), 默认滤掉会让节点散成孤点。要纯依赖图(只 imports/renders/
    # calls_api 等硬边)时显式传 false。软边 kind 不同(plays_role 等), 前端可据 kind 配色区分,
    # 避免误读为依赖(如 thorn6 组件"连到" arch_layer:component 实为"它是组件层"的分组标注)。
    includeSoft: bool = True


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


class GraphAuditResponse(BaseModel):
    """统一图谱结构审计结果 (Phase 3): errors 阻断, warnings 待 review。"""

    clean: bool = Field(True, description="无结构 error 即 clean")
    errorCount: int = Field(0, description="结构 error 总数")
    danglingEdges: int = Field(0, description="断链边数 (指向不存在节点)")
    crossProjectNodes: int = Field(0, description="跨租户串台节点数")
    orphanSoftPlugins: int = Field(0, description="软产物孤儿 plugin 数 (plugin 漂移残留)")
    duplicateNodes: int = Field(0, description="重复节点组数 (warning)")
    lowConfidenceEdges: int = Field(0, description="低置信硬边数 (warning, fuzzy 推断)")
    nodes: int = Field(0, description="节点总数")
    edges: int = Field(0, description="边总数")


class GraphSoftQualityResponse(BaseModel):
    """A1/A2 软标签健康度诊断结果: 分布/覆盖/退化信号。空软层(未跑 analyzer) = healthy。"""

    healthy: bool = Field(True, description="无退化信号即 healthy")
    flagCount: int = Field(0, description="退化信号总数")
    flags: list[str] = Field(default_factory=list, description="人类可读退化信号清单")
    domainCount: int = Field(0, description="业务域(A1)软节点数")
    domainCoverage: float | None = Field(None, description="业务域标注覆盖率 (labeled/eligible)")
    domainGiant: int = Field(0, description="业务域巨型 cluster 数 (成员占比超阈值, 疑似退化)")
    layerCount: int = Field(0, description="架构层(A2)软节点数")
    layerCoverage: float | None = Field(None, description="架构层标注覆盖率")
    layerGiant: int = Field(0, description="架构层巨型 cluster 数")


class UnifiedGraphStatsResponse(BaseModel):
    nodesByKind: dict[str, int] = Field(default_factory=dict)
    edgesByKind: dict[str, int] = Field(default_factory=dict)
    totalNodes: int = 0
    totalEdges: int = 0


# ---- 多跳影响路径 (Phase 5: find_impact_paths web 暴露) ----


class GraphImpactPathsRequest(BaseModel):
    """改某节点 → top-N 最强依赖路径分析入参。"""

    nodeRef: str = Field(..., min_length=1, max_length=512, description="目标节点 ref (id / name / file)")
    topN: int = Field(10, ge=1, le=50, description="返回 top-N 最强路径")
    certainOnly: bool = Field(False, description="只走确定依赖边 (滤候选边)")


class ImpactNodeBrief(BaseModel):
    """路径上的节点摘要 (对齐 graph.impact._node_brief)。"""

    id: str
    kind: str | None = None
    name: str | None = None
    layer: str | None = None
    file: str | None = None
    line: int | None = None


class ImpactPathHop(BaseModel):
    """路径中的一跳: 到达节点 + 经由边 + 来源/置信 (可解释)。"""

    node: ImpactNodeBrief
    viaEdge: str | None = None
    src: str | None = None
    confidence: float | None = None
    certain: bool = False


class ImpactPath(BaseModel):
    """一条依赖路径: 依赖方 endpoint + 评分 + 逐跳。"""

    endpoint: ImpactNodeBrief
    score: float = 0.0
    depth: int = 0
    certain: bool = False
    hops: list[ImpactPathHop] = Field(default_factory=list)


class GraphImpactPathsResponse(BaseModel):
    """top-N 依赖路径 (Phase 5)。store 缺 / 节点未找到 → found=False 空。"""

    found: bool = False
    target: ImpactNodeBrief | None = None
    paths: list[ImpactPath] = Field(default_factory=list)
    count: int = 0
    totalReached: int = 0
