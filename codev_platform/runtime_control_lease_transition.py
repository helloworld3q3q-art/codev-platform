"""control lease pending transition 的无秘密规范意图契约。"""

from __future__ import annotations

import hmac
import json
from dataclasses import dataclass
from enum import StrEnum

from codev_platform._runtime_contract_support import (
    RuntimeContractSupportError,
    decode_exact_json_mapping,
    require_schema,
    require_strictly_later,
)
from codev_platform.core.runtime_models import RuntimeModelError, canonical_json_bytes
from codev_platform.runtime_fencing import (
    ControlLeaseRecord,
    ControlLeaseStatus,
    FencingContractError,
    control_lease_record_sha256,
    decode_control_lease_record,
    encode_control_lease_record,
)


MAX_CONTROL_LEASE_TRANSITION_BYTES = 33_280
_TRANSITION_FIELDS = frozenset(
    {"schema_version", "kind", "expected_record", "next_record"},
)


class ControlLeaseTransitionError(ValueError):
    """pending transition 的结构、规范字节或状态边不满足契约。"""


class ControlLeaseTransitionKind(StrEnum):
    """唯一允许持久化的 control lease 状态转换种类。"""

    INITIAL = "initial"
    TAKEOVER = "takeover"
    RETIRE = "retire"


@dataclass(frozen=True, slots=True)
class ControlLeaseTransitionIntent:
    """可恢复提交决定；只保存完整公开 record，不保存 capability。"""

    schema_version: int
    kind: ControlLeaseTransitionKind
    expected_record: ControlLeaseRecord | None
    next_record: ControlLeaseRecord

    def __post_init__(self) -> None:
        """在构造边界完成纯结构校验，持久语义由 fencing 层复验。"""
        try:
            require_schema(self.schema_version, 1)
        except RuntimeContractSupportError as error:
            raise ControlLeaseTransitionError(str(error)) from None
        if type(self.kind) is not ControlLeaseTransitionKind:
            raise ControlLeaseTransitionError("kind 必须是 ControlLeaseTransitionKind")
        if (
            self.expected_record is not None
            and type(self.expected_record) is not ControlLeaseRecord
        ):
            raise ControlLeaseTransitionError("expected_record 必须是 ControlLeaseRecord 或 None")
        if type(self.next_record) is not ControlLeaseRecord:
            raise ControlLeaseTransitionError("next_record 必须是 ControlLeaseRecord")
        _require_transition_shape(self)

    @property
    def expected_record_sha256(self) -> str | None:
        """从完整旧 record 派生 CAS 前置摘要，避免持久双真值。"""
        if self.expected_record is None:
            return None
        return control_lease_record_sha256(self.expected_record)

    @property
    def next_record_sha256(self) -> str:
        """从完整新 record 派生唯一候选摘要，避免持久双真值。"""
        return control_lease_record_sha256(self.next_record)


def encode_control_lease_transition_intent(
    value: ControlLeaseTransitionIntent,
) -> bytes:
    """严格编码四字段 pending intent，嵌套 record 仍使用既有规范 codec。"""
    if type(value) is not ControlLeaseTransitionIntent:
        raise ControlLeaseTransitionError("只接受 ControlLeaseTransitionIntent")
    try:
        return canonical_json_bytes(
            {
                "schema_version": value.schema_version,
                "kind": value.kind.value,
                "expected_record": _record_mapping(value.expected_record),
                "next_record": _record_mapping(value.next_record),
            },
        )
    except (FencingContractError, RuntimeModelError, TypeError, ValueError) as error:
        raise ControlLeaseTransitionError("pending transition 无法规范编码") from error


def decode_control_lease_transition_intent(
    payload: bytes,
) -> ControlLeaseTransitionIntent:
    """严格解码字段集合与嵌套 record，拒绝任何非规范意图。"""
    try:
        values = decode_exact_json_mapping(
            payload,
            required_fields=_TRANSITION_FIELDS,
            optional_defaults={},
            max_bytes=MAX_CONTROL_LEASE_TRANSITION_BYTES,
        )
        raw_kind = values["kind"]
        if type(raw_kind) is not str:
            raise ControlLeaseTransitionError("kind 类型无效")
        expected = _decode_optional_record(values["expected_record"], "expected_record")
        next_record = _decode_required_record(values["next_record"], "next_record")
        intent = ControlLeaseTransitionIntent(
            schema_version=values["schema_version"],
            kind=ControlLeaseTransitionKind(raw_kind),
            expected_record=expected,
            next_record=next_record,
        )
    except (
        ControlLeaseTransitionError,
        FencingContractError,
        RuntimeContractSupportError,
        TypeError,
        ValueError,
    ) as error:
        if type(error) is ControlLeaseTransitionError:
            raise
        raise ControlLeaseTransitionError("pending transition 无法严格解码") from error
    if encode_control_lease_transition_intent(intent) != payload:
        raise ControlLeaseTransitionError("pending transition 不是规范序列化")
    return intent


