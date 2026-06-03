"""Job service —— 长任务提交 / 查询 / 取消的编排 (plan §十二)。

HTTP routes 不直接碰 repo / lock, 必须经本 service 保持并发与审计边界。职责:
- 提交: 项目级互斥锁 (同 project 同 job_type 互斥) → 建 Pending job → 触发 stub runner。
- 查询: 取 job detail (未知 job → PlatformError(PROJECT_UNKNOWN, 语义=resource_unknown))。
- 取消: 活跃态 (Pending/Running) → Cancelled + 释放锁; 终态拒绝。

本片不真跑 reindex —— 重活提交给平台既有写锁通道 (reindex worker 串行, plan §12.1)。
runner 是可注入 stub (默认 no-op), 便于测试且不旁路平台写锁。锁随 job 生命周期:
acquire 在提交, release 在进入终态。
"""
from __future__ import annotations

from collections.abc import Callable

from codev_platform.core.errors import ErrorCode, PlatformError
from codev_platform.web.domain import job as job_domain
from codev_platform.web.domain.job import Job
from codev_platform.web.domain.locks import ProjectLockRegistry
from codev_platform.web.domain.locks import registry as default_lock_registry
from codev_platform.web.repositories.job_read_repo import (
    InMemoryJobReadRepo,
    JobReadRepository,
)
from codev_platform.web.repositories.job_write_repo import (
    InMemoryJobWriteRepo,
    JobWriteRepository,
    new_in_memory_store,
)

# 触发器签名: (job) -> None。本片默认 no-op (不真跑 reindex, plan §12.1)。
JobTrigger = Callable[[Job], None]


def _noop_trigger(job: Job) -> None:
    """默认 stub: 不真跑 reindex。真实重建走平台既有 reindex worker 串行通道。"""
    return None


class JobService:
    def __init__(
        self,
        *,
        read_repo: JobReadRepository,
        write_repo: JobWriteRepository,
        locks: ProjectLockRegistry | None = None,
        trigger: JobTrigger | None = None,
    ) -> None:
        self._read = read_repo
        self._write = write_repo
        self._locks = locks if locks is not None else default_lock_registry
        self._trigger = trigger if trigger is not None else _noop_trigger

    def submit(self, project_id: str, job_type: str) -> Job:
        """提交一个长任务 job。同 project 同 job_type 已在跑 → RATE_LIMITED (可退避重试)。"""
        if not self._locks.try_acquire(project_id, job_type):
            raise PlatformError(
                ErrorCode.RATE_LIMITED,
                f"A '{job_type}' job is already running for this project.",
                detail=f"project_id={project_id} job_type={job_type}",
            )
        try:
            job = self._write.create(job_domain.create_job(project_id, job_type))
        except Exception:
            # 建 job 失败 → 立即释放锁, 不留死锁。
            self._locks.release(project_id, job_type)
            raise
        # 触发 dispatch (web 默认派进真实 reindex 队列; 测试注 no-op)。派发失败 (如入队
        # 写盘失败) → 释放锁让用户可重试, 与上面 create 失败同范式 (否则锁悬挂堵后续提交)。
        try:
            self._trigger(job)
        except Exception:
            self._locks.release(project_id, job_type)
            raise
        return job

    def list_jobs(self, *, project_id: str | None, offset: int, limit: int) -> tuple[list[Job], int]:
        """列某项目 job (project_id=None 列全部), 按创建时间倒序 (新在前) + 分页。"""
        rows = sorted(self._read.list(project_id), key=lambda j: j.created_at, reverse=True)
        total = len(rows)
        return rows[offset:offset + limit], total

    def get_detail(self, job_id: str) -> Job:
        job = self._read.get(job_id)
        if job is None:
            raise PlatformError(
                ErrorCode.PROJECT_UNKNOWN,  # 8 类无 resource_unknown, 就近归 (plan §十)
                f"Unknown job: {job_id}",
                detail=f"job_id={job_id}",
            )
        return job

    def cancel(self, job_id: str) -> Job:
        """取消活跃 job (Pending/Running)。终态 → 拒绝 (invalid_params)。"""
        job = self.get_detail(job_id)
        if job.is_terminal:
            raise PlatformError(
                ErrorCode.INVALID_PARAMS,
                f"Job is already in terminal state '{job.status}', cannot cancel.",
                detail=f"job_id={job_id} status={job.status}",
            )
        cancelled = self._write.save(job.to_cancelled())
        self._locks.release(cancelled.project_id, cancelled.job_type)
        return cancelled


def build_in_memory_job_service(
    *, locks: ProjectLockRegistry | None = None, trigger: JobTrigger | None = None
) -> JobService:
    """组装一套内存 job service (读写仓共享 store)。Web 进程默认实例 + 测试都用它。"""
    store = new_in_memory_store()
    return JobService(
        read_repo=InMemoryJobReadRepo(store),
        write_repo=InMemoryJobWriteRepo(store),
        locks=locks,
        trigger=trigger,
    )
