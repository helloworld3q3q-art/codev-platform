"""维护 marker 状态机与仅待命恢复许可。"""

from __future__ import annotations

import os
import re
import stat
import time
from collections.abc import Iterator
from contextlib import contextmanager
from math import ceil, isfinite
from pathlib import Path
from uuid import uuid4

from .maintenance_gate_record import (
    MaintenanceGateRecord,
    MaintenanceGateRecordError,
    decode_maintenance_gate_record,
    restore_armed_record,
    restore_claimed_record,
)
from .file_durability import read_regular_file_bounded


def read_maintenance_gate_record(path: Path | None = None) -> MaintenanceGateRecord:
    """读取 marker 的受控状态；缺失、替换或未知内容均拒绝解释。"""
    gate = _gate_module()
    marker = gate._marker_path(path)
    try:
        before = marker.lstat()
        if not stat.S_ISREG(before.st_mode):
            raise gate.MaintenanceGateError("维护 marker 必须是普通文件")
        if gate._is_linux() and gate._is_default_marker(marker):
            if (
                before.st_uid != gate._global_owner_uid()
                or stat.S_IMODE(before.st_mode) != gate._MARKER_MODE
            ):
                raise gate.MaintenanceGateError("维护 marker 所有者或权限不安全")
        payload = read_regular_file_bounded(marker, max_bytes=gate._MAX_MARKER_BYTES)
        after = marker.lstat()
        if not gate._same_file(before, after):
            raise gate.MaintenanceGateError("维护 marker 在读取期间变化")
        return decode_maintenance_gate_record(payload)
    except gate.MaintenanceGateError:
        raise
    except (MaintenanceGateRecordError, OSError, TypeError, ValueError):
        raise gate.MaintenanceGateError("维护 marker 状态无法证明") from None


def arm_restore_standby(
    *,
    ttl_sec: float,
    path: Path | None = None,
) -> MaintenanceGateRecord:
    """将维护稳态切换为短时恢复待命；该状态永远不授予写权限。"""
    gate = _gate_module()
    duration = _restore_standby_duration(ttl_sec, gate)
    marker = gate._marker_path(path)
    try:
        with gate._exclusive_gate_lock(marker):
            current = read_maintenance_gate_record(path=marker)
            if current.phase != "maintenance":
                raise gate.MaintenanceGateError("reindex 维护窗口不处于可恢复稳态")
            record = restore_armed_record(
                generation=uuid4().hex,
                expires_at=int(ceil(time.time() + duration)),
            )
            gate._write_maintenance_gate_record(marker, record)
            return record
    except gate.MaintenanceGateError:
        raise
    except (MaintenanceGateRecordError, OSError, TypeError, ValueError):
        raise gate.MaintenanceGateError("无法预备 reindex 恢复待命状态") from None


def claim_restore_standby(
    *,
    generation: str,
    invocation_id: str,
    path: Path | None = None,
) -> MaintenanceGateRecord:
    """把已预备待命状态绑定到唯一的 systemd invocation。"""
    gate = _gate_module()
    marker = gate._marker_path(path)
    try:
        with gate._exclusive_gate_lock(marker):
            current = read_maintenance_gate_record(path=marker)
            if (
                current.phase != "restore_armed"
                or current.generation != generation
                or current.expired(now=time.time())
            ):
                raise gate.MaintenanceGateError("reindex 恢复待命状态已失效")
            record = restore_claimed_record(
                generation=generation,
                expires_at=current.expires_at,
                invocation_id=invocation_id,
            )
            gate._write_maintenance_gate_record(marker, record)
            return record
    except gate.MaintenanceGateError:
        raise
    except (MaintenanceGateRecordError, OSError, TypeError, ValueError):
        raise gate.MaintenanceGateError("无法绑定 reindex 恢复待命身份") from None