def _record_mapping(record: ControlLeaseRecord | None) -> dict[str, object] | None:
    if record is None:
        return None
    try:
        decoded = json.loads(encode_control_lease_record(record).decode("utf-8"))
    except (FencingContractError, UnicodeError, ValueError) as error:
        raise ControlLeaseTransitionError("control lease record 无法转换为嵌套映射") from error
    if type(decoded) is not dict:
        raise ControlLeaseTransitionError("control lease record 嵌套映射无效")
    return decoded


def _decode_optional_record(value: object, field: str) -> ControlLeaseRecord | None:
    if value is None:
        return None
    return _decode_required_record(value, field)


def _decode_required_record(value: object, field: str) -> ControlLeaseRecord:
    if type(value) is not dict:
        raise ControlLeaseTransitionError(f"{field} 必须是 JSON 对象")
    try:
        return decode_control_lease_record(canonical_json_bytes(value))
    except (FencingContractError, RuntimeModelError, TypeError, ValueError) as error:
        raise ControlLeaseTransitionError(f"{field} 不是严格 control lease record") from error


def _require_transition_shape(intent: ControlLeaseTransitionIntent) -> None:
    if intent.kind is ControlLeaseTransitionKind.INITIAL:
        _require_initial_shape(intent)
        return
    if intent.kind is ControlLeaseTransitionKind.TAKEOVER:
        _require_takeover_shape(intent)
        return
    _require_retire_shape(intent)


def _require_initial_shape(intent: ControlLeaseTransitionIntent) -> None:
    next_record = intent.next_record
    expected = intent.expected_record
    if (
        next_record.status is not ControlLeaseStatus.ACTIVE
        or next_record.epoch != 1
        or next_record.predecessor_sha256 is not None
    ):
        raise ControlLeaseTransitionError("initial 必须发布无前驱的 epoch=1 ACTIVE record")
    if expected is not None and (
        expected.status is not ControlLeaseStatus.RETIRED
        or expected.attempt_id == next_record.attempt_id
    ):
        raise ControlLeaseTransitionError(
            "initial 的旧 record 只能是不同 attempt 的 RETIRED tombstone"
        )


def _require_takeover_shape(intent: ControlLeaseTransitionIntent) -> None:
    expected = _require_active_expected(intent, "takeover")
    next_record = intent.next_record
    if next_record.status is not ControlLeaseStatus.ACTIVE:
        raise ControlLeaseTransitionError("takeover 必须发布 ACTIVE record")
    if (
        next_record.schema_version != expected.schema_version
        or next_record.attempt_id != expected.attempt_id
        or next_record.reservation_sha256 != expected.reservation_sha256
        or next_record.epoch != expected.epoch + 1
        or next_record.predecessor_sha256 != control_lease_record_sha256(expected)
        or hmac.compare_digest(next_record.token_sha256, expected.token_sha256)
    ):
        raise ControlLeaseTransitionError("takeover successor 与旧活动 record 不一致")
    try:
        require_strictly_later(
            next_record.issued_at,
            expected.issued_at,
            field="next_record.issued_at",
            boundary_field="expected_record.issued_at",
        )
    except RuntimeContractSupportError as error:
        raise ControlLeaseTransitionError(str(error)) from None


def _require_retire_shape(intent: ControlLeaseTransitionIntent) -> None:
    expected = _require_active_expected(intent, "retire")
    next_record = intent.next_record
    if next_record.status is not ControlLeaseStatus.RETIRED:
        raise ControlLeaseTransitionError("retire 必须发布 RETIRED record")
    stable_fields = (
        "schema_version",
        "attempt_id",
        "reservation_sha256",
        "epoch",
        "token_sha256",
        "owner",
        "issued_at",
        "predecessor_sha256",
    )
    if any(getattr(next_record, field) != getattr(expected, field) for field in stable_fields):
        raise ControlLeaseTransitionError("retire tombstone 与旧活动 record 不一致")
    if next_record.retired_from_sha256 != control_lease_record_sha256(expected):
        raise ControlLeaseTransitionError("retire tombstone 未精确绑定旧活动 record")


def _require_active_expected(
    intent: ControlLeaseTransitionIntent,
    kind: str,
) -> ControlLeaseRecord:
    expected = intent.expected_record
    if expected is None or expected.status is not ControlLeaseStatus.ACTIVE:
        raise ControlLeaseTransitionError(f"{kind} 必须带有 ACTIVE expected_record")
    return expected


__all__ = [
    "ControlLeaseTransitionError",
    "ControlLeaseTransitionIntent",
    "ControlLeaseTransitionKind",
    "MAX_CONTROL_LEASE_TRANSITION_BYTES",
    "decode_control_lease_transition_intent",
    "encode_control_lease_transition_intent",
]
