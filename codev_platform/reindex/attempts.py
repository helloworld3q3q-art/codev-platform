"""固定重建索引 attempt 协议、严格编解码与结果验证边界。"""
from __future__ import annotations

import dataclasses
import json
import math
import re
from dataclasses import dataclass
from enum import Enum
from pathlib import Path
from typing import TYPE_CHECKING

from .file_durability import (
    durable_write_once,
    durable_write_replace,
    read_regular_file_bounded,
)

if TYPE_CHECKING:
    from .attempt_validation import (  # noqa: F401 - 兼容导出的静态发现
        AttemptValidationEvidence,
        ValidatedAttemptResult,
        validate_attempt_result,
    )

_MAX_ATTEMPT_JSON_BYTES = 1024 * 1024
_OID_RE = re.compile(r"(?:[0-9a-f]{40}|[0-9a-f]{64})\Z")


def _pairs_object(pairs: list[tuple[str, object]]) -> dict[str, object]:
    value: dict[str, object] = {}
    for key, item in pairs:
        if key in value:
            raise ValueError("JSON 包含重复键")
        value[key] = item
    return value


def _reject_constant(_value: str) -> object:
    raise ValueError("JSON 包含非有限数")


def _byte_length(text: str) -> int:
    try:
        return len(text.encode("utf-8"))
    except UnicodeError:
        raise ValueError("JSON 不是有效 UTF-8 文本") from None


def _canonical_text(raw: str, max_bytes: int) -> str:
    if type(raw) is not str or type(max_bytes) is not int or max_bytes <= 0:
        raise ValueError("JSON 文本或大小上限无效")
    if _byte_length(raw) > max_bytes:
        raise ValueError("JSON 超过大小上限")
    try:
        value = json.loads(
            raw,
            object_pairs_hook=_pairs_object,
            parse_constant=_reject_constant,
        )
    except (json.JSONDecodeError, TypeError, ValueError):
        raise ValueError("JSON 对象无效") from None
    if type(value) is not dict:
        raise ValueError("JSON 根必须是对象")
    try:
        text = json.dumps(
            value,
            ensure_ascii=True,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        )
    except (TypeError, ValueError):
        raise ValueError("JSON 对象无法规范化") from None
    if _byte_length(text) > max_bytes:
        raise ValueError("规范 JSON 超过大小上限")
    return text


def _validate_json_value(value: object) -> None:
    if value is None or type(value) in {bool, int, str}:
        return
    if type(value) is float:
        if math.isfinite(value):
            return
        raise ValueError("JSON 包含非有限数")
    if type(value) is list:
        for item in value:
            _validate_json_value(item)
        return
    if type(value) is dict:
        for key, item in value.items():
            if type(key) is not str:
                raise ValueError("JSON 对象键必须是字符串")
            _validate_json_value(item)
        return
    raise ValueError("JSON 包含动态 Python 类型")


@dataclass(frozen=True, slots=True, init=False)
class CanonicalJsonObject:
    """只保存规范 JSON 对象文本的不可变值。"""

    text: str

    def __init__(self, raw: str, *, max_bytes: int = _MAX_ATTEMPT_JSON_BYTES) -> None:
        object.__setattr__(self, "text", _canonical_text(raw, max_bytes))

    @classmethod
    def from_text(
        cls,
        raw: str,
        *,
        max_bytes: int = _MAX_ATTEMPT_JSON_BYTES,
    ) -> CanonicalJsonObject:
        return cls(raw, max_bytes=max_bytes)

    @classmethod
    def from_value(
        cls,
        value: dict[str, object],
        *,
        max_bytes: int = _MAX_ATTEMPT_JSON_BYTES,
    ) -> CanonicalJsonObject:
        if type(value) is not dict:
            raise ValueError("JSON 根必须是对象")
        _validate_json_value(value)
        try:
            raw = json.dumps(
                value,
                ensure_ascii=True,
                sort_keys=True,
                separators=(",", ":"),
                allow_nan=False,
            )
        except (TypeError, ValueError):
            raise ValueError("JSON 对象无法规范化") from None
        return cls(raw, max_bytes=max_bytes)

    def to_value(self) -> dict[str, object]:
        return json.loads(self.text)


