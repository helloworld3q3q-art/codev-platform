"""部署尝试的两阶段预留与冻结契约。"""

from __future__ import annotations

import secrets
from dataclasses import dataclass
from enum import StrEnum
from typing import TypeVar

from codev_platform._runtime_contract_support import (
    RuntimeContractSupportError,
    decode_exact_json_mapping,
    require_attempt_id,
    require_schema,
    require_sha256,
    require_utc_rfc3339_z,
)
from codev_platform.core.runtime_models import canonical_json_bytes, canonical_sha256


_MAX_ATTEMPT_BYTES = 16_384
_RESERVATION_FIELDS = frozenset(
    {
        "schema_version",
        "attempt_id",
        "operation",
        "plan_sha256",
        "controller_sha256",
        "created_at",
    }
)
_DEPLOYMENT_ATTEMPT_FIELDS = _RESERVATION_FIELDS | frozenset(
    {
        "target_generation_id",
        "baseline_generation_id",
        "baseline_observation_sha256",
    }
)
_Model = TypeVar("_Model")


class AttemptContractError(ValueError):
    """部署尝试字段或序列化载荷不符合契约。"""


class AttemptOperation(StrEnum):
    DEPLOY = "deploy"
    ROLLBACK = "rollback"


@dataclass(frozen=True, slots=True)
class AttemptReservation:
    schema_version: int
    attempt_id: str
    operation: AttemptOperation
    plan_sha256: str
    controller_sha256: str
    created_at: str

    def __post_init__(self) -> None:
        _require_common_fields(self)


@dataclass(frozen=True, slots=True)
class DeploymentAttempt:
    schema_version: int
    attempt_id: str
    operation: AttemptOperation
    target_generation_id: str
    baseline_generation_id: str
    baseline_observation_sha256: str
    plan_sha256: str
    controller_sha256: str
    created_at: str

    def __post_init__(self) -> None:
        _require_common_fields(self)
        _require_digests(
            target_generation_id=self.target_generation_id,
            baseline_generation_id=self.baseline_generation_id,
            baseline_observation_sha256=self.baseline_observation_sha256,
        )
        if self.target_generation_id == self.baseline_generation_id:
            raise AttemptContractError("目标与基线 generation 不能相同")


def reserve_deployment_attempt(
    *,
    operation: AttemptOperation,
    plan_sha256: str,
    controller_sha256: str,
    created_at: str,
) -> AttemptReservation:
    """为一次人工操作生成新的 128 位预留身份。"""
    return AttemptReservation(
        schema_version=1,
        attempt_id=secrets.token_hex(16),
        operation=operation,
        plan_sha256=plan_sha256,
        controller_sha256=controller_sha256,
        created_at=created_at,
    )


def freeze_deployment_attempt(
    reservation: AttemptReservation,
    *,
    target_generation_id: str,
    baseline_generation_id: str,
    baseline_observation_sha256: str,
) -> DeploymentAttempt:
    """目标代际完成后，以原预留身份冻结完整部署尝试。"""
    if type(reservation) is not AttemptReservation:
        raise AttemptContractError("只接受已验证的 AttemptReservation")
    return DeploymentAttempt(
        schema_version=reservation.schema_version,
        attempt_id=reservation.attempt_id,
        operation=reservation.operation,
        target_generation_id=target_generation_id,
        baseline_generation_id=baseline_generation_id,
        baseline_observation_sha256=baseline_observation_sha256,
        plan_sha256=reservation.plan_sha256,
        controller_sha256=reservation.controller_sha256,
        created_at=reservation.created_at,
    )


def encode_attempt_reservation(value: AttemptReservation) -> bytes:
    """编码必须先落盘的 attempt 预留记录。"""
    return _encode_exact(value, AttemptReservation)


def attempt_reservation_sha256(value: AttemptReservation) -> str:
    """计算覆盖预留记录全部不可变字段的规范摘要。"""
    if type(value) is not AttemptReservation:
        raise AttemptContractError("只接受 AttemptReservation")
    return canonical_sha256(value)


def deployment_attempt_sha256(value: DeploymentAttempt) -> str:
    """计算覆盖完整部署尝试全部不可变字段的规范摘要。"""
    if type(value) is not DeploymentAttempt:
        raise AttemptContractError("只接受 DeploymentAttempt")
    return canonical_sha256(value)