def renew_claimed_restore_standby(
    *,
    generation: str,
    invocation_id: str,
    ttl_sec: float,
    path: Path | None = None,
) -> MaintenanceGateRecord:
    """仅为已绑定的同一待命实例续租，慢恢复期间始终不授予写权限。"""
    gate = _gate_module()
    duration = _restore_standby_duration(ttl_sec, gate)
    marker = gate._marker_path(path)
    try:
        with gate._exclusive_gate_lock(marker):
            return _renew_claimed_restore_standby_record(
                gate,
                marker,
                generation=generation,
                invocation_id=invocation_id,
                ttl_sec=duration,
            )
    except gate.MaintenanceGateError:
        raise
    except (MaintenanceGateRecordError, OSError, TypeError, ValueError):
        raise gate.MaintenanceGateError("无法续租 reindex 恢复待命状态") from None


def renew_claimed_restore_standby_while_systemd_transition_locked(
    *,
    generation: str,
    invocation_id: str,
    ttl_sec: float,
) -> MaintenanceGateRecord:
    """仅供已持全局转换 EX 的组合状态机续租，不再次取得 flock。"""
    gate = _gate_module()
    duration = _restore_standby_duration(ttl_sec, gate)
    marker = gate._marker_path(None)
    try:
        gate.require_systemd_transition_guard()
        return _renew_claimed_restore_standby_record(
            gate,
            marker,
            generation=generation,
            invocation_id=invocation_id,
            ttl_sec=duration,
        )
    except gate.MaintenanceGateError:
        raise
    except (MaintenanceGateRecordError, OSError, TypeError, ValueError):
        raise gate.MaintenanceGateError("无法续租 reindex 恢复待命状态") from None


def complete_restore_standby(
    *,
    generation: str,
    invocation_id: str,
    path: Path | None = None,
) -> None:
    """仅在已验证的待命实例完成基线恢复后，最终移除 marker。"""
    gate = _gate_module()
    marker = gate._marker_path(path)
    try:
        with gate._exclusive_gate_lock(marker):
            _complete_restore_standby_record(
                gate,
                marker,
                generation=generation,
                invocation_id=invocation_id,
            )
    except gate.MaintenanceGateError:
        raise
    except (MaintenanceGateRecordError, OSError, TypeError, ValueError):
        raise gate.MaintenanceGateError("无法完成 reindex 恢复交接") from None


def complete_restore_standby_while_systemd_transition_locked(
    *,
    generation: str,
    invocation_id: str,
) -> None:
    """仅供已持全局转换 EX 的组合状态机最终删除 marker。"""
    gate = _gate_module()
    marker = gate._marker_path(None)
    try:
        gate.require_systemd_transition_guard()
        _complete_restore_standby_record(
            gate,
            marker,
            generation=generation,
            invocation_id=invocation_id,
        )
    except gate.MaintenanceGateError:
        raise
    except (MaintenanceGateRecordError, OSError, TypeError, ValueError):
        raise gate.MaintenanceGateError("无法完成 reindex 恢复交接") from None


def _renew_claimed_restore_standby_record(
    gate: object,
    marker: Path,
    *,
    generation: str,
    invocation_id: str,
    ttl_sec: float,
) -> MaintenanceGateRecord:
    _require_claimed_restore_record(
        marker,
        generation=generation,
        invocation_id=invocation_id,
        error_message="reindex 恢复待命身份无法续租",
    )
    record = restore_claimed_record(
        generation=generation,
        expires_at=int(ceil(time.time() + ttl_sec)),
        invocation_id=invocation_id,
    )
    gate._write_maintenance_gate_record(marker, record)
    return record


def _complete_restore_standby_record(
    gate: object,
    marker: Path,
    *,
    generation: str,
    invocation_id: str,
) -> None:
    _require_claimed_restore_record(
        marker,
        generation=generation,
        invocation_id=invocation_id,
        error_message="reindex 恢复待命身份无法证明",
    )
    gate.durable_unlink(marker)