class AttemptOutcome(str, Enum):
    SUCCEEDED = "succeeded"
    RETRYABLE = "retryable"
    FAILED = "failed"
    TIMED_OUT = "timed_out"
    QUARANTINED = "quarantined"
    SUPERSEDED = "superseded"


def _schema(value: object) -> None:
    if type(value) is not int or value != 1:
        raise ValueError("schema_version 只接受整数 1")


def _text(value: object, field: str, *, empty: bool = False) -> None:
    if type(value) is not str or (not empty and not value.strip()):
        raise ValueError(f"{field} 字符串无效")


def _oid(value: object, field: str) -> None:
    if type(value) is not str or _OID_RE.fullmatch(value) is None or set(value) == {"0"}:
        raise ValueError(f"{field} 必须是完整非零小写 OID")


def _positive_float(value: object, field: str) -> None:
    if type(value) is not float or not math.isfinite(value) or value <= 0:
        raise ValueError(f"{field} 必须是有限正浮点数")


def _time_value(value: object, field: str) -> None:
    if type(value) is not float or not math.isfinite(value) or value < 0:
        raise ValueError(f"{field} 必须是有限非负浮点数")


def _optional_int(value: object, field: str, *, positive: bool = False) -> None:
    if value is None:
        return
    if type(value) is not int or (positive and value <= 0):
        raise ValueError(f"{field} 整数无效")


def _optional_text(value: object, field: str) -> None:
    if value is not None:
        _text(value, field)


def _contains_claim_token(value: object) -> bool:
    if type(value) is dict:
        return "claim_token" in value or any(_contains_claim_token(item) for item in value.values())
    if type(value) is list:
        return any(_contains_claim_token(item) for item in value)
    return False


def _oid_pairs(value: object, field: str) -> None:
    if type(value) is not tuple:
        raise ValueError(f"{field} 必须是 tuple-of-tuples")
    seen: set[str] = set()
    for item in value:
        if type(item) is not tuple or len(item) != 2:
            raise ValueError(f"{field} 必须是 tuple-of-tuples")
        key, oid = item
        _text(key, field)
        _oid(oid, field)
        if key in seen:
            raise ValueError(f"{field} 键重复")
        seen.add(key)


def _timing_pairs(value: object) -> None:
    if type(value) is not tuple:
        raise ValueError("timing 必须是 tuple-of-tuples")
    seen: set[str] = set()
    for item in value:
        if type(item) is not tuple or len(item) != 2:
            raise ValueError("timing 必须是 tuple-of-tuples")
        key, duration = item
        _text(key, "timing")
        _time_value(duration, "timing")
        if key in seen:
            raise ValueError("timing 键重复")
        seen.add(key)


@dataclass(frozen=True, slots=True)
class AttemptSpec:
    schema_version: int
    attempt_id: str
    fence: str
    project_id: str
    kind: str
    input_kind: str
    input_payload: CanonicalJsonObject
    target_commit: str
    timeout_sec: float
    runtime_revision: str

    @classmethod
    def from_persisted_value(cls, value: object) -> AttemptSpec:
        """从严格 JSON 对象恢复已落盘的 spec，不接受调用方预构造的嵌套类型。"""
        if type(value) is not dict:
            raise ValueError("attempt spec 必须是对象")
        payload = dict(value)
        expected = {field.name for field in dataclasses.fields(cls)}
        if set(payload) != expected:
            raise ValueError("AttemptSpec 字段集合不匹配")
        raw_input = payload["input_payload"]
        if type(raw_input) is not dict:
            raise ValueError("input_payload 必须是 JSON 对象")
        payload["input_payload"] = CanonicalJsonObject.from_value(raw_input)
        return cls(**payload)

    def __post_init__(self) -> None:
        _schema(self.schema_version)
        for field in ("attempt_id", "fence", "project_id", "kind"):
            _text(getattr(self, field), field)
        if self.input_kind != "configured":
            raise ValueError("input_kind 当前只接受 configured")
        if type(self.input_payload) is not CanonicalJsonObject:
            raise ValueError("input_payload 必须是 CanonicalJsonObject")
        payload = self.input_payload.to_value()
        if set(payload) - {"project_id", "repo_targets"} or "project_id" not in payload:
            raise ValueError("configured payload 字段无效")
        if payload["project_id"] != self.project_id or _contains_claim_token(payload):
            raise ValueError("configured payload 身份无效")
        if "repo_targets" in payload:
            targets = payload["repo_targets"]
            if type(targets) is not dict:
                raise ValueError("repo_targets 必须是对象")
            for repo, target in targets.items():
                _text(repo, "repo_targets")
                _oid(target, "repo_targets")
        _oid(self.target_commit, "target_commit")
        _positive_float(self.timeout_sec, "timeout_sec")
        _oid(self.runtime_revision, "runtime_revision")


