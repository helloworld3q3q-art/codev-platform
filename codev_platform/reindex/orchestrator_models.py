"""reindex 单一控制循环的状态、配置与窄端口。"""
from __future__ import annotations

import math
import time
from dataclasses import dataclass
from enum import Enum
from typing import Protocol

from .attempt_finalization import (
    AttemptFinalizationCheckpoint,
    FinalizationEvidence,
)
from .attempts import AttemptJournalEntry, AttemptSpec
from .health_refresh import HealthRefreshPort


class AttemptJournalPhase(str, Enum):
    CLAIMED = "claimed"
    PREPARING = "preparing"
    EXECUTING = "executing"
    TERMINATING = "terminating"
    FINALIZING = "finalizing"
    QUARANTINED = "quarantined"


def _process_shape(entry: AttemptJournalEntry) -> str:
    fields = (
        entry.pid,
        entry.process_identity,
        entry.containment_kind,
        entry.native_ref,
    )
    if all(value is None for value in fields):
        return "empty"
    if all(value is not None for value in fields):
        return "full"
    raise ValueError("journal 进程字段必须全空或全有")


@dataclass(frozen=True, slots=True)
class AttemptJournalRecord:
    """父侧唯一 attempt journal 记录；checkpoint 不复制 queue 终态。"""

    entry: AttemptJournalEntry
    finalization: AttemptFinalizationCheckpoint | None = None
    spec: AttemptSpec | None = None

    def __post_init__(self) -> None:
        if type(self.entry) is not AttemptJournalEntry:
            raise ValueError("journal entry 类型无效")
        if self.finalization is not None and type(self.finalization) is not AttemptFinalizationCheckpoint:
            raise ValueError("journal finalization 类型无效")
        if self.spec is not None and type(self.spec) is not AttemptSpec:
            raise ValueError("journal spec 类型无效")
        phase = self.phase
        shape = _process_shape(self.entry)
        if phase in {AttemptJournalPhase.CLAIMED, AttemptJournalPhase.PREPARING}:
            self._require(shape == "empty" and self.finalization is None)
        elif phase in {AttemptJournalPhase.EXECUTING, AttemptJournalPhase.TERMINATING}:
            self._require(shape == "full" and self.finalization is None)
        elif phase is AttemptJournalPhase.FINALIZING:
            self._validate_finalizing(shape)
        elif phase is AttemptJournalPhase.QUARANTINED:
            self._require(shape == "full" and self.finalization is None)
        if self.finalization is not None:
            if (
                self.finalization.attempt_id != self.entry.attempt_id
                or self.finalization.fence != self.entry.fence
            ):
                raise ValueError("journal 与 finalization attempt/fence 身份不匹配")
        if self.spec is not None:
            expected = (
                self.entry.attempt_id,
                self.entry.fence,
                self.entry.project_id,
                self.entry.kind,
            )
            actual = (
                self.spec.attempt_id,
                self.spec.fence,
                self.spec.project_id,
                self.spec.kind,
            )
            if actual != expected:
                raise ValueError("journal 与 spec 身份不匹配")
            if (
                self.finalization is not None
                and self.finalization.target_commit != self.spec.target_commit
            ):
                raise ValueError("journal checkpoint 与 spec target_commit 不匹配")

    @property
    def phase(self) -> AttemptJournalPhase:
        try:
            return AttemptJournalPhase(self.entry.state)
        except ValueError:
            raise ValueError("journal phase 无效") from None

    @staticmethod
    def _require(condition: bool) -> None:
        if not condition:
            raise ValueError("journal phase、进程形状与 finalization 不一致")

    def _validate_finalizing(self, shape: str) -> None:
        checkpoint = self.finalization
        self._require(type(checkpoint) is AttemptFinalizationCheckpoint)
        no_process = checkpoint.evidence in {
            FinalizationEvidence.DEPENDENCY_BLOCK,
            FinalizationEvidence.NO_PROCESS_RETRY,
        }
        self._require(shape == ("empty" if no_process else "full"))


_JOURNAL_TRANSITIONS = {
    AttemptJournalPhase.CLAIMED: frozenset({
        AttemptJournalPhase.PREPARING,
        AttemptJournalPhase.FINALIZING,
    }),
    AttemptJournalPhase.PREPARING: frozenset({
        AttemptJournalPhase.EXECUTING,
        AttemptJournalPhase.FINALIZING,
        AttemptJournalPhase.QUARANTINED,
    }),
    AttemptJournalPhase.EXECUTING: frozenset({
        AttemptJournalPhase.TERMINATING,
        AttemptJournalPhase.FINALIZING,
        AttemptJournalPhase.QUARANTINED,
    }),
    AttemptJournalPhase.TERMINATING: frozenset({
        AttemptJournalPhase.TERMINATING,
        AttemptJournalPhase.FINALIZING,
        AttemptJournalPhase.QUARANTINED,
    }),
    AttemptJournalPhase.FINALIZING: frozenset({
        AttemptJournalPhase.FINALIZING,
        AttemptJournalPhase.QUARANTINED,
    }),
    AttemptJournalPhase.QUARANTINED: frozenset(),
}


