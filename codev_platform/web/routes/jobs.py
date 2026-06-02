"""Jobs 路由 (plan §十五 Jobs) —— 查询 / 取消长任务。

GET  /api/v1/jobs/detail   按 jobId 查状态
POST /api/v1/jobs/cancel   取消活跃 job

身份/项目边界经 require_project_access (复用 core.acl, plan §六依赖)。本片用进程内默认
JobService 单例 (内存 store); indexes.router 复用同一实例 (共享 store + 项目锁), 保证
"提交 → 查 → 取消" 闭环一致。
"""
from __future__ import annotations

from fastapi import APIRouter, Depends, Query, Request

from codev_platform.core.httpkit.envelope import CommonResult, ok
from codev_platform.core.httpkit.permissions import require_project_access
from codev_platform.web.schemas.jobs import JobCancelRequest, JobDTO
from codev_platform.web.services.job_service import build_in_memory_job_service

router = APIRouter()

# 进程内默认 job service (内存 store + 模块级项目锁)。indexes.router import 同一实例。
job_service = build_in_memory_job_service()


def _rid(request: Request) -> str | None:
    return getattr(request.state, "request_id", None)


@router.get(
    "/api/v1/jobs/detail",
    tags=["JobAPI-长任务"],
    summary="长任务-查询任务详情",
    operation_id="getJobDetail",
    response_model=CommonResult[JobDTO],
)
def get_job_detail(
    request: Request,
    jobId: str = Query(..., min_length=1, description="任务 ID"),
    _access=Depends(require_project_access),
) -> CommonResult[JobDTO]:
    job = job_service.get_detail(jobId)
    return ok(JobDTO.of(job), request_id=_rid(request))


@router.post(
    "/api/v1/jobs/cancel",
    tags=["JobAPI-长任务"],
    summary="长任务-取消任务",
    operation_id="cancelJob",
    response_model=CommonResult[JobDTO],
)
def cancel_job(
    request: Request,
    body: JobCancelRequest,
    _access=Depends(require_project_access),
) -> CommonResult[JobDTO]:
    job = job_service.cancel(body.jobId)
    return ok(JobDTO.of(job), request_id=_rid(request))