def _require_claimed_restore_record(
    marker: Path,
    *,
    generation: str,
    invocation_id: str,
    error_message: str,
) -> MaintenanceGateRecord:
    current = read_maintenance_gate_record(path=marker)
    if (
        current.phase != "restore_claimed"
        or current.generation != generation
        or current.invocation_id != invocation_id
        or current.expired(now=time.time())
    ):
        raise _gate_module().MaintenanceGateError(error_message)
    return current


def maintenance_restore_standby_permit() -> bool:
    """仅允许 marker 内已受控的 service 等待恢复交接，绝不代表可写。"""
    gate = _gate_module()
    if not gate._is_linux():
        return False
    try:
        descriptor = gate._open_global_reader_gate_lock()
    except (FileNotFoundError, gate._MaintenanceLockUnavailable):
        return False
    try:
        try:
            if (
                not gate.maintenance_gate_active()
                or gate._current_process_is_reindex_unit() is not True
            ):
                return False
            record = read_maintenance_gate_record()
            if record.phase == "restore_armed":
                return not record.expired(now=time.time())
            if record.phase != "restore_claimed" or record.expired(now=time.time()):
                return False
            return record.invocation_id == _current_invocation_id(gate)
        except Exception:
            return False
    finally:
        gate._release_linux_lock(descriptor)


def wait_for_restore_standby_release(
    *,
    poll_sec: float = 0.2,
    sleeper=time.sleep,
) -> bool:
    """仅在 marker 删除后交接写阶段；待命期间不加载运行时也不触碰队列。"""
    if type(poll_sec) not in (int, float) or not isfinite(float(poll_sec)) or poll_sec <= 0:
        raise ValueError("恢复待命轮询间隔无效")
    if not callable(sleeper):
        raise ValueError("恢复待命休眠器不可用")
    gate = _gate_module()
    while gate.maintenance_gate_active():
        if gate.maintenance_restore_standby_permit() is not True:
            if not gate.maintenance_gate_active():
                return True
            return False
        sleeper(float(poll_sec))
    return True


@contextmanager
def maintenance_admin_window_permit() -> Iterator[bool]:
    """在 intent EX 与 gate EX 内串行 owner、迁移及维护状态写入。"""
    gate = _gate_module()
    if not gate._is_linux():
        yield False
        return
    entered = False
    try:
        with gate.maintenance_systemd_transition_lock():
            entered = True
            try:
                permitted = read_maintenance_gate_record().phase == "maintenance"
            except Exception:
                permitted = False
            yield permitted
    except (FileNotFoundError, gate.MaintenanceGateError):
        if entered:
            raise
        yield False


def _current_invocation_id(gate: object) -> str:
    """仅接受 systemd 继承的严格 InvocationID，缺失时 fail-closed。"""
    value = os.environ.get("INVOCATION_ID")
    if type(value) is not str or re.fullmatch(r"[0-9a-f]{32}", value) is None:
        raise gate.MaintenanceGateError("当前进程缺少有效 systemd InvocationID")
    return value


def _restore_standby_duration(value: object, gate: object) -> float:
    if type(value) not in (int, float):
        raise gate.MaintenanceGateError("恢复待命时长无效")
    duration = float(value)
    if not isfinite(duration) or duration <= 0 or duration > gate._MAX_RESTORE_STANDBY_SEC:
        raise gate.MaintenanceGateError("恢复待命时长无效")
    return duration


def _gate_module():
    """延迟取得底层门禁存储，避免状态机与锁实现形成导入环。"""
    from . import maintenance_gate

    return maintenance_gate


__all__ = [
    "arm_restore_standby",
    "claim_restore_standby",
    "complete_restore_standby",
    "complete_restore_standby_while_systemd_transition_locked",
    "maintenance_admin_window_permit",
    "maintenance_restore_standby_permit",
    "read_maintenance_gate_record",
    "renew_claimed_restore_standby",
    "renew_claimed_restore_standby_while_systemd_transition_locked",
    "wait_for_restore_standby_release",
]
