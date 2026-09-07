"""控制租约与 serving 围栏的记录、capability 和校验边界。"""

from __future__ import annotations

import hashlib
import hmac
import re
from dataclasses import dataclass, field, replace
from enum import StrEnum
from typing import TYPE_CHECKING

from codev_platform._runtime_contract_support import (
    RuntimeContractSupportError,
    decode_exact_json_mapping,
    require_attempt_id,
    require_schema,
    require_sha256,
    require_strictly_later,
    require_utc_rfc3339_z,
)
from codev_platform.core.runtime_models import canonical_json_bytes, canonical_sha256
from codev_platform.runtime_attempt_contract import (
    AttemptReservation,
    DeploymentAttempt,
    attempt_reservation_sha256,
)

if TYPE_CHECKING:
    from codev_platform.runtime_generation_state import GenerationState


_MAX_RECORD_BYTES = 16_384
_MAX_EPOCH = 2**63 - 1
_TOKEN_BYTES = 32
_FENCE_ID = re.compile(r"[A-Za-z0-9][A-Za-z0-9._:-]{0,127}\Z")
_OWNER = re.compile(r"[A-Za-z0-9][A-Za-z0-9._:@-]{0,127}\Z")
_CONTROL_FIELDS = frozenset(
    {
        "schema_version",
        "attempt_id",
        "reservation_sha256",
        "epoch",
        "token_sha256",
        "owner",
        "status",
        "issued_at",
        "predecessor_sha256",
        "terminal_journal_sha256",
        "terminal_evidence_sha256",
        "retired_at",
        "retired_from_sha256",
    }
)
_SERVING_FIELDS = frozenset(
    {
        "schema_version",
        "fence_id",
        "generation_id",
        "accepted_attempt_id",
        "epoch",
        "token_sha256",
        "issued_at",
    }
)


class FencingContractError(ValueError):
    """租约、围栏或 capability 校验不符合领域契约。"""


class ControlLeaseStatus(StrEnum):
    """控制租约公开记录的生命周期。"""

    ACTIVE = "active"
    RETIRED = "retired"


@dataclass(frozen=True, slots=True)
class ControlLeaseRecord:
    """仅含摘要、可安全持久化的控制租约审计记录。"""

    schema_version: int
    attempt_id: str
    reservation_sha256: str
    epoch: int
    token_sha256: str
    owner: str
    status: ControlLeaseStatus
    issued_at: str
    predecessor_sha256: str | None
    terminal_journal_sha256: str | None = None
    terminal_evidence_sha256: str | None = None
    retired_at: str | None = None
    retired_from_sha256: str | None = None

    def __post_init__(self) -> None:
        _require_control_record(self)


@dataclass(frozen=True, slots=True)
class ControlLeaseProof:
    """只在受保护调用链中传递的控制租约 capability。"""

    attempt_id: str
    epoch: int
    token: bytes = field(repr=False)

    def __post_init__(self) -> None:
        _require_control_proof(self)

    @property
    def token_sha256(self) -> str:
        """仅在校验边界计算审计摘要。"""
        return _token_sha256(self.token)


@dataclass(frozen=True, slots=True)
class ServingFenceRecord:
    """可安全持久化的 serving 围栏公开记录。"""

    schema_version: int
    fence_id: str
    generation_id: str
    accepted_attempt_id: str
    epoch: int
    token_sha256: str
    issued_at: str

    def __post_init__(self) -> None:
        _require_serving_record(self)


@dataclass(frozen=True, slots=True)
class ServingFenceProof:
    """稳态 writer 持有、不可持久化的 serving capability。"""

    fence_id: str
    epoch: int
    token: bytes = field(repr=False)

    def __post_init__(self) -> None:
        _require_serving_proof(self)

    @property
    def token_sha256(self) -> str:
        """仅在校验边界计算 serving capability 摘要。"""
        return _token_sha256(self.token)


