"""reindex 队列的领域模型、公共端口与中性校验。"""
from __future__ import annotations

import math
import re
from collections.abc import AsyncIterator
from contextlib import AbstractContextManager
from dataclasses import dataclass, field
from enum import Enum
from typing import TYPE_CHECKING, Protocol, runtime_checkable

if TYPE_CHECKING:
    from codev_platform.reindex.attempts import ConfirmedProcessDeath

_KEY_SEPARATOR = "__"
_MIN_TIMEOUT_SEC = 0.01
_OID_RE = re.compile(r"(?:[0-9a-f]{40}|[0-9a-f]{64})\Z")


class QueueOperationTimeout(TimeoutError):
    """队列操作未能在调用方预算内完成。"""


class QueueClaimLost(RuntimeError):
    """当前 worker 已无法证明自己仍持有目标 claim。"""


class PendingMigrationOutcome(str, Enum):
    """一次精确 pending 迁移的确定性结果。"""

    MIGRATED = "migrated"
    NOT_PENDING = "not_pending"
    VERSION_CONFLICT = "version_conflict"
    BUSY = "busy"


class DependencyQueueState(str, Enum):
    """依赖队列只读视图的有限状态。"""

    ACTIVE = "active"
    PENDING = "pending"
    REPLACEMENT = "replacement"
    ABSENT = "absent"


def _required_text(value: object, field_name: str) -> str:
    if type(value) is not str or not value.strip():
        raise ValueError(f"{field_name} 必须是非空字符串")
    return value


def validate_timeout(value: object) -> float:
    """校验 File/PG 都能表达的有限操作预算。"""
    if type(value) not in (int, float):
        raise ValueError("timeout_sec 必须是有限数字")
    timeout = float(value)
    if not math.isfinite(timeout) or timeout < _MIN_TIMEOUT_SEC:
        raise ValueError(f"timeout_sec 必须大于等于 {_MIN_TIMEOUT_SEC}")
    return timeout


def validate_positive_number(value: object, field_name: str) -> float:
    if type(value) not in (int, float):
        raise ValueError(f"{field_name} 必须是有限正数")
    number = float(value)
    if not math.isfinite(number) or number <= 0:
        raise ValueError(f"{field_name} 必须是有限正数")
    return number


def validate_job_identity(project_id: str, kind: str) -> tuple[str, str]:
    """以同一真值校验所有队列适配器的 key。"""
    from codev_platform.core.project_id import validate as validate_project_id
    from codev_platform.reindex.runners import kinds

    project = validate_project_id(project_id)
    resolved_kind = _required_text(kind, "kind")
    known = kinds()
    if known and resolved_kind not in known:
        raise ValueError(f"未知 reindex kind: {resolved_kind!r} (允许: {sorted(known)})")
    if any(separator in resolved_kind for separator in ("/", "\\", "..", _KEY_SEPARATOR)):
        raise ValueError(f"非法 reindex kind: {resolved_kind!r} (含路径分隔符)")
    return project, resolved_kind


def validate_target_commit(value: object) -> str:
    """校验依赖视图用于精确匹配的目标版本。"""
    target = _required_text(value, "target_commit")
    if _OID_RE.fullmatch(target) is None or set(target) == {"0"}:
        raise ValueError("target_commit 必须是完整非零小写 OID")
    return target


@dataclass(frozen=True)
class JobMeta:
    """队列协议 v2 元数据；默认值兼容旧生产者。"""

    source: str = "legacy"
    pull_policy: str | None = None
    target_commit: str | None = None


@dataclass(frozen=True)
class Job:
    """worker 与管理命令看到的 reindex 请求。"""

    project_id: str
    kind: str
    enqueued_at: float
    token: str | None = None
    meta: JobMeta = field(default_factory=JobMeta)
    lease_expires_at: float | None = None
    pending_version: str | None = None
    owner_token: str | None = None

    @property
    def key(self) -> str:
        return f"{self.project_id}{_KEY_SEPARATOR}{self.kind}"


@dataclass(frozen=True, slots=True)
class ClaimedJob:
    job: Job
    claim_token: str
    owner_token: str
    lease_expires_at: float

    def __post_init__(self) -> None:
        if not isinstance(self.job, Job):
            raise ValueError("job 必须是 Job")
        _required_text(self.claim_token, "claim_token")
        _required_text(self.owner_token, "owner_token")
        validate_positive_number(self.lease_expires_at, "lease_expires_at")


