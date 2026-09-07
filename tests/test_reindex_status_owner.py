from __future__ import annotations

import pytest

from codev_platform.reindex.queue import QueueSnapshot
from codev_platform.reindex.queue import Job
from codev_platform.reindex.status import summarize

from tests import reindex_status_support as support

def test_status当前bootstrap事实优先阻断stale清理建议(monkeypatch, tmp_path):
    from codev_platform.reindex import status as status_module
    from codev_platform.reindex.file_queue import FileSpoolQueue
    from codev_platform.reindex.owner_readiness import (
        OwnerReadiness,
        OwnerReadinessReport,
    )
    from codev_platform.reindex.runtime_owner import QueueBackendBinding

    queue = FileSpoolQueue(tmp_path / "spool")
    binding = QueueBackendBinding("file", "a" * 64)
    monkeypatch.setattr(
        queue,
        "snapshot",
        lambda: QueueSnapshot(pending=[Job("demo-proj", "chroma", 0)]),
    )
    monkeypatch.setattr(
        "codev_platform.reindex.supervisor.worker_status",
        lambda now=None: {"running": False, "pid": None, "phase": "stopped", "phase_at": 0},
    )
    monkeypatch.setattr(
        status_module,
        "queue_backend_binding",
        lambda actual_queue, cfg=None: binding if actual_queue is queue else None,
        raising=False,
    )
    monkeypatch.setattr(
        status_module,
        "inspect_owner_readiness",
        lambda actual_binding: OwnerReadinessReport(
            OwnerReadiness.BOOTSTRAP_REQUIRED
            if actual_binding is binding
            else pytest.fail("owner binding 不得漂移")
        ),
        raising=False,
    )

    summary = summarize(queue=queue, now=2000)

    assert summary["owner_readiness"] == "bootstrap_required"
    assert summary["recommended_action"] == "init-owner"


@pytest.mark.parametrize(
    ("readiness", "value"),
    [
        ("RECOVERY_REQUIRED", "recovery_required"),
        ("BINDING_MISMATCH", "binding_mismatch"),
        ("UNAVAILABLE", "unavailable"),
    ],
)
def test_status未知安全owner事实不建议prune_stale(
    monkeypatch,
    tmp_path,
    readiness,
    value,
):
    from codev_platform.reindex import status as status_module
    from codev_platform.reindex.file_queue import FileSpoolQueue
    from codev_platform.reindex.owner_readiness import (
        OwnerReadiness,
        OwnerReadinessReport,
    )
    from codev_platform.reindex.runtime_owner import QueueBackendBinding

    queue = FileSpoolQueue(tmp_path / "spool")
    binding = QueueBackendBinding("file", "b" * 64)
    monkeypatch.setattr(
        queue,
        "snapshot",
        lambda: QueueSnapshot(pending=[Job("demo-proj", "chroma", 0)]),
    )
    monkeypatch.setattr(
        "codev_platform.reindex.supervisor.worker_status",
        lambda now=None: {"running": False, "pid": None, "phase": "stopped", "phase_at": 0},
    )
    monkeypatch.setattr(
        status_module,
        "queue_backend_binding",
        lambda _queue, cfg=None: binding,
        raising=False,
    )
    monkeypatch.setattr(
        status_module,
        "inspect_owner_readiness",
        lambda _binding: OwnerReadinessReport(getattr(OwnerReadiness, readiness)),
        raising=False,
    )

    summary = summarize(queue=queue, now=2000)

    assert summary["owner_readiness"] == value
    assert summary["recommended_action"] == "inspect-owner"


@pytest.mark.parametrize(
    ("active", "expired_active", "worker"),
    [
        (
            [Job("demo-proj", "chroma", 900, token="t1", lease_expires_at=1060)],
            [],
            {"running": False, "pid": None, "phase": "stopped", "phase_at": 0},
        ),
        (
            [],
            [Job("demo-proj", "chroma", 900, token="t1", lease_expires_at=990)],
            {"running": True, "pid": 123, "phase": "runner", "phase_at": 995},
        ),
    ],
)
def test_status活跃lease始终优先于owner初始化建议(
    monkeypatch,
    tmp_path,
    active,
    expired_active,
    worker,
):
    from codev_platform.reindex import status as status_module
    from codev_platform.reindex.file_queue import FileSpoolQueue
    from codev_platform.reindex.owner_readiness import (
        OwnerReadiness,
        OwnerReadinessReport,
    )
    from codev_platform.reindex.runtime_owner import QueueBackendBinding

    queue = FileSpoolQueue(tmp_path / "spool")
    binding = QueueBackendBinding("file", "c" * 64)
    monkeypatch.setattr(
        queue,
        "snapshot",
        lambda: QueueSnapshot(active=active, expired_active=expired_active),
    )
    monkeypatch.setattr("codev_platform.reindex.supervisor.worker_status", lambda now=None: worker)
    monkeypatch.setattr(
        status_module,
        "queue_backend_binding",
        lambda _queue, cfg=None: binding,
        raising=False,
    )
    monkeypatch.setattr(
        status_module,
        "inspect_owner_readiness",
        lambda _binding: OwnerReadinessReport(OwnerReadiness.BOOTSTRAP_REQUIRED),
        raising=False,
    )

    summary = summarize(queue=queue, now=1000)

    assert summary["recommended_action"] == "break-lease"


