from __future__ import annotations

import pytest

from codev_platform.reindex.queue import Job
from codev_platform.reindex.status import format_summary, summarize

from tests import reindex_status_support as support

def test_reindex_status_ok_when_queue_empty_and_worker_stopped(monkeypatch):
    monkeypatch.setattr("codev_platform.reindex.supervisor.worker_status",
                        lambda now=None: {"running": False, "pid": None, "exit_reason": "idle"})

    summary = summarize(queue=support._Queue(), now=1000)

    assert summary["severity"] == "OK"
    assert summary["pending_count"] == 0
    assert "worker=stopped" in format_summary(summary)


def test_reindex_status_warns_when_pending_without_worker(monkeypatch):
    monkeypatch.setattr("codev_platform.reindex.supervisor.worker_status",
                        lambda now=None: {"running": False, "pid": None})

    summary = summarize(queue=support._Queue(pending=[Job("demo-proj", "chroma", 100)]), now=1000)

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

    summary = summarize(queue=support._Queue(pending=[Job("demo-proj", "chroma", 100)]), now=1000)

    assert summary["severity"] == "OK"
    assert summary["stale_count"] == 0
    assert "worker=running pid=123" in format_summary(summary)


def test_reindex_status_warns_when_git_sync_phase_stale(monkeypatch):
    monkeypatch.setattr(
        "codev_platform.reindex.supervisor.worker_status",
        lambda now=None: {
            "running": True,
            "pid": 123,
            "mode": "short",
            "heartbeat_sec": 10,
            "heartbeat_age_sec": 1,
            "phase": "git_sync",
            "phase_at": 920,
            "active_job": "demo-proj__chroma",
        },
    )

    summary = summarize(queue=support._Queue(active=[Job("demo-proj", "chroma", 900, token="t1")]), now=1000)

    assert summary["severity"] == "WARN"
    assert summary["phase"] == "git_sync"
    assert summary["phase_age_sec"] == 80


def test_reindex_status_fails_when_git_sync_phase_exceeds_fail_threshold(monkeypatch):
    monkeypatch.setattr(
        "codev_platform.reindex.supervisor.worker_status",
        lambda now=None: {
            "running": True,
            "pid": 123,
            "mode": "short",
            "heartbeat_sec": 10,
            "heartbeat_age_sec": 1,
            "phase": "git_sync",
            "phase_at": 810,
            "active_job": "demo-proj__chroma",
        },
    )

    summary = summarize(queue=support._Queue(active=[Job("demo-proj", "chroma", 800, token="t1")]), now=1000)

    assert summary["severity"] == "FAIL"
    assert summary["recommended_action"] == "break-lease"


def test_reindex_status_warns_when_heartbeat_stale(monkeypatch):
    monkeypatch.setattr(
        "codev_platform.reindex.supervisor.worker_status",
        lambda now=None: {
            "running": True,
            "pid": 123,
            "mode": "short",
            "heartbeat_sec": 10,
            "heartbeat_age_sec": 35,
            "phase": "runner",
            "phase_at": 990,
        },
    )

    summary = summarize(queue=support._Queue(active=[Job("demo-proj", "chroma", 900, token="t1")]), now=1000)

    assert summary["severity"] == "WARN"
    assert summary["recommended_action"] == "inspect-log"


def test_reindex_status_fails_when_heartbeat_stale_for_active_job(monkeypatch):
    monkeypatch.setattr(
        "codev_platform.reindex.supervisor.worker_status",
        lambda now=None: {
            "running": True,
            "pid": 123,
            "mode": "short",
            "heartbeat_sec": 10,
            "heartbeat_age_sec": 130,
            "phase": "runner",
            "phase_at": 990,
            "active_job": "demo-proj__chroma",
        },
    )

    summary = summarize(queue=support._Queue(active=[Job("demo-proj", "chroma", 900, token="t1")]), now=1000)

    assert summary["severity"] == "FAIL"
    assert summary["expired_active_count"] == 1
    assert summary["recommended_action"] == "break-lease"


