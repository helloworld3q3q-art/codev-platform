"""固定 launcher 可消费的稳定恢复 envelope 纯领域契约。"""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import PurePosixPath, PureWindowsPath

from codev_platform._runtime_contract_support import (
    RuntimeContractSupportError,
    decode_exact_json_mapping,
    require_attempt_id,
    require_schema,
    require_sha256,
    require_utc_rfc3339_z,
)
from codev_platform.core.runtime_models import canonical_json_bytes, canonical_sha256
from codev_platform.runtime_attempt_contract import (
    AttemptReservation,
    attempt_reservation_sha256,
)
from codev_platform.runtime_transaction_contract import TransactionJournal


_MAX_ENVELOPE_BYTES = 32_768
_MAX_RELATIVE_PATH_BYTES = 4_095
_MAX_SEGMENT_BYTES = 255
_PATH_SEGMENT = re.compile(r"[A-Za-z0-9][A-Za-z0-9._+-]{0,254}\Z")
_STORE_ID = re.compile(r"[A-Za-z0-9][A-Za-z0-9._:-]{0,127}\Z")
_ENVELOPE_FIELDS = frozenset(
    {
        "schema_version",
        "attempt_id",
        "reservation_sha256",
        "controller_root_relative",
        "controller_tree_sha256",
        "interpreter_relative",
        "interpreter_sha256",
        "transaction_store_id",
        "journal_genesis_sha256",
        "created_at",
    }
)


class RecoveryContractError(ValueError):
    """恢复 envelope 字段、路径或序列化载荷不符合契约。"""


@dataclass(frozen=True, slots=True)
class RecoveryEnvelope:
    """只绑定 immutable controller 与 journal/store genesis 的恢复身份。"""

    schema_version: int
    attempt_id: str
    reservation_sha256: str
    controller_root_relative: str
    controller_tree_sha256: str
    interpreter_relative: str
    interpreter_sha256: str
    transaction_store_id: str
    journal_genesis_sha256: str
    created_at: str

    def __post_init__(self) -> None:
        _require_envelope_fields(self)
        _require_controller_layout(self)


def create_recovery_envelope(
    reservation: AttemptReservation,
    journal: TransactionJournal,
    *,
    controller_root_relative: str,
    interpreter_relative: str,
    interpreter_sha256: str,
    transaction_store_id: str,
    created_at: str,
) -> RecoveryEnvelope:
    """只从早期 AttemptReservation 派生稳定外层恢复身份。"""
    if type(reservation) is not AttemptReservation:
        raise RecoveryContractError("只接受 AttemptReservation")
    if type(journal) is not TransactionJournal:
        raise RecoveryContractError("只接受 TransactionJournal")
    envelope = RecoveryEnvelope(
        schema_version=1,
        attempt_id=reservation.attempt_id,
        reservation_sha256=attempt_reservation_sha256(reservation),
        controller_root_relative=controller_root_relative,
        controller_tree_sha256=reservation.controller_sha256,
        interpreter_relative=interpreter_relative,
        interpreter_sha256=interpreter_sha256,
        transaction_store_id=transaction_store_id,
        journal_genesis_sha256=journal.journal_genesis_sha256,
        created_at=created_at,
    )
    return verify_recovery_envelope(envelope, reservation, journal)


def verify_recovery_envelope(
    envelope: RecoveryEnvelope,
    reservation: AttemptReservation,
    journal: TransactionJournal,
) -> RecoveryEnvelope:
    """复验 envelope 与 reservation、journal genesis 的强绑定。"""
    if type(envelope) is not RecoveryEnvelope:
        raise RecoveryContractError("只接受 RecoveryEnvelope")
    if type(reservation) is not AttemptReservation:
        raise RecoveryContractError("只接受 AttemptReservation")
    if type(journal) is not TransactionJournal:
        raise RecoveryContractError("只接受 TransactionJournal")
    if envelope.attempt_id != reservation.attempt_id:
        raise RecoveryContractError("RecoveryEnvelope 与 reservation 身份不一致")
    if envelope.reservation_sha256 != attempt_reservation_sha256(reservation):
        raise RecoveryContractError("RecoveryEnvelope 与 reservation 摘要不一致")
    if envelope.controller_tree_sha256 != reservation.controller_sha256:
        raise RecoveryContractError("RecoveryEnvelope controller 身份不一致")
    if journal.attempt_id != reservation.attempt_id:
        raise RecoveryContractError("TransactionJournal 与 reservation 身份不一致")
    if journal.reservation_sha256 != attempt_reservation_sha256(reservation):
        raise RecoveryContractError("TransactionJournal 与 reservation 摘要不一致")
    if envelope.journal_genesis_sha256 != journal.journal_genesis_sha256:
        raise RecoveryContractError("RecoveryEnvelope journal genesis 不一致")
    return envelope


