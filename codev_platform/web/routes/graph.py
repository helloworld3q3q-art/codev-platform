"""Graph 组路由 (plan §十五 Graph + §二十) —— 替代 Java codegraph-api(:18082) 的 12 个只读接口。

经 integrations 直读 per-project 只读 SQLite (codegraph.db) + graph store, 不经 daemon、
不起模型 (plan §12.1 资源隔离铁律)。全部 POST + X-Project-Id, 经 require_project_access 鉴权
(core.acl 单一真值源, 与 3 MCP + agent + memory 同一套)。

字段形状对齐 Java DTO (camelCase), 前端 force-graph 从 :18082 平滑迁移。错误统一走 envelope:
DB 缺失=index_missing(503) / 入参非法=invalid_params(400) / sqlite 故障=upstream_unavailable(503)。
"""
from __future__ import annotations

import sqlite3

from fastapi import APIRouter, Depends, Request

from codev_platform.core.errors import ErrorCode, PlatformError
from codev_platform.core.httpkit.envelope import CommonResult, ok
from codev_platform.core.httpkit.permissions import require_project_access
from codev_platform.graph.schema import AnalyzerResult
from codev_platform.graph.store import graph_store_path, load_graph
from codev_platform.web.integrations.codegraph_client import CodegraphClient
from codev_platform.web.schemas import graph as S

router = APIRouter()

_CODEGRAPH_TAG = "GraphAPI-代码图谱"
_UNIFIED_TAG = "GraphAPI-统一图谱"


def _rid(request: Request) -> str | None:
    return getattr(request.state, "request_id", None)


def _is_missing(exc: PlatformError) -> bool:
    """index_missing = 该项目尚无此索引 → 概览(stats)/可视化(graph)返回空(非错误);
    具体查询(node/search/table-refs 等)仍抛 503, 让"找不到"是显式错误。"""
    return exc.code == ErrorCode.INDEX_MISSING


# 统一图谱 store 只读访问 (unified 组直读全量节点/边)。


def _open_store_ro(project_id: str) -> sqlite3.Connection | None:
    """只读打开统一图谱 store; 文件不存在返回 None (走 fallback)。"""
    path = graph_store_path(project_id)
    if not path.exists():
        return None
    conn = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
    return conn


# ======================================================================
# codegraph 组 (6)
# ======================================================================


@router.post(
    "/api/v1/graph/codegraph/stats",
    tags=[_CODEGRAPH_TAG],
    summary="代码图谱-总体统计",
    operation_id="graphCodegraphStats",
    response_model=CommonResult[S.CodegraphStatsResponse],
)
def codegraph_stats(request: Request, ctx=Depends(require_project_access)) -> CommonResult:
    _identity, project_id = ctx
    try:
        with CodegraphClient(project_id) as cli:
            data = cli.stats()
    except PlatformError as exc:
        if _is_missing(exc):
            return ok(S.CodegraphStatsResponse(), request_id=_rid(request))
        raise
    return ok(S.CodegraphStatsResponse(**data), request_id=_rid(request))


@router.post(
    "/api/v1/graph/codegraph/search",
    tags=[_CODEGRAPH_TAG],
    summary="代码图谱-全文检索节点",
    operation_id="graphCodegraphSearch",
    response_model=CommonResult[S.CodegraphSearchResponse],
)
def codegraph_search(request: Request, body: S.CodegraphSearchRequest,
                     ctx=Depends(require_project_access)) -> CommonResult:
    _identity, project_id = ctx
    with CodegraphClient(project_id) as cli:
        items = cli.search(body.keyword, body.languages, body.kinds, body.limit)
    resp = S.CodegraphSearchResponse(items=[S.CodegraphNode(**n) for n in items])
    return ok(resp, request_id=_rid(request))


@router.post(
    "/api/v1/graph/codegraph/node",
    tags=[_CODEGRAPH_TAG],
    summary="代码图谱-节点详情",
    operation_id="graphCodegraphNode",
    response_model=CommonResult[S.CodegraphNode],
)
def codegraph_node(request: Request, body: S.CodegraphNodeRequest,
                   ctx=Depends(require_project_access)) -> CommonResult:
    _identity, project_id = ctx
    with CodegraphClient(project_id) as cli:
        node = cli.node(body.id)
    data = S.CodegraphNode(**node) if node is not None else None
    return ok(data, request_id=_rid(request))


