"""运行代际状态的不可变模型、字段约束与严格 codec。"""

from __future__ import annotations

import re
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


_MAX_STATE_BYTES = 32_768
_MAX_VERSION = 2**63 - 1
_FENCE_ID = re.compile(r"[A-Za-z0-9][A-Za-z0-9._:-]{0,127}\Z")
_STATE_FIELDS = frozenset(
    {
        "schema_version",
        "state_version",
        "mode",
        "serving_generation_id",
        "serving_fence_id",
        "serving_fence_epoch",
        "serving_fence_token_sha256",
        "desired_generation_id",
        "rollback_generation_id",
        "control_attempt_id",
        "control_reservation_sha256",
        "control_lease_record_sha256",
        "control_lease_epoch",
        "acceptance_sha256",
        "maintenance_active",
        "updated_at",
    }
)


class GenerationStateError(ValueError):
    """代际状态字段、状态边或序列化载荷不符合契约。"""


class GenerationMode(StrEnum):
    """运行代际控制面的有限状态。"""

    STEADY = "steady"
    SWITCHING = "switching"
    VALIDATING = "validating"
    RESTRICTED = "restricted"
    SAFETY_UNPROVEN = "safety_unproven"


@dataclass(frozen=True, slots=True)
class GenerationState:
    """当前 serving、回滚基线与一次控制尝试的不可变状态快照。"""

    schema_version: int
    state_version: int
    mode: GenerationMode
    serving_generation_id: str
    serving_fence_id: str
    serving_fence_epoch: int
    serving_fence_token_sha256: str
    desired_generation_id: str | None
    rollback_generation_id: str | None
    control_attempt_id: str | None
    control_reservation_sha256: str | None
    control_lease_record_sha256: str | None
    control_lease_epoch: int | None
    acceptance_sha256: str | None
    maintenance_active: bool
    updated_at: str

    def __post_init__(self) -> None:
        _require_state_fields(self)
        _require_state_shape(self)


def generation_state_sha256(value: GenerationState) -> str:
    """计算覆盖完整持久状态快照的规范摘要。"""
    if type(value) is not GenerationState:
        raise GenerationStateError("只接受 GenerationState")
    return canonical_sha256(value)


def encode_generation_state(value: GenerationState) -> bytes:
    """编码完整状态快照，不接受映射或相似对象。"""
    if type(value) is not GenerationState:
        raise GenerationStateError("只接受 GenerationState")
    return canonical_json_bytes(value)


def decode_generation_state(payload: bytes) -> GenerationState:
    """严格解码字段集合精确的状态快照。"""
    try:
        values = decode_exact_json_mapping(
            payload,
            required_fields=_STATE_FIELDS,
            optional_defaults={},
            max_bytes=_MAX_STATE_BYTES,
        )
        mode = values.get("mode")
        if type(mode) is not str:
            raise GenerationStateError("mode 类型无效")
        values["mode"] = GenerationMode(mode)
        return GenerationState(**values)
    except (RuntimeContractSupportError, TypeError, ValueError):
        raise GenerationStateError("GenerationState 序列化载荷无效") from None


def _require_state_fields(value: GenerationState) -> None:
    try:
        require_schema(value.schema_version, 1)
        _require_version(value.state_version, "state_version")
        require_sha256(value.serving_generation_id, field="serving_generation_id")
        require_sha256(
            value.serving_fence_token_sha256,
            field="serving_fence_token_sha256",
        )
        _require_optional_sha256(value.desired_generation_id, "desired_generation_id")
        _require_optional_sha256(value.rollback_generation_id, "rollback_generation_id")
        _require_optional_attempt_id(value.control_attempt_id)
        _require_optional_sha256(
            value.control_reservation_sha256,
            "control_reservation_sha256",
        )
        _require_optional_sha256(
            value.control_lease_record_sha256,
            "control_lease_record_sha256",
        )
        _require_optional_epoch(value.control_lease_epoch, "control_lease_epoch")
        _require_optional_sha256(value.acceptance_sha256, "acceptance_sha256")
        require_utc_rfc3339_z(value.updated_at, field="updated_at")
    except RuntimeContractSupportError as exc:
        raise GenerationStateError(str(exc)) from None
    if type(value.mode) is not GenerationMode:
        raise GenerationStateError("mode 必须是 GenerationMode")
    if (
        type(value.serving_fence_id) is not str
        or _FENCE_ID.fullmatch(value.serving_fence_id) is None
    ):
        raise GenerationStateError("serving_fence_id 必须是受限非空标识")
    _require_version(value.serving_fence_epoch, "serving_fence_epoch")
    if type(value.maintenance_active) is not bool:
        raise GenerationStateError("maintenance_active 必须是布尔值")


