"""PgJobQueue —— JobQueue 的 PostgreSQL 实现(多机共享 reindex 队列)。

替代单机 FileSpoolQueue。语义对齐(worker 零改, 只依赖 JobQueue Protocol):
- enqueue: UPSERT ON CONFLICT(project_id,kind) bump enqueued_at + 清 claim_token —— 同 key 合并。
- pending: 一条 SQL 用 **FOR UPDATE SKIP LOCKED** 原子认领 pending / 租约过期的 job, 写 lease
  (claimed_by + lease_expires_at)+ 给每行打**唯一 claim_token**(gen_random_uuid)→ 跨机多 worker
  不会抢到同一 (project,kind); 返回 Job 带 token。按 runner 注册依赖序排序。
- complete: 按 **claim_token 精确匹配自己那次认领** DELETE —— lease 接管后旧 worker 的 token 与
  新 worker 不同, 旧 complete 删不掉新 worker 在跑的行(修审计 P0); 运行期被重新 enqueue 会清
  token, 旧 complete 也删不中 → 保留(dirty 重入)。不靠墙钟 float 比较(修审计 dirty-tie edge)。
- peek: 只读 SELECT(不认领), status/诊断用(修审计 P1: status 误用 pending 锁全表 30min)。
- watch: poll(复用 worker 周期兜底)。

连接复用 memory 全栈 PG 范式(psycopg_pool ConnectionPool, open=False lazy)。pool timeout 设短,
配合 open_default_queue 的探活 → PG 不可达时 fail-soft 回退 file(不让 hook 卡死/丢 enqueue)。
`table` 参数仅供测试隔离; 表名经 _TABLE_RE 校验后内插(代码控制标识符, 非用户输入)。
"""
from __future__ import annotations

import logging
import re
import socket
import time

from codev_platform.reindex.queue import Job

logger = logging.getLogger(__name__)

_DEFAULT_LEASE_TTL_SEC = 1800   # 认领租约: worker 崩 → 租约过期后另一 worker 接管(幂等重跑)
_POOL_TIMEOUT_SEC = 5           # 取连接等待上限(短)—— PG 抖动时 enqueue 不卡满 30s(配合 fail-soft)
_TABLE_RE = re.compile(r"^[a-z_][a-z0-9_]*$")


def _kind_rank() -> dict[str, int]:
    """runner 注册序 = 依赖序(codegraph 先于下游 ingest/code_vec), 与 FileSpoolQueue tie-break 一致。"""
    from codev_platform.reindex.runners import kinds as _kinds
    return {k: i for i, k in enumerate(_kinds())}


