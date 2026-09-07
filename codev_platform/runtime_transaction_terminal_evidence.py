"""事务终态证据模型、规范摘要与严格持久化 codec。"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum

from codev_platform._runtime_contract_support import (
    RuntimeContractSupportError,
    decode_exact_json_mapping,
    require_attempt_id,
    require_schema,
    require_sha256,
    require_utc_rfc3339_z,
)
from codev_platform.core.runtime_models import canonical_json_bytes, canonical_sha256
from codev_platform.runtime_generation_state import GenerationMode


_MAX_TERMINAL_EVIDENCE_BYTES = 32_768
_MAX_EPOCH = 2**63 - 1
_TERMINAL_EVIDENCE_FIELDS = frozenset(
    {
        "schema_version",
        "attempt_id",
        "reservation_sha256",
        "deployment_attempt_sha256",
        "journal_genesis_sha256",
        "active_journal_sha256",
        "outcome",
        "final_mode",
        "final_serving_generation_id",
        "maintenance_active",
        "final_state_sha256",
        "acceptance_sha256",
        "serving_fence_sha256",
        "serve_permit_sha256",
        "control_lease_epoch_audit",
        "control_token_sha256_audit",
        "completion_control_lease_sha256",
        "completed_at",
    }
)


class TransactionTerminalError(ValueError):
    """事务终态证据或完成边界不符合契约。"""


class TransactionTerminalOutcome(StrEnum):
    TARGET_COMMITTED = "target-committed"
    BASELINE_RESTORED = "baseline-restored"
    SAFETY_UNPROVEN = "safety-unproven"


@dataclass(frozen=True, slots=True)
class TransactionTerminalEvidence:
    """绑定完成前 journal 与最终公开状态的不可变证据。"""

    schema_version: int
    attempt_id: str
    reservation_sha256: str
    deployment_attempt_sha256: str
    journal_genesis_sha256: str
    active_journal_sha256: str
    outcome: TransactionTerminalOutcome
    final_mode: GenerationMode
    final_serving_generation_id: str
    maintenance_active: bool
    final_state_sha256: str
    acceptance_sha256: str | None
    serving_fence_sha256: str
    serve_permit_sha256: str | None
    control_lease_epoch_audit: int
    control_token_sha256_audit: str
    completion_control_lease_sha256: str
    completed_at: str

    def __post_init__(self) -> None:
        _require_terminal_evidence(self)


def transaction_terminal_evidence_sha256(value: TransactionTerminalEvidence) -> str:
    """计算覆盖全部终态事实的规范摘要。"""
    if type(value) is not TransactionTerminalEvidence:
        raise TransactionTerminalError("只接受 TransactionTerminalEvidence")
    return canonical_sha256(value)


def encode_transaction_terminal_evidence(value: TransactionTerminalEvidence) -> bytes:
    """编码不含原始 capability 的终态证据。"""
    if type(value) is not TransactionTerminalEvidence:
        raise TransactionTerminalError("只接受 TransactionTerminalEvidence")
    return canonical_json_bytes(value)


def decode_transaction_terminal_evidence(payload: bytes) -> TransactionTerminalEvidence:
    """严格解码字段集合精确的终态证据。"""
    try:
        values = decode_exact_json_mapping(
            payload,
            required_fields=_TERMINAL_EVIDENCE_FIELDS,
            optional_defaults={},
            max_bytes=_MAX_TERMINAL_EVIDENCE_BYTES,
        )
        outcome = values.get("outcome")
        final_mode = values.get("final_mode")
        if type(outcome) is not str or type(final_mode) is not str:
            raise TransactionTerminalError("终态枚举类型无效")
        values["outcome"] = TransactionTerminalOutcome(outcome)
        values["final_mode"] = GenerationMode(final_mode)
        return TransactionTerminalEvidence(**values)
    except (RuntimeContractSupportError, TypeError, ValueError):
        raise TransactionTerminalError("TransactionTerminalEvidence 数据无效") from None


def _require_terminal_evidence(value: TransactionTerminalEvidence) -> None:
    try:
        require_schema(value.schema_version, 1)
        require_attempt_id(value.attempt_id)
        for field in (
            "reservation_sha256",
            "deployment_attempt_sha256",
            "journal_genesis_sha256",
            "active_journal_sha256",
            "final_serving_generation_id",
            "final_state_sha256",
            "serving_fence_sha256",
            "control_token_sha256_audit",
            "completion_control_lease_sha256",
        ):
            require_sha256(getattr(value, field), field=field)
        for field in ("acceptance_sha256", "serve_permit_sha256"):
            digest = getattr(value, field)
            if digest is not None:
                require_sha256(digest, field=field)
        require_utc_rfc3339_z(value.completed_at, field="completed_at")
    except RuntimeContractSupportError as exc:
        raise TransactionTerminalError(str(exc)) from None
    if type(value.outcome) is not TransactionTerminalOutcome:
        raise TransactionTerminalError("outcome 必须是 TransactionTerminalOutcome")
    if type(value.final_mode) is not GenerationMode:
        raise TransactionTerminalError("final_mode 必须是 GenerationMode")
    if type(value.maintenance_active) is not bool:
        raise TransactionTerminalError("maintenance_active 必须是布尔值")
    if (
        type(value.control_lease_epoch_audit) is not int
        or not 1 <= value.control_lease_epoch_audit <= _MAX_EPOCH
    ):
        raise TransactionTerminalError("control lease epoch 审计无效")
    _require_evidence_outcome_shape(value)


def _require_evidence_outcome_shape(value: TransactionTerminalEvidence) -> None:
    if value.outcome is TransactionTerminalOutcome.TARGET_COMMITTED:
        if (
            value.final_mode is not GenerationMode.STEADY
            or value.maintenance_active
            or value.acceptance_sha256 is None
            or value.serve_permit_sha256 is None
        ):
            raise TransactionTerminalError("target_committed 证据形状无效")
        return
    if value.outcome is TransactionTerminalOutcome.BASELINE_RESTORED:
        if value.final_mode is GenerationMode.STEADY:
            if (
                value.maintenance_active
                or value.acceptance_sha256 is None
                or value.serve_permit_sha256 is None
            ):
                raise TransactionTerminalError("公开 baseline 证据形状无效")
            return
        if value.final_mode is GenerationMode.RESTRICTED:
            if not value.maintenance_active or value.serve_permit_sha256 is not None:
                raise TransactionTerminalError("restricted baseline 证据形状无效")
            return
        raise TransactionTerminalError("baseline_restored 证据 mode 无效")
    if (
        value.final_mode is not GenerationMode.SAFETY_UNPROVEN
        or not value.maintenance_active
        or value.serve_permit_sha256 is not None
    ):
        raise TransactionTerminalError("safety_unproven 证据形状无效")


__all__ = [
    "TransactionTerminalError",
    "TransactionTerminalEvidence",
    "TransactionTerminalOutcome",
    "decode_transaction_terminal_evidence",
    "encode_transaction_terminal_evidence",
    "transaction_terminal_evidence_sha256",
]
