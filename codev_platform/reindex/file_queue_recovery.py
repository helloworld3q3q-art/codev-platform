"""File 队列 owner 恢复扫描的独立读侧实现。"""
from __future__ import annotations

from collections.abc import Callable
from contextlib import AbstractContextManager

from codev_platform.reindex.file_queue_store import ACTIVE, FileQueueStore, OperationDeadline
from codev_platform.reindex.queue_ports import ClaimedJob


def recover_owned(
    store: FileQueueStore,
    lock_key: Callable[[str, OperationDeadline], AbstractContextManager[None]],
    owner_token: str,
    deadline: OperationDeadline,
    blocked_keys: set[str],
) -> list[ClaimedJob]:
    """逐 key 重读并只返回精确匹配 owner 的持久 active claim。"""
    recovered: list[ClaimedJob] = []
    active_records = store.sorted_records(store.phase_records(ACTIVE))
    deadline.check()
    for scanned in active_records:
        deadline.check()
        if scanned.key in blocked_keys:
            continue
        with lock_key(scanned.key, deadline):
            if store.read_quarantine(scanned.key) is not None:
                continue
            active = store.read_phase(ACTIVE, scanned.key)
            if (
                active is None
                or active.owner_token != owner_token
                or not active.claim_token
                or not active.lease_expires_at
            ):
                continue
            recovered.append(ClaimedJob(
                active.to_job(), active.claim_token, active.owner_token, active.lease_expires_at,
            ))
    deadline.check()
    return recovered


__all__ = ["recover_owned"]