@dataclass(frozen=True, slots=True)
class QuarantineRecord:
    project_id: str
    kind: str
    claim_token: str
    attempt_id: str
    fence: str
    process_identity: str
    containment_kind: str
    native_ref: str
    reason: str
    quarantined_at: float

    def __post_init__(self) -> None:
        for field_name in (
            "project_id", "kind", "claim_token", "attempt_id", "fence",
            "process_identity", "containment_kind", "native_ref", "reason",
        ):
            _required_text(getattr(self, field_name), field_name)
        value = self.quarantined_at
        if type(value) not in (int, float) or not math.isfinite(float(value)) or value < 0:
            raise ValueError("quarantined_at 必须是有限非负时间")


@dataclass(frozen=True)
class QueueSnapshot:
    """互斥展示 pending、active、result 与 quarantine 分桶。"""

    pending: list[Job] = field(default_factory=list)
    active: list[Job] = field(default_factory=list)
    results: list[Job] = field(default_factory=list)
    expired_active: list[Job] = field(default_factory=list)
    quarantined: list[QuarantineRecord] = field(default_factory=list)


@runtime_checkable
class JobQueue(Protocol):
    """生产者、legacy worker 与诊断命令的兼容协议。"""

    def enqueue(self, project_id: str, kind: str, meta: JobMeta | None = None) -> None: ...
    def pending(self, projects: set[str] | None = None,
                limit: int | None = None) -> list[Job]: ...
    def peek(self) -> list[Job]: ...
    def snapshot(self) -> QueueSnapshot: ...
    def complete(self, job: Job) -> bool: ...
    def discard(self, job: Job) -> bool: ...
    def watch(self) -> AsyncIterator[None]: ...


@dataclass(frozen=True, slots=True)
class PendingMigrationResult:
    """迁移后端返回的无副作用结果。"""

    outcome: PendingMigrationOutcome

    @property
    def migrated(self) -> bool:
        return self.outcome is PendingMigrationOutcome.MIGRATED


@runtime_checkable
class PendingMigrationQueue(Protocol):
    """仅支持 pending 精确 CAS 迁移的可选队列端口。"""

    def migrate_pending(
        self,
        expected: Job,
        new_meta: JobMeta,
        *,
        timeout_sec: float,
    ) -> PendingMigrationResult: ...


class PublishPermit(Protocol):
    @property
    def superseded(self) -> bool: ...
    def ack(self) -> bool: ...


class WorkerQueuePort(Protocol):
    def claim(self, *, owner_token: str, projects: set[str] | None, limit: int,
              timeout_sec: float) -> list[ClaimedJob]: ...
    def renew(self, claim: ClaimedJob, *, ttl_sec: float,
              timeout_sec: float) -> bool: ...
    def recover_owned(self, *, owner_token: str,
                      timeout_sec: float) -> list[ClaimedJob]: ...
    def retry(self, claim: ClaimedJob, *, reason: str,
              timeout_sec: float) -> bool: ...
    def reject(self, claim: ClaimedJob, *, reason: str,
               timeout_sec: float) -> bool: ...
    def quarantine(self, claim: ClaimedJob, *, attempt_id: str, fence: str,
                   process_identity: str, containment_kind: str,
                   native_ref: str, reason: str,
                   timeout_sec: float) -> QuarantineRecord: ...
    def begin_publish(self, claim: ClaimedJob, *, desired_revision: str,
                      timeout_sec: float) -> AbstractContextManager[PublishPermit]: ...


class DependencyQueueViewPort(Protocol):
    """供依赖门禁查询队列事实，不领取或改写任何任务。"""

    def dependency_state(
        self,
        project_id: str,
        kind: str,
        target_commit: str,
        *,
        timeout_sec: float,
    ) -> DependencyQueueState: ...


class AdminQueuePort(Protocol):
    def clear_quarantine(self, record: QuarantineRecord, *,
                         death_proof: ConfirmedProcessDeath,
                         timeout_sec: float) -> bool: ...


__all__ = [
    "AdminQueuePort", "ClaimedJob", "DependencyQueueState",
    "DependencyQueueViewPort", "Job", "JobMeta", "JobQueue",
    "PendingMigrationOutcome", "PendingMigrationQueue", "PendingMigrationResult",
    "PublishPermit", "QuarantineRecord", "QueueClaimLost",
    "QueueOperationTimeout", "QueueSnapshot", "WorkerQueuePort",
    "validate_job_identity", "validate_positive_number", "validate_target_commit",
    "validate_timeout",
]
