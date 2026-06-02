"""Job 写仓储 (plan §十一 CQRS 写侧)。

接口 (Protocol) + 内存实现 (InMemoryJobWriteRepo)。create 落库, save 用状态流转后的
新 Job 快照覆盖 (Job 不可变, 状态机在 domain.job)。PG 实现留待后续 (plan D5)。

读写仓共享同一 dict store → 单进程内读写一致, 便于测试。
"""
from __future__ import annotations

from typing import Protocol, runtime_checkable

from codev_platform.web.domain.job import Job


@runtime_checkable
class JobWriteRepository(Protocol):
    def create(self, job: Job) -> Job:
        """落库一个新 job, 返回它。"""
        ...

    def save(self, job: Job) -> Job:
        """覆盖已存在 job (状态流转后的新快照)。"""
        ...


class InMemoryJobWriteRepo:
    """内存写实现。store 是 job_id → Job 的 dict (与读仓共享)。"""

    def __init__(self, store: dict[str, Job]) -> None:
        self._store = store

    def create(self, job: Job) -> Job:
        self._store[job.job_id] = job
        return job

    def save(self, job: Job) -> Job:
        self._store[job.job_id] = job
        return job


# TODO(PG): class PgJobWriteRepo —— INSERT/UPDATE jobs 表 (plan D5; 走平台既有 PG 连接)。


def new_in_memory_store() -> dict[str, Job]:
    """建一个读写仓共享的内存 store。"""
    return {}
