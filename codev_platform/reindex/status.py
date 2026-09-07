"""Read-only reindex queue/worker status helpers."""
from __future__ import annotations

import time
from typing import Any

from codev_platform.reindex.file_queue import FileSpoolQueue
from codev_platform.reindex.owner_readiness import OwnerReadiness, inspect_owner_readiness
from codev_platform.reindex.pg_queue import PgJobQueue
from codev_platform.reindex.queue_backend_binding import queue_backend_binding
from codev_platform.reindex.runtime_owner import QueueBackendBinding, fingerprint_backend

_STALE_PENDING_SEC = 300
_STOPPED_PENDING_FAIL_SEC = 1800
_PHASE_THRESHOLDS = {
    "git_sync": (75, 180),
    "manifest": (10, 60),
    "queue_scan": (30, 120),
    "queue_complete": (30, 120),
    "health_refresh": (90, 180),
}
_RUNNER_FAIL_GRACE_SEC = 60
_SEVERITY_RANK = {"OK": 0, "WARN": 1, "FAIL": 2}


def _join_severity(*levels: str) -> str:
    best = "OK"
    for level in levels:
        if _SEVERITY_RANK.get(level, 0) > _SEVERITY_RANK[best]:
            best = level
    return best


def _age_from(ts: Any, now: float) -> int | None:
    if not isinstance(ts, int | float):
        return None
    return max(0, int(now - float(ts)))


def _heartbeat_thresholds(worker: dict[str, Any]) -> tuple[int, int]:
    heartbeat_sec = worker.get("heartbeat_sec")
    if not isinstance(heartbeat_sec, int | float) or float(heartbeat_sec) <= 0:
        heartbeat_sec = 10
    base = float(heartbeat_sec)
    return int(max(3 * base, 30)), int(max(6 * base, 120))


def _runner_thresholds() -> tuple[int, int]:
    try:
        from codev_platform.core.config import load_config
        from codev_platform.reindex import runners

        timeout = runners._runner_timeout(load_config())  # noqa: SLF001 - reuse existing config logic
    except Exception:
        timeout = 1800
    if timeout is None:
        timeout = 1800
    timeout_i = max(int(float(timeout)), 1)
    return timeout_i, timeout_i + _RUNNER_FAIL_GRACE_SEC


def _phase_thresholds(phase: str | None) -> tuple[int, int] | None:
    if not phase:
        return None
    if phase == "runner":
        return _runner_thresholds()
    return _PHASE_THRESHOLDS.get(phase)


def _phase_severity(phase: str | None, phase_age_sec: int | None) -> str:
    if phase_age_sec is None:
        return "OK"
    thresholds = _phase_thresholds(phase)
    if thresholds is None:
        return "OK"
    warn_sec, fail_sec = thresholds
    if phase_age_sec >= fail_sec:
        return "FAIL"
    if phase_age_sec >= warn_sec:
        return "WARN"
    return "OK"


def _heartbeat_severity(worker: dict[str, Any]) -> str:
    if not worker.get("running"):
        return "OK"
    age = worker.get("heartbeat_age_sec")
    if not isinstance(age, int | float):
        return "OK"
    warn_sec, fail_sec = _heartbeat_thresholds(worker)
    if age >= fail_sec:
        return "FAIL"
    if age >= warn_sec:
        return "WARN"
    return "OK"


def _stopped_pending_severity(pending_count: int, oldest_pending_age_sec: int | None) -> str:
    if pending_count <= 0:
        return "OK"
    if oldest_pending_age_sec is None:
        return "WARN"
    if oldest_pending_age_sec < _STALE_PENDING_SEC:
        return "OK"
    if oldest_pending_age_sec >= _STOPPED_PENDING_FAIL_SEC:
        return "FAIL"
    return "WARN"


def _result_brief(last_result: Any) -> str | None:
    if not isinstance(last_result, dict):
        return None
    job_key = last_result.get("job_key")
    status = last_result.get("status")
    detail = last_result.get("detail") or last_result.get("note")
    parts = [str(part) for part in (job_key, status, detail) if part]
    return ":".join(parts) if parts else None


