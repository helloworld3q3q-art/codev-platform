"""Graph 组路由 (plan §十五 Graph + §二十) —— 替代 Java codegraph-api(:18082) 的 12 个只读接口。

经 integrations 直读 per-project 只读 SQLite (codegraph.db / cross_layer.sqlite), 不经 daemon、
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
from codev_platform.graph.schema import AnalyzerResult, GraphEdge, GraphNode
from codev_platform.graph.store import graph_store_path, load_graph
from codev_platform.web.integrations.codegraph_client import CodegraphClient
from codev_platform.web.integrations.cross_link_client import CrossLinkClient
from codev_platform.web.schemas import graph as S

router = APIRouter()

_CODEGRAPH_TAG = "GraphAPI-代码图谱"
_CROSSLINK_TAG = "GraphAPI-跨层链路"


def _rid(request: Request) -> str | None:
    return getattr(request.state, "request_id", None)


def _is_missing(exc: PlatformError) -> bool:
    """index_missing = 该项目尚无此索引 → 概览(stats)/可视化(graph)返回空(非错误);
    具体查询(node/search/table-refs 等)仍抛 503, 让"找不到"是显式错误。"""
    return exc.code == ErrorCode.INDEX_MISSING


# ======================================================================
# 统一图谱 store (插件产出) → cross-link 响应映射 (store 优先, 空则 fallback)
# ======================================================================
#
# 设计:统一 store (graph/store.py) 是插件产出的聚合落点; 当它有 cross-link 数据时,
# 页面消费插件数据。store 空 / 无该 project → fallback 现有 CrossLinkClient
# (cross_layer.sqlite), 保证现有页面不破。
#
# 映射用 cross_link 适配器在 meta 里留底的原始 kind / rel (`cross_link_kind` /
# `cross_link_rel`) 还原成前端期望的 cross-link kind (java_endpoint / frontend_api /
# table / ...), 让响应结构与 CrossLinkClient 完全一致 (前端无感)。仅消费 cross-link
# 适配器产出的节点 (meta 含 cross_link_kind), 其它插件节点不混入本响应。


def _crosslink_kind(node: GraphNode) -> str | None:
    """从统一节点还原 cross-link 原始 kind; 非 cross-link 节点 (无留底) 返回 None。"""
    raw = node.meta.get("cross_link_kind")
    return str(raw) if raw else None


def _crosslink_rel(edge: GraphEdge) -> str:
    """从统一边还原 cross-link 原始 rel; 缺留底时退回统一 kind。"""
    raw = edge.meta.get("cross_link_rel")
    return str(raw) if raw else edge.kind


def _open_store_ro(project_id: str) -> sqlite3.Connection | None:
    """只读打开统一图谱 store; 文件不存在返回 None (走 fallback)。"""
    path = graph_store_path(project_id)
    if not path.exists():
        return None
    conn = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
    return conn


def _load_crosslink_from_store(project_id: str) -> AnalyzerResult | None:
    """读 store 内本 project 的 cross-link 子图 (仅 cross-link 适配器节点)。

    返回 None 表示 store 无可用 cross-link 数据 (文件缺 / 无该类节点) → caller fallback。
    """
    conn = _open_store_ro(project_id)
    if conn is None:
        return None
    try:
        result = load_graph(conn, project_id)
    finally:
        conn.close()
    nodes = [n for n in result.nodes if _crosslink_kind(n) is not None]
    if not nodes:
        return None
    node_ids = {n.id for n in nodes}
    edges = [
        e for e in result.edges if e.source in node_ids and e.target in node_ids
    ]
    return AnalyzerResult(nodes=nodes, edges=edges)


def _store_to_graph_response(result: AnalyzerResult) -> S.CrossLinkGraphResponse:
    """统一 AnalyzerResult (cross-link 子图) → CrossLinkGraphResponse (前端形状不变)。"""
    nodes = [
        S.CrossLinkGraphNode(
            id=n.id,
            kind=_crosslink_kind(n),
            name=n.name,
            filePath=n.file,
            startLine=n.line,
            language=n.language,
        )
        for n in result.nodes
    ]
    edges = [
        S.CrossLinkGraphEdge(source=e.source, target=e.target, kind=_crosslink_rel(e))
        for e in result.edges
    ]
    return S.CrossLinkGraphResponse(
        nodes=nodes, edges=edges, nodeCount=len(nodes), edgeCount=len(edges)
    )


def _store_to_stats_response(result: AnalyzerResult) -> S.CrossLinkStatsResponse:
    """统一 AnalyzerResult (cross-link 子图) → CrossLinkStatsResponse。"""
    nodes_by_kind: dict[str, int] = {}
    for n in result.nodes:
        k = _crosslink_kind(n)
        if k is not None:
            nodes_by_kind[k] = nodes_by_kind.get(k, 0) + 1
    edges_by_rel: dict[str, int] = {}
    for e in result.edges:
        rel = _crosslink_rel(e)
        edges_by_rel[rel] = edges_by_rel.get(rel, 0) + 1
    return S.CrossLinkStatsResponse(
        lastBuildAt=None, nodesByKind=nodes_by_kind, edgesByRel=edges_by_rel
    )


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
# cross-link 组 (6)
# ======================================================================


@router.post(
    "/api/v1/graph/cross-link/stats",
    tags=[_CROSSLINK_TAG],
    summary="跨层链路-统计",
    operation_id="graphCrossLinkStats",
    response_model=CommonResult[S.CrossLinkStatsResponse],
)
def cross_link_stats(request: Request, ctx=Depends(require_project_access)) -> CommonResult:
    _identity, project_id = ctx
    # store 优先: 有插件产出的 cross-link 数据 → 用它统计。
    store_result = _load_crosslink_from_store(project_id)
    if store_result is not None:
        return ok(_store_to_stats_response(store_result), request_id=_rid(request))
    # fallback: 现有 cross_layer.sqlite。
    try:
        with CrossLinkClient(project_id) as cli:
            data = cli.stats()
    except PlatformError as exc:
        if _is_missing(exc):
            return ok(S.CrossLinkStatsResponse(), request_id=_rid(request))
        raise
    return ok(S.CrossLinkStatsResponse(**data), request_id=_rid(request))


@router.post(
    "/api/v1/graph/cross-link/tables",
    tags=[_CROSSLINK_TAG],
    summary="跨层链路-表清单",
    operation_id="graphCrossLinkTables",
    response_model=CommonResult[S.CrossLinkTablesResponse],
)
def cross_link_tables(request: Request, ctx=Depends(require_project_access)) -> CommonResult:
    _identity, project_id = ctx
    try:
        with CrossLinkClient(project_id) as cli:
            data = cli.tables()
    except PlatformError as exc:
        if _is_missing(exc):
            return ok(S.CrossLinkTablesResponse(), request_id=_rid(request))
        raise
    return ok(S.CrossLinkTablesResponse(**data), request_id=_rid(request))


@router.post(
    "/api/v1/graph/cross-link/table-refs",
    tags=[_CROSSLINK_TAG],
    summary="跨层链路-表引用清单",
    operation_id="graphCrossLinkTableRefs",
    response_model=CommonResult[S.CrossLinkTableRefsResponse],
)
def cross_link_table_refs(request: Request, body: S.CrossLinkTableRefsRequest,
                          ctx=Depends(require_project_access)) -> CommonResult:
    _identity, project_id = ctx
    with CrossLinkClient(project_id) as cli:
        data = cli.table_refs(body.table)
    return ok(S.CrossLinkTableRefsResponse(**data), request_id=_rid(request))


@router.post(
    "/api/v1/graph/cross-link/endpoint-link",
    tags=[_CROSSLINK_TAG],
    summary="跨层链路-前后端 endpoint 关联",
    operation_id="graphCrossLinkEndpointLink",
    response_model=CommonResult[list[S.CrossLinkEndpointLinkItem]],
)
def cross_link_endpoint_link(request: Request, body: S.CrossLinkEndpointLinkRequest,
                             ctx=Depends(require_project_access)) -> CommonResult:
    _identity, project_id = ctx
    with CrossLinkClient(project_id) as cli:
        items = cli.endpoint_link(body.name)
    data = [S.CrossLinkEndpointLinkItem(**it) for it in items]
    return ok(data, request_id=_rid(request))


@router.post(
    "/api/v1/graph/cross-link/search-nodes",
    tags=[_CROSSLINK_TAG],
    summary="跨层链路-节点模糊检索",
    operation_id="graphCrossLinkSearchNodes",
    response_model=CommonResult[S.CrossLinkSearchNodesResponse],
)
def cross_link_search_nodes(request: Request, body: S.CrossLinkSearchNodesRequest,
                            ctx=Depends(require_project_access)) -> CommonResult:
    _identity, project_id = ctx
    with CrossLinkClient(project_id) as cli:
        data = cli.search_nodes(body.query, body.kind, body.limit)
    return ok(S.CrossLinkSearchNodesResponse(**data), request_id=_rid(request))


@router.post(
    "/api/v1/graph/cross-link/graph",
    tags=[_CROSSLINK_TAG],
    summary="跨层链路-全图加载",
    operation_id="graphCrossLinkGraph",
    response_model=CommonResult[S.CrossLinkGraphResponse],
)
def cross_link_graph(request: Request, body: S.CrossLinkGraphRequest | None = None,
                     ctx=Depends(require_project_access)) -> CommonResult:
    _identity, project_id = ctx
    b = body or S.CrossLinkGraphRequest()
    # store 优先: 有插件产出的 cross-link 数据 → 直接消费 (前端响应结构不变)。
    store_result = _load_crosslink_from_store(project_id)
    if store_result is not None:
        return ok(_store_to_graph_response(store_result), request_id=_rid(request))
    # fallback: 现有 cross_layer.sqlite (含 mode/kinds 过滤 + 缺索引空图)。
    try:
        with CrossLinkClient(project_id) as cli:
            data = cli.graph(b.mode, b.kinds, b.excludeKinds, b.rels, b.excludeRels, b.limit)
    except PlatformError as exc:
        if _is_missing(exc):
            return ok(S.CrossLinkGraphResponse(), request_id=_rid(request))
        raise
    return ok(S.CrossLinkGraphResponse(**data), request_id=_rid(request))
