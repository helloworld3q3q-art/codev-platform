"""PostgreSQL reindex 队列适配器：显式 claim、持久隔离与发布围栏。"""
from __future__ import annotations

import logging
import socket
import threading
import time
from collections.abc import AsyncIterator, Iterator
from contextlib import contextmanager
from dataclasses import replace
from typing import overload

from codev_platform.reindex.attempts import ConfirmedProcessDeath
from codev_platform.reindex.pg_publish_permit import (
    PgPublishPermit,
    acknowledge_active,
)
from codev_platform.reindex.pg_queue_binding import (
    bare_table_name,
    pg_binding_base_locator,
    stored_owner_binding_locator,
)
from codev_platform.reindex.pg_queue_codec import (
    kind_rank as _kind_rank,
    meta_from_json as _meta_from_json,
    meta_json as _meta_json,
    pending_version_from_row as _pending_version,
    required_text as _required_text,
)
from codev_platform.reindex.pg_queue_schema import (
    PgQueueInitializationError,
    build_queue_pool,
    open_and_verify_queue_schema,
)
from codev_platform.reindex.pg_queue_sql import (
    claim_sql,
    clear_quarantine_sql,
    discard_sql,
    enqueue_sql,
    publish_lock_sql,
    quarantine_sql,
    reclaim_owned_sql,
    recover_owned_sql,
    renew_sql,
    scan_sql,
)
from codev_platform.reindex.pg_queue_tx import (
    PgOperationBudget,
    RollbackTransaction,
    bounded_connection,
    execute_with_budget,
)
from codev_platform.reindex.pg_queue_transitions import (
    reject_claim,
    reject_unowned_legacy_active,
    retry_claim,
)
from codev_platform.reindex.pg_queue_view import PgDependencyQueueView
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

logger = logging.getLogger(__name__)

_DEFAULT_LEASE_TTL_SEC = 1800.0
_DEFAULT_OPERATION_TIMEOUT_SEC = 5.0
_POOL_TIMEOUT_SEC = 5.0


@contextmanager
def _permit_lifecycle(
    permit: PgPublishPermit,
    rollback_signal: RollbackTransaction,
) -> Iterator[PgPublishPermit]:
    """关闭 permit，并在未确认时请求事务 context 回滚。"""
    try:
        yield permit
        if not permit.acked:
            raise rollback_signal
    finally:
        permit.close()


