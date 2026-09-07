"""reindex attempt 的平台无关进程隔离契约。"""
from __future__ import annotations

import hashlib
import hmac
import math
import re
import time
from collections.abc import Sequence
from dataclasses import dataclass
from enum import Enum
from pathlib import Path
from typing import Protocol

from codev_platform.reindex.attempts import (
    AttemptJournalEntry,
    ConfirmedProcessDeath,
)

_MAX_TEXT_BYTES = 4096
_PROCESS_IDENTITY_PREFIX = "reindex-process:v1"
_PROCESS_IDENTITY_RE = re.compile(
    rf"{re.escape(_PROCESS_IDENTITY_PREFIX)}:(?P<pid>[1-9][0-9]*):"
    r"(?P<native>[0-9a-f]{64}):(?P<birth>[0-9a-f]{64})\Z"
)


def _bounded_text(value: object, field_name: str) -> str:
    if type(value) is not str or not value.strip():
        raise ValueError(f"{field_name} 必须是非空字符串")
    try:
        size = len(value.encode("utf-8"))
    except UnicodeError:
        raise ValueError(f"{field_name} 必须是有效 UTF-8 文本") from None
    if size > _MAX_TEXT_BYTES:
        raise ValueError(f"{field_name} 超过长度上限")
    return value


def _positive_pid(value: object) -> int:
    if type(value) is not int or value <= 0:
        raise ValueError("pid 必须是正整数")
    return value


def _finite_nonnegative(value: object, field_name: str) -> float:
    if type(value) not in (int, float):
        raise ValueError(f"{field_name} 必须是有限非负数")
    number = float(value)
    if not math.isfinite(number) or number < 0:
        raise ValueError(f"{field_name} 必须是有限非负数")
    return number


def _strict_bool(value: object, field_name: str) -> bool:
    if type(value) is not bool:
        raise ValueError(f"{field_name} 必须是 bool")
    return value