@router.post(
    "/api/v1/graph/codegraph/neighbors",
    tags=[_CODEGRAPH_TAG],
    summary="代码图谱-1 跳邻居",
    operation_id="graphCodegraphNeighbors",
    response_model=CommonResult[S.CodegraphNeighborsResponse],
)
def codegraph_neighbors(request: Request, body: S.CodegraphNeighborsRequest,
                        ctx=Depends(require_project_access)) -> CommonResult:
    _identity, project_id = ctx
    with CodegraphClient(project_id) as cli:
        data = cli.neighbors(body.id, body.direction, body.edgeKinds)
    resp = S.CodegraphNeighborsResponse(
        center=S.CodegraphNode(**data["center"]) if data["center"] is not None else None,
        nodes=[S.CodegraphNode(**n) for n in data["nodes"]],
        edges=[S.CodegraphEdge(**e) for e in data["edges"]],
    )
    return ok(resp, request_id=_rid(request))


@router.post(
    "/api/v1/graph/codegraph/file-tree",
    tags=[_CODEGRAPH_TAG],
    summary="代码图谱-文件树",
    operation_id="graphCodegraphFileTree",
    response_model=CommonResult[S.CodegraphFileTreeResponse],
)
def codegraph_file_tree(request: Request, body: S.CodegraphFileTreeRequest | None = None,
                        ctx=Depends(require_project_access)) -> CommonResult:
    _identity, project_id = ctx
    prefix = body.prefix if body else None
    with CodegraphClient(project_id) as cli:
        items = cli.file_tree(prefix)
    resp = S.CodegraphFileTreeResponse(items=[S.CodegraphFile(**f) for f in items])
    return ok(resp, request_id=_rid(request))


@router.post(
    "/api/v1/graph/codegraph/graph",
    tags=[_CODEGRAPH_TAG],
    summary="代码图谱-全图加载",
    operation_id="graphCodegraphGraph",
    response_model=CommonResult[S.CodegraphGraphResponse],
)
def codegraph_graph(request: Request, body: S.CodegraphGraphRequest | None = None,
                    ctx=Depends(require_project_access)) -> CommonResult:
    _identity, project_id = ctx
    b = body or S.CodegraphGraphRequest()
    try:
        with CodegraphClient(project_id) as cli:
            data = cli.graph(b.limit, b.languages, b.kinds, b.edgeKinds)
    except PlatformError as exc:
        if _is_missing(exc):
            return ok(S.CodegraphGraphResponse(), request_id=_rid(request))
        raise
    resp = S.CodegraphGraphResponse(
        nodes=[S.CodegraphNode(**n) for n in data["nodes"]],
        edges=[S.CodegraphEdge(**e) for e in data["edges"]],
        totalNodes=data["totalNodes"], totalEdges=data["totalEdges"],
    )
    return ok(resp, request_id=_rid(request))


# ======================================================================
# 统一图谱组 (2) —— 直读 graph/store.py 全量节点/边 (所有插件聚合)
# ======================================================================
#
# 本组返回 store 内全部 GraphNode/GraphEdge (frontend/backend/database 等所有栈插件
# + 核心 linker), 用统一 NodeKind/EdgeKind 直出, 让插件产出 (尤其 sql 的
# db_table/db_column) 完整可见。
# store 缺失 / 空 → 返回空 (200, 非错误), 与现有 graph 路由的 graceful-empty 一致。


def _load_unified_from_store(project_id: str) -> AnalyzerResult:
    """读 store 内本 project 的全量图谱 (所有插件); store 缺/空 → 空 AnalyzerResult。"""
    conn = _open_store_ro(project_id)
    if conn is None:
        return AnalyzerResult()
    try:
        return load_graph(conn, project_id)
    finally:
        conn.close()


@router.post(
    "/api/v1/graph/unified/graph",
    tags=[_UNIFIED_TAG],
    summary="统一图谱-全图加载",
    operation_id="graphUnifiedGraph",
    response_model=CommonResult[S.UnifiedGraphResponse],
)
def unified_graph(request: Request, ctx=Depends(require_project_access)) -> CommonResult:
    _identity, project_id = ctx
    result = _load_unified_from_store(project_id)
    nodes = [
        S.UnifiedGraphNode(
            id=n.id, kind=n.kind, name=n.name, filePath=n.file,
            startLine=n.line, language=n.language, meta=n.meta,
        )
        for n in result.nodes
    ]
    edges = [
        S.UnifiedGraphEdge(source=e.source, target=e.target, kind=e.kind)
        for e in result.edges
    ]
    resp = S.UnifiedGraphResponse(
        nodes=nodes, edges=edges, nodeCount=len(nodes), edgeCount=len(edges)
    )
    return ok(resp, request_id=_rid(request))


