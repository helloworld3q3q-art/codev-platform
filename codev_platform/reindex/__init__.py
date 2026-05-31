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

from pathlib import Path

from codev_platform.reindex.queue import FileSpoolQueue, Job, JobQueue
from codev_platform.reindex.worker import ReindexWorker

__all__ = ["FileSpoolQueue", "Job", "JobQueue", "ReindexWorker", "open_default_queue", "spool_dir"]


def spool_dir() -> Path:
    """队列落盘目录: data_root()/reindex_queue (与 chroma / cross_link 数据同源 data/)。"""
    from codev_platform.core.paths import data_root
    return data_root() / "reindex_queue"


def open_default_queue() -> FileSpoolQueue:
    """单机默认队列 (file spool)。多机共享时此工厂改返 PgQueue, 上层不动。"""
    return FileSpoolQueue(spool_dir())
