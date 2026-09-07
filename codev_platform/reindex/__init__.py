"""reindex 任务队列 —— 写侧串行化 (读并发不变)。

分层 (依赖单向, 互不耦合):
  queue.py   持久层  JobQueue 协议 + FileSpoolQueue (可换 PgQueue 多机共享)
  runners.py 执行层  ReindexRunner 协议 + registry (加索引类型 = 加一个 runner)
  worker.py  编排层  ReindexWorker: 组合 queue + runners, watch→合并→串行→complete

生产者 (hook / webhook / CLI) 只 enqueue 即返回; worker 是唯一消费者 (串行满足 SQLite 单写者,
合并密集触发, dirty 重入不丢尾部提交)。详见
docs/plans/roadmap-2026-05-29/dual-instance-codeindex-2026-05-30.md 写侧队列设计。
"""
from __future__ import annotations

from importlib import import_module
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from codev_platform.reindex.queue import JobQueue

_QUEUE_EXPORTS = {"FileSpoolQueue", "Job", "JobMeta", "JobQueue", "QueueSnapshot"}
_SUBMODULE_EXPORTS = {
    "attempts",
    "executor",
    "git_sync",
    "index_status_client",
    "pg_queue_sql",
    "queue",
    "runner_logs",
    "runners",
    "status",
    "supervisor",
    "worker",
}

__all__ = [
    "FileSpoolQueue",
    "Job",
    "JobMeta",
    "JobQueue",
    "QueueSnapshot",
    "ReindexWorker",
    "open_default_queue",
    "spool_dir",
]


def __getattr__(name: str):
    """兼容旧 façade，同时避免 executor 导入时提前加载 queue/worker。"""
    if name in _QUEUE_EXPORTS:
        value = getattr(import_module("codev_platform.reindex.queue"), name)
    elif name == "ReindexWorker":
        value = getattr(import_module("codev_platform.reindex.worker"), name)
    elif name in _SUBMODULE_EXPORTS:
        value = import_module(f"codev_platform.reindex.{name}")
    else:
        raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
    globals()[name] = value
    return value


def spool_dir() -> Path:
    """队列落盘目录: data_root()/reindex_queue (与 chroma / graph 数据同源 data/)。"""
    from codev_platform.core.paths import data_root
    return data_root() / "reindex_queue"


def open_default_queue(*, fail_soft: bool = True) -> JobQueue:
    """按 config 选队列后端, 上层(worker / 生产者)只依赖 JobQueue Protocol 不动。

    `reindex.queue_backend`: "file"(默认, 单机 FileSpoolQueue)| "pg"(多机共享 PgJobQueue)。
    pg 需 memory.pg_dsn(env CODEV_PLATFORM_MEMORY_DSN)+ psycopg_pool; 缺任一 → 告警回退 file
    (fail-soft: 配错不至于让本地 hook 整个瘫掉)。webhook 这类远端生产者可传
    fail_soft=False: PG 配置不可用时直接抛错, 避免 HTTP 200 但共享 worker 看不到任务。
    """
    from codev_platform.core.config import get as _get
    from codev_platform.core.config import load_config
    from codev_platform.reindex.producer_route import (
        effective_queue_backend,
        require_local_hook_queue_route,
    )
    from codev_platform.reindex.queue import FileSpoolQueue
    cfg = load_config()
    backend = effective_queue_backend(cfg)
    if backend == "pg":
        import os as _os
        dsn = _os.environ.get("CODEV_PLATFORM_MEMORY_DSN") or _get(cfg, "memory.pg_dsn")
        if not dsn:
            msg = "queue_backend=pg 但 memory.pg_dsn / CODEV_PLATFORM_MEMORY_DSN 未配"
            if not fail_soft:
                raise RuntimeError(msg)
            print(f"[reindex] {msg}, 回退 file", flush=True)
        else:
            try:
                from codev_platform.reindex.pg_queue import PgJobQueue
                q = PgJobQueue(dsn)
                q.probe()   # 探活(短超时): PG 不可达此处即失败 → 回退 file, 不让生产者首次 enqueue 卡死/丢
                return q
            except Exception as exc:  # noqa: BLE001 — psycopg 缺 / 连不上 / 构造失败 → 回退 file 不瘫
                error_kind = type(exc).__name__
                if not fail_soft:
                    raise RuntimeError(f"PgJobQueue 不可用: {error_kind}") from None
                print(f"[reindex] PgJobQueue 不可用({error_kind}), 回退 file spool", flush=True)
    root = spool_dir()
    require_local_hook_queue_route(
        cfg,
        queue_root=root,
        selected_backend="file",
    )
    return FileSpoolQueue(root)
