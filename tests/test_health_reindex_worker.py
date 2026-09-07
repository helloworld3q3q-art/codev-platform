from __future__ import annotations

from codev_platform.ops.health._checks import _check_reindex_worker
from codev_platform.ops.health._util import Report


def test_health_reindex_worker_warns_when_queue_waits_without_worker(monkeypatch):
    monkeypatch.setattr(
        "codev_platform.reindex.status.summarize",
        lambda: {
            "severity": "FAIL",
            "running": False,
            "worker": {"running": False},
            "queue_backend": "FileSpoolQueue",
            "pending_count": 1,
            "active_count": 0,
            "results_count": 0,
            "dirty_after_active_count": 0,
            "expired_active_count": 0,
            "oldest_pending_age_sec": 700,
            "stale_count": 1,
            "phase": "stopped",
            "phase_age_sec": 700,
            "active_job": None,
            "last_result": None,
            "recommended_action": "prune-stale",
        },
    )
    r = Report()

    _check_reindex_worker(r)

    assert r.red == 1
    assert r.rows[0]["tag"] == "reindex worker"
    assert "pending=1" in r.rows[0]["msg"]
