"""chroma daemon —— GPU 推理统计 + 进程信息 (从 server.py 抽出, file-discipline §1)。

_stats 是 in-memory 累加器, daemon 重启归零 (widget 实时观测用)。**字典原地 mutate
(s["calls"] += 1), 从不 rebind** —— 故 `from ._stats import _stats` 跨模块共享同一 dict
对象, 无 stale-binding 问题。线程安全前提同原注释 (仅 asyncio 单线程操作)。
"""
from __future__ import annotations

import os
import time
from typing import Any

# 全局 stats 累加器 (跨 project 共享 GPU model, 全部 project 调用一起统计)
_stats: dict[str, dict[str, Any]] = {
    "embedding": {"calls": 0, "ms_total": 0.0, "errors": 0, "last_error": None, "load_retries": 0},
    "reranker": {"calls": 0, "ms_total": 0.0, "errors": 0, "last_error": None, "load_retries": 0},
}

# daemon 进程启动时刻 (uptime 计算)
_DAEMON_START = time.time()


def _record_stat(kind: str, elapsed_ms: float) -> None:
    """累加调用次数 + 总耗时。 kind: embedding / reranker.

    Notes:
    - _stats 是 in-memory, daemon 重启归零 (不持久化, widget 仅做实时观测用)
    - 线程安全: 仅在 asyncio event loop 单线程操作 (encode/rerank 都在 coroutine 内直调).
      若未来改 asyncio.to_thread 把 GPU 跑后台线程, 必须加 threading.Lock 保护
    """
    s = _stats.get(kind)
    if s is None:
        return
    s["calls"] += 1
    s["ms_total"] += elapsed_ms


def _record_stat_error(kind: str, exc: BaseException) -> None:
    """记录一次 GPU 推理失败 (embedding / reranker), widget 显错误率 + last_error。"""
    s = _stats.get(kind)
    if s is None:
        return
    s["errors"] = int(s.get("errors", 0)) + 1
    s["last_error"] = f"{type(exc).__name__}: {exc}"[:200]


def _process_info() -> dict[str, Any]:
    """daemon 进程 RSS / uptime / pid。psutil 缺失时 rss_mb=None (uptime/pid 仍可)。"""
    rss_mb: float | None = None
    try:
        import psutil  # type: ignore
        rss_mb = round(psutil.Process().memory_info().rss / (1024 * 1024), 1)
    except Exception:
        pass
    return {
        "pid": os.getpid(),
        "uptime_sec": int(time.time() - _DAEMON_START),
        "rss_mb": rss_mb,
    }