def issue_control_lease(
    reservation: AttemptReservation,
    *,
    epoch: int,
    token: bytes,
    owner: str,
    issued_at: str,
) -> tuple[ControlLeaseRecord, ControlLeaseProof]:
    """把外部安全源提供的 capability 分成公开记录和私有 proof。"""
    if type(reservation) is not AttemptReservation:
        raise FencingContractError("只接受 AttemptReservation")
    proof = ControlLeaseProof(
        attempt_id=reservation.attempt_id,
        epoch=epoch,
        token=token,
    )
    record = ControlLeaseRecord(
        schema_version=1,
        attempt_id=reservation.attempt_id,
        reservation_sha256=attempt_reservation_sha256(reservation),
        epoch=epoch,
        token_sha256=proof.token_sha256,
        owner=owner,
        status=ControlLeaseStatus.ACTIVE,
        issued_at=issued_at,
        predecessor_sha256=None,
    )
    return record, proof


def recover_control_lease(
    current: ControlLeaseRecord,
    reservation: AttemptReservation,
    *,
    epoch: int,
    token: bytes,
    owner: str,
    issued_at: str,
) -> tuple[ControlLeaseRecord, ControlLeaseProof]:
    """只为同一活动 attempt 签发严格更高 epoch 的接管租约。"""
    if type(current) is not ControlLeaseRecord:
        raise FencingContractError("只接受 ControlLeaseRecord")
    if type(reservation) is not AttemptReservation:
        raise FencingContractError("只接受 AttemptReservation")
    if current.status is not ControlLeaseStatus.ACTIVE:
        raise FencingContractError("只能接管活动 control lease")
    if reservation.attempt_id != current.attempt_id:
        raise FencingContractError("recovery 只能接管同一活动 attempt")
    if not hmac.compare_digest(
        current.reservation_sha256,
        attempt_reservation_sha256(reservation),
    ):
        raise FencingContractError("recovery reservation 摘要不一致")
    if type(epoch) is not int or epoch <= current.epoch:
        raise FencingContractError("recovery epoch 必须严格提升")
    try:
        require_strictly_later(
            issued_at,
            current.issued_at,
            field="issued_at",
            boundary_field="当前 control lease issued_at",
        )
    except RuntimeContractSupportError as exc:
        raise FencingContractError(str(exc)) from None
    record, proof = issue_control_lease(
        reservation,
        epoch=epoch,
        token=token,
        owner=owner,
        issued_at=issued_at,
    )
    if hmac.compare_digest(record.token_sha256, current.token_sha256):
        raise FencingContractError("recovery 必须轮换 control capability")
    return replace(
        record,
        predecessor_sha256=control_lease_record_sha256(current),
    ), proof


def control_lease_record_sha256(value: ControlLeaseRecord) -> str:
    """计算单条持久 control lease 记录的完整规范摘要。"""
    if type(value) is not ControlLeaseRecord:
        raise FencingContractError("只接受 ControlLeaseRecord")
    return canonical_sha256(value)


def verify_control_lease(
    record: ControlLeaseRecord,
    proof: ControlLeaseProof,
) -> ControlLeaseProof:
    """以常量时间摘要比较验证活动 control capability。"""
    if type(record) is not ControlLeaseRecord:
        raise FencingContractError("只接受 ControlLeaseRecord")
    if type(proof) is not ControlLeaseProof:
        raise FencingContractError("只接受 ControlLeaseProof")
    if record.status is not ControlLeaseStatus.ACTIVE:
        raise FencingContractError("control lease 不是活动状态")
    if proof.attempt_id != record.attempt_id:
        raise FencingContractError("ControlLeaseProof attempt 身份不一致")
    if proof.epoch != record.epoch:
        raise FencingContractError("ControlLeaseProof epoch 不一致")
    if not hmac.compare_digest(record.token_sha256, proof.token_sha256):
        raise FencingContractError("ControlLeaseProof token 不一致")
    return proof


def issue_serving_fence(
    attempt: DeploymentAttempt,
    *,
    fence_id: str,
    epoch: int,
    token: bytes,
    issued_at: str,
) -> tuple[ServingFenceRecord, ServingFenceProof]:
    """为已冻结 attempt 的目标 generation 创建一次稳定 serving 围栏。"""
    if type(attempt) is not DeploymentAttempt:
        raise FencingContractError("只接受 DeploymentAttempt")
    proof = ServingFenceProof(fence_id=fence_id, epoch=epoch, token=token)
    record = ServingFenceRecord(
        schema_version=1,
        fence_id=fence_id,
        generation_id=attempt.target_generation_id,
        accepted_attempt_id=attempt.attempt_id,
        epoch=epoch,
        token_sha256=proof.token_sha256,
        issued_at=issued_at,
    )
    return record, proof