def _digest(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _parse_process_identity(process_identity: object) -> tuple[int, str, str]:
    identity = _bounded_text(process_identity, "process_identity")
    match = _PROCESS_IDENTITY_RE.fullmatch(identity)
    if match is None:
        raise ValueError("process_identity 格式或版本无效")
    return int(match["pid"]), match["native"], match["birth"]


def build_process_identity(*, pid: int, native_ref: str, birth_marker: str) -> str:
    """由 PID、原生引用和内核出生标记构造不可混淆的版本化身份。"""
    resolved_pid = _positive_pid(pid)
    resolved_ref = _bounded_text(native_ref, "native_ref")
    resolved_birth = _bounded_text(birth_marker, "birth_marker")
    return (
        f"{_PROCESS_IDENTITY_PREFIX}:{resolved_pid}:"
        f"{_digest(resolved_ref)}:{_digest(resolved_birth)}"
    )


def validate_process_identity(
    process_identity: str,
    *,
    native_ref: str,
    pid: int | None = None,
    birth_marker: str | None = None,
) -> int:
    """校验身份与原生引用，可选复核 PID 和当前内核出生标记。"""
    embedded_pid, native_digest, birth_digest = _parse_process_identity(
        process_identity
    )
    resolved_ref = _bounded_text(native_ref, "native_ref")
    if not hmac.compare_digest(native_digest, _digest(resolved_ref)):
        raise ValueError("process_identity 与 native_ref 不匹配")
    if pid is not None and embedded_pid != _positive_pid(pid):
        raise ValueError("process_identity 与 pid 不匹配")
    if birth_marker is not None:
        resolved_birth = _bounded_text(birth_marker, "birth_marker")
        if not hmac.compare_digest(birth_digest, _digest(resolved_birth)):
            raise ValueError("process_identity 与出生标记不匹配")
    return embedded_pid


def _validate_death_proof(proof: object) -> ConfirmedProcessDeath:
    if not isinstance(proof, ConfirmedProcessDeath):
        raise ValueError("death_proof 类型无效")
    _parse_process_identity(proof.process_identity)
    _bounded_text(proof.containment_kind, "death_proof.containment_kind")
    _bounded_text(proof.evidence, "death_proof.evidence")
    _finite_nonnegative(proof.confirmed_at, "death_proof.confirmed_at")
    return proof


@dataclass(frozen=True, slots=True)
class Deadline:
    """只保存单调绝对截止点的统一时间预算。"""

    expires_at: float

    def __post_init__(self) -> None:
        object.__setattr__(
            self,
            "expires_at",
            _finite_nonnegative(self.expires_at, "expires_at"),
        )

    @classmethod
    def start(cls, timeout_sec: float, *, now: float | None = None) -> Deadline:
        """从一个有限非负时长创建截止点；零表示立即到期。"""
        timeout = _finite_nonnegative(timeout_sec, "timeout_sec")
        started = cls._now(now)
        expires_at = started + timeout
        if not math.isfinite(expires_at):
            raise ValueError("deadline 截止点必须有限")
        return cls(expires_at)

    def remaining(self, *, now: float | None = None) -> float:
        """返回可直接交给等待 API 的非负剩余秒数。"""
        return max(0.0, self.expires_at - self._now(now))

    def expired(self, *, now: float | None = None) -> bool:
        """判断同一单调预算是否已经耗尽。"""
        return self.remaining(now=now) <= 0.0

    @staticmethod
    def _now(now: float | None) -> float:
        value = time.monotonic() if now is None else now
        return _finite_nonnegative(value, "单调时间")


@dataclass(frozen=True, slots=True)
class ExecutionHandle:
    """已创建 containment 及其根进程的不可变句柄。"""

    attempt_id: str
    pid: int
    process_identity: str
    containment_kind: str
    native_ref: str
    started_at: float

    def __post_init__(self) -> None:
        _bounded_text(self.attempt_id, "attempt_id")
        _bounded_text(self.containment_kind, "containment_kind")
        validate_process_identity(
            self.process_identity,
            pid=self.pid,
            native_ref=self.native_ref,
        )
        object.__setattr__(
            self,
            "started_at",
            _finite_nonnegative(self.started_at, "started_at"),
        )


def handle_epoch_time(handle: ExecutionHandle, observed_at: float) -> float:
    """把句柄相关事件时间夹紧到进程启动时刻，抵御 wall clock 回拨。"""
    if not isinstance(handle, ExecutionHandle):
        raise ValueError("handle 类型无效")
    observed = _finite_nonnegative(observed_at, "事件时间")
    return max(handle.started_at, observed)


def validate_death_proof_for_handle(
    handle: ExecutionHandle,
    proof: ConfirmedProcessDeath,
) -> ConfirmedProcessDeath:
    """证明必须属于精确 handle，且不得早于该进程启动。"""
    if not isinstance(handle, ExecutionHandle):
        raise ValueError("handle 类型无效")
    resolved = _validate_death_proof(proof)
    if (
        resolved.process_identity != handle.process_identity
        or resolved.containment_kind != handle.containment_kind
        or resolved.confirmed_at < handle.started_at
    ):
        raise ValueError("死亡证明与 handle 不匹配")
    return resolved


@dataclass(frozen=True, slots=True)
class ProcessReference:
    """管理面死亡确认所需的最小持久进程引用。"""

    process_identity: str
    containment_kind: str
    native_ref: str

    def __post_init__(self) -> None:
        _bounded_text(self.containment_kind, "containment_kind")
        validate_process_identity(
            self.process_identity,
            native_ref=self.native_ref,
        )


def execution_handle_from_journal(
    journal: AttemptJournalEntry,
) -> ExecutionHandle | None:
    """从 journal 恢复句柄；进程字段必须全有或全无。"""
    if not isinstance(journal, AttemptJournalEntry):
        raise ValueError("journal 类型无效")
    fields = (
        journal.pid,
        journal.process_identity,
        journal.containment_kind,
        journal.native_ref,
    )
    present = tuple(value is not None for value in fields)
    if not any(present):
        return None
    if not all(present):
        raise ValueError("journal 进程字段必须全有或全无")
    return ExecutionHandle(
        attempt_id=journal.attempt_id,
        pid=journal.pid,
        process_identity=journal.process_identity,
        containment_kind=journal.containment_kind,
        native_ref=journal.native_ref,
        started_at=journal.started_at,
    )


@dataclass(frozen=True, slots=True)
class TerminationReport:
    """一次有界终止请求的结构化结果。"""

    requested_at: float
    finished_at: float
    graceful: bool
    forced: bool
    confirmed_dead: bool
    death_proof: ConfirmedProcessDeath | None
    note: str

    def __post_init__(self) -> None:
        requested = _finite_nonnegative(self.requested_at, "requested_at")
        finished = _finite_nonnegative(self.finished_at, "finished_at")
        graceful = _strict_bool(self.graceful, "graceful")
        forced = _strict_bool(self.forced, "forced")
        confirmed = _strict_bool(self.confirmed_dead, "confirmed_dead")
        _bounded_text(self.note, "note")
        if finished < requested:
            raise ValueError("finished_at 不得早于 requested_at")
        if confirmed != (self.death_proof is not None):
            raise ValueError("confirmed_dead 必须与 death_proof 一致")
        self._validate_termination_state(graceful, forced, confirmed)
        if self.death_proof is not None:
            self._validate_proof_window(requested, finished)
        object.__setattr__(self, "requested_at", requested)
        object.__setattr__(self, "finished_at", finished)

    @staticmethod
    def _validate_termination_state(
        graceful: bool,
        forced: bool,
        confirmed: bool,
    ) -> None:
        if graceful and forced:
            raise ValueError("graceful 与 forced 不能同时为真")
        if graceful and not confirmed:
            raise ValueError("graceful 退出必须有死亡证明")
        if confirmed and not (graceful or forced):
            raise ValueError("已确认死亡必须标明退出路径")

    def _validate_proof_window(self, requested: float, finished: float) -> None:
        proof = _validate_death_proof(self.death_proof)
        if proof.confirmed_at < requested or proof.confirmed_at > finished:
            raise ValueError("death_proof 时间不在终止报告窗口内")


class RecoveryState(str, Enum):
    """父进程重启后对 journal 中进程状态的封闭分类。"""

    ACTIVE = "active"
    CONFIRMED_DEAD = "confirmed_dead"
    NEVER_STARTED = "never_started"
    UNCONFIRMED = "unconfirmed"


@dataclass(frozen=True, slots=True)
class RecoveryReport:
    """恢复探测结果；每个状态只允许唯一字段组合。"""

    state: RecoveryState
    handle: ExecutionHandle | None
    death_proof: ConfirmedProcessDeath | None
    note: str

    def __post_init__(self) -> None:
        if type(self.state) is not RecoveryState:
            raise ValueError("state 必须是 RecoveryState")
        _bounded_text(self.note, "note")
        has_handle = isinstance(self.handle, ExecutionHandle)
        has_proof = isinstance(self.death_proof, ConfirmedProcessDeath)
        if self.handle is not None and not has_handle:
            raise ValueError("handle 类型无效")
        if self.death_proof is not None and not has_proof:
            raise ValueError("death_proof 类型无效")
        expected = {
            RecoveryState.ACTIVE: (True, False),
            RecoveryState.CONFIRMED_DEAD: (True, True),
            RecoveryState.NEVER_STARTED: (False, False),
            RecoveryState.UNCONFIRMED: (False, False),
        }[self.state]
        if (has_handle, has_proof) != expected:
            raise ValueError("恢复状态与字段组合不一致")
        if has_proof:
            validate_death_proof_for_handle(self.handle, self.death_proof)


class ProcessBackendReadinessError(RuntimeError):
    """生产 containment 能力无法在预算内得到无歧义证明。"""


class AttemptProcessStartError(RuntimeError):
    """启动部分成功时保留句柄，避免丢失待隔离的进程引用。"""

    __slots__ = ("handle", "death_proof", "retryable", "note")

    def __init__(
        self,
        *,
        handle: ExecutionHandle | None,
        death_proof: ConfirmedProcessDeath | None,
        retryable: bool,
        note: str,
    ) -> None:
        if handle is not None and not isinstance(handle, ExecutionHandle):
            raise ValueError("handle 类型无效")
        resolved_retryable = _strict_bool(retryable, "retryable")
        resolved_note = _bounded_text(note, "note")
        self._validate_fields(handle, death_proof, resolved_retryable)
        super().__init__(resolved_note)
        self.handle = handle
        self.death_proof = death_proof
        self.retryable = resolved_retryable
        self.note = resolved_note

    @staticmethod
    def _validate_fields(
        handle: ExecutionHandle | None,
        proof: ConfirmedProcessDeath | None,
        retryable: bool,
    ) -> None:
        if handle is None and proof is not None:
            raise ValueError("没有 handle 时不得携带 death_proof")
        if handle is not None and proof is None and retryable:
            raise ValueError("进程死亡未确认时不得直接重试")
        if proof is None:
            return
        validate_death_proof_for_handle(handle, proof)


class AttemptProcessBackend(Protocol):
    """各平台 containment 后端共同实现的无分支端口。"""

    def assert_ready(self, deadline: Deadline) -> None:
        """在领取任务前有界证明生产 containment 能力，不启动业务目标。"""
        raise NotImplementedError("进程后端方法")

    def prepare(
        self,
        *,
        attempt_id: str,
        argv: Sequence[str],
        cwd: Path,
        bootstrap_log: Path,
        deadline: Deadline,
    ) -> ExecutionHandle:
        """创建已受围栏保护、但目标尚未执行的 blocked/suspended 进程。

        调用方必须先持久化返回的 handle 并确认落盘，再调用 activate；
        持久化失败时只能终止该 handle，禁止放行目标。
        """
        raise NotImplementedError("进程后端方法")

    def activate(
        self,
        handle: ExecutionHandle,
        deadline: Deadline,
    ) -> None:
        """在同一 handle 已持久化后放行目标。

        失败必须抛出携带同一 handle 的 AttemptProcessStartError。
        """
        raise NotImplementedError("进程后端方法")

    def poll(self, handle: ExecutionHandle) -> int | None:
        """只执行非阻塞状态探测。"""
        raise NotImplementedError("进程后端方法")

    def terminate(
        self,
        handle: ExecutionHandle,
        *,
        grace_sec: float,
        deadline: Deadline,
    ) -> TerminationReport:
        raise NotImplementedError("进程后端方法")

    def recover(
        self,
        journal: AttemptJournalEntry,
        deadline: Deadline,
    ) -> RecoveryReport:
        raise NotImplementedError("进程后端方法")

    def recover_handle(
        self,
        handle: ExecutionHandle,
        deadline: Deadline,
    ) -> RecoveryReport:
        """仅按完整持久句柄恢复，不得推断为 NEVER_STARTED。"""
        raise NotImplementedError("进程后端方法")

    def confirm_dead(
        self,
        handle: ExecutionHandle,
        deadline: Deadline,
    ) -> ConfirmedProcessDeath | None:
        raise NotImplementedError("进程后端方法")

    def confirm_reference_dead(
        self,
        reference: ProcessReference,
        deadline: Deadline,
    ) -> ConfirmedProcessDeath | None:
        raise NotImplementedError("进程后端方法")


__all__ = [
    "AttemptProcessBackend",
    "AttemptProcessStartError",
    "Deadline",
    "ExecutionHandle",
    "ProcessReference",
    "ProcessBackendReadinessError",
    "RecoveryReport",
    "RecoveryState",
    "TerminationReport",
    "build_process_identity",
    "execution_handle_from_journal",
    "handle_epoch_time",
    "validate_death_proof_for_handle",
    "validate_process_identity",
]
