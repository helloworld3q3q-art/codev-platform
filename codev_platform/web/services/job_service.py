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
        # 提交完成即释放锁(审计事实A 修复): 锁只防**同一瞬间**并发提交, 不跨 submit 持有 ——
        # reindex worker 是独立进程、不回写 job 终态, 跨 submit 持有会让锁永久悬挂(成功路径无终态
        # 回调 → 同 project 同 job_type 第二次永久 RATE_LIMITED, 须 cancel/重启才解)。不重复跑由
        # 下游 FileSpoolQueue 同 key 合并 + worker 串行 + runner .reindex.lock 三层保证(不靠本锁)。
        self._locks.release(project_id, job_type)
        return job

    def list_jobs(self, *, project_id: str | None, offset: int, limit: int) -> tuple[list[Job], int]:
        """列某项目 job (project_id=None 列全部), 按创建时间倒序 (新在前) + 分页。"""
        rows = sorted(self._read.list(project_id), key=lambda j: j.created_at, reverse=True)
        total = len(rows)
        return rows[offset:offset + limit], total

    def get_detail(self, job_id: str, *, project_id: str | None = None) -> Job:
        job = self._read.get(job_id)
        # 跨项目隔离: job 不属本项目 → 当 not found(不泄露"存在但非本项目")。
        if job is None or (project_id is not None and job.project_id != project_id):
            raise PlatformError(
                ErrorCode.PROJECT_UNKNOWN,  # 8 类无 resource_unknown, 就近归 (plan §十)
                f"Unknown job: {job_id}",
                detail=f"job_id={job_id}",
            )
        return job

    def cancel(self, job_id: str, *, project_id: str | None = None) -> Job:
        """取消活跃 job (Pending/Running)。终态 → 拒绝 (invalid_params)。"""
        job = self.get_detail(job_id, project_id=project_id)   # 复用 get_detail 的跨项目校验
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


def build_pg_job_service(
    dsn: str, *, locks: ProjectLockRegistry | None = None, trigger: JobTrigger | None = None
) -> JobService:
    """组装 PG-backed job service (读写仓各持 engine, 同库 jobs 表)。缺 psycopg → 构造抛 ImportError。"""
    from codev_platform.web.repositories.job_read_repo import PgJobReadRepo
    from codev_platform.web.repositories.job_write_repo import PgJobWriteRepo
    return JobService(
        read_repo=PgJobReadRepo(dsn),
        write_repo=PgJobWriteRepo(dsn),
        locks=locks,
        trigger=trigger,
    )


def bind_job_service(
    cfg: dict | None = None, *, locks: ProjectLockRegistry | None = None,
    trigger: JobTrigger | None = None,
) -> JobService:
    """按 config 选 job 存储后端 —— memory.pg_dsn + psycopg 可用 → PG, 否则内存 (优雅回退,
    复刻 bind_account_stores)。prod 配 PG 却缺 psycopg / 初始化失败 → fail-fast, 不静默降级
    (防运维以为用 PG 实际走内存、重启即丢 job 历史)。codex P2 决策项: job 历史持久 + 多 worker 一致。"""
    from codev_platform.core.config import get as _cfg_get
    dsn = _cfg_get(cfg or {}, "memory.pg_dsn", None)
    if not dsn:
        return build_in_memory_job_service(locks=locks, trigger=trigger)
    try:
        return build_pg_job_service(dsn, locks=locks, trigger=trigger)
    except Exception:  # noqa: BLE001 — ImportError(缺 psycopg) 或 engine 初始化失败
        mode = _cfg_get(cfg or {}, "deployment.mode", "dev")
        if mode == "prod":
            raise  # prod 配 PG 却失败 → fail-fast(与 bind_account_stores 同策略)
        return build_in_memory_job_service(locks=locks, trigger=trigger)
