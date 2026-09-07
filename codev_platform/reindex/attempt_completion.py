"""attempt 完成凭据的严格 codec、摘要绑定与可信返回码边界。"""
from __future__ import annotations

import dataclasses
import hashlib
import math
import re
from dataclasses import dataclass
from pathlib import Path

from .attempts import (
    AttemptOutcome,
    AttemptResult,
    AttemptSpec,
    CanonicalJsonObject,
    encode_attempt_result,
    encode_attempt_spec,
)
from .file_durability import durable_write_once, read_regular_file_bounded

_MAX_RECEIPT_BYTES = 16 * 1024
_MAX_IDENTITY_BYTES = 512
_DIGEST_RE = re.compile(r"[0-9a-f]{64}\Z")
_OID_RE = re.compile(r"(?:[0-9a-f]{40}|[0-9a-f]{64})\Z")
_MIN_PROCESS_RC = -(2**31)
_MAX_PROCESS_RC = 2**32 - 1
_RESULT_WALL_CLOCK_FIELDS = frozenset({"started_at", "finished_at"})


def _bounded_text(value: object, field: str) -> None:
    if type(value) is not str or value != value.strip() or not value:
        raise ValueError(f"{field} 必须是非空规范字符串")
    try:
        size = len(value.encode("utf-8"))
    except UnicodeError:
        raise ValueError(f"{field} 不是有效 UTF-8 字符串") from None
    if size > _MAX_IDENTITY_BYTES or any(ord(char) < 32 for char in value):
        raise ValueError(f"{field} 超过上限或包含控制字符")


def _oid(value: object, field: str) -> None:
    if type(value) is not str or _OID_RE.fullmatch(value) is None or set(value) == {"0"}:
        raise ValueError(f"{field} 必须是完整非零小写 OID")


def _digest(value: object, field: str) -> None:
    if type(value) is not str or _DIGEST_RE.fullmatch(value) is None:
        raise ValueError(f"{field} 必须是小写 SHA-256")


def _process_rc(value: object) -> None:
    if type(value) is not int or not _MIN_PROCESS_RC <= value <= _MAX_PROCESS_RC:
        raise ValueError("process_rc 超出进程返回码范围")


def _time_value(value: object) -> None:
    if type(value) is not float or not math.isfinite(value) or value < 0:
        raise ValueError("observed_at 必须是有限非负浮点数")


@dataclass(frozen=True, slots=True)
class AttemptCompletionReceipt:
    """观察器在内部 executor 退出并完成复核后发布的一次性凭据。"""

    schema_version: int
    attempt_id: str
    fence: str
    project_id: str
    kind: str
    target_commit: str
    runtime_revision: str
    spec_sha256: str
    result_sha256: str
    process_rc: int
    observed_at: float

    def __post_init__(self) -> None:
        if type(self.schema_version) is not int or self.schema_version != 1:
            raise ValueError("schema_version 只接受整数 1")
        for field in ("attempt_id", "fence", "project_id", "kind"):
            _bounded_text(getattr(self, field), field)
        _oid(self.target_commit, "target_commit")
        _oid(self.runtime_revision, "runtime_revision")
        _digest(self.spec_sha256, "spec_sha256")
        _digest(self.result_sha256, "result_sha256")
        _process_rc(self.process_rc)
        _time_value(self.observed_at)


def attempt_spec_digest(spec: AttemptSpec) -> str:
    """计算与严格 spec codec 一致、可跨进程复算的规范摘要。"""
    return hashlib.sha256(encode_attempt_spec(spec)).hexdigest()


def attempt_result_digest(result: AttemptResult) -> str:
    """计算与严格 result codec 一致、可作为幂等键的规范摘要。"""
    return hashlib.sha256(encode_attempt_result(result)).hexdigest()


def _result_wall_clock_floor(result: AttemptResult) -> float:
    values = (
        value
        for key, value in result.timing
        if key in _RESULT_WALL_CLOCK_FIELDS
    )
    return max(values, default=0.0)


def _completion_observed_at(result: AttemptResult, observed_at: float) -> float:
    _time_value(observed_at)
    return max(observed_at, _result_wall_clock_floor(result))


def _receipt_payload(receipt: AttemptCompletionReceipt) -> dict[str, object]:
    return {
        field.name: getattr(receipt, field.name)
        for field in dataclasses.fields(AttemptCompletionReceipt)
    }