def verify_serving_fence(
    record: ServingFenceRecord,
    proof: ServingFenceProof,
) -> ServingFenceProof:
    """以常量时间摘要比较验证 serving capability。"""
    if type(record) is not ServingFenceRecord:
        raise FencingContractError("只接受 ServingFenceRecord")
    if type(proof) is not ServingFenceProof:
        raise FencingContractError("只接受 ServingFenceProof")
    if proof.fence_id != record.fence_id:
        raise FencingContractError("ServingFenceProof fence 身份不一致")
    if proof.epoch != record.epoch:
        raise FencingContractError("ServingFenceProof epoch 不一致")
    if not hmac.compare_digest(record.token_sha256, proof.token_sha256):
        raise FencingContractError("ServingFenceProof token 不一致")
    return proof


def authorize_serving_writer(
    state: GenerationState,
    generation_id: str,
    record: ServingFenceRecord,
    proof: ServingFenceProof,
) -> ServingFenceProof:
    """仅在公开稳态允许当前 generation/current fence 的 writer。"""
    from codev_platform.runtime_generation_state import GenerationMode, GenerationState

    if type(state) is not GenerationState:
        raise FencingContractError("只接受 GenerationState")
    if state.mode is not GenerationMode.STEADY or state.maintenance_active:
        raise FencingContractError("serving writer 在维护或过渡状态无条件关闭")
    if type(generation_id) is not str or generation_id != state.serving_generation_id:
        raise FencingContractError("writer generation 不是当前 serving generation")
    if type(record) is not ServingFenceRecord:
        raise FencingContractError("只接受 ServingFenceRecord")
    state_matches = (
        record.generation_id == state.serving_generation_id
        and record.fence_id == state.serving_fence_id
        and record.epoch == state.serving_fence_epoch
    )
    digest_matches = hmac.compare_digest(
        record.token_sha256,
        state.serving_fence_token_sha256,
    )
    if not state_matches or not digest_matches:
        raise FencingContractError("writer fence 不是当前 serving fence")
    return verify_serving_fence(record, proof)


def encode_control_lease_record(value: ControlLeaseRecord) -> bytes:
    """只编码 control lease 公开记录。"""
    if type(value) is not ControlLeaseRecord:
        raise FencingContractError("只接受 ControlLeaseRecord")
    return canonical_json_bytes(value)


def decode_control_lease_record(payload: bytes) -> ControlLeaseRecord:
    """严格解码 control lease 公开记录。"""
    try:
        values = decode_exact_json_mapping(
            payload,
            required_fields=_CONTROL_FIELDS,
            optional_defaults={},
            max_bytes=_MAX_RECORD_BYTES,
        )
        status = values.get("status")
        if type(status) is not str:
            raise FencingContractError("status 类型无效")
        values["status"] = ControlLeaseStatus(status)
        return ControlLeaseRecord(**values)
    except (RuntimeContractSupportError, TypeError, ValueError):
        raise FencingContractError("ControlLeaseRecord 序列化载荷无效") from None


def encode_serving_fence_record(value: ServingFenceRecord) -> bytes:
    """只编码 serving fence 公开记录。"""
    if type(value) is not ServingFenceRecord:
        raise FencingContractError("只接受 ServingFenceRecord")
    return canonical_json_bytes(value)


def decode_serving_fence_record(payload: bytes) -> ServingFenceRecord:
    """严格解码 serving fence 公开记录。"""
    try:
        values = decode_exact_json_mapping(
            payload,
            required_fields=_SERVING_FIELDS,
            optional_defaults={},
            max_bytes=_MAX_RECORD_BYTES,
        )
        return ServingFenceRecord(**values)
    except (RuntimeContractSupportError, TypeError, ValueError):
        raise FencingContractError("ServingFenceRecord 序列化载荷无效") from None


