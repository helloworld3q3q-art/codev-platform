"""reindex 队列 legacy 检测与受控迁移的中性契约。"""
from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Literal, Protocol

from .queue_ports import (
    Job,
    QueueSnapshot,
    validate_target_commit,
    validate_timeout,
)


class LegacyQueueReason(str, Enum):
    """旧队列记录不能进入 isolated worker 的确定性原因。"""

    MISSING_TARGET_COMMIT = "missing_target_commit"
    SYMBOLIC_TARGET_COMMIT = "symbolic_target_commit"
    NONCANONICAL_TARGET_COMMIT = "noncanonical_target_commit"
    MISSING_OWNER_TOKEN = "missing_owner_token"
    FOREIGN_OWNER_TOKEN = "foreign_owner_token"
    STABLE_OWNER_UNAVAILABLE = "stable_owner_unavailable"


@dataclass(frozen=True, slots=True)
class LegacyQueueEntry:
    """一次只读扫描发现的单条 legacy 队列记录。"""

    phase: str
    job: Job
    reason: LegacyQueueReason


class LegacyActiveAction(str, Enum):
    """受控处置一个过期 legacy active 的结果。"""

    REJECTED = "rejected"
    RETRIED = "retried"
    MANUAL_RECOVERY_REQUIRED = "manual_recovery_required"
    VERSION_CONFLICT = "version_conflict"


@dataclass(frozen=True, slots=True)
class LegacyActiveResolution:
    """不暴露 owner token 的单条受控处置结果。"""

    phase: str
    key: str
    action: LegacyActiveAction
    reason: LegacyQueueReason | None


class LegacyActiveMigrationQueue(Protocol):
    """旧 active 处置所需的最小队列写侧能力。"""

    def settle_expired_legacy_active(
        self,
        expected: Job,
        *,
        action: Literal["retry", "reject"],
        reason: str,
        timeout_sec: float,
    ) -> bool: ...

    def reject_unowned_legacy_active(
        self,
        expected: Job,
        *,
        reason: str,
        timeout_sec: float,
    ) -> bool: ...


def _target_reason(job: Job) -> LegacyQueueReason | None:
    target = job.meta.target_commit
    if type(target) is not str or not target.strip():
        return LegacyQueueReason.MISSING_TARGET_COMMIT
    if target.strip() == "HEAD":
        return LegacyQueueReason.SYMBOLIC_TARGET_COMMIT
    try:
        validate_target_commit(target)
    except ValueError:
        return LegacyQueueReason.NONCANONICAL_TARGET_COMMIT
    return None


def _stable_owner_token(value: object) -> str | None:
    if value is None:
        return None
    if type(value) is not str or not value.strip():
        raise ValueError("stable_owner_token 必须是非空字符串或 None")
    return value


def _bootstrap_enabled(value: object) -> bool:
    if type(value) is not bool:
        raise ValueError("bootstrap 必须是布尔值")
    return value


def _owner_reason(job: Job, stable_owner_token: str | None) -> LegacyQueueReason | None:
    if not job.owner_token:
        return LegacyQueueReason.MISSING_OWNER_TOKEN
    if stable_owner_token is None:
        return LegacyQueueReason.STABLE_OWNER_UNAVAILABLE
    if job.owner_token != stable_owner_token:
        return LegacyQueueReason.FOREIGN_OWNER_TOKEN
    return None


def _resolution(
    job: Job,
    action: LegacyActiveAction,
    reason: LegacyQueueReason | None,
) -> LegacyActiveResolution:
    return LegacyActiveResolution("expired_active", job.key, action, reason)


def _active_plan(
    job: Job,
    *,
    stable_owner_token: str | None,
    bootstrap: bool,
) -> tuple[LegacyQueueReason | None, LegacyActiveAction] | None:
    """将纯事实快照转成安全计划，缺少稳定 owner 时默认绝不写入。"""
    target_reason = _target_reason(job)
    owner_reason = _owner_reason(job, stable_owner_token)
    if target_reason is None and owner_reason is None:
        return None
    reason = target_reason or owner_reason
    if stable_owner_token is None and not bootstrap:
        return reason, LegacyActiveAction.MANUAL_RECOVERY_REQUIRED
    if not job.owner_token:
        action = (
            LegacyActiveAction.REJECTED
            if target_reason is not None and job.token
            else LegacyActiveAction.MANUAL_RECOVERY_REQUIRED
        )
        return reason, action
    return (
        reason,
        LegacyActiveAction.REJECTED
        if target_reason is not None
        else LegacyActiveAction.RETRIED,
    )


