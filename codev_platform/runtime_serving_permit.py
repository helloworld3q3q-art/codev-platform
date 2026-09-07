"""公开稳态 ServingPermit 的类型化绑定契约。"""

from __future__ import annotations

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
from codev_platform.runtime_fencing import (
    ServingFenceRecord,
)
from codev_platform.runtime_generation_acceptance import (
    GenerationAcceptance,
    serving_binding_sha256,
)
from codev_platform.runtime_generation_acceptance_validation import (
    GenerationAcceptanceBindingError,
    verify_serving_fence_acceptance,
)
from codev_platform.runtime_generation_state import (
    GenerationMode,
    GenerationState,
    generation_state_sha256,
)


class ServingPermitError(ValueError):
    """ServingPermit 字段或绑定关系不符合契约。"""


_MAX_PERMIT_BYTES = 16_384
_PERMIT_FIELDS = frozenset(
    {
        "schema_version",
        "attempt_id",
        "generation_id",
        "generation_state_sha256",
        "acceptance_sha256",
        "serving_fence_sha256",
        "issued_at",
    }
)


@dataclass(frozen=True, slots=True)
class ServingPermitRecord:
    """只含公开摘要、精确绑定目标状态 B 的 staged permit。"""

    schema_version: int
    attempt_id: str
    generation_id: str
    generation_state_sha256: str
    acceptance_sha256: str
    serving_fence_sha256: str
    issued_at: str

    def __post_init__(self) -> None:
        _require_permit_fields(self)


def create_serving_permit(
    state: GenerationState,
    acceptance: GenerationAcceptance,
    fence: ServingFenceRecord,
    *,
    issued_at: str,
) -> ServingPermitRecord:
    """只从相互一致且门禁已关闭的 typed 公开稳态构造 permit。"""
    _verify_serving_context(state, acceptance, fence)
    return ServingPermitRecord(
        schema_version=1,
        attempt_id=acceptance.attempt_id,
        generation_id=state.serving_generation_id,
        generation_state_sha256=generation_state_sha256(state),
        acceptance_sha256=serving_binding_sha256(acceptance),
        serving_fence_sha256=canonical_sha256(fence),
        issued_at=issued_at,
    )


def verify_serving_permit(
    permit: ServingPermitRecord,
    state: GenerationState,
    acceptance: GenerationAcceptance,
    fence: ServingFenceRecord,
) -> ServingPermitRecord:
    """复算并验证 permit 与 state/acceptance/fence 的全部绑定。"""
    if type(permit) is not ServingPermitRecord:
        raise ServingPermitError("只接受 ServingPermitRecord")
    _verify_serving_context(state, acceptance, fence)
    expected = (
        acceptance.attempt_id,
        state.serving_generation_id,
        generation_state_sha256(state),
        serving_binding_sha256(acceptance),
        canonical_sha256(fence),
    )
    actual = (
        permit.attempt_id,
        permit.generation_id,
        permit.generation_state_sha256,
        permit.acceptance_sha256,
        permit.serving_fence_sha256,
    )
    if actual != expected:
        raise ServingPermitError("ServingPermit 与公开稳态绑定不一致")
    return permit


def serving_permit_sha256(value: ServingPermitRecord) -> str:
    """计算覆盖 permit 全部公开字段的规范摘要。"""
    if type(value) is not ServingPermitRecord:
        raise ServingPermitError("只接受 ServingPermitRecord")
    return canonical_sha256(value)


def encode_serving_permit(value: ServingPermitRecord) -> bytes:
    """编码字段集合固定且不含原始 capability 的 permit。"""
    if type(value) is not ServingPermitRecord:
        raise ServingPermitError("只接受 ServingPermitRecord")
    return canonical_json_bytes(value)


def decode_serving_permit(payload: bytes) -> ServingPermitRecord:
    """在固定大小上限内严格解码 ServingPermit。"""
    try:
        values = decode_exact_json_mapping(
            payload,
            required_fields=_PERMIT_FIELDS,
            optional_defaults={},
            max_bytes=_MAX_PERMIT_BYTES,
        )
        return ServingPermitRecord(**values)
    except (RuntimeContractSupportError, TypeError, ValueError):
        raise ServingPermitError("ServingPermitRecord 数据无效") from None


def _verify_serving_context(
    state: GenerationState,
    acceptance: GenerationAcceptance,
    fence: ServingFenceRecord,
) -> None:
    if type(state) is not GenerationState:
        raise ServingPermitError("只接受 GenerationState")
    if type(acceptance) is not GenerationAcceptance:
        raise ServingPermitError("只接受 GenerationAcceptance")
    if type(fence) is not ServingFenceRecord:
        raise ServingPermitError("只接受 ServingFenceRecord")
    if state.mode is not GenerationMode.STEADY or state.maintenance_active:
        raise ServingPermitError("ServingPermit 只绑定公开 steady 状态")
    try:
        verify_serving_fence_acceptance(fence, acceptance)
    except GenerationAcceptanceBindingError as exc:
        raise ServingPermitError(str(exc)) from None
    state_matches = (
        state.serving_generation_id == fence.generation_id
        and state.serving_fence_id == fence.fence_id
        and state.serving_fence_epoch == fence.epoch
        and state.serving_fence_token_sha256 == fence.token_sha256
        and state.acceptance_sha256 == serving_binding_sha256(acceptance)
    )
    if not state_matches:
        raise ServingPermitError("state/acceptance/fence 不是同一公开稳态")


def _require_permit_fields(value: ServingPermitRecord) -> None:
    try:
        require_schema(value.schema_version, 1)
        require_attempt_id(value.attempt_id)
        for field in (
            "generation_id",
            "generation_state_sha256",
            "acceptance_sha256",
            "serving_fence_sha256",
        ):
            require_sha256(getattr(value, field), field=field)
        require_utc_rfc3339_z(value.issued_at, field="issued_at")
    except RuntimeContractSupportError as exc:
        raise ServingPermitError(str(exc)) from None


__all__ = [
    "ServingPermitError",
    "ServingPermitRecord",
    "create_serving_permit",
    "decode_serving_permit",
    "encode_serving_permit",
    "serving_permit_sha256",
    "verify_serving_permit",
]
