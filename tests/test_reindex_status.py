from __future__ import annotations

from codev_platform.reindex.queue import Job
from codev_platform.reindex.status import format_summary, summarize


class _Queue:
    def __init__(self, jobs):
        self._jobs = list(jobs)

    def peek(self):
        return list(self._jobs)


def test_reindex_status_ok_when_queue_empty_and_worker_stopped(monkeypatch):
    monkeypatch.setattr("codev_platform.reindex.supervisor.worker_status",
                        lambda now=None: {"running": False, "pid": None, "exit_reason": "idle"})

    summary = summarize(queue=_Queue([]), now=1000)

    assert summary["severity"] == "OK"
    assert summary["pending_count"] == 0
    assert "worker=stopped" in format_summary(summary)


def test_reindex_status_warns_when_pending_without_worker(monkeypatch):
    monkeypatch.setattr("codev_platform.reindex.supervisor.worker_status",
                        lambda now=None: {"running": False, "pid": None})

    summary = summarize(queue=_Queue([Job("demo-proj", "chroma", 100)]), now=1000)

    assert summary["severity"] == "WARN"
    assert summary["pending_count"] == 1
    assert summary["stale_count"] == 1
    line = format_summary(summary)
    assert "pending=1" in line and "stale=1" in line


def test_reindex_status_ok_when_pending_and_worker_running(monkeypatch):
    monkeypatch.setattr(
        "codev_platform.reindex.supervisor.worker_status",
        lambda now=None: {
            "running": True,
            "pid": 123,
            "mode": "short",
            "heartbeat_age_sec": 1,
            "last_job": "demo-proj__chroma",
            "last_job_status": "running",
        },
    )

    summary = summarize(queue=_Queue([Job("demo-proj", "chroma", 100)]), now=1000)

    assert summary["severity"] == "OK"
    assert summary["stale_count"] == 0
    assert "worker=running pid=123" in format_summary(summary)
