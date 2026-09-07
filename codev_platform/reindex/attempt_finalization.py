"""FINALIZING 恢复意图的不可变模型、严格 codec 与组合校验。"""
from __future__ import annotations

import dataclasses
import re
from dataclasses import dataclass
from enum import Enum

from .attempt_completion import attempt_result_digest
from .attempt_validation import validate_dependency_block_result_shape
from .attempts import (
    AttemptJournalEntry,
    AttemptOutcome,
    AttemptResult,
    AttemptSpec,
    CanonicalJsonObject,
)

_MAX_CHECKPOINT_BYTES = 8 * 1024
_MAX_TEXT_BYTES = 512
_DIGEST_RE = re.compile(r"[0-9a-f]{64}\Z")
_OID_RE = re.compile(r"(?:[0-9a-f]{40}|[0-9a-f]{64})\Z")


class FinalizationAction(str, Enum):
    """最终化要重放的唯一 queue 动作。"""

    GUARDED_ACK = "guarded_ack"
    RETRY = "retry"


class FinalizationEvidence(str, Enum):
    """动作意图所依据的互斥事实。"""

    COMPLETION_RECEIPT = "completion_receipt"
    INCOMPLETE_ARTIFACT = "incomplete_artifact"
    DEPENDENCY_BLOCK = "dependency_block"
    NO_PROCESS_RETRY = "no_process_retry"


def _text(value: object, field: str) -> None:
    if type(value) is not str or value != value.strip() or not value:
        raise ValueError(f"{field} 必须是非空规范字符串")
    try:
        size = len(value.encode("utf-8"))
    except UnicodeError:
        raise ValueError(f"{field} 不是有效 UTF-8") from None
    if size > _MAX_TEXT_BYTES or any(ord(char) < 32 for char in value):
        raise ValueError(f"{field} 超过上限或包含控制字符")


def _validate_digest(value: object, *, required: bool) -> None:
    if value is None and not required:
        return
    if type(value) is not str or _DIGEST_RE.fullmatch(value) is None:
        raise ValueError("result_digest 必须是规范小写 SHA-256")


def _validate_combination(
    action: FinalizationAction,
    evidence: FinalizationEvidence,
    result_digest: str | None,
) -> None:
    if evidence in {
        FinalizationEvidence.INCOMPLETE_ARTIFACT,
        FinalizationEvidence.NO_PROCESS_RETRY,
    }:
        if action is not FinalizationAction.RETRY or result_digest is not None:
            raise ValueError("无结果最终化只允许无摘要 RETRY")
        return
    _validate_digest(result_digest, required=True)
    if evidence is FinalizationEvidence.DEPENDENCY_BLOCK:
        if action is not FinalizationAction.GUARDED_ACK:
            raise ValueError("依赖阻断只允许 GUARDED_ACK 动作")
        return
    if evidence is not FinalizationEvidence.COMPLETION_RECEIPT:
        raise ValueError("最终化证据类型无效")


@dataclass(frozen=True, slots=True)
class AttemptFinalizationCheckpoint:
    """journal 内可重放但不能替代死亡证明的最终化意图。"""

    schema_version: int
    attempt_id: str
    fence: str
    target_commit: str
    action: FinalizationAction
    evidence: FinalizationEvidence
    result_digest: str | None

    def __post_init__(self) -> None:
        if type(self.schema_version) is not int or self.schema_version != 1:
            raise ValueError("schema_version 只接受整数 1")
        _text(self.attempt_id, "attempt_id")
        _text(self.fence, "fence")
        if type(self.target_commit) is not str or _OID_RE.fullmatch(self.target_commit) is None:
            raise ValueError("target_commit 必须是完整小写 OID")
        if set(self.target_commit) == {"0"}:
            raise ValueError("target_commit 不能是全零 OID")
        if type(self.action) is not FinalizationAction:
            raise ValueError("action 必须是 FinalizationAction")
        if type(self.evidence) is not FinalizationEvidence:
            raise ValueError("evidence 必须是 FinalizationEvidence")
        _validate_combination(self.action, self.evidence, self.result_digest)


def encode_finalization_checkpoint(
    checkpoint: AttemptFinalizationCheckpoint,
) -> CanonicalJsonObject:
    """编码为 journal 唯一允许保存的规范 JSON 对象。"""
    if type(checkpoint) is not AttemptFinalizationCheckpoint:
        raise ValueError("只接受 AttemptFinalizationCheckpoint")
    payload = {
        field.name: getattr(checkpoint, field.name)
        for field in dataclasses.fields(AttemptFinalizationCheckpoint)
    }
    payload["action"] = checkpoint.action.value
    payload["evidence"] = checkpoint.evidence.value
    return CanonicalJsonObject.from_value(payload, max_bytes=_MAX_CHECKPOINT_BYTES)


