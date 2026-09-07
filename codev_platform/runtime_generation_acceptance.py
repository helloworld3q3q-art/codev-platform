"""按部署 attempt 独立冻结的运行代际验收契约。"""

from __future__ import annotations

import re
from dataclasses import dataclass

from codev_platform._runtime_contract_support import (
    RuntimeContractSupportError,
    decode_exact_json_mapping,
    require_attempt_id,
    require_schema,
    require_sha256,
    require_utc_rfc3339_z,
)
from codev_platform.core.runtime_models import canonical_json_bytes, canonical_sha256


_FENCE_ID = re.compile(r"[A-Za-z0-9][A-Za-z0-9._:-]{0,127}\Z")
_MAX_EPOCH = 2**63 - 1
_MAX_ACCEPTANCE_BYTES = 32_768
_ACCEPTANCE_FIELDS = frozenset(
    {
        "schema_version",
        "attempt_id",
        "generation_id",
        "serving_fence_id",
        "serving_fence_epoch",
        "serving_fence_token_sha256",
        "control_lease_epoch_audit",
        "control_token_sha256_audit",
        "control_lease_record_sha256_audit",
        "entrypoint_proof_sha256",
        "database_proof_sha256",
        "systemd_proof_sha256",
        "index_set_proof_sha256",
        "health_proof_sha256",
        "accepted_at",
    }
)


class GenerationAcceptanceError(ValueError):
    """运行代际验收字段或序列化载荷不符合契约。"""


@dataclass(frozen=True, slots=True)
class GenerationAcceptance:
    schema_version: int
    attempt_id: str
    generation_id: str
    serving_fence_id: str
    serving_fence_epoch: int
    serving_fence_token_sha256: str
    control_lease_epoch_audit: int
    control_token_sha256_audit: str
    control_lease_record_sha256_audit: str
    entrypoint_proof_sha256: str
    database_proof_sha256: str
    systemd_proof_sha256: str
    index_set_proof_sha256: str
    health_proof_sha256: str
    accepted_at: str

    def __post_init__(self) -> None:
        try:
            require_schema(self.schema_version, 1)
            require_attempt_id(self.attempt_id)
        except RuntimeContractSupportError as exc:
            raise GenerationAcceptanceError(str(exc)) from None
        _require_fence_id(self.serving_fence_id)
        _require_epoch(self.serving_fence_epoch, "serving_fence_epoch")
        _require_epoch(self.control_lease_epoch_audit, "control_lease_epoch_audit")
        _require_digests(
            generation_id=self.generation_id,
            serving_fence_token_sha256=self.serving_fence_token_sha256,
            control_token_sha256_audit=self.control_token_sha256_audit,
            control_lease_record_sha256_audit=self.control_lease_record_sha256_audit,
            entrypoint_proof_sha256=self.entrypoint_proof_sha256,
            database_proof_sha256=self.database_proof_sha256,
            systemd_proof_sha256=self.systemd_proof_sha256,
            index_set_proof_sha256=self.index_set_proof_sha256,
            health_proof_sha256=self.health_proof_sha256,
        )
        _require_utc_timestamp(self.accepted_at)


def serving_binding_sha256(value: GenerationAcceptance) -> str:
    """计算 state/permit 使用的验收绑定，不纳入 control 审计和时间。"""
    if type(value) is not GenerationAcceptance:
        raise GenerationAcceptanceError("只接受 GenerationAcceptance")
    return canonical_sha256(
        {
            "attempt_id": value.attempt_id,
            "database_proof_sha256": value.database_proof_sha256,
            "entrypoint_proof_sha256": value.entrypoint_proof_sha256,
            "generation_id": value.generation_id,
            "health_proof_sha256": value.health_proof_sha256,
            "index_set_proof_sha256": value.index_set_proof_sha256,
            "schema_version": value.schema_version,
            "serving_fence_epoch": value.serving_fence_epoch,
            "serving_fence_id": value.serving_fence_id,
            "serving_fence_token_sha256": value.serving_fence_token_sha256,
            "systemd_proof_sha256": value.systemd_proof_sha256,
        }
    )


def acceptance_record_sha256(value: GenerationAcceptance) -> str:
    """计算覆盖全部持久字段的审计记录摘要。"""
    if type(value) is not GenerationAcceptance:
        raise GenerationAcceptanceError("只接受 GenerationAcceptance")
    return canonical_sha256(value)


def encode_generation_acceptance(value: GenerationAcceptance) -> bytes:
    """编码完整验收审计记录，不编码任何原始 capability。"""
    if type(value) is not GenerationAcceptance:
        raise GenerationAcceptanceError("只接受 GenerationAcceptance")
    return canonical_json_bytes(value)


def decode_generation_acceptance(payload: bytes) -> GenerationAcceptance:
    """严格解码验收记录，未知、缺失或重复字段一律拒绝。"""
    try:
        values = decode_exact_json_mapping(
            payload,
            required_fields=_ACCEPTANCE_FIELDS,
            optional_defaults={},
            max_bytes=_MAX_ACCEPTANCE_BYTES,
        )
        return GenerationAcceptance(**values)
    except (RuntimeContractSupportError, TypeError, ValueError):
        raise GenerationAcceptanceError("GenerationAcceptance 序列化载荷无效") from None


def _require_fence_id(value: object) -> None:
    if type(value) is not str or _FENCE_ID.fullmatch(value) is None:
        raise GenerationAcceptanceError("serving_fence_id 必须是受限非空标识")


def _require_epoch(value: object, field: str) -> None:
    if type(value) is not int or not 1 <= value <= _MAX_EPOCH:
        raise GenerationAcceptanceError(f"{field} 必须在 1..2^63-1")


def _require_digests(**values: object) -> None:
    try:
        for field, value in values.items():
            require_sha256(value, field=field)
    except RuntimeContractSupportError:
        raise GenerationAcceptanceError("验收摘要无效") from None


def _require_utc_timestamp(value: object) -> None:
    try:
        require_utc_rfc3339_z(value, field="accepted_at")
    except RuntimeContractSupportError as exc:
        raise GenerationAcceptanceError(str(exc)) from None


__all__ = [
    "GenerationAcceptance",
    "GenerationAcceptanceError",
    "acceptance_record_sha256",
    "decode_generation_acceptance",
    "encode_generation_acceptance",
    "serving_binding_sha256",
]
