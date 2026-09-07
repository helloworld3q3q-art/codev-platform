"""Indexes 路由 (plan §十五 Indexes) —— 提交索引重建 (返回 jobId, 不阻塞)。

POST /api/v1/indexes/rebuild   提交重建, 返回 jobId

复用 jobs.router 的同一 JobService 单例 (共享内存 store + 项目级互斥锁), 使
"提交 rebuild → GET jobs/detail" 闭环一致。真正重建走平台既有 reindex worker 串行
写锁通道 (plan §12.1), 本片只提交 (stub trigger)。
"""
from __future__ import annotations

from fastapi import APIRouter, Depends, Request

from codev_platform.core.httpkit.envelope import CommonResult, ok
from codev_platform.core.httpkit.permissions import require_project_access
from codev_platform.web.routes.jobs import job_service
from codev_platform.web.schemas.jobs import (
    IndexRebuildRequest,
    IndexStatusItem,
    IndexStatusResponse,
    JobIdData,
)
from codev_platform.web.services.index_service import IndexService

router = APIRouter()

index_service = IndexService(job_service)


@router.post(
    "/api/v1/indexes/rebuild",
    tags=["IndexAPI-索引"],
    summary="索引-提交重建任务",
    operation_id="rebuildIndex",
    response_model=CommonResult[JobIdData],
)
def rebuild_index(
    request: Request,
    body: IndexRebuildRequest | None = None,
    access=Depends(require_project_access),
) -> CommonResult[JobIdData]:
    _identity, project_id = access
    index_kind = body.indexKind if body else None
    job = index_service.rebuild(project_id, index_kind)
    rid = getattr(request.state, "request_id", None)
    return ok(JobIdData(jobId=job.job_id), request_id=rid)


@router.post(
    "/api/v1/indexes/status",
    tags=["IndexAPI-索引"],
    summary="索引-各类新鲜度状态",
    operation_id="indexStatus",
    response_model=CommonResult[IndexStatusResponse],
)
def index_status(
    request: Request,
    access=Depends(require_project_access),
) -> CommonResult[IndexStatusResponse]:
    """读统一 IndexManifest, 返回各类索引相对当前 HEAD 的新鲜度 (Phase 1, 纯读不触发构建)。"""
    _identity, project_id = access
    from codev_platform.core.config import get, load_config
    from codev_platform.index_manifest import freshness

    repo = get(load_config(), f"projects.{project_id}.repo_path")
    rows = freshness(project_id, repo)
    head = rows[0]["head"] if rows else None
    items = [
        IndexStatusItem(
            kind=r["kind"], status=r["status"], gitCommit=r["git_commit"],
            fresh=r["fresh"], reason=r["reason"],
            finishedAt=r["finished_at"], elapsedSec=r["elapsed_sec"],
        )
        for r in rows
    ]
    rid = getattr(request.state, "request_id", None)
    return ok(IndexStatusResponse(headCommit=head, items=items), request_id=rid)
