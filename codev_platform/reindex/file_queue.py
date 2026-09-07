"""FileSpool 队列状态机与 legacy 公共接口兼容层。"""
from __future__ import annotations

import socket
import time
from collections.abc import AsyncIterator, Iterator
from contextlib import contextmanager
from dataclasses import replace
from pathlib import Path
from typing import overload
from uuid import uuid4

from codev_platform.reindex.attempts import ConfirmedProcessDeath
from codev_platform.reindex.file_publish_permit import DeferredFilePublishPermit
from codev_platform.reindex.file_queue_view import (
    FileDependencyQueueView,
    peek_jobs,
    quarantine_map,
    queue_snapshot,
)
from codev_platform.reindex.file_queue_store import (
    ACTIVE,
    PENDING,
    QUARANTINED,
    RESULTS,
    FileQueueRecord,
    FileQueueStore,
    OperationDeadline,
    active_payload,
    pending_payload,
    quarantine_payload,
    result_payload,
)
from codev_platform.reindex.file_queue_recovery import recover_owned as recover_owned_claims
from codev_platform.reindex.file_queue_transitions import (
    claim_matches,
    reject_claim,
    restore_active_pending,
    retire_active,
    retry_claim,
    settle_expired_legacy_active as settle_expired_legacy_active_locked,
)
from codev_platform.reindex.queue_ports import (
    ClaimedJob,
    Job,
    JobMeta,
    PendingMigrationResult,
    QuarantineRecord,
    QueueClaimLost,
    QueueOperationTimeout,
    QueueSnapshot,
    validate_job_identity,
    validate_positive_number,
)

_DEFAULT_LEASE_TTL_SEC = 1800.0
_LEGACY_OPERATION_TIMEOUT_SEC = 5.0


def _required_text(value: object, field_name: str) -> str:
    if type(value) is not str or not value.strip():
        raise ValueError(f"{field_name} 必须是非空字符串")
    return value