def decode_finalization_checkpoint(
    value: CanonicalJsonObject,
) -> AttemptFinalizationCheckpoint:
    """拒绝未知键、缺字段和宽松枚举的严格解码。"""
    if type(value) is not CanonicalJsonObject:
        raise ValueError("finalization 必须是 CanonicalJsonObject")
    payload = value.to_value()
    expected = {field.name for field in dataclasses.fields(AttemptFinalizationCheckpoint)}
    if set(payload) != expected:
        raise ValueError("AttemptFinalizationCheckpoint 字段集合不匹配")
    try:
        payload["action"] = FinalizationAction(payload["action"])
        payload["evidence"] = FinalizationEvidence(payload["evidence"])
    except (TypeError, ValueError):
        raise ValueError("finalization action 或 evidence 无效") from None
    return AttemptFinalizationCheckpoint(**payload)


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
    raise ValueError("FINALIZING journal 进程字段必须全空或全有")


def _validate_result_binding(
    checkpoint: AttemptFinalizationCheckpoint,
    spec: AttemptSpec,
    result: AttemptResult | None,
) -> None:
    without_result = checkpoint.evidence in {
        FinalizationEvidence.INCOMPLETE_ARTIFACT,
        FinalizationEvidence.NO_PROCESS_RETRY,
    }
    if without_result:
        if result is not None:
            raise ValueError("不完整 artifact checkpoint 不能绑定 result")
        return
    if type(result) is not AttemptResult:
        raise ValueError("有结果的最终化证据必须绑定 AttemptResult")
    spec_identity = (
        spec.schema_version,
        spec.attempt_id,
        spec.fence,
        spec.project_id,
        spec.kind,
        spec.target_commit,
        spec.runtime_revision,
    )
    result_identity = (
        result.schema_version,
        result.attempt_id,
        result.fence,
        result.project_id,
        result.kind,
        result.target_commit,
        result.runtime_revision,
    )
    if result_identity != spec_identity:
        raise ValueError("checkpoint 绑定的 result 与 spec 身份不匹配")
    if checkpoint.result_digest != attempt_result_digest(result):
        raise ValueError("checkpoint result_digest 与规范结果摘要不匹配")
    if checkpoint.evidence is FinalizationEvidence.DEPENDENCY_BLOCK:
        validate_dependency_block_result_shape(spec, result)
        return
    retry_outcomes = {AttemptOutcome.RETRYABLE, AttemptOutcome.TIMED_OUT}
    guarded_outcomes = {
        AttemptOutcome.SUCCEEDED,
        AttemptOutcome.FAILED,
        AttemptOutcome.SUPERSEDED,
    }
    if checkpoint.action is FinalizationAction.RETRY and result.outcome not in retry_outcomes:
        raise ValueError("RETRY 动作与 result outcome 不一致")
    if checkpoint.action is FinalizationAction.GUARDED_ACK and result.outcome not in guarded_outcomes:
        raise ValueError("GUARDED_ACK 动作与 result outcome 不一致")


def validate_finalization_checkpoint(
    checkpoint: AttemptFinalizationCheckpoint,
    entry: AttemptJournalEntry,
    *,
    spec: AttemptSpec,
    result: AttemptResult | None,
) -> None:
    """校验 checkpoint 与外层 journal/spec 身份和进程形状一致。"""
    if type(checkpoint) is not AttemptFinalizationCheckpoint:
        raise ValueError("checkpoint 类型无效")
    if type(entry) is not AttemptJournalEntry or entry.state.upper() != "FINALIZING":
        raise ValueError("checkpoint 只允许用于 FINALIZING journal")
    if checkpoint.attempt_id != entry.attempt_id or checkpoint.fence != entry.fence:
        raise ValueError("checkpoint 与 journal 身份不匹配")
    if type(spec) is not AttemptSpec:
        raise ValueError("spec 类型无效")
    identity = (spec.attempt_id, spec.fence, spec.target_commit)
    expected = (checkpoint.attempt_id, checkpoint.fence, checkpoint.target_commit)
    if identity != expected:
        raise ValueError("checkpoint 与 spec 身份不匹配")
    _validate_result_binding(checkpoint, spec, result)
    shape = _process_shape(entry)
    no_process = checkpoint.evidence in {
        FinalizationEvidence.DEPENDENCY_BLOCK,
        FinalizationEvidence.NO_PROCESS_RETRY,
    }
    if no_process and shape != "empty":
        raise ValueError("无进程最终化必须证明从未 prepare，进程字段应全空")
    if not no_process and shape != "full":
        raise ValueError("进程最终化必须保留完整进程引用")


__all__ = [
    "AttemptFinalizationCheckpoint",
    "FinalizationAction",
    "FinalizationEvidence",
    "decode_finalization_checkpoint",
    "encode_finalization_checkpoint",
    "validate_finalization_checkpoint",
]