def test_status当前ready事实保留stale清理基线(monkeypatch, tmp_path):
    from codev_platform.reindex import status as status_module
    from codev_platform.reindex.file_queue import FileSpoolQueue
    from codev_platform.reindex.owner_readiness import (
        OwnerReadiness,
        OwnerReadinessReport,
    )
    from codev_platform.reindex.runtime_owner import QueueBackendBinding

    queue = FileSpoolQueue(tmp_path / "spool")
    binding = QueueBackendBinding("file", "d" * 64)
    monkeypatch.setattr(
        queue,
        "snapshot",
        lambda: QueueSnapshot(pending=[Job("demo-proj", "chroma", 0)]),
    )
    monkeypatch.setattr(
        "codev_platform.reindex.supervisor.worker_status",
        lambda now=None: {"running": False, "pid": None, "phase": "stopped", "phase_at": 0},
    )
    monkeypatch.setattr(
        status_module,
        "queue_backend_binding",
        lambda _queue, cfg=None: binding,
        raising=False,
    )
    monkeypatch.setattr(
        status_module,
        "inspect_owner_readiness",
        lambda _binding: OwnerReadinessReport(OwnerReadiness.READY),
        raising=False,
    )

    summary = summarize(queue=queue, now=2000)

    assert summary["owner_readiness"] == "ready"
    assert summary["recommended_action"] == "prune-stale"


def test_status仅有历史bootstrap退出时不建议prune_stale(monkeypatch):
    monkeypatch.setattr(
        "codev_platform.reindex.supervisor.worker_status",
        lambda now=None: {
            "running": False,
            "pid": None,
            "phase": "stopped",
            "phase_at": 0,
            "exit_reason": "bootstrap-required",
        },
    )

    summary = summarize(queue=support._Queue(pending=[Job("demo-proj", "chroma", 0)]), now=2000)

    assert summary["owner_readiness"] is None
    assert summary["recommended_action"] == "inspect-owner"


def test_status仅有历史人工处理退出且实时owner不可读取时不建议prune_stale(
    monkeypatch,
):
    from codev_platform.reindex import status as status_module
    from codev_platform.reindex.owner_readiness import OwnerReadiness

    monkeypatch.setattr(
        "codev_platform.reindex.supervisor.worker_status",
        lambda now=None: {
            "running": False,
            "pid": None,
            "phase": "stopped",
            "phase_at": 0,
            "exit_reason": "owner-operator-blocked",
        },
    )
    monkeypatch.setattr(
        status_module,
        "_current_owner_readiness",
        lambda _queue: OwnerReadiness.UNAVAILABLE,
    )

    summary = summarize(queue=support._Queue(pending=[Job("demo-proj", "chroma", 0)]), now=2000)

    assert summary["owner_readiness"] == "unavailable"
    assert summary["recommended_action"] == "inspect-owner"


def test_status已知后端探针异常按unavailable闭合(monkeypatch, tmp_path):
    from codev_platform.reindex import status as status_module
    from codev_platform.reindex.file_queue import FileSpoolQueue

    queue = FileSpoolQueue(tmp_path / "spool")
    monkeypatch.setattr(
        queue,
        "snapshot",
        lambda: QueueSnapshot(pending=[Job("demo-proj", "chroma", 0)]),
    )
    monkeypatch.setattr(
        "codev_platform.reindex.supervisor.worker_status",
        lambda now=None: {"running": False, "pid": None, "phase": "stopped", "phase_at": 0},
    )
    monkeypatch.setattr(
        status_module,
        "queue_backend_binding",
        lambda _queue, cfg=None: (_ for _ in ()).throw(OSError("probe denied")),
        raising=False,
    )

    summary = summarize(queue=queue, now=2000)

    assert summary["owner_readiness"] == "unavailable"
    assert summary["recommended_action"] == "inspect-owner"


def test_status透传已知后端探针终止性异常(monkeypatch, tmp_path):
    from codev_platform.reindex import status as status_module
    from codev_platform.reindex.file_queue import FileSpoolQueue

    queue = FileSpoolQueue(tmp_path / "spool")
    monkeypatch.setattr(queue, "snapshot", lambda: QueueSnapshot())
    monkeypatch.setattr(
        status_module,
        "queue_backend_binding",
        lambda _queue, cfg=None: (_ for _ in ()).throw(KeyboardInterrupt()),
        raising=False,
    )

    with pytest.raises(KeyboardInterrupt):
        summarize(queue=queue, now=2000)


def test_status读取已冻结pg绑定时不触发schema初始化(monkeypatch):
    from codev_platform.reindex import status as status_module
    from codev_platform.reindex.owner_readiness import (
        OwnerReadiness,
        OwnerReadinessReport,
    )
    from codev_platform.reindex.pg_queue import PgJobQueue

    queue = object.__new__(PgJobQueue)
    monkeypatch.setattr(queue, "snapshot", lambda: QueueSnapshot())
    monkeypatch.setattr(
        PgJobQueue,
        "ensure_owner_binding_locator",
        lambda _queue: pytest.fail("status 不得初始化 Pg queue schema"),
    )
    monkeypatch.setattr(PgJobQueue, "owner_binding_locator", lambda _queue: "frozen-pg-target")
    monkeypatch.setattr(
        "codev_platform.reindex.supervisor.worker_status",
        lambda now=None: {"running": False, "pid": None, "phase": "stopped", "phase_at": 0},
    )
    monkeypatch.setattr(
        status_module,
        "inspect_owner_readiness",
        lambda _binding: OwnerReadinessReport(OwnerReadiness.READY),
        raising=False,
    )

    summary = summarize(queue=queue, now=2000)

    assert summary["owner_readiness"] == "ready"