class PgJobQueue(PgDependencyQueueView):
    """同一行表达 pending/active/result/quarantine 的共享队列。"""

    def __init__(
        self,
        dsn: str,
        *,
        lease_ttl_sec: float = _DEFAULT_LEASE_TTL_SEC,
        max_size: int = 4,
        table: str = "reindex_jobs",
        owner: str | None = None,
    ) -> None:
        from psycopg_pool import ConnectionPool

        self._bare_table = bare_table_name(table)
        self._binding_base = pg_binding_base_locator(dsn)
        self._pool = build_queue_pool(
            ConnectionPool,
            dsn,
            max_size=max_size,
            pool_timeout=_POOL_TIMEOUT_SEC,
        )
        self._lease_ttl = validate_positive_number(lease_ttl_sec, "lease_ttl_sec")
        self._owner = owner or socket.gethostname()
        self._ensure_lock = threading.Lock()
        self._pool_started = False
        self._opened = False

    def owner_binding_locator(self) -> str:
        """返回已在真实连接中冻结的无秘密物理目标描述符。"""
        if getattr(self, "_frozen_binding", None) is None:
            raise ValueError("PG owner 绑定尚未完成 schema 冻结")
        return stored_owner_binding_locator(getattr(self, "_backend_locator", None))

    def ensure_owner_binding_locator(self) -> str:
        """受控完成首次初始化后返回 owner 绑定，供启动阶段调用。"""
        self._ensure()
        return self.owner_binding_locator()

    def _ensure(self, budget: PgOperationBudget | None = None) -> None:
        if self._opened:
            return
        operation = budget or PgOperationBudget.start(_DEFAULT_OPERATION_TIMEOUT_SEC)
        lock = getattr(self, "_ensure_lock", None)
        if lock is None:
            lock = threading.Lock()
            self._ensure_lock = lock
        if not lock.acquire(timeout=operation.remaining()):
            raise QueueOperationTimeout("等待 Pg schema 初始化超过时间预算")
        try:
            if self._opened:
                return
            open_and_verify_queue_schema(self, operation)
        finally:
            lock.release()

    def _ready(self, timeout_sec: float) -> PgOperationBudget:
        budget = PgOperationBudget.start(timeout_sec)
        self._ensure(budget)
        return budget

    def close(self) -> None:
        if getattr(self, "_pool_started", self._opened):
            try:
                self._pool.close()
            except Exception:  # noqa: BLE001
                logger.warning("关闭 Pg 队列连接池失败")
        self._pool_started = False
        self._opened = False

    def probe(self) -> None:
        budget = self._ready(_DEFAULT_OPERATION_TIMEOUT_SEC)
        with bounded_connection(self._pool, budget) as conn:
            execute_with_budget(conn, budget, "SELECT 1")

    @staticmethod
    def _job(project_id: str, kind: str, enqueued_at: float, *,
             token: str | None = None, meta_json: object = None,
             lease_expires_at: float | None = None,
             pending_version: str | None = None,
             owner_token: str | None = None) -> Job:
        return Job(
            project_id,
            kind,
            float(enqueued_at),
            token=token,
            meta=_meta_from_json(meta_json),
            lease_expires_at=lease_expires_at,
            pending_version=pending_version,
            owner_token=owner_token,
        )

    @staticmethod
    def _pending_job(row) -> Job | None:
        if row[3] is not None:
            return PgJobQueue._job(
                row[0], row[1], row[3], meta_json=row[4],
                pending_version=_pending_version(row),
            )
        if row[5] == "pending" and row[8] is None:
            return PgJobQueue._job(
                row[0], row[1], row[2],
                pending_version=_pending_version(row),
            )
        if row[8] is not None and row[9] is not None and row[9] < row[13]:
            return PgJobQueue._job(row[0], row[1], row[6] or row[2], meta_json=row[7])
        return None

    @staticmethod
    def _active_job(row) -> Job | None:
        if row[8] is None or row[9] is None or row[9] < row[13]:
            return None
        return PgJobQueue._job(
            row[0], row[1], row[6] or row[2], token=row[8],
            meta_json=row[7], lease_expires_at=row[9],
            owner_token=row[14] if len(row) > 14 and type(row[14]) is str else None,
        )

    @staticmethod
    def _expired_active_job(row) -> Job | None:
        if row[8] is None or row[9] is None or row[9] >= row[13]:
            return None
        return PgJobQueue._job(
            row[0], row[1], row[6] or row[2], token=row[8],
            meta_json=row[7], lease_expires_at=row[9],
            owner_token=row[14] if len(row) > 14 and type(row[14]) is str else None,
        )

    def enqueue(self, project_id: str, kind: str, meta: JobMeta | None = None) -> None:
        project, resolved_kind = validate_job_identity(project_id, kind)
        budget = self._ready(_DEFAULT_OPERATION_TIMEOUT_SEC)
        with bounded_connection(self._pool, budget) as conn:
            execute_with_budget(
                conn,
                budget,
                enqueue_sql(self._t),
                (project, resolved_kind, _meta_json(meta)),
            )

    def claim(self, *, owner_token: str, projects: set[str] | None, limit: int,
              timeout_sec: float) -> list[ClaimedJob]:
        owner = _required_text(owner_token, "owner_token")
        if type(limit) is not int or limit <= 0:
            raise ValueError("limit 必须是正整数")
        budget = PgOperationBudget.start(timeout_sec)
        if projects is not None and not projects:
            return []
        self._ensure(budget)
        params: list[object] = []
        if projects is not None:
            params.append(list(projects))
        params.extend((limit, owner, self._lease_ttl))
        rank = _kind_rank()
        budget.remaining()
        with bounded_connection(self._pool, budget) as conn:
            rows = execute_with_budget(
                conn,
                budget,
                claim_sql(self._t, project_filter=projects is not None, limited=True),
                tuple(params),
            ).fetchall()
            claims = [ClaimedJob(
                self._job(
                    row[0], row[1], row[2],
                    meta_json=row[6], lease_expires_at=row[5],
                ),
                row[3], row[4], row[5],
            ) for row in rows]
            claims.sort(key=lambda item: (
                item.job.enqueued_at,
                rank.get(item.job.kind, len(rank)),
                item.job.key,
            ))
        return claims

    def pending(self, projects: set[str] | None = None,
                limit: int | None = None) -> list[Job]:
        if projects is not None and not projects:
            return []
        if limit is not None and (type(limit) is not int or limit <= 0):
            return []
        claims = self.claim(
            owner_token=self._owner,
            projects=projects,
            limit=limit or 2_147_483_647,
            timeout_sec=_DEFAULT_OPERATION_TIMEOUT_SEC,
        )
        return [replace(
            claim.job,
            token=claim.claim_token,
            lease_expires_at=claim.lease_expires_at,
        ) for claim in claims]

    def peek(self) -> list[Job]:
        budget = self._ready(_DEFAULT_OPERATION_TIMEOUT_SEC)
        with bounded_connection(self._pool, budget) as conn:
            rows = execute_with_budget(
                conn, budget, scan_sql(self._t, snapshot=False),
            ).fetchall()
        jobs = [job for row in rows if (job := self._pending_job(row)) is not None]
        budget.remaining()
        return jobs

    def snapshot(self) -> QueueSnapshot:
        budget = self._ready(_DEFAULT_OPERATION_TIMEOUT_SEC)
        with bounded_connection(self._pool, budget) as conn:
            rows = execute_with_budget(
                conn, budget, scan_sql(self._t, snapshot=True),
            ).fetchall()
        pending: list[Job] = []
        active: list[Job] = []
        expired: list[Job] = []
        results: list[Job] = []
        quarantined: list[QuarantineRecord] = []
        for row in rows:
            if len(row) > 21 and row[21] is not None:
                try:
                    quarantined.append(QuarantineRecord(
                        row[0], row[1], row[8], row[16], row[17], row[18],
                        row[19], row[20], row[15], row[21],
                    ))
                except ValueError:
                    continue
                continue
            if (job := self._pending_job(row)) is not None:
                pending.append(job)
            if (job := self._active_job(row)) is not None:
                active.append(job)
            if (job := self._expired_active_job(row)) is not None:
                expired.append(job)
            if row[10] is not None and row[11] is not None:
                results.append(self._job(row[0], row[1], row[11], meta_json=row[12]))
        budget.remaining()
        return QueueSnapshot(
            pending=pending,
            active=active,
            results=results,
            expired_active=expired,
            quarantined=quarantined,
        )

    def migrate_pending(self, expected: Job, new_meta: JobMeta, *, timeout_sec: float) -> PendingMigrationResult:
        from .pg_queue_migration import migrate_pending
        return migrate_pending(self, expected, new_meta, timeout_sec=timeout_sec)

    def recover_owned(self, *, owner_token: str,
                      timeout_sec: float) -> list[ClaimedJob]:
        owner = _required_text(owner_token, "owner_token")
        budget = self._ready(timeout_sec)
        with bounded_connection(self._pool, budget) as conn:
            rows = execute_with_budget(
                conn, budget, recover_owned_sql(self._t), (owner,),
            ).fetchall()
        claims = [ClaimedJob(
            self._job(row[0], row[1], row[2], meta_json=row[6], lease_expires_at=row[5]),
            row[3], row[4], row[5],
        ) for row in rows]
        budget.remaining()
        return claims

    @overload
    def renew(self, claim: ClaimedJob, *, ttl_sec: float,
              timeout_sec: float) -> bool: ...

    @overload
    def renew(self, claim: Job) -> bool: ...

    def renew(self, claim: ClaimedJob | Job, *, ttl_sec: float | None = None,
              timeout_sec: float | None = None) -> bool:
        if isinstance(claim, Job):
            if ttl_sec is not None or timeout_sec is not None:
                raise TypeError("legacy renew 只接受单个 Job")
            legacy = self._legacy_claim(claim)
            if legacy is None:
                return False
            return self._renew_claim(
                legacy, self._lease_ttl, _DEFAULT_OPERATION_TIMEOUT_SEC,
            )
        if not isinstance(claim, ClaimedJob) or ttl_sec is None or timeout_sec is None:
            raise TypeError("新 renew 必须显式传 ClaimedJob、ttl_sec 和 timeout_sec")
        return self._renew_claim(
            claim, validate_positive_number(ttl_sec, "ttl_sec"), timeout_sec,
        )

    def _renew_claim(self, claim: ClaimedJob, ttl_sec: float,
                     timeout_sec: float) -> bool:
        budget = self._ready(timeout_sec)
        with bounded_connection(self._pool, budget) as conn:
            cursor = execute_with_budget(
                conn,
                budget,
                renew_sql(self._t),
                (
                    ttl_sec, claim.job.project_id, claim.job.kind,
                    claim.claim_token, claim.owner_token,
                ),
            )
        return cursor.rowcount == 1

    def retry(self, claim: ClaimedJob, *, reason: str,
              timeout_sec: float) -> bool:
        _required_text(reason, "reason")
        budget = self._ready(timeout_sec)
        return retry_claim(self._pool, self._t, budget, claim)

    def reject(self, claim: ClaimedJob, *, reason: str,
               timeout_sec: float) -> bool:
        failure_reason = _required_text(reason, "reason")
        budget = self._ready(timeout_sec)
        return reject_claim(self._pool, self._t, budget, claim, reason=failure_reason)

    def settle_expired_legacy_active(
        self, expected: Job, *, action: str, reason: str, timeout_sec: float,
    ) -> bool:
        from .pg_queue_transitions import settle_expired_legacy_active

        return settle_expired_legacy_active(
            self._pool, self._t, self._ready(timeout_sec), expected,
            action=action, reason=_required_text(reason, "reason"),
        )

    def reject_unowned_legacy_active(
        self, expected: Job, *, reason: str, timeout_sec: float,
    ) -> bool:
        budget = self._ready(timeout_sec)
        return reject_unowned_legacy_active(
            self._pool,
            self._t,
            budget,
            expected,
            reason=_required_text(reason, "reason"),
        )

    def quarantine(self, claim: ClaimedJob, *, attempt_id: str, fence: str,
                   process_identity: str, containment_kind: str,
                   native_ref: str, reason: str,
                   timeout_sec: float) -> QuarantineRecord:
        identity = tuple(_required_text(value, name) for value, name in (
            (attempt_id, "attempt_id"), (fence, "fence"),
            (process_identity, "process_identity"),
            (containment_kind, "containment_kind"), (native_ref, "native_ref"),
            (reason, "reason"),
        ))
        budget = self._ready(timeout_sec)
        params = (
            *identity,
            claim.job.project_id, claim.job.kind, claim.claim_token, claim.owner_token,
            *identity,
        )
        with bounded_connection(self._pool, budget) as conn:
            row = execute_with_budget(
                conn, budget, quarantine_sql(self._t), params,
            ).fetchone()
            if row is None:
                raise QueueClaimLost("quarantine claim 已失权或隔离身份冲突")
            record = QuarantineRecord(*row)
        return record

    def clear_quarantine(self, record: QuarantineRecord, *,
                         death_proof: ConfirmedProcessDeath,
                         timeout_sec: float) -> bool:
        budget = PgOperationBudget.start(timeout_sec)
        if not isinstance(record, QuarantineRecord):
            return False
        if not isinstance(death_proof, ConfirmedProcessDeath):
            return False
        if (
            death_proof.process_identity != record.process_identity
            or death_proof.containment_kind != record.containment_kind
            or death_proof.confirmed_at < record.quarantined_at
        ):
            return False
        self._ensure(budget)
        params = (
            record.project_id, record.kind, record.claim_token,
            record.attempt_id, record.fence, record.process_identity,
            record.containment_kind, record.native_ref, record.reason,
            record.quarantined_at,
        )
        with bounded_connection(self._pool, budget) as conn:
            cursor = execute_with_budget(
                conn, budget, clear_quarantine_sql(self._t), params,
            )
        return cursor.rowcount == 1

    def _load_publish_permit(
        self,
        conn,
        budget: PgOperationBudget,
        claim: ClaimedJob,
        desired_revision: str,
    ) -> PgPublishPermit:
        row = execute_with_budget(
            conn,
            budget,
            publish_lock_sql(self._t),
            (
                claim.job.project_id, claim.job.kind,
                claim.claim_token, claim.owner_token,
            ),
        ).fetchone()
        if row is None:
            raise QueueClaimLost("publish claim 已失权")
        if len(row) < 3:
            raise QueueClaimLost("publish guard 缺少持久 active 元数据")
        active_meta = _meta_from_json(row[2])
        if active_meta.target_commit != desired_revision:
            raise QueueClaimLost("active 期望版本与发布版本不一致")
        pending_meta = _meta_from_json(row[1]) if row[0] is not None else None
        superseded = (
            pending_meta is not None
            and pending_meta.target_commit != desired_revision
        )
        return PgPublishPermit(
            conn=conn,
            budget=budget,
            table=self._t,
            claim=claim,
            superseded=superseded,
        )

    @contextmanager
    def begin_publish(self, claim: ClaimedJob, *, desired_revision: str,
                      timeout_sec: float) -> Iterator[PgPublishPermit]:
        desired = _required_text(desired_revision, "desired_revision")
        budget = self._ready(timeout_sec)
        rollback_signal = RollbackTransaction("publish permit 未确认")
        try:
            with bounded_connection(self._pool, budget) as conn:
                permit = self._load_publish_permit(
                    conn, budget, claim, desired,
                )
                with _permit_lifecycle(permit, rollback_signal) as active_permit:
                    yield active_permit
        except RollbackTransaction as exc:
            if exc is not rollback_signal:
                raise

    def _legacy_claim(self, job: Job) -> ClaimedJob | None:
        if not job.token:
            return None
        lease = job.lease_expires_at or (time.time() + self._lease_ttl)
        return ClaimedJob(replace(job, token=None), job.token, self._owner, lease)

    def complete(self, job: Job) -> bool:
        if not job.token:
            return False
        budget = self._ready(_DEFAULT_OPERATION_TIMEOUT_SEC)
        with bounded_connection(self._pool, budget) as conn:
            matched, clean = acknowledge_active(
                conn,
                budget,
                self._t,
                project_id=job.project_id,
                kind=job.kind,
                claim_token=job.token,
                owner_token=self._owner,
                status="completed",
            )
        return matched and clean

    def discard(self, job: Job) -> bool:
        budget = self._ready(_DEFAULT_OPERATION_TIMEOUT_SEC)
        with bounded_connection(self._pool, budget) as conn:
            cursor = execute_with_budget(
                conn,
                budget,
                discard_sql(self._t),
                (job.project_id, job.kind, job.enqueued_at),
            )
        return cursor.rowcount > 0

    def release(self, job: Job) -> bool:
        claim = self._legacy_claim(job)
        return False if claim is None else self.retry(
            claim, reason="legacy release", timeout_sec=_DEFAULT_OPERATION_TIMEOUT_SEC,
        )

    def reclaim_stale_own(self) -> int:
        budget = self._ready(_DEFAULT_OPERATION_TIMEOUT_SEC)
        with bounded_connection(self._pool, budget) as conn:
            cursor = execute_with_budget(
                conn, budget, reclaim_owned_sql(self._t), (self._owner,),
            )
        return cursor.rowcount

    def break_lease(self, job: Job) -> bool:
        claim = self._legacy_claim(job)
        if claim is None:
            return False
        budget = self._ready(_DEFAULT_OPERATION_TIMEOUT_SEC)
        return retry_claim(
            self._pool,
            self._t,
            budget,
            claim,
            require_owner=False,
        )

    async def watch(self) -> AsyncIterator[None]:
        import asyncio

        while True:
            await asyncio.sleep(5.0)
            yield None

__all__ = ["PgJobQueue", "PgQueueInitializationError"]
