"""Job 读仓储 (plan §十一 CQRS 读侧)。

接口 (Protocol) + 内存实现 (InMemoryJobReadRepo, 便于测试)。
PG 实现 (PgJobReadRepo) 留待 Phase 后续 —— 对齐已落地的 *_store_pg.py (plan D5)。
内存实现与写仓共享同一 dict (由 service 注入同一 store), 读写一致。
"""
from __future__ import annotations

from typing import Protocol, runtime_checkable

from codev_platform.web.domain.job import Job


@runtime_checkable
class JobReadRepository(Protocol):
    def get(self, job_id: str) -> Job | None:
        """按 id 取 job; 不存在返回 None。"""
        ...

    def list(self, project_id: str | None = None) -> list[Job]:
        """列 job; project_id 给定则只返回该项目的 (jobs list 按当前项目过滤)。"""
        ...


class InMemoryJobReadRepo:
    """内存读实现。store 是 job_id → Job 的 dict (与写仓共享)。"""

    def __init__(self, store: dict[str, Job]) -> None:
        self._store = store

    def get(self, job_id: str) -> Job | None:
        return self._store.get(job_id)

    def list(self, project_id: str | None = None) -> list[Job]:
        jobs = list(self._store.values())
        if project_id is not None:
            jobs = [j for j in jobs if j.project_id == project_id]
        return jobs


# TODO(PG): class PgJobReadRepo —— 走 psycopg 读 jobs 表 (与 memory_store_pg 同库, plan D5)。
