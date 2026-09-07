"""attempt 结果的父侧验证边界与互斥证据模型。"""
from __future__ import annotations

import math
import re
from dataclasses import dataclass
from enum import Enum
from typing import TYPE_CHECKING

from .attempts import AttemptOutcome, AttemptResult, AttemptSpec

if TYPE_CHECKING:
    from .queue_ports import ClaimedJob

_OID_RE = re.compile(r"(?:[0-9a-f]{40}|[0-9a-f]{64})\Z")


class AttemptValidationEvidence(str, Enum):
    """父侧认可结果时使用的互斥证据。"""

    COMPLETION_RECEIPT = "completion_receipt"
    DEPENDENCY_BLOCK = "dependency_block"


@dataclass(frozen=True, slots=True, init=False)
class ValidatedAttemptResult:
    """只允许由本模块两个验证边界构造的不可变结果。"""

    spec: AttemptSpec
    result: AttemptResult
    claim_token: str
    process_rc: int | None
    validated_at: float
    evidence: AttemptValidationEvidence

    def __init__(self, *_args: object, **_kwargs: object) -> None:
        raise TypeError("ValidatedAttemptResult 只能由验证边界构造")


def _required_text(value: object, field: str) -> str:
    if type(value) is not str or not value.strip():
        raise ValueError(f"{field} 字符串无效")
    return value


def _target(value: object, field: str) -> str:
    if type(value) is not str or _OID_RE.fullmatch(value) is None or set(value) == {"0"}:
        raise ValueError(f"{field} 必须是完整非零小写 OID")
    return value


def _validated_time(value: object) -> float:
    if type(value) is not float or not math.isfinite(value) or value < 0:
        raise ValueError("validated_at 必须是有限非负浮点数")
    return value


def _claim_identity(claim: ClaimedJob) -> tuple[str, str, str, str]:
    try:
        token = _required_text(claim.claim_token, "claim_token")
        project_id = _required_text(claim.job.project_id, "claim.project_id")
        kind = _required_text(claim.job.kind, "claim.kind")
        target = _target(claim.job.meta.target_commit, "claim.target_commit")
    except AttributeError:
        raise ValueError("claim 协议无效") from None
    return token, project_id, kind, target


def _identity_matches(
    spec: AttemptSpec,
    result: AttemptResult,
    claim_identity: tuple[str, str, str, str],
) -> bool:
    _token, project_id, kind, target = claim_identity
    return (
        spec.schema_version == result.schema_version
        and spec.attempt_id == result.attempt_id
        and spec.fence == result.fence
        and spec.project_id == result.project_id == project_id
        and spec.kind == result.kind == kind
        and spec.target_commit == result.target_commit == target
        and spec.runtime_revision == result.runtime_revision
    )


def _validated_result(
    spec: AttemptSpec,
    result: AttemptResult,
    claim_token: str,
    process_rc: int | None,
    validated_at: float,
    evidence: AttemptValidationEvidence,
) -> ValidatedAttemptResult:
    value = object.__new__(ValidatedAttemptResult)
    for field, item in (
        ("spec", spec),
        ("result", result),
        ("claim_token", claim_token),
        ("process_rc", process_rc),
        ("validated_at", validated_at),
        ("evidence", evidence),
    ):
        object.__setattr__(value, field, item)
    return value


def validate_attempt_result(
    spec: AttemptSpec,
    result: AttemptResult,
    *,
    claim: ClaimedJob,
    process_rc: int | None,
    validated_at: float,
) -> ValidatedAttemptResult:
    """用 completion receipt 观测码验证正常或恢复路径结果。"""
    if type(spec) is not AttemptSpec or type(result) is not AttemptResult:
        raise ValueError("spec 或 result 类型无效")
    if type(process_rc) is not int:
        raise ValueError("completion receipt 验证必须提供整数进程返回码")
    checked_at = _validated_time(validated_at)
    identity = _claim_identity(claim)
    if not _identity_matches(spec, result, identity):
        raise ValueError("attempt、result 与 claim 身份不匹配")
    if result.rc != process_rc:
        raise ValueError("result.rc 与进程返回码不一致")
    if result.outcome is AttemptOutcome.SUCCEEDED:
        proof = result.proof.to_value()
        if process_rc != 0 or not result.input_root.strip() or proof.get("success") is not True:
            raise ValueError("成功结果缺少返回码、输入根或成功证明")
    return _validated_result(
        spec,
        result,
        identity[0],
        process_rc,
        checked_at,
        AttemptValidationEvidence.COMPLETION_RECEIPT,
    )


def validate_dependency_block_result_shape(
    spec: AttemptSpec,
    result: AttemptResult,
) -> None:
    """验证无进程依赖失败的完整身份、队列与 manifest 证明形状。"""
    if type(spec) is not AttemptSpec or type(result) is not AttemptResult:
        raise ValueError("spec 或依赖阻断 result 类型无效")
    proof = result.proof.to_value()
    dependency = proof.get("dependency")
    manifest = dependency.get("manifest") if type(dependency) is dict else None
    bound_decision = (
        type(dependency) is dict
        and set(dependency) == {
            "dependency_kind",
            "dependent_kind",
            "disposition",
            "manifest",
            "project_id",
            "queue_state",
            "target_commit",
        }
        and dependency.get("project_id") == spec.project_id
        and dependency.get("dependent_kind") == spec.kind
        and dependency.get("target_commit") == spec.target_commit
        and dependency.get("disposition") == "block"
        and type(dependency.get("dependency_kind")) is str
        and bool(dependency["dependency_kind"].strip())
        and dependency.get("queue_state") == "absent"
        and type(manifest) is dict
        and set(manifest) == {"attempt_id", "result_digest", "status", "target_commit"}
        and manifest.get("status") == "failed"
        and manifest.get("target_commit") == spec.target_commit
    )
    valid_failure = (
        result.outcome is AttemptOutcome.FAILED
        and result.rc is None
        and result.retryable is False
        and not result.input_root
        and not result.input_commits
        and not result.input_trees
        and not result.timing
        and result.log_ref is None
        and proof.get("success") is False
        and proof.get("evidence") == AttemptValidationEvidence.DEPENDENCY_BLOCK.value
        and bound_decision
    )
    if not valid_failure:
        raise ValueError("依赖阻断结果不能伪造进程、输入或成功事实")


def _validated_dependency_block(
    spec: AttemptSpec,
    result: AttemptResult,
    *,
    claim: ClaimedJob,
    validated_at: float,
) -> ValidatedAttemptResult:
    """验证进程启动前的确定依赖失败，明确保留无进程事实。"""
    checked_at = _validated_time(validated_at)
    identity = _claim_identity(claim)
    if not _identity_matches(spec, result, identity):
        raise ValueError("依赖阻断 attempt、result 与 claim 身份不匹配")
    validate_dependency_block_result_shape(spec, result)
    return _validated_result(
        spec,
        result,
        identity[0],
        None,
        checked_at,
        AttemptValidationEvidence.DEPENDENCY_BLOCK,
    )


def validate_persisted_dependency_block(
    spec: AttemptSpec,
    result: AttemptResult,
    *,
    claim: ClaimedJob,
    validated_at: float,
) -> ValidatedAttemptResult:
    """恢复已持久化的依赖阻断结果，不重新评估会变化的依赖事实。"""
    return _validated_dependency_block(
        spec,
        result,
        claim=claim,
        validated_at=validated_at,
    )


__all__ = [
    "AttemptValidationEvidence",
    "ValidatedAttemptResult",
    "validate_attempt_result",
    "validate_dependency_block_result_shape",
    "validate_persisted_dependency_block",
]