def recovery_envelope_sha256(value: RecoveryEnvelope) -> str:
    """计算覆盖 envelope 全部稳定持久字段的规范摘要。"""
    if type(value) is not RecoveryEnvelope:
        raise RecoveryContractError("只接受 RecoveryEnvelope")
    return canonical_sha256(value)


def encode_recovery_envelope(value: RecoveryEnvelope) -> bytes:
    """编码不含模块、argv、环境覆盖或变化 journal head 的 envelope。"""
    if type(value) is not RecoveryEnvelope:
        raise RecoveryContractError("只接受 RecoveryEnvelope")
    return canonical_json_bytes(value)


def decode_recovery_envelope(payload: bytes) -> RecoveryEnvelope:
    """严格解码字段集合精确的稳定恢复 envelope。"""
    try:
        values = decode_exact_json_mapping(
            payload,
            required_fields=_ENVELOPE_FIELDS,
            optional_defaults={},
            max_bytes=_MAX_ENVELOPE_BYTES,
        )
        return RecoveryEnvelope(**values)
    except (RuntimeContractSupportError, TypeError, ValueError):
        raise RecoveryContractError("RecoveryEnvelope 序列化载荷无效") from None


def decode_recovery_envelope_for_context(
    reservation: AttemptReservation,
    journal: TransactionJournal,
    payload: bytes,
) -> RecoveryEnvelope:
    """严格解码并绑定复验指定 attempt 与 journal genesis。"""
    return verify_recovery_envelope(
        decode_recovery_envelope(payload),
        reservation,
        journal,
    )


def _require_envelope_fields(value: RecoveryEnvelope) -> None:
    try:
        require_schema(value.schema_version, 1)
        require_attempt_id(value.attempt_id)
        require_sha256(value.reservation_sha256, field="reservation_sha256")
        require_sha256(
            value.controller_tree_sha256,
            field="controller_tree_sha256",
        )
        require_sha256(value.interpreter_sha256, field="interpreter_sha256")
        require_sha256(
            value.journal_genesis_sha256,
            field="journal_genesis_sha256",
        )
        require_utc_rfc3339_z(value.created_at, field="created_at")
    except RuntimeContractSupportError as exc:
        raise RecoveryContractError(str(exc)) from None
    if (
        type(value.transaction_store_id) is not str
        or _STORE_ID.fullmatch(value.transaction_store_id) is None
    ):
        raise RecoveryContractError("transaction_store_id 必须是受限非空标识")


def _require_controller_layout(value: RecoveryEnvelope) -> None:
    root = _require_runtime_relative(
        value.controller_root_relative,
        "controller_root_relative",
    )
    interpreter = _require_runtime_relative(
        value.interpreter_relative,
        "interpreter_relative",
    )
    try:
        nested = interpreter.relative_to(root)
    except ValueError:
        raise RecoveryContractError("interpreter 必须位于 immutable controller tree 内") from None
    if not nested.parts:
        raise RecoveryContractError("interpreter 必须是 controller tree 内的文件路径")


def _require_runtime_relative(value: object, field: str) -> PurePosixPath:
    if type(value) is not str or not value:
        raise RecoveryContractError(f"{field} 必须是非空 runtime root 相对路径")
    if len(value.encode("utf-8")) > _MAX_RELATIVE_PATH_BYTES:
        raise RecoveryContractError(f"{field} 超出固定字节上限")
    posix = PurePosixPath(value)
    windows = PureWindowsPath(value)
    if (
        "\\" in value
        or posix.is_absolute()
        or windows.is_absolute()
        or bool(windows.drive)
        or value != posix.as_posix()
        or not posix.parts
    ):
        raise RecoveryContractError(f"{field} 必须是规范 POSIX 相对路径")
    for segment in posix.parts:
        if (
            segment in {"", ".", ".."}
            or segment.casefold() == "current"
            or len(segment.encode("utf-8")) > _MAX_SEGMENT_BYTES
            or _PATH_SEGMENT.fullmatch(segment) is None
        ):
            raise RecoveryContractError(f"{field} 含不安全路径段或 current 链接")
    return posix


__all__ = [
    "RecoveryContractError",
    "RecoveryEnvelope",
    "create_recovery_envelope",
    "decode_recovery_envelope",
    "decode_recovery_envelope_for_context",
    "encode_recovery_envelope",
    "recovery_envelope_sha256",
    "verify_recovery_envelope",
]
