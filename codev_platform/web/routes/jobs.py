"""Jobs 路由 (plan §十五 Jobs) —— 查询 / 取消长任务。

GET  /api/v1/jobs/detail   按 jobId 查状态
POST /api/v1/jobs/cancel   取消活跃 job

身份/项目边界经 require_project_access (复用 core.acl, plan §六依赖)。本片用进程内默认
JobService 单例 (内存 store); indexes.router 复用同一实例 (共享 store + 项目锁), 保证
"提交 → 查 → 取消" 闭环一致。
"""
from __future__ import annotations

from fastapi import APIRouter, Depends, Query, Request

from codev_platform.core.httpkit.envelope import CommonResult, PageResult, ok, page
from codev_platform.core.httpkit.pagination import PageBody
from codev_platform.core.httpkit.permissions import require_project_access
from codev_platform.web.schemas.jobs import JobCancelRequest, JobDTO
from codev_platform.web.services.index_service import make_reindex_dispatch_trigger
from codev_platform.web.services.job_service import build_in_memory_job_service

router = APIRouter()

# 进程内默认 job service (内存 store + 模块级项目锁)。indexes.router import 同一实例。
# trigger 派 index_rebuild job 进真实 reindex 队列 (FileSpoolQueue, worker 消费) ——
# 让 web "重建索引" 真触发重建, 而非空壳 (deep-audit-2026-06-03-review 净新增②)。
job_service = build_in_memory_job_service(trigger=make_reindex_dispatch_trigger())


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


@router.post(
    "/api/v1/jobs/list",
    tags=["JobAPI-长任务"],
    summary="长任务-列表(按当前项目过滤)",
    operation_id="listJobs",
    response_model=PageResult[JobDTO],
)
def list_jobs(
    request: Request,
    body: PageBody | None = None,   # 分页从 body 取(前端 post 发 body); 缺/空 → 默认第 1 页
    ctx=Depends(require_project_access),
) -> PageResult[JobDTO]:
    # project_id 来自 X-Project-Id (require_project_access 解析 + 鉴权); 只列当前项目的 job。
    pg = body or PageBody()
    _identity, project_id = ctx
    rows, total = job_service.list_jobs(project_id=project_id, offset=pg.offset, limit=pg.pageSize)
    return page(
        [JobDTO.of(j) for j in rows],
        page_number=pg.pageNumber, page_size=pg.pageSize, total=total,
        request_id=_rid(request),
    )
