"""Index service —— 索引重建提交 (plan §十二 / §十五 Indexes)。

POST /api/v1/indexes/rebuild 的业务编排: 校验 index_kind → 委托 JobService.submit
建一个 job_type='index_rebuild:<kind>' 的 job (返回 jobId, 不阻塞)。同 project 同 kind
互斥由 JobService 的项目级锁保证。本片不真跑 reindex (plan §12.1: 重活走平台既有
reindex worker 串行写锁通道)。

index_kind 取值对齐 reindex.runners 的注册集合 (chroma / codegraph);
不传 kind 默认 'all' (一次重建所有, job_type='index_rebuild:all')。
"""
from __future__ import annotations

from codev_platform.core.errors import ErrorCode, PlatformError
from codev_platform.web.domain.job import Job
from codev_platform.web.services.job_service import JobService, JobTrigger

_ALL = "all"

_JOB_TYPE_PREFIX = "index_rebuild"


def _allowed_kinds() -> tuple[str, ...]:
    """允许的 index kind = reindex.runners 注册集 + 'all' 聚合 —— 动态读, 避免硬编码漂移。
    codex P2 #6: 原硬编码 ('all','chroma','codegraph') 漏了 ingest, 但 all 展开却 enqueue ingest,
    API 合约与 runner 真值源不一致(单独重建统一图谱 ingest 只能走 all, 多余重建 + 锁冲突)。"""
    from codev_platform.reindex.runners import kinds as _runner_kinds
    return (_ALL, *sorted(_runner_kinds()))


def job_type_for(index_kind: str) -> str:
    return f"{_JOB_TYPE_PREFIX}:{index_kind}"


class IndexService:
    def __init__(self, job_service: JobService) -> None:
        self._jobs = job_service

    def rebuild(self, project_id: str, index_kind: str | None = None) -> Job:
        """提交索引重建。返回新建 job (含 jobId)。同 project 同 kind 在跑 → RATE_LIMITED。"""
        kind = (index_kind or _ALL).strip()
        allowed = _allowed_kinds()
        if kind not in allowed:
            raise PlatformError(
                ErrorCode.INVALID_PARAMS,
                f"unknown index kind: {kind!r} (allowed: {list(allowed)})",
                detail=f"project_id={project_id} index_kind={index_kind!r}",
            )
        return self._jobs.submit(project_id, job_type_for(kind))


def make_reindex_dispatch_trigger(queue_factory=None) -> JobTrigger:
    """造一个把 index_rebuild job 真正派进平台 reindex 队列的 JobService.trigger。

    修 deep-audit-2026-06-03-review 净新增②: 原 _noop_trigger 让 web "重建索引" 按钮是
    空壳 —— 建了 Pending job 但无人真重建。本 trigger 把 job 落进平台既有 reindex 写队列
    (FileSpoolQueue, 与 webhook 同一条), 由 codev-reindex worker 串行消费, 真正触发重建。

    job_type='index_rebuild:<kind>' → enqueue(project_id, kind);kind='all' 展开成
    runners.kinds() 全集 (FileSpoolQueue 只认已注册 kind, 不认 'all')。非 index_rebuild 的
    job_type 一律忽略 (本 trigger 只管索引重建, 不拦截未来其它 job 类型)。
    queue_factory 默认 reindex.open_default_queue (测试可注入假队列, 不碰真实 spool)。
    """
    prefix = f"{_JOB_TYPE_PREFIX}:"

    def _trigger(job: Job) -> None:
        if not job.job_type.startswith(prefix):
            return
        kind = job.job_type[len(prefix):]
        from codev_platform.reindex import open_default_queue
        from codev_platform.reindex.runners import kinds as _kinds
        queue = (queue_factory or open_default_queue)()
        targets = list(_kinds()) if kind == _ALL else [kind]
        for k in targets:
            queue.enqueue(job.project_id, k)

    return _trigger
