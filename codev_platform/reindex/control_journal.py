"""独立 control journal：为 attempt 与 health 提供耐久、严格、CAS 状态。"""
from __future__ import annotations

import dataclasses
import json
import math
import time
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

from codev_platform.core.paths import data_root

from .attempt_finalization import (
    decode_finalization_checkpoint,
    encode_finalization_checkpoint,
)
from .attempt_process import ExecutionHandle
from .attempts import AttemptJournalEntry, AttemptSpec, CanonicalJsonObject
from .file_advisory_lock import advisory_lock
from .file_durability import durable_write_replace, read_regular_file_bounded
from .health_refresh import HealthOperationEntry
from .orchestrator_models import (
    AttemptJournalPhase,
    AttemptJournalRecord,
    validate_journal_transition,
)

_SCHEMA_VERSION = 1
_MAX_BYTES = 128 * 1024
_LOCK_TIMEOUT_SEC = 5.0
_JOURNAL_NAME = "reindex-control-journal.json"


class ControlJournalError(RuntimeError):
    """control journal 读写或一致性失败。"""


class ControlJournalConflictError(ControlJournalError):
    """CAS 前提不成立，调用方已失去对旧状态的写入权。"""


class ControlJournalCorruptionError(ControlJournalError):
    """journal 文件不满足严格 schema，必须停止领取新任务。"""


class ControlJournalTimeout(ControlJournalError):
    """在有限预算内无法取得独立 journal 锁。"""


@dataclass(frozen=True, slots=True)
class _Envelope:
    attempt: AttemptJournalRecord | None
    health: HealthOperationEntry | None


@dataclass(frozen=True, slots=True)
class _LockDeadline:
    expires_at: float

    @classmethod
    def start(cls, timeout_sec: float) -> _LockDeadline:
        if type(timeout_sec) not in (int, float) or not math.isfinite(timeout_sec):
            raise ValueError("journal 锁超时必须是有限正数")
        if timeout_sec <= 0:
            raise ValueError("journal 锁超时必须是有限正数")
        return cls(time.monotonic() + float(timeout_sec))

    def remaining(self) -> float:
        return max(0.0, self.expires_at - time.monotonic())

    def check(self) -> None:
        if self.remaining() <= 0:
            raise ControlJournalTimeout("control journal 锁等待超时")


def control_journal_path() -> Path:
    """返回唯一受管 control journal 路径。"""
    root = data_root() / "run"
    root.mkdir(parents=True, exist_ok=True)
    return root / _JOURNAL_NAME


def _text(value: object, field: str) -> str:
    if type(value) is not str or value != value.strip() or not value:
        raise ValueError(f"{field} 必须是非空规范字符串")
    try:
        encoded = value.encode("utf-8")
    except UnicodeError:
        raise ValueError(f"{field} 必须是有效 UTF-8") from None
    if len(encoded) > 4096 or any(ord(char) < 32 for char in value):
        raise ValueError(f"{field} 超过长度上限或包含控制字符")
    return value


def _pairs_object(pairs: list[tuple[str, object]]) -> dict[str, object]:
    value: dict[str, object] = {}
    for key, item in pairs:
        if key in value:
            raise ValueError("journal JSON 包含重复键")
        value[key] = item
    return value


def _reject_constant(_value: str) -> object:
    raise ValueError("journal JSON 不允许非有限数")


def _object(value: object, field: str) -> dict[str, object]:
    if type(value) is not dict:
        raise ValueError(f"{field} 必须是对象")
    return dict(value)


def _fields(value: object, model: type[object], field: str) -> dict[str, object]:
    payload = _object(value, field)
    expected = {item.name for item in dataclasses.fields(model)}
    if set(payload) != expected:
        raise ValueError(f"{field} 字段集合不匹配")
    return payload


def _encode_dataclass(value: object, model: type[object]) -> dict[str, object]:
    if type(value) is not model:
        raise ValueError(f"只接受 {model.__name__}")
    return {item.name: getattr(value, item.name) for item in dataclasses.fields(model)}


def _encode_spec(spec: AttemptSpec) -> dict[str, object]:
    payload = _encode_dataclass(spec, AttemptSpec)
    payload["input_payload"] = spec.input_payload.to_value()
    return payload


def _decode_spec(value: object) -> AttemptSpec:
    return AttemptSpec.from_persisted_value(_fields(value, AttemptSpec, "attempt spec"))


def _encode_record(record: AttemptJournalRecord) -> dict[str, object]:
    return {
        "schema_version": _SCHEMA_VERSION,
        "entry": _encode_dataclass(record.entry, AttemptJournalEntry),
        "finalization": (
            None
            if record.finalization is None
            else encode_finalization_checkpoint(record.finalization).to_value()
        ),
        "spec": _encode_spec(record.spec),
    }