@dataclass(frozen=True, slots=True)
class AttemptResult:
    schema_version: int
    attempt_id: str
    fence: str
    project_id: str
    kind: str
    input_root: str
    target_commit: str
    input_commits: tuple[tuple[str, str], ...]
    input_trees: tuple[tuple[str, str], ...]
    runtime_revision: str
    outcome: AttemptOutcome
    rc: int | None
    retryable: bool
    note: str
    timing: tuple[tuple[str, float], ...]
    log_ref: str | None
    proof: CanonicalJsonObject

    def __post_init__(self) -> None:
        _schema(self.schema_version)
        for field in ("attempt_id", "fence", "project_id", "kind"):
            _text(getattr(self, field), field)
        _text(self.input_root, "input_root", empty=True)
        _oid(self.target_commit, "target_commit")
        _oid_pairs(self.input_commits, "input_commits")
        _oid_pairs(self.input_trees, "input_trees")
        _oid(self.runtime_revision, "runtime_revision")
        if type(self.outcome) is not AttemptOutcome:
            raise ValueError("outcome 必须是 AttemptOutcome")
        _optional_int(self.rc, "rc")
        if type(self.retryable) is not bool:
            raise ValueError("retryable 必须是 bool")
        _text(self.note, "note", empty=True)
        _timing_pairs(self.timing)
        _optional_text(self.log_ref, "log_ref")
        if type(self.proof) is not CanonicalJsonObject or _contains_claim_token(self.proof.to_value()):
            raise ValueError("proof 无效或包含 claim_token")


@dataclass(frozen=True, slots=True)
class AttemptJournalEntry:
    schema_version: int
    owner_token: str
    claim_token: str
    attempt_id: str
    fence: str
    project_id: str
    kind: str
    spec_path: str
    result_path: str
    pid: int | None
    process_identity: str | None
    containment_kind: str | None
    native_ref: str | None
    state: str
    started_at: float
    timeout_sec: float

    def __post_init__(self) -> None:
        _schema(self.schema_version)
        required = (
            "owner_token", "claim_token", "attempt_id", "fence", "project_id",
            "kind", "spec_path", "result_path", "state",
        )
        for field in required:
            _text(getattr(self, field), field)
        _optional_int(self.pid, "pid", positive=True)
        for field in ("process_identity", "containment_kind", "native_ref"):
            _optional_text(getattr(self, field), field)
        _time_value(self.started_at, "started_at")
        _positive_float(self.timeout_sec, "timeout_sec")


@dataclass(frozen=True, slots=True)
class ConfirmedProcessDeath:
    process_identity: str
    containment_kind: str
    confirmed_at: float
    evidence: str

    def __post_init__(self) -> None:
        for field in ("process_identity", "containment_kind", "evidence"):
            _text(getattr(self, field), field)
        _time_value(self.confirmed_at, "confirmed_at")


@dataclass(frozen=True, slots=True)
class CleanupReport:
    released: bool
    attempt_id: str
    note: str

    def __post_init__(self) -> None:
        if type(self.released) is not bool:
            raise ValueError("released 必须是 bool")
        _text(self.attempt_id, "attempt_id")
        _text(self.note, "note", empty=True)


def _jsonable(value: object) -> object:
    if type(value) is CanonicalJsonObject:
        return value.to_value()
    if type(value) is AttemptOutcome:
        return value.value
    if type(value) is tuple:
        return [_jsonable(item) for item in value]
    return value


def _model_payload(value: object) -> dict[str, object]:
    return {
        field.name: _jsonable(getattr(value, field.name))
        for field in dataclasses.fields(value)
    }