def _require_state_shape(value: GenerationState) -> None:
    control_bindings = (
        value.control_attempt_id,
        value.control_reservation_sha256,
        value.control_lease_record_sha256,
        value.control_lease_epoch,
    )
    if any(item is None for item in control_bindings) and any(
        item is not None for item in control_bindings
    ):
        raise GenerationStateError(
            "control attempt、reservation、record 与 lease epoch 必须成组绑定"
        )
    if value.mode is GenerationMode.STEADY:
        _require_steady_shape(value)
        return
    if not value.maintenance_active:
        raise GenerationStateError("非公开稳态必须保持维护门禁关闭服务")
    if value.mode in (GenerationMode.SWITCHING, GenerationMode.VALIDATING):
        _require_transition_shape(value)
        return
    _require_hazard_shape(value)


def _require_steady_shape(value: GenerationState) -> None:
    if value.acceptance_sha256 is None:
        raise GenerationStateError("公开或待发布稳态必须绑定 acceptance")
    control_bindings = (
        value.control_attempt_id,
        value.control_reservation_sha256,
        value.control_lease_record_sha256,
        value.control_lease_epoch,
    )
    if not value.maintenance_active:
        if value.desired_generation_id is not None:
            raise GenerationStateError("公开稳态不得保留 desired generation")
        if any(item is not None for item in control_bindings):
            raise GenerationStateError("公开稳态不得保留控制尝试绑定")
        _require_persistent_rollback(value)
        return
    if any(item is None for item in control_bindings):
        raise GenerationStateError("待发布稳态必须保留完整控制尝试绑定")
    if value.desired_generation_id != value.serving_generation_id:
        raise GenerationStateError("待发布稳态的 desired 必须已经成为 serving")
    _require_persistent_rollback(value)


def _require_transition_shape(value: GenerationState) -> None:
    bindings = (
        value.desired_generation_id,
        value.rollback_generation_id,
        value.control_attempt_id,
        value.control_reservation_sha256,
        value.control_lease_record_sha256,
        value.control_lease_epoch,
        value.acceptance_sha256,
    )
    if any(item is None for item in bindings):
        raise GenerationStateError("切换与验收态必须保留完整代际和控制绑定")
    if value.rollback_generation_id != value.serving_generation_id:
        raise GenerationStateError("切换与验收态的 rollback 必须是当前 serving")
    if value.desired_generation_id == value.serving_generation_id:
        raise GenerationStateError("切换与验收态的 desired 不能等于当前 serving")


def _require_hazard_shape(value: GenerationState) -> None:
    """危险态必须保留可审计控制锚点和最近验收绑定。"""
    control_bindings = (
        value.control_attempt_id,
        value.control_reservation_sha256,
        value.control_lease_record_sha256,
        value.control_lease_epoch,
    )
    if any(item is None for item in control_bindings):
        raise GenerationStateError("危险态必须保留完整 control lease 锚点")
    if value.acceptance_sha256 is None:
        raise GenerationStateError("危险态必须保留最近 acceptance 绑定")


def _require_persistent_rollback(value: GenerationState) -> None:
    if value.rollback_generation_id is None:
        raise GenerationStateError("正常 steady 必须保留 rollback generation")
    if value.rollback_generation_id == value.serving_generation_id:
        raise GenerationStateError("rollback generation 不能等于当前 serving")


def _require_version(value: object, field: str) -> None:
    if type(value) is not int or not 1 <= value <= _MAX_VERSION:
        raise GenerationStateError(f"{field} 必须在 1..2^63-1")


def _require_optional_sha256(value: object, field: str) -> None:
    if value is not None:
        require_sha256(value, field=field)


def _require_optional_attempt_id(value: object) -> None:
    if value is not None:
        require_attempt_id(value)


def _require_optional_epoch(value: object, field: str) -> None:
    if value is not None:
        _require_version(value, field)


__all__ = [
    "GenerationMode",
    "GenerationState",
    "GenerationStateError",
    "decode_generation_state",
    "encode_generation_state",
    "generation_state_sha256",
]