def _current_owner_readiness(queue: Any) -> OwnerReadiness | None:
    """读取已打开的已知队列 owner 事实；无法证明时安全闭合。"""

    if not isinstance(queue, (FileSpoolQueue, PgJobQueue)):
        return None
    try:
        binding = _readonly_owner_binding(queue)
        readiness = inspect_owner_readiness(binding).status
    except (MemoryError, KeyboardInterrupt, SystemExit):
        raise
    except Exception:
        return OwnerReadiness.UNAVAILABLE
    return readiness if type(readiness) is OwnerReadiness else OwnerReadiness.UNAVAILABLE


def _readonly_owner_binding(queue: FileSpoolQueue | PgJobQueue) -> QueueBackendBinding:
    """从已打开队列读取绑定，不为 status 初始化 Pg schema。"""

    if isinstance(queue, FileSpoolQueue):
        return queue_backend_binding(queue, None)
    locator = queue.owner_binding_locator()
    return QueueBackendBinding("pg", fingerprint_backend("pg", locator))


def summarize(*, queue: Any | None = None, now: float | None = None,
              stale_pending_sec: int = _STALE_PENDING_SEC) -> dict[str, Any]:
    """Return a read-only queue + worker summary for health/status commands."""
    now = time.time() if now is None else now
    try:
        from codev_platform.reindex import open_default_queue, supervisor

        q = queue if queue is not None else open_default_queue()
        snapshot = q.snapshot()
        try:
            worker = supervisor.worker_status(now=now)
        except TypeError:
            worker = supervisor.worker_status()
    except Exception as exc:  # noqa: BLE001 - diagnostics must degrade
        return {"severity": "WARN", "error": f"{type(exc).__name__}: {exc}"}

    owner_readiness = _current_owner_readiness(q)
    pending = list(getattr(snapshot, "pending", []) or [])
    active = list(getattr(snapshot, "active", []) or [])
    results = list(getattr(snapshot, "results", []) or [])
    expired_active = list(getattr(snapshot, "expired_active", []) or [])
    pending_keys = {job.key for job in pending}
    active_keys = {job.key for job in active}
    dirty_after_active_count = len(pending_keys & active_keys)

    running = bool(worker.get("running"))
    phase = worker.get("phase") or ("idle" if running else "stopped")
    phase_age_sec = _age_from(worker.get("phase_at"), now)
    oldest_pending_age_sec = None
    if pending:
        oldest_pending_age_sec = max(0, int(now - min(job.enqueued_at for job in pending)))
    stale_count = 0
    if not running:
        stale_count = sum(1 for job in pending if (now - float(job.enqueued_at)) >= stale_pending_sec)

    heartbeat_severity = _heartbeat_severity(worker)
    phase_severity = _phase_severity(phase, phase_age_sec)
    stopped_pending_severity = _stopped_pending_severity(len(pending), oldest_pending_age_sec) if not running else "OK"
    dirty_severity = "WARN" if dirty_after_active_count else "OK"
    expired_keys = {job.key for job in expired_active}
    missing_lease_active = 0
    for job in active:
        lease_expires_at = getattr(job, "lease_expires_at", None)
        if isinstance(lease_expires_at, int | float) and float(lease_expires_at) <= now:
            expired_keys.add(job.key)
        elif not isinstance(lease_expires_at, int | float):
            missing_lease_active += 1
    real_expired_active_count = len(expired_keys)
    unhealthy_worker = not running or heartbeat_severity == "FAIL" or phase_severity == "FAIL"
    expired_active_count = real_expired_active_count
    if unhealthy_worker and missing_lease_active:
        expired_active_count += missing_lease_active
    active_severity = "FAIL" if expired_active_count else "OK"

    severity = _join_severity(
        heartbeat_severity,
        phase_severity,
        stopped_pending_severity,
        dirty_severity,
        active_severity,
    )

    if real_expired_active_count > 0 or (active and unhealthy_worker):
        recommended_action = "break-lease"
    elif not running and owner_readiness is OwnerReadiness.BOOTSTRAP_REQUIRED:
        recommended_action = "init-owner"
    elif not running and owner_readiness in {
        OwnerReadiness.RECOVERY_REQUIRED,
        OwnerReadiness.BINDING_MISMATCH,
        OwnerReadiness.UNAVAILABLE,
    }:
        recommended_action = "inspect-owner"
    elif not running and worker.get("exit_reason") in {
        "bootstrap-required",
        "owner-operator-blocked",
    }:
        recommended_action = "inspect-owner"
    elif severity == "FAIL":
        recommended_action = "prune-stale" if not running and pending else "inspect-log"
    elif dirty_after_active_count:
        recommended_action = "wait"
    elif heartbeat_severity == "WARN" or phase_severity == "WARN":
        recommended_action = "inspect-log"
    elif not running and stale_count:
        recommended_action = "prune-stale"
    else:
        recommended_action = "wait"

    return {
        "severity": severity,
        "worker": worker,
        "running": running,
        "queue_backend": type(q).__name__,
        "owner_readiness": (
            owner_readiness.value if owner_readiness is not None else None
        ),
        "pending_count": len(pending),
        "active_count": len(active),
        "results_count": len(results),
        "dirty_after_active_count": dirty_after_active_count,
        "expired_active_count": expired_active_count,
        "oldest_pending_age_sec": oldest_pending_age_sec,
        "stale_count": stale_count,
        "phase": phase,
        "phase_age_sec": phase_age_sec,
        "active_job": worker.get("active_job"),
        "last_result": worker.get("last_result"),
        "heartbeat_severity": heartbeat_severity,
        "phase_severity": phase_severity,
        "recommended_action": recommended_action,
    }


