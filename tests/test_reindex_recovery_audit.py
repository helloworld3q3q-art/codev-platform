"""隔离 worker 启动前恢复状态与稳定 owner 的审计测试。"""
from __future__ import annotations

from types import SimpleNamespace

import pytest

from codev_platform.reindex.queue_ports import Job, QueueSnapshot
from codev_platform.reindex.recovery_audit import (
    RecoveryOwnershipError,
    inspect_startup_recovery,
    verify_startup_owner,
)


class _Queue:
    def __init__(self, snapshot: QueueSnapshot) -> None:
        self.snapshot_value = snapshot
        self.calls = 0

    def snapshot(self) -> QueueSnapshot:
        self.calls += 1
        return self.snapshot_value


class _Journal:
    def __init__(self, record=None, health=None) -> None:
        self.record = record
        self.health = health

    def load(self):
        return self.record

    def load_health(self):
        return self.health


def _active(owner_token: str | None) -> Job:
    return Job(
        "demo",
        "codegraph",
        1.0,
        token="claim-a",
        owner_token=owner_token,
    )


def _record(owner_token: str):
    return SimpleNamespace(entry=SimpleNamespace(owner_token=owner_token))


def test_读取启动恢复快照包含attempt_health_active与quarantine事实() -> None:
    queue = _Queue(QueueSnapshot(
        active=[_active("queue-owner-a")],
        quarantined=[SimpleNamespace()],
    ))
    journal = _Journal(_record("queue-owner-a"), health=SimpleNamespace())

    snapshot = inspect_startup_recovery(queue, journal)

    assert queue.calls == 1
    assert snapshot.attempt_present is True
    assert snapshot.health_present is True
    assert snapshot.active_count == 1
    assert snapshot.quarantined_count == 1


def test_启动恢复快照的repr不泄露owner_token() -> None:
    snapshot = inspect_startup_recovery(
        _Queue(QueueSnapshot(active=[_active("owner-secret")])),
        _Journal(_record("owner-secret")),
    )

    assert "owner-secret" not in repr(snapshot)


def test_启动前拒绝journal_owner与稳定owner不一致() -> None:
    snapshot = inspect_startup_recovery(
        _Queue(QueueSnapshot()),
        _Journal(_record("queue-owner-old")),
    )

    with pytest.raises(RecoveryOwnershipError, match="journal|owner"):
        verify_startup_owner(snapshot, "queue-owner-new")


def test_启动前拒绝active_owner与稳定owner不一致() -> None:
    snapshot = inspect_startup_recovery(
        _Queue(QueueSnapshot(active=[_active("queue-owner-old")])),
        _Journal(),
    )

    with pytest.raises(RecoveryOwnershipError, match="active|owner"):
        verify_startup_owner(snapshot, "queue-owner-new")


def test_稳定owner匹配时允许后续recover_owned() -> None:
    snapshot = inspect_startup_recovery(
        _Queue(QueueSnapshot(expired_active=[_active("queue-owner-a")])),
        _Journal(_record("queue-owner-a")),
    )

    verify_startup_owner(snapshot, "queue-owner-a")
