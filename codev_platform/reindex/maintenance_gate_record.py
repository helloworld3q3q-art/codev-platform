"""维护门禁 marker 的严格状态记录编解码。"""

from __future__ import annotations

import re
from dataclasses import dataclass

_LEGACY_MAINTENANCE_PAYLOAD = b"codev-platform-reindex-maintenance-gate-v1\n"
_MAINTENANCE_PAYLOAD = b"codev-platform-reindex-maintenance-gate-v2\n"
_ARMED_PATTERN = re.compile(
    rb"codev-platform-reindex-restore-armed-v2 ([0-9a-f]{32}) ([0-9]{1,19})\n\Z"
)
_CLAIMED_PATTERN = re.compile(
    rb"codev-platform-reindex-restore-claimed-v2 ([0-9a-f]{32}) ([0-9]{1,19}) ([0-9a-f]{32})\n\Z"
)
_MAX_TIMESTAMP = (1 << 63) - 1


class MaintenanceGateRecordError(ValueError):
    """维护 marker 的内容不符合受控状态机协议。"""


@dataclass(frozen=True, slots=True)
class MaintenanceGateRecord:
    """marker 的单一持久化状态；恢复态绝不代表可写。"""

    phase: str
    generation: str | None = None
    expires_at: int | None = None
    invocation_id: str | None = None

    def expired(self, *, now: float) -> bool:
        """只有恢复待命状态有截止时间；维护稳态永不按时间放宽。"""
        if self.phase == "maintenance":
            return False
        if type(now) not in (int, float) or self.expires_at is None:
            return True
        return float(now) >= float(self.expires_at)


def maintenance_record() -> MaintenanceGateRecord:
    """构造阻断所有写入的维护稳态记录。"""
    return MaintenanceGateRecord("maintenance")


def restore_armed_record(*, generation: str, expires_at: int) -> MaintenanceGateRecord:
    """构造只允许受控 service 待命的恢复预备记录。"""
    return MaintenanceGateRecord(
        "restore_armed",
        generation=_validate_hex_identifier(generation, "恢复 generation"),
        expires_at=_validate_timestamp(expires_at),
    )


def restore_claimed_record(
    *,
    generation: str,
    expires_at: int,
    invocation_id: str,
) -> MaintenanceGateRecord:
    """构造已绑定本次 systemd invocation 的恢复待命记录。"""
    return MaintenanceGateRecord(
        "restore_claimed",
        generation=_validate_hex_identifier(generation, "恢复 generation"),
        expires_at=_validate_timestamp(expires_at),
        invocation_id=_validate_hex_identifier(invocation_id, "systemd InvocationID"),
    )


def encode_maintenance_gate_record(record: MaintenanceGateRecord) -> bytes:
    """将经过严格构造的记录编码为有限、确定性的 marker 内容。"""
    if type(record) is not MaintenanceGateRecord:
        raise MaintenanceGateRecordError("维护 marker 记录类型无效")
    if record.phase == "maintenance":
        if (
            record.generation is not None
            or record.expires_at is not None
            or record.invocation_id is not None
        ):
            raise MaintenanceGateRecordError("维护稳态不能携带恢复字段")
        return _MAINTENANCE_PAYLOAD
    if record.phase == "restore_armed":
        armed = restore_armed_record(
            generation=_require_value(record.generation, "恢复 generation"),
            expires_at=_require_value(record.expires_at, "恢复截止时间"),
        )
        return (
            b"codev-platform-reindex-restore-armed-v2 "
            + armed.generation.encode("ascii")
            + b" "
            + str(armed.expires_at).encode("ascii")
            + b"\n"
        )
    if record.phase == "restore_claimed":
        claimed = restore_claimed_record(
            generation=_require_value(record.generation, "恢复 generation"),
            expires_at=_require_value(record.expires_at, "恢复截止时间"),
            invocation_id=_require_value(record.invocation_id, "systemd InvocationID"),
        )
        return (
            b"codev-platform-reindex-restore-claimed-v2 "
            + claimed.generation.encode("ascii")
            + b" "
            + str(claimed.expires_at).encode("ascii")
            + b" "
            + claimed.invocation_id.encode("ascii")
            + b"\n"
        )
    raise MaintenanceGateRecordError("维护 marker 状态未知")


def decode_maintenance_gate_record(payload: bytes) -> MaintenanceGateRecord:
    """严格解析新旧兼容的 marker；未知内容一律拒绝解释。"""
    if type(payload) is not bytes:
        raise MaintenanceGateRecordError("维护 marker 内容类型无效")
    if payload in {_LEGACY_MAINTENANCE_PAYLOAD, _MAINTENANCE_PAYLOAD}:
        return maintenance_record()
    armed = _ARMED_PATTERN.fullmatch(payload)
    if armed is not None:
        return restore_armed_record(
            generation=armed.group(1).decode("ascii"),
            expires_at=_decode_timestamp(armed.group(2)),
        )
    claimed = _CLAIMED_PATTERN.fullmatch(payload)
    if claimed is not None:
        return restore_claimed_record(
            generation=claimed.group(1).decode("ascii"),
            expires_at=_decode_timestamp(claimed.group(2)),
            invocation_id=claimed.group(3).decode("ascii"),
        )
    raise MaintenanceGateRecordError("维护 marker 内容不受信任")


def _decode_timestamp(value: bytes) -> int:
    try:
        return _validate_timestamp(int(value.decode("ascii")))
    except (UnicodeDecodeError, ValueError):
        raise MaintenanceGateRecordError("恢复截止时间无效") from None


def _validate_timestamp(value: object) -> int:
    if type(value) is not int or value <= 0 or value > _MAX_TIMESTAMP:
        raise MaintenanceGateRecordError("恢复截止时间无效")
    return value


def _validate_hex_identifier(value: object, label: str) -> str:
    if type(value) is not str or re.fullmatch(r"[0-9a-f]{32}", value) is None:
        raise MaintenanceGateRecordError(f"{label}无效")
    return value


def _require_value(value: object, label: str):
    if value is None:
        raise MaintenanceGateRecordError(f"{label}缺失")
    return value


__all__ = [
    "MaintenanceGateRecord",
    "MaintenanceGateRecordError",
    "decode_maintenance_gate_record",
    "encode_maintenance_gate_record",
    "maintenance_record",
    "restore_armed_record",
    "restore_claimed_record",
]