class FileSpoolQueue(FileDependencyQueueView):
    """以逐 key 耐久文件实现 queue v2 与 isolated worker 端口。"""

    def __init__(
        self,
        spool_dir: Path,
        *,
        lease_ttl_sec: float = _DEFAULT_LEASE_TTL_SEC,
        owner_token: str | None = None,
    ) -> None:
        self._lease_ttl = validate_positive_number(lease_ttl_sec, "lease_ttl_sec")
        self._owner_token = owner_token or socket.gethostname()
        self._store = FileQueueStore(
            Path(spool_dir),
            lock_owner_token=self._owner_token,
        )

    @property
    def location(self) -> Path:
        return self._store.location

    @staticmethod
    def _validate(project_id: str, kind: str) -> tuple[str, str]:
        return validate_job_identity(project_id, kind)

    @staticmethod
    def _normalize_meta(meta: JobMeta | None) -> JobMeta:
        return meta if isinstance(meta, JobMeta) else JobMeta()

    @contextmanager
    def _locked(self, key: str, deadline: OperationDeadline) -> Iterator[None]:
        with self._store.key_lock(key, deadline) as acquired:
            if not acquired:
                raise RuntimeError("blocking File key lock 未取得锁")
            yield

    @contextmanager
    def _key_lock(self, key: str) -> Iterator[None]:
        deadline = OperationDeadline.start(_LEGACY_OPERATION_TIMEOUT_SEC)
        with self._locked(key, deadline):
            yield

    def _write_phase(self, phase: str, key: str, payload: dict[str, object]) -> None:
        path = self._store.phase_path(phase, key)
        if path is None:
            raise ValueError(f"非法队列 key: {key!r}")
        self._store.write_json_atomic(path, payload)

    def _migrate_locked(self, key: str) -> None:
        self._store.migrate_legacy_locked(key)

    def _migrate(self, deadline: OperationDeadline, *, blocking: bool = True) -> None:
        self._store.migrate_legacy(deadline, blocking=blocking)

    def enqueue(self, project_id: str, kind: str, meta: JobMeta | None = None) -> None:
        project, resolved_kind = self._validate(project_id, kind)
        key = f"{project}__{resolved_kind}"
        deadline = OperationDeadline.start(_LEGACY_OPERATION_TIMEOUT_SEC)
        with self._locked(key, deadline):
            self._migrate_locked(key)
            deadline.check()
            job = Job(
                project,
                resolved_kind,
                time.time(),
                meta=self._normalize_meta(meta),
            )
            self._write_phase(PENDING, key, pending_payload(job))

    @staticmethod
    def _validate_claim_inputs(owner_token: str, limit: int) -> None:
        _required_text(owner_token, "owner_token")
        if type(limit) is not int or limit <= 0:
            raise ValueError("limit 必须是正整数")

    def _try_claim_record(
        self,
        scanned: FileQueueRecord,
        *,
        owner_token: str,
        projects: set[str] | None,
        deadline: OperationDeadline,
    ) -> ClaimedJob | None:
        deadline.check()
        if projects is not None and scanned.project_id not in projects:
            return None
        with self._store.key_lock(scanned.key, deadline, blocking=False) as acquired:
            if not acquired:
                return None
            return self._claim_locked(scanned.key, owner_token, deadline)

    def _claim_batch(
        self,
        *,
        owner_token: str,
        projects: set[str] | None,
        limit: int | None,
        deadline: OperationDeadline,
    ) -> list[ClaimedJob]:
        self._migrate(deadline, blocking=False)
        claims: list[ClaimedJob] = []
        records = self._store.sorted_records(self._store.phase_records(PENDING))
        deadline.check()
        for scanned in records:
            if limit is not None and len(claims) >= limit:
                break
            if claims and deadline.remaining() <= 0:
                break
            try:
                claim = self._try_claim_record(
                    scanned,
                    owner_token=owner_token,
                    projects=projects,
                    deadline=deadline,
                )
            except QueueOperationTimeout:
                if claims:
                    break
                raise
            if claim is not None:
                claims.append(claim)
        if not claims:
            deadline.check()
        return claims

    def _claim_locked(self, key: str, owner_token: str,
                      deadline: OperationDeadline) -> ClaimedJob | None:
        self._migrate_locked(key)
        if self._store.read_quarantine(key) is not None:
            return None
        pending = self._store.read_phase(PENDING, key)
        if pending is None or self._store.read_phase(ACTIVE, key) is not None:
            return None
        claim_token = uuid4().hex
        lease_expires_at = time.time() + self._lease_ttl
        deadline.check()
        self._write_phase(
            ACTIVE,
            key,
            active_payload(
                pending,
                claim_token=claim_token,
                owner_token=owner_token,
                lease_expires_at=lease_expires_at,
            ),
        )
        try:
            deadline.check()
        except BaseException:
            active_path = self._store.phase_path(ACTIVE, key)
            if active_path is not None:
                self._store.unlink(active_path)
            raise
        self._store.unlink(pending.path)
        job = Job(
            pending.project_id,
            pending.kind,
            pending.enqueued_at,
            meta=pending.meta,
            lease_expires_at=lease_expires_at,
        )
        return ClaimedJob(job, claim_token, owner_token, lease_expires_at)

    def claim(self, *, owner_token: str, projects: set[str] | None, limit: int,
              timeout_sec: float) -> list[ClaimedJob]:
        self._validate_claim_inputs(owner_token, limit)
        deadline = OperationDeadline.start(timeout_sec)
        if projects is not None and not projects:
            return []
        return self._claim_batch(
            owner_token=owner_token,
            projects=projects,
            limit=limit,
            deadline=deadline,
        )

    def pending(self, projects: set[str] | None = None,
                limit: int | None = None) -> list[Job]:
        if projects is not None and not projects:
            return []
        if limit is not None and (type(limit) is not int or limit <= 0):
            return []
        claims = self._claim_batch(
            owner_token=self._owner_token,
            projects=projects,
            limit=limit,
            deadline=OperationDeadline.start(_LEGACY_OPERATION_TIMEOUT_SEC),
        )
        return [replace(
            claim.job,
            token=claim.claim_token,
            lease_expires_at=claim.lease_expires_at,
        ) for claim in claims]

    def _quarantine_map(self) -> dict[str, QuarantineRecord]:
        return quarantine_map(self._store)

    def peek(self) -> list[Job]:
        deadline = OperationDeadline.start(_LEGACY_OPERATION_TIMEOUT_SEC)
        self._migrate(deadline, blocking=False)
        jobs = peek_jobs(self._store)
        deadline.check()
        return jobs

    def snapshot(self) -> QueueSnapshot:
        deadline = OperationDeadline.start(_LEGACY_OPERATION_TIMEOUT_SEC)
        self._migrate(deadline, blocking=False)
        snapshot = queue_snapshot(self._store)
        deadline.check()
        return snapshot

    def migrate_pending(self, expected: Job, new_meta: JobMeta, *, timeout_sec: float) -> PendingMigrationResult:
        from .file_queue_migration import migrate_pending
        return migrate_pending(self._store, self._locked, expected, new_meta, timeout_sec=timeout_sec)

    def recover_owned(self, *, owner_token: str,
                      timeout_sec: float) -> list[ClaimedJob]:
        _required_text(owner_token, "owner_token")
        deadline = OperationDeadline.start(timeout_sec)
        return recover_owned_claims(
            self._store, self._locked, owner_token, deadline, set(self._quarantine_map()),
        )

    @overload
    def renew(self, claim: ClaimedJob, *, ttl_sec: float,
              timeout_sec: float) -> bool: ...

    @overload
    def renew(self, claim: Job) -> bool: ...

    def renew(self, claim: ClaimedJob | Job, *, ttl_sec: float | None = None,
              timeout_sec: float | None = None) -> bool:
        if isinstance(claim, ClaimedJob):
            if ttl_sec is None or timeout_sec is None:
                raise TypeError("新 renew 必须显式传 ttl_sec 和 timeout_sec")
            ttl = validate_positive_number(ttl_sec, "ttl_sec")
            deadline = OperationDeadline.start(timeout_sec)
            return self._renew_claim(claim, ttl, deadline)
        if not isinstance(claim, Job) or ttl_sec is not None or timeout_sec is not None:
            raise TypeError("legacy renew 只接受单个 Job")
        if not claim.token:
            return False
        legacy = ClaimedJob(
            claim,
            claim.token,
            self._owner_token,
            claim.lease_expires_at or (time.time() + self._lease_ttl),
        )
        return self._renew_claim(
            legacy,
            self._lease_ttl,
            OperationDeadline.start(_LEGACY_OPERATION_TIMEOUT_SEC),
        )

    def _renew_claim(self, claim: ClaimedJob, ttl_sec: float,
                     deadline: OperationDeadline) -> bool:
        with self._locked(claim.job.key, deadline):
            if self._store.read_quarantine(claim.job.key) is not None:
                return False
            active = self._store.read_phase(ACTIVE, claim.job.key)
            if not claim_matches(active, claim):
                return False
            deadline.check()
            lease = time.time() + ttl_sec
            self._write_phase(
                ACTIVE,
                active.key,
                active_payload(
                    active,
                    claim_token=claim.claim_token,
                    owner_token=claim.owner_token,
                    lease_expires_at=lease,
                ),
            )
            return True

    def retry(self, claim: ClaimedJob, *, reason: str,
              timeout_sec: float) -> bool:
        _required_text(reason, "reason")
        deadline = OperationDeadline.start(timeout_sec)
        with self._locked(claim.job.key, deadline):
            return retry_claim(self._store, claim, deadline)

    def reject(self, claim: ClaimedJob, *, reason: str,
               timeout_sec: float) -> bool:
        failure_reason = _required_text(reason, "reason")
        deadline = OperationDeadline.start(timeout_sec)
        with self._locked(claim.job.key, deadline):
            return reject_claim(
                self._store,
                claim,
                reason=failure_reason,
                deadline=deadline,
            )

    def settle_expired_legacy_active(
        self, expected: Job, *, action: str, reason: str, timeout_sec: float,
    ) -> bool:
        """在同 key 锁内以当前租约和精确 claim/owner 围栏收口旧 active。"""
        deadline = OperationDeadline.start(timeout_sec)
        with self._locked(expected.key, deadline):
            return settle_expired_legacy_active_locked(
                self._store,
                expected,
                action=action,
                reason=_required_text(reason, "reason"),
                deadline=deadline,
            )

    def reject_unowned_legacy_active(
        self, expected: Job, *, reason: str, timeout_sec: float,
    ) -> bool:
        from .file_queue_transitions import reject_unowned_legacy_active

        deadline = OperationDeadline.start(timeout_sec)
        with self._locked(expected.key, deadline):
            return reject_unowned_legacy_active(
                self._store, expected, reason=_required_text(reason, "reason"), deadline=deadline,
            )

    def quarantine(self, claim: ClaimedJob, *, attempt_id: str, fence: str,
                   process_identity: str, containment_kind: str,
                   native_ref: str, reason: str,
                   timeout_sec: float) -> QuarantineRecord:
        requested = (
            _required_text(attempt_id, "attempt_id"),
            _required_text(fence, "fence"),
            _required_text(process_identity, "process_identity"),
            _required_text(containment_kind, "containment_kind"),
            _required_text(native_ref, "native_ref"),
            _required_text(reason, "reason"),
        )
        deadline = OperationDeadline.start(timeout_sec)
        with self._locked(claim.job.key, deadline):
            active = self._store.read_phase(ACTIVE, claim.job.key)
            if not claim_matches(active, claim):
                raise QueueClaimLost("quarantine claim 已失权")
            existing = self._store.read_quarantine(claim.job.key)
            if existing is not None:
                expected = QuarantineRecord(
                    claim.job.project_id, claim.job.kind, claim.claim_token,
                    *requested, existing.quarantined_at,
                )
                if existing == expected:
                    return existing
                raise QueueClaimLost("目标 key 已被不同身份隔离")
            deadline.check()
            record = QuarantineRecord(
                claim.job.project_id,
                claim.job.kind,
                claim.claim_token,
                *requested,
                time.time(),
            )
            self._write_phase(
                QUARANTINED,
                claim.job.key,
                quarantine_payload(record),
            )
            return record

    def clear_quarantine(self, record: QuarantineRecord, *,
                         death_proof: ConfirmedProcessDeath,
                         timeout_sec: float) -> bool:
        deadline = OperationDeadline.start(timeout_sec)
        if not isinstance(record, QuarantineRecord):
            return False
        if not isinstance(death_proof, ConfirmedProcessDeath):
            return False
        proof_matches = (
            death_proof.process_identity == record.process_identity
            and death_proof.containment_kind == record.containment_kind
            and death_proof.confirmed_at >= record.quarantined_at
        )
        if not proof_matches:
            return False
        key = f"{record.project_id}__{record.kind}"
        with self._locked(key, deadline):
            persisted = self._store.read_quarantine(key)
            if persisted != record:
                return False
            active = self._store.read_phase(ACTIVE, key)
            pending = self._store.read_phase(PENDING, key)
            if active is None and pending is None:
                return False
            deadline.check()
            if active is not None:
                if active.claim_token != record.claim_token:
                    return False
                restore_active_pending(self._store, active)
                self._store.unlink(active.path)
            path = self._store.phase_path(QUARANTINED, key)
            if path is None:
                return False
            self._store.unlink(path)
            return True

    @contextmanager
    def begin_publish(self, claim: ClaimedJob, *, desired_revision: str,
                      timeout_sec: float) -> Iterator[DeferredFilePublishPermit]:
        desired = _required_text(desired_revision, "desired_revision")
        deadline = OperationDeadline.start(timeout_sec)
        with self._locked(claim.job.key, deadline):
            if self._store.read_quarantine(claim.job.key) is not None:
                raise QueueClaimLost("quarantine key 不得进入发布围栏")
            active = self._store.read_phase(ACTIVE, claim.job.key)
            if not claim_matches(active, claim):
                raise QueueClaimLost("publish claim 已失权")
            if active.meta.target_commit != desired:
                raise QueueClaimLost("active 期望版本与发布版本不一致")
            pending_invalid = False
            try:
                pending = self._store.read_phase(PENDING, claim.job.key)
            except ValueError:
                pending = None
                pending_invalid = True
            superseded = pending_invalid or (
                pending is not None and pending.meta.target_commit != desired
            )

            def _commit() -> bool:
                deadline.check()
                current = self._store.read_phase(ACTIVE, claim.job.key)
                if not claim_matches(current, claim):
                    return False
                status = "superseded" if superseded else "completed"
                return retire_active(self._store, current, status=status)

            permit = DeferredFilePublishPermit(superseded=superseded, commit=_commit)
            try:
                yield permit
            except BaseException:
                raise
            else:
                permit.commit_if_requested()
            finally:
                permit.close()

    def complete(self, job: Job) -> bool:
        deadline = OperationDeadline.start(_LEGACY_OPERATION_TIMEOUT_SEC)
        with self._locked(job.key, deadline):
            self._migrate_locked(job.key)
            if self._store.read_quarantine(job.key) is not None:
                return False
            active = self._store.read_phase(ACTIVE, job.key)
            if (
                active is None
                or active.claim_token != job.token
                or active.owner_token != self._owner_token
            ):
                return False
            dirty = self._store.read_phase(PENDING, job.key) is not None
            return retire_active(self._store, active, status="completed") and not dirty

    def discard(self, job: Job) -> bool:
        deadline = OperationDeadline.start(_LEGACY_OPERATION_TIMEOUT_SEC)
        with self._locked(job.key, deadline):
            self._migrate_locked(job.key)
            if self._store.read_quarantine(job.key) is not None:
                return False
            pending = self._store.read_phase(PENDING, job.key)
            if pending is None:
                return self._store.read_phase(ACTIVE, job.key) is None
            if pending.enqueued_at > job.enqueued_at:
                return False
            self._write_phase(RESULTS, job.key, result_payload(pending, status="discarded"))
            self._store.unlink(pending.path)
            return True

    def release(self, job: Job) -> bool:
        deadline = OperationDeadline.start(_LEGACY_OPERATION_TIMEOUT_SEC)
        with self._locked(job.key, deadline):
            if self._store.read_quarantine(job.key) is not None:
                return False
            active = self._store.read_phase(ACTIVE, job.key)
            if (
                active is None
                or active.claim_token != job.token
                or active.owner_token != self._owner_token
            ):
                return False
            restore_active_pending(self._store, active)
            self._store.unlink(active.path)
            return True

    def reclaim_stale_own(self) -> int:
        deadline = OperationDeadline.start(_LEGACY_OPERATION_TIMEOUT_SEC)
        reclaimed = 0
        blocked = set(self._quarantine_map())
        active_records = self._store.phase_records(ACTIVE)
        deadline.check()
        for scanned in active_records:
            if scanned.key in blocked:
                continue
            with self._locked(scanned.key, deadline):
                if self._store.read_quarantine(scanned.key) is not None:
                    continue
                active = self._store.read_phase(ACTIVE, scanned.key)
                if active is None or active.owner_token != self._owner_token:
                    continue
                restore_active_pending(self._store, active)
                self._store.unlink(active.path)
                reclaimed += 1
        return reclaimed

    def break_lease(self, job: Job) -> bool:
        if not job.token:
            return False
        deadline = OperationDeadline.start(_LEGACY_OPERATION_TIMEOUT_SEC)
        with self._locked(job.key, deadline):
            if self._store.read_quarantine(job.key) is not None:
                return False
            active = self._store.read_phase(ACTIVE, job.key)
            if active is None or active.claim_token != job.token:
                return False
            restore_active_pending(self._store, active)
            self._store.unlink(active.path)
            return True

    async def watch(self) -> AsyncIterator[None]:
        from watchfiles import awatch

        async for _changes in awatch(str(self.location)):
            yield None


__all__ = ["FileSpoolQueue"]
