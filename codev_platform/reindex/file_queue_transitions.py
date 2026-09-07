"""File 队列 active 的可恢复 retry/reject 状态转换。"""

from __future__ import annotations

import math
import time

from codev_platform.reindex.file_queue_store import (
    ACTIVE,
    PENDING,
    RESULTS,
    FileQueueRecord,
    FileQueueStore,
    OperationDeadline,
    pending_payload,
    result_payload,
)
from codev_platform.reindex.queue_ports import ClaimedJob, Job


class FileQueueTransitionError(RuntimeError):
    """耐久写入后无法删除旧 active，状态尚未完成收口。"""


def claim_matches(active: FileQueueRecord | None, claim: ClaimedJob) -> bool:
    """只承认 key、claim token 与 owner incarnation 全部一致。"""
    return bool(
        active is not None
        and active.key == claim.job.key
        and active.claim_token == claim.claim_token
        and active.owner_token == claim.owner_token
    )


def _write_phase(
    store: FileQueueStore,
    phase: str,
    key: str,
    payload: dict[str, object],
) -> None:
    path = store.phase_path(phase, key)
    if path is None:
        raise ValueError(f"非法队列 key: {key!r}")
    store.write_json_atomic(path, payload)


def _next_tail_time(
    store: FileQueueStore,
    active: FileQueueRecord,
    deadline: OperationDeadline,
) -> float:
    pending = store.phase_records(PENDING)
    deadline.check()
    latest = max(
        time.time(),
        active.enqueued_at,
        *(record.enqueued_at for record in pending),
    )
    tail = math.nextafter(latest, math.inf)
    if not math.isfinite(tail):
        raise ValueError("File 队列无法生成有限队尾时间")
    return tail


def _lease_expired(active: FileQueueRecord) -> bool:
    """只以持久 active 当前租约判断，调用方必须已持有同 key 锁。"""
    return active.lease_expires_at is not None and active.lease_expires_at < time.time()


def _lease_matches(active: FileQueueRecord, expected: float | None) -> bool:
    return expected is not None and active.lease_expires_at == expected


def _remove_active(store: FileQueueStore, active: FileQueueRecord) -> None:
    if not store.unlink(active.path):
        raise FileQueueTransitionError("active 删除失败，状态未完成收口")


def retry_claim(
    store: FileQueueStore,
    claim: ClaimedJob,
    deadline: OperationDeadline,
    *,
    require_expired: bool = False,
    expected_lease_expires_at: float | None = None,
) -> bool:
    """在 active 删除这一线性化点前，先耐久写入队尾 pending。"""
    if store.read_quarantine(claim.job.key) is not None:
        return False
    active = store.read_phase(ACTIVE, claim.job.key)
    if not claim_matches(active, claim):
        return False
    if require_expired and not _lease_expired(active):
        return False
    if expected_lease_expires_at is not None and not _lease_matches(active, expected_lease_expires_at):
        return False
    pending = store.read_phase(PENDING, claim.job.key)
    reentry = pending if pending is not None else active
    deadline.check()
    _write_phase(
        store,
        PENDING,
        active.key,
        pending_payload(
            reentry,
            enqueued_at=_next_tail_time(store, active, deadline),
        ),
    )
    _remove_active(store, active)
    return True


def restore_active_pending(store: FileQueueStore, active: FileQueueRecord) -> None:
    """在 active 收口前保留已有 pending，避免覆盖新入队版本。"""
    if store.read_phase(PENDING, active.key) is None:
        _write_phase(store, PENDING, active.key, pending_payload(active))


def retire_active(
    store: FileQueueStore,
    active: FileQueueRecord,
    *,
    status: str,
) -> bool:
    """退休相同 claim token 的 active，并耐久保留结果。"""
    current = store.read_phase(ACTIVE, active.key)
    if current is None or current.claim_token != active.claim_token:
        return False
    _write_phase(store, RESULTS, active.key, result_payload(current, status=status))
    _remove_active(store, current)
    return True


def reject_claim(
    store: FileQueueStore,
    claim: ClaimedJob,
    *,
    reason: str,
    deadline: OperationDeadline,
    require_expired: bool = False,
    expected_lease_expires_at: float | None = None,
) -> bool:
    """退休精确 active claim，并先耐久写入失败审计。"""
    if store.read_quarantine(claim.job.key) is not None:
        return False
    active = store.read_phase(ACTIVE, claim.job.key)
    if not claim_matches(active, claim):
        return False
    if require_expired and not _lease_expired(active):
        return False
    if expected_lease_expires_at is not None and not _lease_matches(active, expected_lease_expires_at):
        return False
    deadline.check()
    _write_phase(
        store,
        RESULTS,
        active.key,
        result_payload(active, status="failed", failure_reason=reason),
    )
    _remove_active(store, active)
    return True


def reject_unowned_legacy_active(
    store: FileQueueStore,
    expected: Job,
    *,
    reason: str,
    deadline: OperationDeadline,
) -> bool:
    """只退休 claim token 不变且持久 owner 缺失的旧 active。"""
    if (
        not expected.token
        or expected.owner_token is not None
        or expected.lease_expires_at is None
        or store.read_quarantine(expected.key) is not None
    ):
        return False
    active = store.read_phase(ACTIVE, expected.key)
    if (
        active is None
        or active.claim_token != expected.token
        or active.owner_token is not None
        or not _lease_expired(active)
        or not _lease_matches(active, expected.lease_expires_at)
    ):
        return False
    deadline.check()
    _write_phase(
        store,
        RESULTS,
        active.key,
        result_payload(active, status="failed", failure_reason=reason),
    )
    _remove_active(store, active)
    return True


def settle_expired_legacy_active(
    store: FileQueueStore,
    expected: Job,
    *,
    action: str,
    reason: str,
    deadline: OperationDeadline,
) -> bool:
    """在同一 key 锁内以当前租约、claim 与 owner 围栏收口已过期旧任务。"""
    if (
        action not in {"retry", "reject"}
        or not expected.token
        or not expected.owner_token
        or expected.lease_expires_at is None
    ):
        return False
    claim = ClaimedJob(
        expected,
        expected.token,
        expected.owner_token,
        max(expected.lease_expires_at, 0.000001),
    )
    if action == "retry":
        return retry_claim(
            store,
            claim,
            deadline,
            require_expired=True,
            expected_lease_expires_at=expected.lease_expires_at,
        )
    return reject_claim(
        store,
        claim,
        reason=reason,
        deadline=deadline,
        require_expired=True,
        expected_lease_expires_at=expected.lease_expires_at,
    )


__all__ = [
    "claim_matches",
    "FileQueueTransitionError",
    "reject_claim",
    "reject_unowned_legacy_active",
    "restore_active_pending",
    "retire_active",
    "retry_claim",
    "settle_expired_legacy_active",
]