def deployment_attempt_reservation_sha256(value: DeploymentAttempt) -> str:
    """复算完整 attempt 所继承的 reservation 身份摘要。"""
    if type(value) is not DeploymentAttempt:
        raise AttemptContractError("只接受 DeploymentAttempt")
    return attempt_reservation_sha256(
        AttemptReservation(
            schema_version=value.schema_version,
            attempt_id=value.attempt_id,
            operation=value.operation,
            plan_sha256=value.plan_sha256,
            controller_sha256=value.controller_sha256,
            created_at=value.created_at,
        )
    )


def decode_attempt_reservation(payload: bytes) -> AttemptReservation:
    """严格解码 attempt 预留记录。"""
    return _decode_attempt(payload, AttemptReservation)


def encode_deployment_attempt(value: DeploymentAttempt) -> bytes:
    """编码目标 generation 完成后冻结的完整 attempt。"""
    return _encode_exact(value, DeploymentAttempt)


def decode_deployment_attempt(payload: bytes) -> DeploymentAttempt:
    """严格解码完整 attempt。"""
    return _decode_attempt(payload, DeploymentAttempt)


def verify_frozen_attempt(
    reservation: AttemptReservation,
    attempt: DeploymentAttempt,
) -> DeploymentAttempt:
    """复验冻结记录完整继承指定预留记录的不可变身份。"""
    if type(reservation) is not AttemptReservation:
        raise AttemptContractError("只接受已验证的 AttemptReservation")
    if type(attempt) is not DeploymentAttempt:
        raise AttemptContractError("只接受已验证的 DeploymentAttempt")
    if any(
        getattr(reservation, field) != getattr(attempt, field)
        for field in (
            "schema_version",
            "attempt_id",
            "operation",
            "plan_sha256",
            "controller_sha256",
            "created_at",
        )
    ):
        raise AttemptContractError("DeploymentAttempt 与预留记录身份不一致")
    return attempt


def decode_deployment_attempt_for_reservation(
    reservation: AttemptReservation,
    payload: bytes,
) -> DeploymentAttempt:
    """严格解码并绑定复验指定预留记录。"""
    return verify_frozen_attempt(reservation, decode_deployment_attempt(payload))


def _require_common_fields(value: AttemptReservation | DeploymentAttempt) -> None:
    try:
        require_schema(value.schema_version, 1)
        require_attempt_id(value.attempt_id)
    except RuntimeContractSupportError as exc:
        raise AttemptContractError(str(exc)) from None
    if type(value.operation) is not AttemptOperation:
        raise AttemptContractError("operation 必须是 AttemptOperation")
    _require_digests(
        plan_sha256=value.plan_sha256,
        controller_sha256=value.controller_sha256,
    )
    try:
        require_utc_rfc3339_z(value.created_at, field="created_at")
    except RuntimeContractSupportError as exc:
        raise AttemptContractError(str(exc)) from None


def _require_digests(**values: object) -> None:
    try:
        for field, value in values.items():
            require_sha256(value, field=field)
    except RuntimeContractSupportError:
        raise AttemptContractError("部署尝试摘要无效") from None


def _encode_exact(value: _Model, expected: type[_Model]) -> bytes:
    if type(value) is not expected:
        raise AttemptContractError(f"只接受 {expected.__name__}")
    return canonical_json_bytes(value)


def _decode_attempt(payload: bytes, model: type[_Model]) -> _Model:
    try:
        required_fields = (
            _RESERVATION_FIELDS if model is AttemptReservation else _DEPLOYMENT_ATTEMPT_FIELDS
        )
        values = decode_exact_json_mapping(
            payload,
            required_fields=required_fields,
            optional_defaults={},
            max_bytes=_MAX_ATTEMPT_BYTES,
        )
        operation = values.get("operation")
        if type(operation) is not str:
            raise AttemptContractError("operation 类型无效")
        values["operation"] = AttemptOperation(operation)
        return model(**values)
    except (TypeError, ValueError, RuntimeContractSupportError):
        raise AttemptContractError(f"{model.__name__} 数据无效") from None


__all__ = [
    "AttemptContractError",
    "AttemptOperation",
    "AttemptReservation",
    "DeploymentAttempt",
    "attempt_reservation_sha256",
    "decode_attempt_reservation",
    "decode_deployment_attempt",
    "decode_deployment_attempt_for_reservation",
    "deployment_attempt_reservation_sha256",
    "deployment_attempt_sha256",
    "encode_attempt_reservation",
    "encode_deployment_attempt",
    "freeze_deployment_attempt",
    "reserve_deployment_attempt",
    "verify_frozen_attempt",
]
