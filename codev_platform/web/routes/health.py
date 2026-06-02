"""Health 路由 (plan §十五) —— 存活探针 (公开)。

GET /api/v1/health/check 走统一 envelope; GET /health 是裸探针 (k8s/负载均衡用)。
第一版只报自身存活; 下游依赖探测 (PG/chroma daemon/agent) 后续填 dependencies (plan §二十二)。
"""
from __future__ import annotations

from fastapi import APIRouter, Request

from codev_platform.core.httpkit.envelope import CommonResult, ok
from codev_platform.web.schemas.health import HealthData

router = APIRouter()


@router.get("/health")
def health_probe() -> dict:
    """裸存活探针 (公开 path, 无 envelope)。"""
    return {"status": "ok"}


@router.get(
    "/api/v1/health/check",
    tags=["HealthAPI-健康检查"],
    summary="健康检查-存活探针",
    operation_id="healthCheck",
    response_model=CommonResult[HealthData],
)
def health_check(request: Request) -> CommonResult[HealthData]:
    rid = getattr(request.state, "request_id", None)
    return ok(HealthData(), request_id=rid)
