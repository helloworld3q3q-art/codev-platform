"""Recall 组路由 (Phase 6) —— 跨 lane 代码召回。

把 graph(架构/跨层节点)+ codegraph(符号 FTS)融成一路带来源解释的统一排名, 让客户端/
agent 一次拿到"最相关代码实体"而不必分调两工具再人脑合并。POST + X-Project-Id, 经
require_project_access 鉴权(与 graph/reports 组同一套)。

融合逻辑全在 recall 包(daemon-free, 每 lane fail-soft); 本路由是薄壳: 鉴权 + 调 recall_code +
装 envelope。store/索引缺失由 recall_code 内部优雅降级(返回空/单 lane), 路由无需特判。
"""
from __future__ import annotations

from fastapi import APIRouter, Depends, Request

from codev_platform.core.httpkit.envelope import CommonResult, ok
from codev_platform.core.httpkit.permissions import require_project_access
from codev_platform.web.schemas import recall as S

router = APIRouter()

_TAG = "RecallAPI-跨lane召回"


def _rid(request: Request) -> str | None:
    return getattr(request.state, "request_id", None)


@router.post(
    "/api/v1/recall/code",
    tags=[_TAG],
    summary="跨lane代码召回-融合 graph + codegraph + vector",
    operation_id="recallCode",
    response_model=CommonResult[S.RecallCodeResponse],
)
def recall_code_route(request: Request, body: S.RecallCodeRequest,
                      ctx=Depends(require_project_access)) -> CommonResult:
    """融合 graph + codegraph + vector(语义)三 lane → 统一可解释排名。空 query / 无索引 → 空结果(非错误)。"""
    _identity, project_id = ctx
    from codev_platform.recall import recall_code
    hits = recall_code(body.query, project_id, weights=body.weights, limit=body.limit)
    lanes = sorted({lane for h in hits for lane in h.lanes})
    resp = S.RecallCodeResponse(
        hits=[S.RecallCodeHit(ref=h.ref, score=h.score, name=h.name, kind=h.kind,
                              file=h.file, lanes=h.lanes) for h in hits],
        count=len(hits), lanes=lanes,
    )
    return ok(resp, request_id=_rid(request))
