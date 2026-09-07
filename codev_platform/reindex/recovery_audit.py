"""隔离 worker 在任何恢复动作前读取并核对耐久恢复事实。"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Protocol

from .queue_ports import QueueSnapshot


class RecoveryAuditError(RuntimeError):
    """启动前恢复事实无法安全读取或解释。"""


class RecoveryOwnershipError(RecoveryAuditError):
    """耐久 attempt 或 active claim 不属于当前稳定 queue owner。"""


class RecoveryQueuePort(Protocol):
    def snapshot(self) -> QueueSnapshot: ...


class RecoveryJournalPort(Protocol):
    def load(self) -> object | None: ...

    def load_health(self) -> object | None: ...


@dataclass(frozen=True, slots=True)
class StartupRecoverySnapshot:
    """一次只读审计得到的恢复状态摘要，不携带 token 或原生进程引用。"""

    attempt_owner: str | None = field(repr=False)
    health_present: bool
    active_owners: tuple[str | None, ...] = field(repr=False)
    quarantined_count: int

    @property
    def attempt_present(self) -> bool:
        return self.attempt_owner is not None

    @property
    def active_count(self) -> int:
        return len(self.active_owners)

    @property
    def recovery_state_present(self) -> bool:
        return bool(
            self.attempt_present
            or self.health_present
            or self.active_owners
            or self.quarantined_count
        )


def _owner_text(value: object, field: str, *, optional: bool = False) -> str | None:
    if value is None and optional:
        return None
    if type(value) is not str or not value.strip():
        raise RecoveryAuditError(f"{field} 缺失或无效")
    return value


def inspect_startup_recovery(
    queue: RecoveryQueuePort,
    journal: RecoveryJournalPort,
) -> StartupRecoverySnapshot:
    """读取 queue 与 journal；任何不确定性都交由调用方停止启动。"""
    try:
        queue_snapshot = queue.snapshot()
    except Exception as error:  # noqa: BLE001 - 避免把后端原始异常传播到状态面
        raise RecoveryAuditError("无法读取 queue 恢复状态") from error
    if type(queue_snapshot) is not QueueSnapshot:
        raise RecoveryAuditError("queue 恢复状态类型无效")
    try:
        record = journal.load()
        health = journal.load_health()
    except Exception as error:  # noqa: BLE001 - journal 不确定时禁止继续恢复
        raise RecoveryAuditError("无法读取 journal 恢复状态") from error
    attempt_owner = None
    if record is not None:
        entry = getattr(record, "entry", None)
        attempt_owner = _owner_text(
            getattr(entry, "owner_token", None),
            "attempt journal owner",
        )
    active_owners: list[str | None] = []
    for job in (*queue_snapshot.active, *queue_snapshot.expired_active):
        active_owners.append(_owner_text(
            getattr(job, "owner_token", None),
            "active claim owner",
            optional=True,
        ))
    return StartupRecoverySnapshot(
        attempt_owner,
        health is not None,
        tuple(active_owners),
        len(queue_snapshot.quarantined),
    )


def verify_startup_owner(
    snapshot: StartupRecoverySnapshot,
    queue_owner_token: str,
) -> None:
    """在续租、终止或 recover_owned 前确认所有可归属状态属于稳定 owner。"""
    if type(snapshot) is not StartupRecoverySnapshot:
        raise ValueError("snapshot 必须是 StartupRecoverySnapshot")
    expected = _owner_text(queue_owner_token, "queue owner")
    if snapshot.attempt_owner is not None and snapshot.attempt_owner != expected:
        raise RecoveryOwnershipError("attempt journal owner 与当前 queue owner 不一致")
    if any(owner != expected for owner in snapshot.active_owners):
        raise RecoveryOwnershipError("active claim owner 与当前 queue owner 不一致")


__all__ = [
    "RecoveryAuditError",
    "RecoveryJournalPort",
    "RecoveryOwnershipError",
    "RecoveryQueuePort",
    "StartupRecoverySnapshot",
    "inspect_startup_recovery",
    "verify_startup_owner",
]