def _encoded_model(value: object) -> bytes:
    text = CanonicalJsonObject.from_value(
        _model_payload(value),
        max_bytes=_MAX_ATTEMPT_JSON_BYTES,
    ).text
    return text.encode("utf-8")


def _encoded_exact(value: object, model_type: type[object]) -> bytes:
    if type(value) is not model_type:
        raise ValueError(f"只接受 {model_type.__name__}")
    return _encoded_model(value)


def encode_attempt_spec(spec: AttemptSpec) -> bytes:
    """返回与 spec 严格落盘 codec 完全一致的规范字节。"""
    return _encoded_exact(spec, AttemptSpec)


def encode_attempt_result(result: AttemptResult) -> bytes:
    """返回与 result 严格落盘 codec 完全一致的规范字节。"""
    return _encoded_exact(result, AttemptResult)


def _read_object(path: Path) -> dict[str, object]:
    data = read_regular_file_bounded(
        Path(path),
        max_bytes=_MAX_ATTEMPT_JSON_BYTES,
    )
    try:
        raw = data.decode("utf-8")
    except UnicodeError:
        raise ValueError("attempt JSON 不是 UTF-8") from None
    return CanonicalJsonObject(raw, max_bytes=_MAX_ATTEMPT_JSON_BYTES).to_value()


def _exact_values(payload: dict[str, object], model_type: type[object]) -> dict[str, object]:
    expected = {field.name for field in dataclasses.fields(model_type)}
    if set(payload) != expected:
        raise ValueError(f"{model_type.__name__} 字段集合不匹配")
    return dict(payload)


def _pairs_from_json(value: object, field: str) -> tuple[tuple[object, object], ...]:
    if type(value) is not list:
        raise ValueError(f"{field} 必须是数组")
    pairs: list[tuple[object, object]] = []
    for item in value:
        if type(item) is not list or len(item) != 2:
            raise ValueError(f"{field} 元素必须是二元数组")
        pairs.append((item[0], item[1]))
    return tuple(pairs)


def write_attempt_spec_atomic(path: Path, spec: AttemptSpec) -> None:
    durable_write_once(Path(path), encode_attempt_spec(spec))


def read_attempt_spec(path: Path) -> AttemptSpec:
    return AttemptSpec.from_persisted_value(_read_object(path))


def write_attempt_result_atomic(path: Path, result: AttemptResult) -> None:
    durable_write_once(Path(path), encode_attempt_result(result))


def read_attempt_result(path: Path) -> AttemptResult:
    values = _exact_values(_read_object(path), AttemptResult)
    proof = values["proof"]
    if type(proof) is not dict or type(values["outcome"]) is not str:
        raise ValueError("result 嵌套字段类型无效")
    values["proof"] = CanonicalJsonObject.from_value(proof)
    values["outcome"] = AttemptOutcome(values["outcome"])
    for field in ("input_commits", "input_trees", "timing"):
        values[field] = _pairs_from_json(values[field], field)
    return AttemptResult(**values)


def write_attempt_journal_atomic(path: Path, entry: AttemptJournalEntry) -> None:
    durable_write_replace(
        Path(path),
        _encoded_exact(entry, AttemptJournalEntry),
    )


def read_attempt_journal(path: Path) -> AttemptJournalEntry:
    values = _exact_values(_read_object(path), AttemptJournalEntry)
    return AttemptJournalEntry(**values)


_VALIDATION_EXPORTS = frozenset({
    "AttemptValidationEvidence",
    "ValidatedAttemptResult",
    "validate_attempt_result",
})


def __getattr__(name: str) -> object:
    """保留原公开导入路径，同时让协议文件只承担模型与 codec。"""
    if name not in _VALIDATION_EXPORTS:
        raise AttributeError(name)
    from . import attempt_validation

    return getattr(attempt_validation, name)


def __dir__() -> list[str]:
    """让静态发现与交互式检查看到兼容导出的验证符号。"""
    return sorted(set(globals()) | _VALIDATION_EXPORTS)


__all__ = tuple(
    name
    for name in globals()
    if not name.startswith("_") and name != "TYPE_CHECKING"
) + tuple(sorted(_VALIDATION_EXPORTS))