def plan_expired_legacy_active(
    snapshot: QueueSnapshot,
    *,
    stable_owner_token: str | None = None,
    bootstrap: bool = False,
) -> tuple[LegacyActiveResolution, ...]:
    """纯读取地给出已过期 legacy active 的处置计划，不执行任何写入。"""
    if type(snapshot) is not QueueSnapshot:
        raise ValueError("snapshot 必须是 QueueSnapshot")
    stable_owner = _stable_owner_token(stable_owner_token)
    bootstrap_enabled = _bootstrap_enabled(bootstrap)
    plan: list[LegacyActiveResolution] = []
    for job in snapshot.expired_active:
        planned = _active_plan(
            job,
            stable_owner_token=stable_owner,
            bootstrap=bootstrap_enabled,
        )
        if planned is None:
            continue
        reason, action = planned
        plan.append(_resolution(job, action, reason))
    return tuple(plan)


def dispose_expired_legacy_active(
    queue: LegacyActiveMigrationQueue,
    snapshot: QueueSnapshot,
    *,
    stable_owner_token: str | None = None,
    bootstrap: bool = False,
    timeout_sec: float,
) -> tuple[LegacyActiveResolution, ...]:
    """只处置已过期 active，已知 owner 精确恢复后再按围栏迁移。"""
    if type(snapshot) is not QueueSnapshot:
        raise ValueError("snapshot 必须是 QueueSnapshot")
    stable_owner = _stable_owner_token(stable_owner_token)
    bootstrap_enabled = _bootstrap_enabled(bootstrap)
    timeout = validate_timeout(timeout_sec)
    results: list[LegacyActiveResolution] = []
    unowned_reject = getattr(queue, "reject_unowned_legacy_active", None)

    for job in snapshot.expired_active:
        planned = _active_plan(
            job,
            stable_owner_token=stable_owner,
            bootstrap=bootstrap_enabled,
        )
        if planned is None:
            continue
        reason, action = planned
        if action is LegacyActiveAction.MANUAL_RECOVERY_REQUIRED:
            results.append(_resolution(job, action, reason))
            continue
        if job.owner_token:
            changed = queue.settle_expired_legacy_active(
                job,
                action="reject" if action is LegacyActiveAction.REJECTED else "retry",
                reason=(
                    "受控淘汰 legacy active"
                    if action is LegacyActiveAction.REJECTED
                    else "受控恢复 legacy active"
                ),
                timeout_sec=timeout,
            )
            results.append(_resolution(
                job,
                action if changed else LegacyActiveAction.VERSION_CONFLICT,
                reason,
            ))
            continue
        if not callable(unowned_reject):
            raise ValueError("队列不支持无 owner legacy active 的受控拒绝")
        rejected = unowned_reject(
            job,
            reason="受控淘汰无 owner legacy active",
            timeout_sec=timeout,
        )
        results.append(_resolution(
            job,
            LegacyActiveAction.REJECTED if rejected else LegacyActiveAction.VERSION_CONFLICT,
            reason,
        ))

    return tuple(results)


def detect_legacy_queue_entries(
    snapshot: QueueSnapshot,
    *,
    stable_owner_token: str | None = None,
) -> tuple[LegacyQueueEntry, ...]:
    """纯读取地枚举 pending、active 与过期 active 的 legacy 记录。"""
    if type(snapshot) is not QueueSnapshot:
        raise ValueError("snapshot 必须是 QueueSnapshot")
    stable_owner = _stable_owner_token(stable_owner_token)
    entries: list[LegacyQueueEntry] = []
    for phase, jobs in (
        ("pending", snapshot.pending),
        ("active", snapshot.active),
        ("expired_active", snapshot.expired_active),
    ):
        for job in jobs:
            reason = _target_reason(job)
            if reason is not None:
                entries.append(LegacyQueueEntry(phase, job, reason))
            if phase != "pending":
                owner_reason = _owner_reason(job, stable_owner)
                if owner_reason is not None:
                    entries.append(LegacyQueueEntry(phase, job, owner_reason))
    return tuple(entries)


__all__ = [
    "LegacyQueueEntry",
    "LegacyActiveAction",
    "LegacyActiveMigrationQueue",
    "LegacyActiveResolution",
    "LegacyQueueReason",
    "detect_legacy_queue_entries",
    "dispose_expired_legacy_active",
    "plan_expired_legacy_active",
]
