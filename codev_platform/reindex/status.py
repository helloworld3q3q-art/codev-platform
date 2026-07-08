"""Read-only reindex queue/worker status helpers."""
from __future__ import annotations

import time
from typing import Any

_STALE_PENDING_SEC = 300


def summarize(*, queue: Any | None = None, now: float | None = None,
              stale_pending_sec: int = _STALE_PENDING_SEC) -> dict[str, Any]:
    """Return a read-only queue + worker summary for health/status commands."""
    now = time.time() if now is None else now
    try:
        from codev_platform.reindex import open_default_queue, supervisor

        q = queue if queue is not None else open_default_queue()
        jobs = q.peek()
        worker = supervisor.worker_status(now=now)
    except Exception as exc:  # noqa: BLE001 - diagnostics must degrade
        return {"severity": "WARN", "error": f"{type(exc).__name__}: {exc}"}

    running = bool(worker.get("running"))
    enqueued = [j.enqueued_at for j in jobs if getattr(j, "enqueued_at", None) is not None]
    oldest = max(0, int(now - min(enqueued))) if enqueued else None
    stale_count = 0
    if not running:
        stale_count = sum(
            1 for j in jobs
            if getattr(j, "enqueued_at", None) is not None
            and now - float(j.enqueued_at) >= stale_pending_sec
        )
    severity = "WARN" if jobs and not running else "OK"
    return {
        "severity": severity,
        "worker": worker,
        "running": running,
        "pending_count": len(jobs),
        "oldest_pending_age_sec": oldest,
        "stale_count": stale_count,
        "queue_backend": type(q).__name__,
    }


def format_summary(summary: dict[str, Any]) -> str:
    if summary.get("error"):
        return f"probe failed: {summary['error']}"
    worker = summary.get("worker") or {}
    if summary.get("running"):
        bits = [
            f"worker=running pid={worker.get('pid')}",
            f"mode={worker.get('mode') or '?'}",
        ]
        if worker.get("heartbeat_age_sec") is not None:
            bits.append(f"heartbeat_age={worker.get('heartbeat_age_sec')}s")
        if worker.get("last_job"):
            bits.append(f"last_job={worker.get('last_job')}:{worker.get('last_job_status') or '?'}")
    else:
        bits = ["worker=stopped"]
        if worker.get("pid"):
            bits.append(f"last_pid={worker.get('pid')}")
        if worker.get("exit_reason"):
            bits.append(f"exit={worker.get('exit_reason')}")
    bits.append(f"queue={summary.get('queue_backend') or '?'}")
    bits.append(f"pending={summary.get('pending_count', 0)}")
    if summary.get("oldest_pending_age_sec") is not None:
        bits.append(f"oldest={summary.get('oldest_pending_age_sec')}s")
    if summary.get("stale_count"):
        bits.append(f"stale={summary.get('stale_count')}")
    return " ".join(bits)