def _encode_receipt(receipt: AttemptCompletionReceipt) -> bytes:
    if type(receipt) is not AttemptCompletionReceipt:
        raise ValueError("只接受 AttemptCompletionReceipt")
    text = CanonicalJsonObject.from_value(
        _receipt_payload(receipt),
        max_bytes=_MAX_RECEIPT_BYTES,
    ).text
    return text.encode("utf-8")


def make_attempt_completion_receipt(
    spec: AttemptSpec,
    result: AttemptResult,
    *,
    process_rc: int,
    observed_at: float,
) -> AttemptCompletionReceipt:
    """从观察到的返回码构造并立即复核完成凭据。"""
    if type(spec) is not AttemptSpec or type(result) is not AttemptResult:
        raise ValueError("spec 或 result 类型无效")
    receipt = AttemptCompletionReceipt(
        schema_version=1,
        attempt_id=spec.attempt_id,
        fence=spec.fence,
        project_id=spec.project_id,
        kind=spec.kind,
        target_commit=spec.target_commit,
        runtime_revision=spec.runtime_revision,
        spec_sha256=attempt_spec_digest(spec),
        result_sha256=attempt_result_digest(result),
        process_rc=process_rc,
        observed_at=_completion_observed_at(result, observed_at),
    )
    validate_attempt_completion(spec, result, receipt)
    return receipt


def _identity_matches(
    spec: AttemptSpec,
    result: AttemptResult,
    receipt: AttemptCompletionReceipt,
) -> bool:
    expected = (
        spec.schema_version,
        spec.attempt_id,
        spec.fence,
        spec.project_id,
        spec.kind,
        spec.target_commit,
        spec.runtime_revision,
    )
    return expected == (
        result.schema_version,
        result.attempt_id,
        result.fence,
        result.project_id,
        result.kind,
        result.target_commit,
        result.runtime_revision,
    ) == (
        receipt.schema_version,
        receipt.attempt_id,
        receipt.fence,
        receipt.project_id,
        receipt.kind,
        receipt.target_commit,
        receipt.runtime_revision,
    )


def validate_attempt_completion(
    spec: AttemptSpec,
    result: AttemptResult,
    receipt: AttemptCompletionReceipt,
) -> int:
    """把 spec、result 与观察凭据收敛为可信进程返回码。"""
    if (
        type(spec) is not AttemptSpec
        or type(result) is not AttemptResult
        or type(receipt) is not AttemptCompletionReceipt
    ):
        raise ValueError("完成凭据输入类型无效")
    if not _identity_matches(spec, result, receipt):
        raise ValueError("spec、result 与完成凭据身份不匹配")
    if receipt.spec_sha256 != attempt_spec_digest(spec):
        raise ValueError("spec 摘要与完成凭据不匹配")
    if receipt.result_sha256 != attempt_result_digest(result):
        raise ValueError("result 摘要与完成凭据不匹配")
    if result.rc is None or result.rc != receipt.process_rc:
        raise ValueError("result.rc 未被观察到的进程返回码背书")
    if receipt.observed_at < _result_wall_clock_floor(result):
        raise ValueError("完成凭据时间早于 executor 结果墙钟")
    if result.outcome is AttemptOutcome.SUCCEEDED:
        proof = result.proof.to_value()
        if receipt.process_rc != 0 or not result.input_root.strip() or proof.get("success") is not True:
            raise ValueError("成功结果缺少零返回码、输入根或成功证明")
    return receipt.process_rc


def write_attempt_completion_receipt(
    path: Path,
    receipt: AttemptCompletionReceipt,
) -> None:
    durable_write_once(Path(path), _encode_receipt(receipt))


def read_attempt_completion_receipt(path: Path) -> AttemptCompletionReceipt:
    data = read_regular_file_bounded(
        Path(path),
        max_bytes=_MAX_RECEIPT_BYTES,
    )
    try:
        raw = data.decode("utf-8")
    except UnicodeError:
        raise ValueError("完成凭据不是 UTF-8") from None
    payload = CanonicalJsonObject(raw, max_bytes=_MAX_RECEIPT_BYTES).to_value()
    expected = {field.name for field in dataclasses.fields(AttemptCompletionReceipt)}
    if set(payload) != expected:
        raise ValueError("AttemptCompletionReceipt 字段集合不匹配")
    return AttemptCompletionReceipt(**payload)


__all__ = [
    "AttemptCompletionReceipt",
    "attempt_result_digest",
    "attempt_spec_digest",
    "make_attempt_completion_receipt",
    "read_attempt_completion_receipt",
    "validate_attempt_completion",
    "write_attempt_completion_receipt",
]