def _decode_record(value: object) -> AttemptJournalRecord:
    payload = _object(value, "attempt journal")
    if set(payload) != {"schema_version", "entry", "finalization", "spec"}:
        raise ValueError("attempt journal 字段集合不匹配")
    if payload["schema_version"] != _SCHEMA_VERSION:
        raise ValueError("attempt journal schema_version 不支持")
    entry = AttemptJournalEntry(
        **_fields(payload["entry"], AttemptJournalEntry, "attempt entry")
    )
    raw_finalization = payload["finalization"]
    finalization = None
    if raw_finalization is not None:
        finalization = decode_finalization_checkpoint(
            CanonicalJsonObject.from_value(
                _object(raw_finalization, "attempt finalization")
            )
        )
    spec = _decode_spec(payload["spec"])
    return AttemptJournalRecord(entry, finalization, spec)


def _encode_health(entry: HealthOperationEntry) -> dict[str, object]:
    payload = _encode_dataclass(entry, HealthOperationEntry)
    payload["handle"] = _encode_dataclass(entry.handle, ExecutionHandle)
    return payload


def _decode_health(value: object) -> HealthOperationEntry:
    payload = _fields(value, HealthOperationEntry, "health entry")
    payload["handle"] = ExecutionHandle(
        **_fields(payload["handle"], ExecutionHandle, "health handle")
    )
    return HealthOperationEntry(**payload)


def _process_shape(entry: AttemptJournalEntry) -> tuple[object, ...] | None:
    values = (
        entry.pid,
        entry.process_identity,
        entry.containment_kind,
        entry.native_ref,
    )
    if all(value is None for value in values):
        return None
    if all(value is not None for value in values):
        return (*values, entry.started_at)
    raise ValueError("journal process 字段必须全空或全有")


def _same_attempt_identity(
    stored: AttemptJournalRecord,
    candidate: AttemptJournalRecord,
) -> str | None:
    before = stored.entry
    after = candidate.entry
    fixed = (
        "schema_version",
        "owner_token",
        "claim_token",
        "attempt_id",
        "fence",
        "project_id",
        "kind",
        "spec_path",
        "result_path",
        "timeout_sec",
    )
    if any(getattr(before, field) != getattr(after, field) for field in fixed):
        return "attempt 身份"
    if stored.spec != candidate.spec:
        return "AttemptSpec"
    before_process = _process_shape(before)
    after_process = _process_shape(after)
    if before_process is not None and after_process != before_process:
        return "已持久 handle"
    if before_process is None and after_process is None and before.started_at != after.started_at:
        return "未启动时间"
    if (
        stored.phase is AttemptJournalPhase.FINALIZING
        and candidate.phase is AttemptJournalPhase.FINALIZING
        and stored != candidate
    ):
        return "FINALIZING 重放内容"
    return None


