"""Graph 组路由 (plan §十五 Graph + §二十) —— 替代 Java codegraph-api(:18082) 的 12 个只读接口。

经 integrations 直读 per-project 只读 SQLite (codegraph.db / cross_layer.sqlite), 不经 daemon、
不起模型 (plan §12.1 资源隔离铁律)。全部 POST + X-Project-Id, 经 require_project_access 鉴权
(core.acl 单一真值源, 与 3 MCP + agent + memory 同一套)。

字段形状对齐 Java DTO (camelCase), 前端 force-graph 从 :18082 平滑迁移。错误统一走 envelope:
DB 缺失=index_missing(503) / 入参非法=invalid_params(400) / sqlite 故障=upstream_unavailable(503)。
"""
from __future__ import annotations

from fastapi import APIRouter, Depends, Request

from codev_platform.core.httpkit.envelope import CommonResult, ok
from codev_platform.core.httpkit.permissions import require_project_access
from codev_platform.web.integrations.codegraph_client import CodegraphClient
from codev_platform.web.integrations.cross_link_client import CrossLinkClient
from codev_platform.web.schemas import graph as S

router = APIRouter()

_CODEGRAPH_TAG = "GraphAPI-代码图谱"
_CROSSLINK_TAG = "GraphAPI-跨层链路"


def _rid(request: Request) -> str | None:
    return getattr(request.state, "request_id", None)


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
    with CodegraphClient(project_id) as cli:
        data = cli.stats()
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
    with CodegraphClient(project_id) as cli:
        data = cli.graph(b.limit, b.languages, b.kinds, b.edgeKinds)
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
    with CrossLinkClient(project_id) as cli:
        data = cli.stats()
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
    with CrossLinkClient(project_id) as cli:
        data = cli.tables()
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
    with CrossLinkClient(project_id) as cli:
        data = cli.graph(b.mode, b.kinds, b.excludeKinds, b.rels, b.excludeRels, b.limit)
    return ok(S.CrossLinkGraphResponse(**data), request_id=_rid(request))
