"""基线观察、受保护载荷引用与完整回滚包契约。"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TypeVar

from codev_platform._runtime_contract_support import (
    RuntimeContractSupportError,
    decode_exact_json_mapping,
    require_attempt_id,
    require_schema,
    require_sha256,
    require_utc_rfc3339_z,
)
from codev_platform._runtime_rollback_payload import (
    MAX_TOTAL_PAYLOADS,
    PayloadCategory,
    PayloadIdentityKind,
    PayloadLogicalRole,
    ProtectedPayloadRef,
    RollbackContractError,
    configuration_manifest_sha256,
    protected_payload_from_mapping,
    require_non_conflicting_paths,
    require_payload_group,
    sort_payloads,
    systemd_receipt_manifest_sha256,
)
from codev_platform.core.runtime_models import (
    RuntimeModelError,
    canonical_json_bytes,
    canonical_sha256,
)
from codev_platform.runtime_attempt_contract import DeploymentAttempt
from codev_platform.runtime_generation_contract import RuntimeGeneration


_MAX_PID = 2**31 - 1
_MAX_ROLLBACK_CONTRACT_BYTES = 1_048_576
_PROTECTED_PAYLOAD_FIELDS = frozenset(
    {
        "category",
        "logical_role",
        "target_key",
        "relative_path",
        "identity_kind",
        "identity_sha256",
        "protection_context_sha256",
        "mode",
        "uid",
        "gid",
    }
)
_OBSERVATION_FIELDS = frozenset(
    {
        "schema_version",
        "serving_generation_id",
        "generation_state_sha256",
        "generation_acceptance_sha256",
        "serve_permit_sha256",
        "main_pid",
        "interpreter_identity_sha256",
        "systemd_state_sha256",
        "configuration_state_sha256",
        "database_state_sha256",
        "index_set_sha256",
        "observed_at",
    }
)
_BUNDLE_FIELDS = frozenset(
    {
        "schema_version",
        "attempt_id",
        "plan_sha256",
        "baseline_observation_sha256",
        "original_serving_generation_id",
        "systemd_payloads",
        "configuration_payloads",
        "receipt_payloads",
        "database_compatibility_sha256",
        "index_set_sha256",
        "created_at",
    }
)
_Model = TypeVar("_Model")


@dataclass(frozen=True, slots=True)
class BaselineObservation:
    schema_version: int
    serving_generation_id: str
    generation_state_sha256: str
    generation_acceptance_sha256: str
    serve_permit_sha256: str
    main_pid: int
    interpreter_identity_sha256: str
    systemd_state_sha256: str
    configuration_state_sha256: str
    database_state_sha256: str
    index_set_sha256: str
    observed_at: str

    def __post_init__(self) -> None:
        _require_schema(self.schema_version)
        _require_digests(
            serving_generation_id=self.serving_generation_id,
            generation_state_sha256=self.generation_state_sha256,
            generation_acceptance_sha256=self.generation_acceptance_sha256,
            serve_permit_sha256=self.serve_permit_sha256,
            interpreter_identity_sha256=self.interpreter_identity_sha256,
            systemd_state_sha256=self.systemd_state_sha256,
            configuration_state_sha256=self.configuration_state_sha256,
            database_state_sha256=self.database_state_sha256,
            index_set_sha256=self.index_set_sha256,
        )
        if type(self.main_pid) is not int or not 1 <= self.main_pid <= _MAX_PID:
            raise RollbackContractError("main_pid 超出有效正整数范围")
        _require_utc_timestamp(self.observed_at, "observed_at")


@dataclass(frozen=True, slots=True)
class RollbackBundle:
    schema_version: int
    attempt_id: str
    plan_sha256: str
    baseline_observation_sha256: str
    original_serving_generation_id: str
    systemd_payloads: tuple[ProtectedPayloadRef, ...]
    configuration_payloads: tuple[ProtectedPayloadRef, ...]
    receipt_payloads: tuple[ProtectedPayloadRef, ...]
    database_compatibility_sha256: str
    index_set_sha256: str
    created_at: str

    def __post_init__(self) -> None:
        _require_schema(self.schema_version)
        try:
            require_attempt_id(self.attempt_id)
        except RuntimeContractSupportError as exc:
            raise RollbackContractError(str(exc)) from None
        _require_digests(
            plan_sha256=self.plan_sha256,
            baseline_observation_sha256=self.baseline_observation_sha256,
            original_serving_generation_id=self.original_serving_generation_id,
            database_compatibility_sha256=self.database_compatibility_sha256,
            index_set_sha256=self.index_set_sha256,
        )
        groups = (
            ("systemd_payloads", self.systemd_payloads, PayloadCategory.SYSTEMD),
            (
                "configuration_payloads",
                self.configuration_payloads,
                PayloadCategory.CONFIGURATION,
            ),
            ("receipt_payloads", self.receipt_payloads, PayloadCategory.RECEIPT),
        )
        for field, payloads, category in groups:
            require_payload_group(payloads, field, category, self.attempt_id)
        if sum(len(payloads) for _, payloads, _ in groups) > MAX_TOTAL_PAYLOADS:
            raise RollbackContractError("受保护载荷总数量超出上限")
        require_non_conflicting_paths(
            tuple(item for _, group, _ in groups for item in group)
        )
        _require_utc_timestamp(self.created_at, "created_at")
        _require_rollback_bundle_size(self)


def baseline_observation_sha256(value: BaselineObservation) -> str:
    """计算排除观察时间的现场事实身份摘要。"""
    if type(value) is not BaselineObservation:
        raise RollbackContractError("只接受 BaselineObservation")
    return canonical_sha256(
        {
            "configuration_state_sha256": value.configuration_state_sha256,
            "database_state_sha256": value.database_state_sha256,
            "generation_acceptance_sha256": value.generation_acceptance_sha256,
            "generation_state_sha256": value.generation_state_sha256,
            "index_set_sha256": value.index_set_sha256,
            "interpreter_identity_sha256": value.interpreter_identity_sha256,
            "main_pid": value.main_pid,
            "schema_version": value.schema_version,
            "serve_permit_sha256": value.serve_permit_sha256,
            "serving_generation_id": value.serving_generation_id,
            "systemd_state_sha256": value.systemd_state_sha256,
        }
    )


def create_rollback_bundle(
    *,
    attempt: DeploymentAttempt,
    baseline_generation: RuntimeGeneration,
    baseline_observation: BaselineObservation,
    systemd_payloads: tuple[ProtectedPayloadRef, ...],
    configuration_payloads: tuple[ProtectedPayloadRef, ...],
    receipt_payloads: tuple[ProtectedPayloadRef, ...],
    created_at: str,
) -> RollbackBundle:
    """从已验证 attempt、代际和现场观察单向派生完整回滚绑定。"""
    if type(attempt) is not DeploymentAttempt:
        raise RollbackContractError("只接受 DeploymentAttempt")
    if type(baseline_generation) is not RuntimeGeneration:
        raise RollbackContractError("只接受 RuntimeGeneration")
    if type(baseline_observation) is not BaselineObservation:
        raise RollbackContractError("只接受 BaselineObservation")
    bundle = RollbackBundle(
        schema_version=1,
        attempt_id=attempt.attempt_id,
        plan_sha256=attempt.plan_sha256,
        baseline_observation_sha256=attempt.baseline_observation_sha256,
        original_serving_generation_id=baseline_generation.generation_id,
        systemd_payloads=sort_payloads(systemd_payloads, "systemd_payloads"),
        configuration_payloads=sort_payloads(
            configuration_payloads,
            "configuration_payloads",
        ),
        receipt_payloads=sort_payloads(receipt_payloads, "receipt_payloads"),
        database_compatibility_sha256=baseline_generation.database_contract_sha256,
        index_set_sha256=baseline_generation.index_set_sha256,
        created_at=created_at,
    )
    return verify_rollback_bundle(
        bundle,
        attempt,
        baseline_generation,
        baseline_observation,
    )


def verify_rollback_bundle(
    bundle: RollbackBundle,
    attempt: DeploymentAttempt,
    baseline_generation: RuntimeGeneration,
    baseline_observation: BaselineObservation,
) -> RollbackBundle:
    """按 factory 同一真值复验读取后的回滚包。"""
    if type(bundle) is not RollbackBundle:
        raise RollbackContractError("只接受 RollbackBundle")
    if type(attempt) is not DeploymentAttempt:
        raise RollbackContractError("只接受 DeploymentAttempt")
    if type(baseline_generation) is not RuntimeGeneration:
        raise RollbackContractError("只接受 RuntimeGeneration")
    if type(baseline_observation) is not BaselineObservation:
        raise RollbackContractError("只接受 BaselineObservation")
    observation_sha256 = baseline_observation_sha256(baseline_observation)
    if attempt.baseline_generation_id != baseline_generation.generation_id:
        raise RollbackContractError("attempt 与 baseline generation 身份不一致")
    if attempt.baseline_observation_sha256 != observation_sha256:
        raise RollbackContractError("attempt 与 baseline observation 摘要不一致")
    if baseline_observation.serving_generation_id != baseline_generation.generation_id:
        raise RollbackContractError("observation serving generation 与基线代际不一致")
    if (
        baseline_observation.systemd_state_sha256
        != baseline_generation.systemd_bundle_sha256
    ):
        raise RollbackContractError("observation systemd 事实与基线代际不一致")
    if (
        baseline_observation.configuration_state_sha256
        != baseline_generation.configuration_bundle_sha256
    ):
        raise RollbackContractError("observation configuration 事实与基线代际不一致")
    if baseline_observation.index_set_sha256 != baseline_generation.index_set_sha256:
        raise RollbackContractError("observation index 事实与基线代际不一致")
    if any(
        (
            bundle.attempt_id != attempt.attempt_id,
            bundle.plan_sha256 != attempt.plan_sha256,
            bundle.baseline_observation_sha256 != observation_sha256,
            bundle.original_serving_generation_id != baseline_generation.generation_id,
            bundle.database_compatibility_sha256
            != baseline_generation.database_contract_sha256,
            bundle.index_set_sha256 != baseline_generation.index_set_sha256,
        )
    ):
        raise RollbackContractError("rollback bundle 对象绑定字段不一致")
    if (
        systemd_receipt_manifest_sha256(
            bundle.systemd_payloads,
            bundle.receipt_payloads,
        )
        != baseline_generation.systemd_bundle_sha256
    ):
        raise RollbackContractError("systemd/receipt payload manifest 与基线代际不一致")
    if (
        configuration_manifest_sha256(bundle.configuration_payloads)
        != baseline_generation.configuration_bundle_sha256
    ):
        raise RollbackContractError("configuration payload manifest 与基线代际不一致")
    return bundle


def rollback_bundle_sha256(value: RollbackBundle) -> str:
    """计算排除创建时间的完整回滚包身份摘要。"""
    if type(value) is not RollbackBundle:
        raise RollbackContractError("只接受 RollbackBundle")
    return canonical_sha256(
        {
            "attempt_id": value.attempt_id,
            "plan_sha256": value.plan_sha256,
            "baseline_observation_sha256": value.baseline_observation_sha256,
            "configuration_payloads": value.configuration_payloads,
            "database_compatibility_sha256": value.database_compatibility_sha256,
            "index_set_sha256": value.index_set_sha256,
            "original_serving_generation_id": value.original_serving_generation_id,
            "receipt_payloads": value.receipt_payloads,
            "schema_version": value.schema_version,
            "systemd_payloads": value.systemd_payloads,
        }
    )


def encode_protected_payload_ref(value: ProtectedPayloadRef) -> bytes:
    """编码不含正文的受保护载荷引用。"""
    return _encode_exact(value, ProtectedPayloadRef)


def decode_protected_payload_ref(payload: bytes) -> ProtectedPayloadRef:
    """严格解码一个受保护载荷引用。"""
    values = _decode_rollback_mapping(payload, _PROTECTED_PAYLOAD_FIELDS, "ProtectedPayloadRef")
    return protected_payload_from_mapping(values)


def encode_baseline_observation(value: BaselineObservation) -> bytes:
    """编码维护窗口前冻结的只读基线观察。"""
    return _encode_exact(value, BaselineObservation)


def decode_baseline_observation(payload: bytes) -> BaselineObservation:
    """严格解码基线观察。"""
    values = _decode_rollback_mapping(payload, _OBSERVATION_FIELDS, "BaselineObservation")
    try:
        return BaselineObservation(**values)
    except (TypeError, ValueError):
        raise RollbackContractError("BaselineObservation 数据无效") from None


def encode_rollback_bundle(value: RollbackBundle) -> bytes:
    """编码完整回滚包引用图，不编码任何受保护正文。"""
    return _encode_exact(value, RollbackBundle)


def decode_rollback_bundle(payload: bytes) -> RollbackBundle:
    """严格解码完整回滚包及三类嵌套载荷引用。"""
    values = _decode_rollback_mapping(payload, _BUNDLE_FIELDS, "RollbackBundle")
    try:
        for field in ("systemd_payloads", "configuration_payloads", "receipt_payloads"):
            nested = values.get(field)
            if type(nested) is not list:
                raise RollbackContractError(f"{field} 必须是 JSON 数组")
            values[field] = tuple(protected_payload_from_mapping(item) for item in nested)
        return RollbackBundle(**values)
    except (TypeError, ValueError):
        raise RollbackContractError("RollbackBundle 数据无效") from None


def _encode_exact(value: _Model, expected: type[_Model]) -> bytes:
    if type(value) is not expected:
        raise RollbackContractError(f"只接受 {expected.__name__}")
    return canonical_json_bytes(value)


def _decode_rollback_mapping(
    payload: bytes,
    required_fields: frozenset[str],
    label: str,
) -> dict[str, object]:
    try:
        return decode_exact_json_mapping(
            payload,
            required_fields=required_fields,
            optional_defaults={},
            max_bytes=_MAX_ROLLBACK_CONTRACT_BYTES,
        )
    except RuntimeContractSupportError:
        raise RollbackContractError(f"{label} 序列化载荷无效") from None


def _require_schema(value: object) -> None:
    try:
        require_schema(value, 1)
    except RuntimeContractSupportError as exc:
        raise RollbackContractError(str(exc)) from None


def _require_digests(**values: object) -> None:
    try:
        for field, value in values.items():
            require_sha256(value, field=field)
    except RuntimeContractSupportError:
        raise RollbackContractError("回滚契约摘要无效") from None


def _require_utc_timestamp(value: object, field: str) -> None:
    try:
        require_utc_rfc3339_z(value, field=field)
    except RuntimeContractSupportError as exc:
        raise RollbackContractError(str(exc)) from None


def _require_rollback_bundle_size(value: RollbackBundle) -> None:
    try:
        encoded = canonical_json_bytes(value)
    except RuntimeModelError:
        raise RollbackContractError("RollbackBundle 无法生成规范序列化载荷") from None
    if len(encoded) > _MAX_ROLLBACK_CONTRACT_BYTES:
        raise RollbackContractError("RollbackBundle 序列化载荷超出 1 MiB 上限")


__all__ = [
    "BaselineObservation",
    "PayloadCategory",
    "PayloadIdentityKind",
    "PayloadLogicalRole",
    "ProtectedPayloadRef",
    "RollbackBundle",
    "RollbackContractError",
    "baseline_observation_sha256",
    "configuration_manifest_sha256",
    "create_rollback_bundle",
    "decode_baseline_observation",
    "decode_protected_payload_ref",
    "decode_rollback_bundle",
    "encode_baseline_observation",
    "encode_protected_payload_ref",
    "encode_rollback_bundle",
    "rollback_bundle_sha256",
    "systemd_receipt_manifest_sha256",
    "verify_rollback_bundle",
]
