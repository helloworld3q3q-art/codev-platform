"""storage 对 isolation 入口的窄转接，避免锁与隔离职责互相堆叠。"""

from __future__ import annotations

from pathlib import Path

from codev_platform.runtime_object_lock_capability import BoundRuntimeObjectLock


def isolate_incomplete_locked_at(
    lock: BoundRuntimeObjectLock,
    kind: str,
    object_id: str,
) -> Path | None:
    """仅在活动排他对象锁内隔离半成品。"""
    from codev_platform.runtime_isolation import isolate_incomplete_locked_at as isolate

    return isolate(lock, kind, object_id)


def isolate_corrupt_completed_locked_at(
    lock: BoundRuntimeObjectLock,
    kind: str,
    object_id: str,
) -> Path | None:
    """仅在活动排他对象锁内隔离损坏完成对象。"""
    from codev_platform.runtime_isolation import isolate_corrupt_completed_locked_at as isolate

    return isolate(lock, kind, object_id)