@router.post(
    "/api/v1/graph/audit",
    tags=[_UNIFIED_TAG],
    summary="统一图谱-结构审计",
    operation_id="graphAudit",
    response_model=CommonResult[S.GraphAuditResponse],
)
def graph_audit(request: Request, ctx=Depends(require_project_access)) -> CommonResult:
    """统一图谱结构审计 (Phase 3, 纯读): 断链/串台/孤儿 plugin/重复/低置信。store 缺/空 → clean。"""
    _identity, project_id = ctx
    from codev_platform.graph.audit import audit_graph
    conn = _open_store_ro(project_id)
    if conn is None:
        return ok(S.GraphAuditResponse(), request_id=_rid(request))
    try:
        rep = audit_graph(conn, project_id)
    finally:
        conn.close()
    err, warn, tot = rep["errors"], rep["warnings"], rep["totals"]
    resp = S.GraphAuditResponse(
        clean=rep["clean"], errorCount=rep["error_count"],
        danglingEdges=err["dangling_edges"]["count"],
        crossProjectNodes=err["cross_project_nodes"]["count"],
        orphanSoftPlugins=err["orphan_soft_plugins"]["count"],
        duplicateNodes=warn["duplicate_nodes"]["count"],
        lowConfidenceEdges=warn["low_confidence_edges"]["count"],
        nodes=tot["nodes"], edges=tot["edges"],
    )
    return ok(resp, request_id=_rid(request))


@router.post(
    "/api/v1/graph/soft-quality",
    tags=[_UNIFIED_TAG],
    summary="统一图谱-软标签健康度",
    operation_id="graphSoftQuality",
    response_model=CommonResult[S.GraphSoftQualityResponse],
)
def graph_soft_quality(request: Request, ctx=Depends(require_project_access)) -> CommonResult:
    """A1/A2 软标签健康度诊断 (纯读): 分布/覆盖/巨型 cluster 退化。store 缺/无软层 → healthy。"""
    _identity, project_id = ctx
    from codev_platform.graph.soft_quality import assess_soft_labels
    conn = _open_store_ro(project_id)
    if conn is None:
        return ok(S.GraphSoftQualityResponse(), request_id=_rid(request))
    try:
        rep = assess_soft_labels(conn, project_id)
    finally:
        conn.close()
    dom, lay = rep["domains"], rep["layers"]
    resp = S.GraphSoftQualityResponse(
        healthy=rep["healthy"], flagCount=len(rep["flags"]), flags=rep["flags"],
        domainCount=dom["soft_nodes"], domainCoverage=dom["coverage"], domainGiant=len(dom["giant"]),
        layerCount=lay["soft_nodes"], layerCoverage=lay["coverage"], layerGiant=len(lay["giant"]),
    )
    return ok(resp, request_id=_rid(request))


@router.post(
    "/api/v1/graph/unified/stats",
    tags=[_UNIFIED_TAG],
    summary="统一图谱-统计",
    operation_id="graphUnifiedStats",
    response_model=CommonResult[S.UnifiedGraphStatsResponse],
)
def unified_stats(request: Request, ctx=Depends(require_project_access)) -> CommonResult:
    _identity, project_id = ctx
    result = _load_unified_from_store(project_id)
    nodes_by_kind: dict[str, int] = {}
    for n in result.nodes:
        nodes_by_kind[n.kind] = nodes_by_kind.get(n.kind, 0) + 1
    edges_by_kind: dict[str, int] = {}
    for e in result.edges:
        edges_by_kind[e.kind] = edges_by_kind.get(e.kind, 0) + 1
    resp = S.UnifiedGraphStatsResponse(
        nodesByKind=nodes_by_kind, edgesByKind=edges_by_kind,
        totalNodes=len(result.nodes), totalEdges=len(result.edges),
    )
    return ok(resp, request_id=_rid(request))
