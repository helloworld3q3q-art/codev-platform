"""File 队列 peek/snapshot 的只读轻量聚合。"""
from __future__ import annotations

import time
from dataclasses import dataclass

from codev_platform.reindex.file_queue_store import (
    ACTIVE,
    KEY_SEPARATOR,
    PENDING,
    QUARANTINED,
    RESULTS,
    FileQueueRecord,
    FileQueueStore,
    OperationDeadline,
)
from codev_platform.reindex.queue_ports import (
    DependencyQueueState,
    Job,
    QuarantineRecord,
    QueueOperationTimeout,
    QueueSnapshot,
    validate_job_identity,
    validate_target_commit,
)


@dataclass(frozen=True, slots=True)
class QuarantineView:
    """隔离详情与失败关闭 key 集合的只读快照。"""

    records: dict[str, QuarantineRecord]
    blocked_keys: frozenset[str]


def quarantine_view(store: FileQueueStore) -> QuarantineView:
    paths = list(store.phase_dir(QUARANTINED).glob("*.json"))
    records: dict[str, QuarantineRecord] = {}
    for path in paths:
        try:
            record = store.read_quarantine(path.stem)
        except (OSError, ValueError):
            continue
        if record is not None:
            records[path.stem] = record
    return QuarantineView(records, frozenset(path.stem for path in paths))


def quarantine_map(store: FileQueueStore) -> dict[str, QuarantineRecord]:
    return quarantine_view(store).records


def peek_jobs(store: FileQueueStore) -> list[Job]:
    blocked = quarantine_view(store).blocked_keys
    records = [
        record for record in store.phase_records(PENDING)
        if record.key not in blocked
    ]
    return [record.to_job() for record in store.sorted_records(records)]


def queue_snapshot(store: FileQueueStore) -> QueueSnapshot:
    quarantine = quarantine_view(store)
    quarantined = quarantine.records
    blocked = quarantine.blocked_keys
    pending = [
        record for record in store.phase_records(PENDING)
        if record.key not in blocked
    ]
    active: list[Job] = []
    expired: list[Job] = []
    now = time.time()
    for record in store.sorted_records(store.phase_records(ACTIVE)):
        if record.key in blocked:
            continue
        job = record.to_job(include_token=True)
        target = expired if record.lease_expires_at is not None and record.lease_expires_at <= now else active
        target.append(job)
    results = store.sorted_records(store.phase_records(RESULTS))
    return QueueSnapshot(
        pending=[record.to_job() for record in store.sorted_records(pending)],
        active=active,
        results=[record.to_job() for record in results],
        expired_active=expired,
        quarantined=sorted(
            quarantined.values(),
            key=lambda item: (item.quarantined_at, item.project_id, item.kind),
        ),
    )


def _target(record: FileQueueRecord | None) -> str | None:
    return None if record is None else record.meta.target_commit


class FileDependencyQueueView:
    """通过逐 key 锁提供稳定、只读的 File 队列依赖事实。"""

    _store: FileQueueStore

    def dependency_state(
        self,
        project_id: str,
        kind: str,
        target_commit: str,
        *,
        timeout_sec: float,
    ) -> DependencyQueueState:
        project, resolved_kind = validate_job_identity(project_id, kind)
        target = validate_target_commit(target_commit)
        deadline = OperationDeadline.start(timeout_sec)
        key = f"{project}{KEY_SEPARATOR}{resolved_kind}"
        with self._store.key_lock(key, deadline) as acquired:
            if not acquired:
                raise QueueOperationTimeout("等待 File 队列 key 锁超过时间预算")
            deadline.check()
            quarantine = self._store.phase_path(QUARANTINED, key)
            if quarantine is not None and quarantine.exists():
                return DependencyQueueState.ABSENT
            active = self._store.read_phase(ACTIVE, key)
            pending = self._store.read_phase(PENDING, key)
            if pending is None:
                pending = self._store.read_legacy(key)
            active_target = _target(active)
            pending_target = _target(pending)
            deadline.check()
            if active_target == target:
                return DependencyQueueState.ACTIVE
            if pending_target == target:
                return DependencyQueueState.PENDING
            if any(value and value != target for value in (active_target, pending_target)):
                return DependencyQueueState.REPLACEMENT
            return DependencyQueueState.ABSENT


__all__ = [
    "FileDependencyQueueView", "QuarantineView", "peek_jobs", "quarantine_map",
    "quarantine_view", "queue_snapshot",
]
