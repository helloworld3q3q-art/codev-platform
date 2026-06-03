"""Reports 组路由 (Track A5) —— 影响分析报告 + 跨层查询。

吃 A1 桥接后**连通**的统一图谱 store (graph/impact.py 引擎), 回答 README 核心卖点:
"改一处 → 跨层影响清单"。全部 POST + X-Project-Id, 经 require_project_access 鉴权
(与 graph 组同一套)。只读 store; store 缺失/空 → found=False (200, 非错误)。

4 个入口:
- /api/v1/reports/impact            改某节点 → 跨层被波及清单 + 风险 (generate_impact_report)
- /api/v1/reports/table-usage       给表 → 哪些函数/端点/前端用它
- /api/v1/reports/page-dependencies 给前端页 → 依赖的端点/函数/表
- /api/v1/reports/api-callers       给端点 → 哪些前端调它
"""
from __future__ import annotations

import sqlite3

from fastapi import APIRouter, Depends, Request

from codev_platform.core.httpkit.envelope import CommonResult, ok
from codev_platform.core.httpkit.permissions import require_project_access
from codev_platform.graph import impact as I
from codev_platform.graph.store import graph_store_path
from codev_platform.web.schemas import reports as S

router = APIRouter()

_TAG = "ReportsAPI-影响分析"


def _rid(request: Request) -> str | None:
    return getattr(request.state, "request_id", None)


def _open_store_ro(project_id: str) -> sqlite3.Connection | None:
    """只读打开统一图谱 store; 文件不存在返回 None。"""
    path = graph_store_path(project_id)
    if not path.exists():
        return None
    return sqlite3.connect(f"file:{path}?mode=ro", uri=True)


@router.post(
    "/api/v1/reports/impact",
    tags=[_TAG],
    summary="影响分析-改动跨层影响报告",
    operation_id="reportImpact",
    response_model=CommonResult[S.ImpactReportResponse],
)
def report_impact(request: Request, body: S.ImpactRequest,
                  ctx=Depends(require_project_access)) -> CommonResult:
    _identity, project_id = ctx
    conn = _open_store_ro(project_id)
    if conn is None:
        return ok(S.ImpactReportResponse(found=False, summary="该项目尚无统一图谱索引"),
                  request_id=_rid(request))
    try:
        r = I.generate_impact_report(conn, project_id, body.nodeRef)
    finally:
        conn.close()
    return ok(
        S.ImpactReportResponse(
            found=r["found"], target=r.get("target"), impact=r.get("impact"),
            risk=r.get("risk"), layersAffected=r.get("layersAffected", []),
            total=r.get("total", 0), summary=r.get("summary", ""),
        ),
        request_id=_rid(request),
    )


def _query(request: Request, project_id: str, fn, *args) -> CommonResult:
    """table-usage / page-deps / api-callers 共用: 开 store -> 调引擎 -> 包 GraphQueryResponse。"""
    conn = _open_store_ro(project_id)
    if conn is None:
        return ok(S.GraphQueryResponse(found=False), request_id=_rid(request))
    try:
        r = fn(conn, project_id, *args)
    finally:
        conn.close()
    return ok(S.GraphQueryResponse(found=r.get("found", False), data=r), request_id=_rid(request))


@router.post(
    "/api/v1/reports/table-usage",
    tags=[_TAG],
    summary="影响分析-表被谁使用",
    operation_id="reportTableUsage",
    response_model=CommonResult[S.GraphQueryResponse],
)
def report_table_usage(request: Request, body: S.TableUsageRequest,
                       ctx=Depends(require_project_access)) -> CommonResult:
    _identity, project_id = ctx
    return _query(request, project_id, I.find_table_usage, body.table)


@router.post(
    "/api/v1/reports/page-dependencies",
    tags=[_TAG],
    summary="影响分析-前端页依赖",
    operation_id="reportPageDependencies",
    response_model=CommonResult[S.GraphQueryResponse],
)
def report_page_dependencies(request: Request, body: S.PageDepsRequest,
                             ctx=Depends(require_project_access)) -> CommonResult:
    _identity, project_id = ctx
    return _query(request, project_id, I.find_page_dependencies, body.pageRef)


@router.post(
    "/api/v1/reports/api-callers",
    tags=[_TAG],
    summary="影响分析-端点被谁调用",
    operation_id="reportApiCallers",
    response_model=CommonResult[S.GraphQueryResponse],
)
def report_api_callers(request: Request, body: S.ApiCallersRequest,
                       ctx=Depends(require_project_access)) -> CommonResult:
    _identity, project_id = ctx
    return _query(request, project_id, I.find_api_callers, body.endpointRef)