def _require_control_record(value: ControlLeaseRecord) -> None:
    try:
        require_schema(value.schema_version, 1)
        require_attempt_id(value.attempt_id)
        require_sha256(value.reservation_sha256, field="reservation_sha256")
        require_sha256(value.token_sha256, field="token_sha256")
        require_utc_rfc3339_z(value.issued_at, field="issued_at")
    except RuntimeContractSupportError as exc:
        raise FencingContractError(str(exc)) from None
    _require_epoch(value.epoch)
    if type(value.owner) is not str or _OWNER.fullmatch(value.owner) is None:
        raise FencingContractError("owner 必须是受限非空标识")
    if type(value.status) is not ControlLeaseStatus:
        raise FencingContractError("status 必须是 ControlLeaseStatus")
    if value.predecessor_sha256 is not None:
        try:
            require_sha256(value.predecessor_sha256, field="predecessor_sha256")
        except RuntimeContractSupportError as exc:
            raise FencingContractError(str(exc)) from None
    terminal = (
        value.terminal_journal_sha256,
        value.terminal_evidence_sha256,
        value.retired_at,
        value.retired_from_sha256,
    )
    if value.status is ControlLeaseStatus.ACTIVE:
        if any(item is not None for item in terminal):
            raise FencingContractError("活动 control lease 不得含终态审计")
        return
    if any(item is None for item in terminal):
        raise FencingContractError("退役 control lease 必须含完整终态审计")
    try:
        require_sha256(
            value.terminal_journal_sha256,
            field="terminal_journal_sha256",
        )
        require_sha256(
            value.terminal_evidence_sha256,
            field="terminal_evidence_sha256",
        )
        require_utc_rfc3339_z(value.retired_at, field="retired_at")
        require_sha256(value.retired_from_sha256, field="retired_from_sha256")
        require_strictly_later(
            value.retired_at,
            value.issued_at,
            field="retired_at",
            boundary_field="issued_at",
        )
    except RuntimeContractSupportError as exc:
        raise FencingContractError(str(exc)) from None
    active = replace(
        value,
        status=ControlLeaseStatus.ACTIVE,
        terminal_journal_sha256=None,
        terminal_evidence_sha256=None,
        retired_at=None,
        retired_from_sha256=None,
    )
    if not hmac.compare_digest(
        value.retired_from_sha256,
        control_lease_record_sha256(active),
    ):
        raise FencingContractError("retired_from_sha256 未绑定对应活动 control lease")


def _require_control_proof(value: ControlLeaseProof) -> None:
    try:
        require_attempt_id(value.attempt_id)
    except RuntimeContractSupportError as exc:
        raise FencingContractError(str(exc)) from None
    _require_epoch(value.epoch)
    _require_token(value.token)


def _require_serving_record(value: ServingFenceRecord) -> None:
    _require_fence_id(value.fence_id)
    try:
        require_schema(value.schema_version, 1)
        require_sha256(value.generation_id, field="generation_id")
        require_attempt_id(value.accepted_attempt_id)
        require_sha256(value.token_sha256, field="token_sha256")
        require_utc_rfc3339_z(value.issued_at, field="issued_at")
    except RuntimeContractSupportError as exc:
        raise FencingContractError(str(exc)) from None
    _require_epoch(value.epoch)


def _require_serving_proof(value: ServingFenceProof) -> None:
    _require_fence_id(value.fence_id)
    _require_epoch(value.epoch)
    _require_token(value.token)


def _require_fence_id(value: object) -> None:
    if type(value) is not str or _FENCE_ID.fullmatch(value) is None:
        raise FencingContractError("fence_id 必须是受限非空标识")


def _require_epoch(value: object) -> None:
    if type(value) is not int or not 1 <= value <= _MAX_EPOCH:
        raise FencingContractError("epoch 必须在 1..2^63-1")


def _require_token(value: object) -> None:
    if type(value) is not bytes or len(value) != _TOKEN_BYTES or not any(value):
        raise FencingContractError("token 必须是非零 32 字节 capability")


def _token_sha256(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


__all__ = [
    "ControlLeaseProof",
    "ControlLeaseRecord",
    "ControlLeaseStatus",
    "FencingContractError",
    "ServingFenceProof",
    "ServingFenceRecord",
    "authorize_serving_writer",
    "control_lease_record_sha256",
    "decode_control_lease_record",
    "decode_serving_fence_record",
    "encode_control_lease_record",
    "encode_serving_fence_record",
    "issue_control_lease",
    "issue_serving_fence",
    "recover_control_lease",
    "verify_control_lease",
    "verify_serving_fence",
]
