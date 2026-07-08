from __future__ import annotations

from codev_platform.ops.health._checks import _check_reindex_worker
from codev_platform.ops.health._util import Report


def test_health_reindex_worker_warns_when_queue_waits_without_worker(monkeypatch):
    monkeypatch.setattr(
        "codev_platform.reindex.status.summarize",
        lambda: {
            "severity": "WARN",
            "running": False,
            "worker": {"running": False},
            "queue_backend": "FileSpoolQueue",
            "pending_count": 1,
            "oldest_pending_age_sec": 700,
            "stale_count": 1,
        },
    )
    r = Report()

    _check_reindex_worker(r)

    assert r.amber == 1
    assert r.rows[0]["tag"] == "reindex worker"
    assert "pending=1" in r.rows[0]["msg"]