class ControlJournal:
    """独立 attempt/health envelope 的严格耐久实现。"""

    def __init__(
        self,
        *,
        owner_token: str,
        path: Path | None = None,
        lock_timeout_sec: float = _LOCK_TIMEOUT_SEC,
    ) -> None:
        self._owner_token = _text(owner_token, "owner_token")
        self._path = control_journal_path() if path is None else Path(path)
        if not self._path.is_absolute():
            raise ValueError("control journal path 必须是绝对路径")
        self._lock_path = self._path.with_name(f"{self._path.name}.lock")
        self._lock_timeout_sec = float(lock_timeout_sec)
        _LockDeadline.start(self._lock_timeout_sec)

    def load(self) -> AttemptJournalRecord | None:
        return self._locked_read().attempt

    def create(self, record: AttemptJournalRecord) -> None:
        if type(record) is not AttemptJournalRecord:
            raise ValueError("create 只接受 AttemptJournalRecord")
        if record.phase is not AttemptJournalPhase.CLAIMED or record.spec is None:
            raise ValueError("create 只接受含 AttemptSpec 的 CLAIMED journal")
        if record.entry.owner_token != self._owner_token:
            raise ControlJournalConflictError("attempt owner 与 queue owner 不匹配")

        def update(current: _Envelope) -> _Envelope:
            if current.attempt is not None:
                raise ControlJournalConflictError("attempt journal 已存在")
            return _Envelope(record, current.health)

        self._mutate(update)

    def transition(
        self,
        record: AttemptJournalRecord,
        *,
        expected: AttemptJournalPhase,
    ) -> None:
        if type(record) is not AttemptJournalRecord or record.spec is None:
            raise ValueError("transition 只接受含 AttemptSpec 的 AttemptJournalRecord")
        if type(expected) is not AttemptJournalPhase:
            raise ValueError("expected 必须是 AttemptJournalPhase")

        def update(current: _Envelope) -> _Envelope:
            stored = current.attempt
            if stored is None or stored.phase is not expected:
                raise ControlJournalConflictError("attempt journal phase CAS 不匹配")
            if stored.entry.owner_token != self._owner_token:
                raise ControlJournalConflictError("attempt journal queue owner CAS 不匹配")
            mismatch = _same_attempt_identity(stored, record)
            if mismatch is not None:
                raise ControlJournalConflictError(f"attempt journal {mismatch} CAS 不匹配")
            try:
                validate_journal_transition(stored.phase, record.phase)
            except ValueError as error:
                raise ControlJournalConflictError("attempt journal 非法状态跃迁") from error
            return _Envelope(record, current.health)

        self._mutate(update)

    def clear(
        self,
        *,
        attempt_id: str,
        fence: str,
        expected: AttemptJournalPhase,
    ) -> None:
        resolved_attempt = _text(attempt_id, "attempt_id")
        resolved_fence = _text(fence, "fence")
        if type(expected) is not AttemptJournalPhase:
            raise ValueError("expected 必须是 AttemptJournalPhase")

        def update(current: _Envelope) -> _Envelope:
            stored = current.attempt
            if stored is None or stored.phase is not expected:
                raise ControlJournalConflictError("attempt journal phase CAS 不匹配")
            if stored.entry.owner_token != self._owner_token:
                raise ControlJournalConflictError("attempt journal queue owner CAS 不匹配")
            if (stored.entry.attempt_id, stored.entry.fence) != (
                resolved_attempt,
                resolved_fence,
            ):
                raise ControlJournalConflictError("attempt journal attempt/fence CAS 不匹配")
            return _Envelope(None, current.health)

        self._mutate(update)

    def load_health(self) -> HealthOperationEntry | None:
        return self._locked_read().health

    def save_health(self, entry: HealthOperationEntry) -> None:
        if type(entry) is not HealthOperationEntry:
            raise ValueError("save_health 只接受 HealthOperationEntry")

        def update(current: _Envelope) -> _Envelope:
            if current.health is not None:
                raise ControlJournalConflictError("health operation 已存在")
            return _Envelope(current.attempt, entry)

        self._mutate(update)

    def clear_health(self, *, operation_id: str) -> None:
        resolved_operation = _text(operation_id, "operation_id")

        def update(current: _Envelope) -> _Envelope:
            health = current.health
            if health is None or health.operation_id != resolved_operation:
                raise ControlJournalConflictError("health operation_id CAS 不匹配")
            return _Envelope(current.attempt, None)

        self._mutate(update)

    def _locked_read(self) -> _Envelope:
        deadline = _LockDeadline.start(self._lock_timeout_sec)
        with advisory_lock(self._lock_path, deadline, blocking=True) as acquired:
            if not acquired:
                raise ControlJournalTimeout("未取得 control journal 锁")
            return self._read_unlocked()

    def _mutate(self, update: Callable[[_Envelope], _Envelope]) -> None:
        deadline = _LockDeadline.start(self._lock_timeout_sec)
        with advisory_lock(self._lock_path, deadline, blocking=True) as acquired:
            if not acquired:
                raise ControlJournalTimeout("未取得 control journal 锁")
            current = self._read_unlocked()
            updated = update(current)
            self._write_unlocked(updated)

    def _read_unlocked(self) -> _Envelope:
        try:
            raw = read_regular_file_bounded(self._path, max_bytes=_MAX_BYTES)
        except FileNotFoundError:
            return _Envelope(None, None)
        except Exception as error:
            raise ControlJournalCorruptionError("control journal 无法安全读取") from error
        try:
            value = json.loads(
                raw.decode("utf-8"),
                object_pairs_hook=_pairs_object,
                parse_constant=_reject_constant,
            )
            payload = _object(value, "control journal")
            if set(payload) != {"schema_version", "attempt", "health"}:
                raise ValueError("control journal 字段集合不匹配")
            if payload["schema_version"] != _SCHEMA_VERSION:
                raise ValueError("control journal schema_version 不支持")
            attempt = None if payload["attempt"] is None else _decode_record(payload["attempt"])
            health = None if payload["health"] is None else _decode_health(payload["health"])
            return _Envelope(attempt, health)
        except (UnicodeError, TypeError, ValueError, json.JSONDecodeError) as error:
            raise ControlJournalCorruptionError("control journal schema 或内容无效") from error

    def _write_unlocked(self, envelope: _Envelope) -> None:
        payload = {
            "schema_version": _SCHEMA_VERSION,
            "attempt": None if envelope.attempt is None else _encode_record(envelope.attempt),
            "health": None if envelope.health is None else _encode_health(envelope.health),
        }
        try:
            raw = json.dumps(
                payload,
                ensure_ascii=False,
                allow_nan=False,
                sort_keys=True,
                separators=(",", ":"),
            ).encode("utf-8")
        except (TypeError, UnicodeError, ValueError) as error:
            raise ControlJournalCorruptionError("control journal 无法规范编码") from error
        if len(raw) > _MAX_BYTES:
            raise ControlJournalCorruptionError("control journal 超过大小上限")
        durable_write_replace(self._path, raw)


__all__ = [
    "ControlJournal",
    "ControlJournalConflictError",
    "ControlJournalCorruptionError",
    "ControlJournalError",
    "ControlJournalTimeout",
    "control_journal_path",
]