def format_summary(summary: dict[str, Any]) -> str:
    if summary.get("error"):
        return f"probe failed: {summary['error']}"
    worker = summary.get("worker") or {}
    bits = []
    if summary.get("running"):
        bits.append(f"worker=running pid={worker.get('pid')}")
        bits.append(f"mode={worker.get('mode') or '?'}")
        if worker.get("heartbeat_age_sec") is not None:
            bits.append(f"heartbeat_age={worker.get('heartbeat_age_sec')}s")
    else:
        bits.append("worker=stopped")
        if worker.get("pid"):
            bits.append(f"last_pid={worker.get('pid')}")
        if worker.get("exit_reason"):
            bits.append(f"exit={worker.get('exit_reason')}")
    if summary.get("phase"):
        bits.append(f"phase={summary.get('phase')}")
    if summary.get("phase_age_sec") is not None:
        bits.append(f"phase_age={summary.get('phase_age_sec')}s")
    if summary.get("active_job"):
        bits.append(f"active_job={summary.get('active_job')}")
    last_result = _result_brief(summary.get("last_result"))
    if last_result:
        bits.append(f"last_result={last_result}")
    bits.append(f"queue={summary.get('queue_backend') or '?'}")
    bits.append(f"pending={summary.get('pending_count', 0)}")
    bits.append(f"active={summary.get('active_count', 0)}")
    bits.append(f"results={summary.get('results_count', 0)}")
    if summary.get("oldest_pending_age_sec") is not None:
        bits.append(f"oldest={summary.get('oldest_pending_age_sec')}s")
    if summary.get("stale_count"):
        bits.append(f"stale={summary.get('stale_count')}")
    if summary.get("dirty_after_active_count"):
        bits.append(f"dirty_after_active={summary.get('dirty_after_active_count')}")
    if summary.get("expired_active_count"):
        bits.append(f"expired_active={summary.get('expired_active_count')}")
    bits.append(f"action={summary.get('recommended_action') or 'wait'}")
    return " ".join(bits)