def validate_journal_transition(
    before: AttemptJournalPhase,
    after: AttemptJournalPhase,
) -> None:
    """拒绝跳跃、回退和从 quarantine 继续执行。"""
    if type(before) is not AttemptJournalPhase or type(after) is not AttemptJournalPhase:
        raise ValueError("journal 跃迁只接受 AttemptJournalPhase")
    if after not in _JOURNAL_TRANSITIONS[before]:
        raise ValueError(f"journal 非法跃迁：{before.value} -> {after.value}")


def _positive(value: object, field: str, *, allow_zero: bool = False) -> float:
    if type(value) not in (int, float):
        raise ValueError(f"{field} 必须是有限数字")
    number = float(value)
    valid = number >= 0 if allow_zero else number > 0
    if not math.isfinite(number) or not valid:
        raise ValueError(f"{field} 超出有限预算范围")
    return number


@dataclass(frozen=True, slots=True)
class OrchestratorSettings:
    poll_sec: float
    queue_op_timeout_sec: float
    heartbeat_sec: float
    renew_sec: float
    lease_ttl_sec: float
    kill_grace_sec: float
    kill_timeout_sec: float
    cleanup_timeout_sec: float
    startup_timeout_sec: float = 10.0
    readiness_timeout_sec: float = 5.0
    recovery_timeout_sec: float = 30.0
    health_timeout_sec: float = 60.0
    max_concurrency: int = 1

    def __post_init__(self) -> None:
        for field in (
            "poll_sec",
            "queue_op_timeout_sec",
            "heartbeat_sec",
            "renew_sec",
            "lease_ttl_sec",
            "kill_timeout_sec",
            "cleanup_timeout_sec",
            "startup_timeout_sec",
            "readiness_timeout_sec",
            "recovery_timeout_sec",
            "health_timeout_sec",
        ):
            object.__setattr__(self, field, _positive(getattr(self, field), field))
        object.__setattr__(
            self,
            "kill_grace_sec",
            _positive(self.kill_grace_sec, "kill_grace_sec", allow_zero=True),
        )
        if type(self.max_concurrency) is not int or self.max_concurrency != 1:
            raise ValueError("isolated reindex 最大并发必须固定为 1")
        if self.lease_ttl_sec <= self.renew_sec + self.poll_sec:
            raise ValueError("lease_ttl_sec 必须覆盖续租与轮询窗口")

    @property
    def termination_lease_ttl_sec(self) -> float:
        return max(
            self.lease_ttl_sec,
            self.kill_grace_sec + self.kill_timeout_sec + 2 * self.poll_sec,
        )

    @property
    def startup_lease_ttl_sec(self) -> float:
        return max(
            self.lease_ttl_sec,
            self.startup_timeout_sec
            + 2 * self.queue_op_timeout_sec
            + 2 * self.poll_sec,
        )

    @property
    def finalization_lease_ttl_sec(self) -> float:
        return max(
            self.termination_lease_ttl_sec,
            self.cleanup_timeout_sec
            + 3 * self.queue_op_timeout_sec
            + 2 * self.poll_sec,
        )


class AttemptJournalPort(Protocol):
    def load(self) -> AttemptJournalRecord | None: ...

    def create(self, record: AttemptJournalRecord) -> None: ...

    def transition(
        self,
        record: AttemptJournalRecord,
        *,
        expected: AttemptJournalPhase,
    ) -> None: ...

    def clear(
        self,
        *,
        attempt_id: str,
        fence: str,
        expected: AttemptJournalPhase,
    ) -> None: ...


class ControlClock(Protocol):
    def monotonic(self) -> float: ...

    def time(self) -> float: ...

    def wait(self, timeout_sec: float) -> None: ...


class ControlStatusPort(Protocol):
    def heartbeat(self, phase: str, claim: object | None = None) -> None: ...


class NullControlStatus:
    def heartbeat(self, phase: str, claim: object | None = None) -> None:
        del phase, claim


class SystemControlClock:
    def monotonic(self) -> float:
        return time.monotonic()

    def time(self) -> float:
        return time.time()

    def wait(self, timeout_sec: float) -> None:
        time.sleep(timeout_sec)


class OrchestratorFatalError(RuntimeError):
    """控制循环必须停止，不能继续领取新任务。"""


class OrchestratorClaimLost(OrchestratorFatalError):
    """当前 attempt 已失去 queue authority。"""


class OrchestratorQuarantined(OrchestratorFatalError):
    """进程树死亡无法证明，已进入持久 quarantine。"""


__all__ = [
    "AttemptJournalPhase",
    "AttemptJournalPort",
    "AttemptJournalRecord",
    "ControlClock",
    "ControlStatusPort",
    "HealthRefreshPort",
    "NullControlStatus",
    "OrchestratorClaimLost",
    "OrchestratorFatalError",
    "OrchestratorQuarantined",
    "OrchestratorSettings",
    "SystemControlClock",
    "validate_journal_transition",
]
