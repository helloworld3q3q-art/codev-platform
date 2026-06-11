"""PgJobQueue —— JobQueue 的 PostgreSQL 实现(多机共享 reindex 队列)。

替代单机 FileSpoolQueue。语义对齐(worker 零改, 只依赖 JobQueue Protocol):
- enqueue: UPSERT ON CONFLICT(project_id,kind) bump enqueued_at —— 同 key 合并(密集触发只跑最新态)。
- pending: 一条 SQL 用 **FOR UPDATE SKIP LOCKED** 原子认领 pending / 租约过期的 job, 写 lease
  (claimed_by + lease_expires_at)→ 跨机多 worker 不会抢到同一 (project,kind)。返回前按 runner
  注册依赖序(codegraph 先于 code_vec, 与 FileSpoolQueue 同 tie-break)排序。
- complete: enqueued_at **行版本**比对 —— 运行期被重新 enqueue(enqueued_at 变新)则 DELETE 不命中
  → 保留(回 pending 下轮重跑, dirty 重入不丢尾); 否则删除。跨机不靠 mtime/时钟。
- watch: poll(复用 worker 周期兜底); LISTEN/NOTIFY 留后。

连接复用 memory 全栈 PG 范式(psycopg_pool ConnectionPool, open=False lazy; schema 首次操作幂等建,
alembic 为权威)。lease 过期抢占替代 FileSpool 的文件锁 stale/PID-liveness —— 跨机原生。
`table` 参数仅供测试隔离(默认 reindex_jobs); 表名是代码控制的标识符(非用户输入), 校验后内插。
"""
from __future__ import annotations

import logging
import os
import re
import socket
import time

from codev_platform.reindex.queue import Job

logger = logging.getLogger(__name__)

_DEFAULT_LEASE_TTL_SEC = 1800   # 认领租约: worker 崩 → 租约过期后另一 worker 接管(幂等重跑)
_TABLE_RE = re.compile(r"^[a-z_][a-z0-9_]*$")


def _kind_rank() -> dict[str, int]:
    """runner 注册序 = 依赖序(codegraph 先于下游 ingest/code_vec), 与 FileSpoolQueue tie-break 一致。"""
    from codev_platform.reindex.runners import kinds as _kinds
    return {k: i for i, k in enumerate(_kinds())}


class PgJobQueue:
    """JobQueue 的 PG 实现。缺 psycopg_pool → 构造期 ImportError(调用方回退 FileSpoolQueue)。"""

    def __init__(self, dsn: str, read_dsn: str | None = None, *,
                 lease_ttl_sec: int = _DEFAULT_LEASE_TTL_SEC, max_size: int = 4,
                 table: str = "reindex_jobs") -> None:
        from psycopg_pool import ConnectionPool  # 缺 → ImportError, 调用方回退
        if not _TABLE_RE.match(table):
            raise ValueError(f"非法表名: {table!r}")
        self._t = table
        self._pool = ConnectionPool(dsn, min_size=1, max_size=max_size, open=False)
        self._lease_ttl = lease_ttl_sec
        self._owner = f"{socket.gethostname()}:{os.getpid()}"   # 认领者标识(诊断 + 跨机区分)
        self._opened = False

    def _ensure(self) -> None:
        if self._opened:
            return
        self._pool.open()
        with self._pool.connection() as conn:
            conn.execute(
                f"CREATE TABLE IF NOT EXISTS {self._t} ("
                "  project_id TEXT NOT NULL, kind TEXT NOT NULL, "
                "  enqueued_at DOUBLE PRECISION NOT NULL, "
                "  status TEXT NOT NULL DEFAULT 'pending', "
                "  claimed_by TEXT, lease_expires_at DOUBLE PRECISION, "
                "  PRIMARY KEY (project_id, kind))"
            )
            conn.execute(
                f"CREATE INDEX IF NOT EXISTS ix_{self._t}_claim ON {self._t} (status, enqueued_at)"
            )
        self._opened = True

    # ---- 生产者 ----

    def enqueue(self, project_id: str, kind: str) -> None:
        from codev_platform.reindex.queue import FileSpoolQueue
        pid, kind = FileSpoolQueue._validate(project_id, kind)   # 复用: pid slug + kind 白名单/防穿越
        self._ensure()
        with self._pool.connection() as conn:
            conn.execute(
                f"INSERT INTO {self._t} (project_id, kind, enqueued_at, status, claimed_by, lease_expires_at) "
                "VALUES (%s, %s, %s, 'pending', NULL, NULL) "
                "ON CONFLICT (project_id, kind) DO UPDATE SET "
                "  enqueued_at = EXCLUDED.enqueued_at, status = 'pending', "
                "  claimed_by = NULL, lease_expires_at = NULL",
                (pid, kind, time.time()),
            )

    # ---- 消费者(worker)----

    def pending(self) -> list[Job]:
        self._ensure()
        now = time.time()
        with self._pool.connection() as conn:
            rows = conn.execute(
                f"UPDATE {self._t} SET status = 'running', claimed_by = %s, lease_expires_at = %s "
                "WHERE (project_id, kind) IN ("
                f"  SELECT project_id, kind FROM {self._t} "
                "  WHERE status = 'pending' OR (status = 'running' AND lease_expires_at < %s) "
                "  ORDER BY enqueued_at "
                "  FOR UPDATE SKIP LOCKED"
                ") "
                "RETURNING project_id, kind, enqueued_at",
                (self._owner, now + self._lease_ttl, now),
            ).fetchall()
        rank = _kind_rank()
        unknown = len(rank)
        jobs = [Job(r[0], r[1], r[2]) for r in rows]
        # 依赖序 tie-break(codegraph 先于 code_vec); SQL 已按 enqueued_at FIFO, 这里稳定细排同时刻批。
        jobs.sort(key=lambda j: (j.enqueued_at, rank.get(j.kind, unknown)))
        return jobs

    def complete(self, job: Job) -> bool:
        """enqueued_at 行版本比对: 未被重触发(enqueued_at <= 认领值)→ 删除返 True; 运行期被重新
        enqueue(enqueued_at 变新)→ DELETE 不命中, 保留(已回 pending)返 False, 下轮重跑(dirty)。"""
        self._ensure()
        with self._pool.connection() as conn:
            cur = conn.execute(
                f"DELETE FROM {self._t} WHERE project_id = %s AND kind = %s AND enqueued_at <= %s",
                (job.project_id, job.kind, job.enqueued_at),
            )
            return cur.rowcount > 0

    async def watch(self):
        """poll: 周期 yield 让 worker drain。LISTEN/NOTIFY 留后(worker 自带周期兜底, poll 足够)。"""
        import asyncio
        while True:
            await asyncio.sleep(5.0)
            yield None
