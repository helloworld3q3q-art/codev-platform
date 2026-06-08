"""Job 读仓储 (plan §十一 CQRS 读侧)。

接口 (Protocol) + 内存实现 (InMemoryJobReadRepo, 便于测试)。
PG 实现 (PgJobReadRepo) 留待 Phase 后续 —— 对齐已落地的 *_store_pg.py (plan D5)。
内存实现与写仓共享同一 dict (由 service 注入同一 store), 读写一致。
"""
from __future__ import annotations

from typing import Protocol, runtime_checkable

from sqlalchemy import select

from codev_platform.web.db import tables
from codev_platform.web.domain.job import Job
from codev_platform.web.repositories.account_store_pg import _PgBase


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


def _row_to_job(row) -> Job:
    return Job(job_id=row[0], project_id=row[1], job_type=row[2], status=row[3],
               created_at=row[4], updated_at=row[5], error=row[6])


class PgJobReadRepo(_PgBase):
    """PG 读实现 —— jobs 表 select。PG 基座复用 account_store_pg._PgBase(同库/同 engine 范式,
    sqlite 单测注入 engine exercise; 未来可抽 _pg_base 共享)。codex P2: job 历史持久 + 多 worker 一致。"""

    def get(self, job_id: str) -> Job | None:
        self._ensure()
        j = tables.jobs
        with self._engine.connect() as conn:
            row = conn.execute(
                select(j.c.job_id, j.c.project_id, j.c.job_type, j.c.status,
                       j.c.created_at, j.c.updated_at, j.c.error).where(j.c.job_id == job_id)
            ).first()
        return _row_to_job(row) if row else None

    def list(self, project_id: str | None = None) -> list[Job]:
        self._ensure()
        j = tables.jobs
        stmt = select(j.c.job_id, j.c.project_id, j.c.job_type, j.c.status,
                      j.c.created_at, j.c.updated_at, j.c.error)
        if project_id is not None:
            stmt = stmt.where(j.c.project_id == project_id)
        with self._engine.connect() as conn:
            rows = conn.execute(stmt).all()
        return [_row_to_job(r) for r in rows]
