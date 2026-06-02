"""Index service —— 索引重建提交 (plan §十二 / §十五 Indexes)。

POST /api/v1/indexes/rebuild 的业务编排: 校验 index_kind → 委托 JobService.submit
建一个 job_type='index_rebuild:<kind>' 的 job (返回 jobId, 不阻塞)。同 project 同 kind
互斥由 JobService 的项目级锁保证。本片不真跑 reindex (plan §12.1: 重活走平台既有
reindex worker 串行写锁通道)。

index_kind 取值对齐 reindex.runners 的注册集合 (chroma / codegraph / cross_link);
不传 kind 默认 'all' (一次重建所有, job_type='index_rebuild:all')。
"""
from __future__ import annotations

from codev_platform.core.errors import ErrorCode, PlatformError
from codev_platform.web.domain.job import Job
from codev_platform.web.services.job_service import JobService

_ALL = "all"
# 允许的索引类型 (对齐 reindex.runners.kinds() + 'all' 聚合)。
_ALLOWED_KINDS = ("all", "chroma", "codegraph", "cross_link")

_JOB_TYPE_PREFIX = "index_rebuild"


def job_type_for(index_kind: str) -> str:
    return f"{_JOB_TYPE_PREFIX}:{index_kind}"


class IndexService:
    def __init__(self, job_service: JobService) -> None:
        self._jobs = job_service

    def rebuild(self, project_id: str, index_kind: str | None = None) -> Job:
        """提交索引重建。返回新建 job (含 jobId)。同 project 同 kind 在跑 → RATE_LIMITED。"""
        kind = (index_kind or _ALL).strip()
        if kind not in _ALLOWED_KINDS:
            raise PlatformError(
                ErrorCode.INVALID_PARAMS,
                f"unknown index kind: {kind!r} (allowed: {list(_ALLOWED_KINDS)})",
                detail=f"project_id={project_id} index_kind={index_kind!r}",
            )
        return self._jobs.submit(project_id, job_type_for(kind))