def test_reindex_status_uses_real_lease_expiry_even_when_worker_still_running(monkeypatch):
    monkeypatch.setattr(
        "codev_platform.reindex.supervisor.worker_status",
        lambda now=None: {
            "running": True,
            "pid": 123,
            "mode": "short",
            "heartbeat_sec": 10,
            "heartbeat_age_sec": 1,
            "phase": "runner",
            "phase_at": 995,
            "active_job": "demo-proj__chroma",
        },
    )

    summary = summarize(
        queue=support._Queue(
            expired_active=[Job("demo-proj", "chroma", 900, token="t1", lease_expires_at=990)],
        ),
        now=1000,
    )

    assert summary["severity"] == "FAIL"
    assert summary["expired_active_count"] == 1
    assert summary["recommended_action"] == "break-lease"


@pytest.mark.parametrize(
    ("worker_status", "now"),
    [
        (
            {"running": False, "pid": None, "exit_reason": "stopped", "phase": "stopped", "phase_at": 0},
            1000,
        ),
        (
            {
                "running": True,
                "pid": 123,
                "mode": "short",
                "heartbeat_sec": 10,
                "heartbeat_age_sec": 130,
                "phase": "runner",
                "phase_at": 990,
                "active_job": "demo-proj__chroma",
            },
            1000,
        ),
    ],
)
def test_reindex_status_does_not_expire_future_lease_when_worker_is_unhealthy(monkeypatch, worker_status, now):
    monkeypatch.setattr(
        "codev_platform.reindex.supervisor.worker_status",
        lambda now=None: worker_status,
    )

    summary = summarize(
        queue=support._Queue(
            active=[Job("demo-proj", "chroma", 900, token="t1", lease_expires_at=now + 60)],
        ),
        now=now,
    )

    assert summary["expired_active_count"] == 0


def test_reindex_status_marks_dirty_after_active(monkeypatch):
    monkeypatch.setattr(
        "codev_platform.reindex.supervisor.worker_status",
        lambda now=None: {
            "running": True,
            "pid": 123,
            "mode": "short",
            "heartbeat_sec": 10,
            "heartbeat_age_sec": 1,
            "phase": "runner",
            "phase_at": 995,
            "active_job": "demo-proj__chroma",
            "last_result": {"job_key": "demo-proj__codegraph", "status": "ok"},
        },
    )

    summary = summarize(
        queue=support._Queue(
            pending=[Job("demo-proj", "chroma", 980)],
            active=[Job("demo-proj", "chroma", 900, token="t1")],
        ),
        now=1000,
    )

    line = format_summary(summary)
    assert summary["severity"] == "WARN"
    assert summary["dirty_after_active_count"] == 1
    assert summary["recommended_action"] == "wait"
    assert "dirty_after_active=1" in line


def test_reindex_status_fails_when_pending_stale_too_long_and_worker_stopped(monkeypatch):
    monkeypatch.setattr(
        "codev_platform.reindex.supervisor.worker_status",
        lambda now=None: {"running": False, "pid": None, "phase": "stopped", "phase_at": 0},
    )

    summary = summarize(queue=support._Queue(pending=[Job("demo-proj", "chroma", 0)]), now=2000)

    assert summary["severity"] == "FAIL"
    assert summary["stale_count"] == 1
    assert summary["recommended_action"] == "prune-stale"


def test_reindex_status_keeps_stopped_pending_ok_below_warn_threshold(monkeypatch):
    monkeypatch.setattr(
        "codev_platform.reindex.supervisor.worker_status",
        lambda now=None: {"running": False, "pid": None, "phase": "stopped", "phase_at": 0},
    )

    summary = summarize(queue=support._Queue(pending=[Job("demo-proj", "chroma", 701)]), now=1000)

    assert summary["severity"] == "OK"
    assert summary["stale_count"] == 0


def test_reindex_status_fails_stopped_pending_only_after_fail_threshold(monkeypatch):
    monkeypatch.setattr(
        "codev_platform.reindex.supervisor.worker_status",
        lambda now=None: {"running": False, "pid": None, "phase": "stopped", "phase_at": 0},
    )

    summary = summarize(queue=support._Queue(pending=[Job("demo-proj", "chroma", 200)]), now=2000)

    assert summary["severity"] == "FAIL"


def test_format_summary_uses_prune_stale_action(monkeypatch):
    monkeypatch.setattr(
        "codev_platform.reindex.supervisor.worker_status",
        lambda now=None: {"running": False, "pid": None, "phase": "stopped", "phase_at": 0},
    )

    summary = summarize(queue=support._Queue(pending=[Job("demo-proj", "chroma", 0)]), now=2000)

    assert "action=prune-stale" in format_summary(summary)