class PgJobQueue:
    """JobQueue 的 PG 实现。缺 psycopg_pool → 构造期 ImportError(调用方回退 FileSpoolQueue)。"""

    def __init__(self, dsn: str, *, lease_ttl_sec: int = _DEFAULT_LEASE_TTL_SEC,
                 max_size: int = 4, table: str = "reindex_jobs", owner: str | None = None) -> None:
        from psycopg_pool import ConnectionPool  # 缺 → ImportError, 调用方回退
        if not _TABLE_RE.match(table):
            raise ValueError(f"非法表名: {table!r}")
        self._t = table
        # timeout=短: 取连接(含首次建连)等待上限, PG 不可达时快速失败而非卡 30s。
        self._pool = ConnectionPool(dsn, min_size=1, max_size=max_size, open=False,
                                    timeout=_POOL_TIMEOUT_SEC)
        self._lease_ttl = lease_ttl_sec
        # owner = **机器级稳定标识**(默认 hostname, 跨重启不变)。**不含 pid** —— 含 pid 则真实崩溃
        # 重启=新 pid=新 owner → reclaim_stale_own 匹配不到崩前(旧 pid)留下的行, 崩溃恢复 no-op
        # (审计 P0 实证)。host 级让"同机重启的 worker 接管自己崩前的行", 跨机不同 hostname 不误伤。
        # **前提: 单机单 worker 实例**(ReindexWorker 设计)。若未来同机起多 worker, 须注入唯一稳定
        # worker_id(非 pid, 重启不变), 否则同机两 worker 会互相复位对方在跑的行。owner 参数供测试
        # 模拟"重启(同 host)"与"另一台机(不同 host)"。
        self._owner = owner or socket.gethostname()
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
                "  claimed_by TEXT, lease_expires_at DOUBLE PRECISION, claim_token TEXT, "
                "  PRIMARY KEY (project_id, kind))"
            )
            # 旧表升级: claim_token 列可能不存在(0004 前建的)→ 幂等补列。
            conn.execute(f"ALTER TABLE {self._t} ADD COLUMN IF NOT EXISTS claim_token TEXT")
            conn.execute(
                f"CREATE INDEX IF NOT EXISTS ix_{self._t}_claim ON {self._t} (status, enqueued_at)"
            )
        self._opened = True

    def close(self) -> None:
        """显式关连接池(测试 / 短命实例)。常驻 worker 进程不需调(随进程退出)。
        显式关避免 psycopg_pool 在解释器 atexit 时自 join worker 线程报错。"""
        if self._opened:
            try:
                self._pool.close()
            except Exception:  # noqa: BLE001
                pass
            self._opened = False

    def probe(self) -> None:
        """轻量探活(open_default_queue 用): 触发建连 + 建表; PG 不可达则在 _POOL_TIMEOUT_SEC 内抛。"""
        self._ensure()
        with self._pool.connection() as conn:
            conn.execute("SELECT 1")

    # ---- 生产者 ----

    def enqueue(self, project_id: str, kind: str) -> None:
        from codev_platform.reindex.queue import FileSpoolQueue
        pid, kind = FileSpoolQueue._validate(project_id, kind)   # 复用: pid slug + kind 白名单/防穿越
        self._ensure()
        with self._pool.connection() as conn:
            conn.execute(
                f"INSERT INTO {self._t} (project_id, kind, enqueued_at, status, claimed_by, "
                "  lease_expires_at, claim_token) VALUES (%s, %s, %s, 'pending', NULL, NULL, NULL) "
                "ON CONFLICT (project_id, kind) DO UPDATE SET "
                "  enqueued_at = EXCLUDED.enqueued_at, status = 'pending', "
                "  claimed_by = NULL, lease_expires_at = NULL, claim_token = NULL",
                (pid, kind, time.time()),
            )

    # ---- 消费者(worker)----

    def pending(self, projects: set[str] | None = None) -> list[Job]:
        """原子认领: 每行打唯一 claim_token, 返回 Job 带 token(complete 据此精确删自己那次认领)。

        projects: None=认领全表所有 project(兼容现状); 传集合=只认领其中 project 的 job ——
        多机/多 org 亲和: worker 只该认领自己 config 配了 repo_path 的 project, 否则会认领
        别机/别 org 的 job 再因本地无 repo_path 而 complete() **删掉**它(吃掉别人的 reindex)。
        空集合 → 不认领任何 job(本 worker 无可处理 project)。"""
        self._ensure()
        now = time.time()
        # projects 为集合时加 project_id = ANY(%s) 过滤; %s 走参数化绑定(非内插)无注入面。
        proj_filter = ""
        proj_params: tuple = ()
        if projects is not None:
            if not projects:
                return []   # 空白名单: 无可处理 project, 不认领(避免 ANY 空数组语义歧义)
            proj_filter = "  AND project_id = ANY(%s) "
            proj_params = (list(projects),)
        with self._pool.connection() as conn:
            rows = conn.execute(
                f"UPDATE {self._t} SET status = 'running', claimed_by = %s, lease_expires_at = %s, "
                # per-row 唯一 token: random()+clock_timestamp() volatile 逐行求值(不用 gen_random_uuid,
                # 免 PG13+ 依赖); md5 hex。每行各得一个, complete 据此只删自己那次认领。
                "  claim_token = md5(random()::text || clock_timestamp()::text) "
                "WHERE (project_id, kind) IN ("
                f"  SELECT project_id, kind FROM {self._t} "
                "  WHERE (status = 'pending' OR (status = 'running' AND lease_expires_at < %s)) "
                f"{proj_filter}"
                "  ORDER BY enqueued_at "
                "  FOR UPDATE SKIP LOCKED"
                ") "
                "RETURNING project_id, kind, enqueued_at, claim_token",
                (self._owner, now + self._lease_ttl, now, *proj_params),
            ).fetchall()
        rank = _kind_rank()
        unknown = len(rank)
        jobs = [Job(r[0], r[1], r[2], token=r[3]) for r in rows]
        # 依赖序 tie-break(codegraph 先于 code_vec); SQL 已按 enqueued_at FIFO, 这里稳定细排同时刻批。
        jobs.sort(key=lambda j: (j.enqueued_at, rank.get(j.kind, unknown)))
        return jobs

    def peek(self) -> list[Job]:
        """只读列出待办(不认领, 无副作用)。status/诊断用。"""
        self._ensure()
        now = time.time()
        with self._pool.connection() as conn:
            rows = conn.execute(
                f"SELECT project_id, kind, enqueued_at FROM {self._t} "
                "WHERE status = 'pending' OR (status = 'running' AND lease_expires_at < %s) "
                "ORDER BY enqueued_at",
                (now,),
            ).fetchall()
        return [Job(r[0], r[1], r[2]) for r in rows]

    def complete(self, job: Job) -> bool:
        """按 claim_token 精确匹配删自己那次认领。token 不匹配(lease 接管 / 运行期被重新 enqueue
        清了 token)→ DELETE 不命中返 False(保留, dirty 重入 / 不误删他人在跑行)。"""
        self._ensure()
        with self._pool.connection() as conn:
            cur = conn.execute(
                f"DELETE FROM {self._t} WHERE project_id = %s AND kind = %s AND claim_token = %s",
                (job.project_id, job.kind, job.token),
            )
            return cur.rowcount > 0

    def reclaim_stale_own(self) -> int:
        """崩溃恢复: 把**本机**(owner=hostname)崩前留下的 status='running' 行复位 pending, 重启即
        接管, 不必干等 lease(默认 1800s)过期。owner 是机器级稳定标识(非 pid)→ systemd 重启后
        新进程 owner 仍 = 本 hostname, 能匹配到崩前的行(若用 host:pid 则重启 owner 变, 匹配 0 行,
        恢复 no-op —— 审计 P0)。跨机不同 hostname 的 running 行不被复位(无误伤)。复位清 lease/token,
        下轮 pending() 重领。返回复位行数。

        前提: **单机单 worker 实例**(ReindexWorker 设计)→ 启动时本机不存在自己另一活跃认领, host 级
        复位安全。若未来同机起多 worker, 须改注入唯一稳定 worker_id 作 owner。"""
        self._ensure()
        with self._pool.connection() as conn:
            cur = conn.execute(
                f"UPDATE {self._t} SET status = 'pending', claimed_by = NULL, "
                "  lease_expires_at = NULL, claim_token = NULL "
                "WHERE status = 'running' AND claimed_by = %s",
                (self._owner,),
            )
            return cur.rowcount

    async def watch(self):
        """poll: 周期 yield 让 worker drain。LISTEN/NOTIFY 留后(worker 自带周期兜底, poll 足够)。"""
        import asyncio
        while True:
            await asyncio.sleep(5.0)
            yield None
