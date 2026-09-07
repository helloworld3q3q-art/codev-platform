"""reindex supervisor 的跨平台 PID 出生身份判定叶子。"""
from __future__ import annotations

import os
import sys
from collections.abc import Callable, Mapping
from enum import Enum
from typing import Any


class LockProcessState(Enum):
    """仅供破坏性 stale 回收使用的严格进程身份结论。"""

    ACTIVE = "active"
    STALE = "stale"
    UNKNOWN = "unknown"


def process_birth_identity(
    pid: int,
    *,
    platform_name: str = sys.platform,
) -> str | None:
    """返回 PID 不可复用的出生身份；无法证明时明确返回 None。"""
    if type(pid) is not int or pid <= 0:
        return None
    if platform_name == "win32":
        return _windows_birth_identity(pid)
    if platform_name.startswith("linux"):
        return _linux_birth_identity(pid)
    return None


def verification_fields(
    pid: int,
    *,
    birth_identity: Callable[[int], str | None],
) -> dict[str, object]:
    """把一次可证明的出生身份规范化为不泄露命令行的状态字段。"""
    identity = birth_identity(pid)
    return {
        "process_birth_identity": identity,
        "process_verified": identity is not None,
    }


def stored_pid(data: Mapping[str, Any]) -> int:
    """从外部状态读取正整数 PID，其他值都按不可信处理。"""
    pid = data.get("pid")
    return pid if type(pid) is int and pid > 0 else 0


def identity_matches(
    data: Mapping[str, Any],
    *,
    is_running: Callable[[int], bool],
    birth_identity: Callable[[int], str | None],
    pid: int | None = None,
) -> bool:
    """同时匹配记录 PID、内核活跃状态和出生身份，任一缺失即失败关闭。"""
    expected = data.get("process_birth_identity")
    actual_pid = stored_pid(data) if pid is None else pid
    if (
        type(expected) is not str
        or not expected
        or stored_pid(data) != actual_pid
        or not is_running(actual_pid)
    ):
        return False
    actual = birth_identity(actual_pid)
    return actual is not None and actual == expected


def lock_process_is_running(
    lock: Mapping[str, Any],
    *,
    schema_version: int,
    is_running: Callable[[int], bool],
    birth_identity: Callable[[int], str | None],
) -> bool:
    """只有当前 lock schema 和完整进程身份均匹配时才视为被占用。"""
    return (
        lock.get("lock_schema_version") == schema_version
        and identity_matches(
            lock,
            is_running=is_running,
            birth_identity=birth_identity,
        )
    )


def probe_lock_process_state(
    lock: Mapping[str, Any],
    *,
    schema_version: int,
    platform_name: str = sys.platform,
) -> LockProcessState:
    """严格区分活跃、可回收和无法证明，供删除运行锁前使用。"""
    if "lock_schema_version" not in lock:
        return LockProcessState.STALE
    if lock.get("lock_schema_version") != schema_version:
        return LockProcessState.UNKNOWN
    expected = lock.get("process_birth_identity")
    if (
        type(expected) is not str
        or not expected
        or type(lock.get("owner_token")) is not str
        or not lock.get("owner_token")
        or stored_pid(lock) <= 0
    ):
        return LockProcessState.UNKNOWN
    pid = stored_pid(lock)
    if platform_name.startswith("linux"):
        return _probe_linux_lock_process(pid, expected)
    if platform_name == "win32":
        return _probe_windows_lock_process(pid, expected)
    return LockProcessState.UNKNOWN


def _linux_birth_identity(pid: int) -> str | None:
    try:
        from codev_platform.reindex.posix_process_identity import (
            LinuxProcessTable,
            linux_birth_marker,
        )

        table = LinuxProcessTable()
        info = table.read(pid)
        if info is None or not info.live:
            return None
        return linux_birth_marker(table.boot_id, info.start_ticks)
    except (OSError, RuntimeError, ValueError):
        return None


def _probe_linux_lock_process(pid: int, expected: str) -> LockProcessState:
    try:
        from codev_platform.reindex.posix_process_identity import (
            LinuxProcessTable,
            linux_birth_marker,
        )

        table = LinuxProcessTable()
        info = table.read(pid)
        if info is None or not info.live:
            return LockProcessState.STALE
        actual = linux_birth_marker(table.boot_id, info.start_ticks)
    except (OSError, RuntimeError, ValueError):
        return LockProcessState.UNKNOWN
    return (
        LockProcessState.ACTIVE
        if actual == expected
        else LockProcessState.STALE
    )


def _windows_birth_identity(pid: int) -> str | None:
    try:
        from codev_platform.reindex.windows_job_native import CtypesWindowsJobNative

        native = CtypesWindowsJobNative()
        handle = native.open_process(pid)
        if handle is None:
            return None
        try:
            if native.poll_process(handle) is not None:
                return None
            return native.process_birth_marker(handle)
        finally:
            native.close_handle(handle)
    except Exception:  # noqa: BLE001 - 原生身份无法证明时必须失败关闭
        return None


def _probe_windows_lock_process(pid: int, expected: str) -> LockProcessState:
    try:
        from codev_platform.reindex.windows_job_native import CtypesWindowsJobNative

        native = CtypesWindowsJobNative()
        handle = native.open_process(pid)
        if handle is None:
            return LockProcessState.STALE
        try:
            if native.poll_process(handle) is not None:
                return LockProcessState.STALE
            actual = native.process_birth_marker(handle)
        finally:
            native.close_handle(handle)
    except Exception:  # noqa: BLE001 - 原生身份不可证明时不能删除运行锁
        return LockProcessState.UNKNOWN
    return (
        LockProcessState.ACTIVE
        if actual == expected
        else LockProcessState.STALE
    )


def is_pid_running(pid: int, *, platform_name: str = sys.platform) -> bool:
    """状态展示使用的宽松 liveness 判断；破坏性回收必须走严格三态探针。"""
    if type(pid) is not int or pid <= 0:
        return False
    if platform_name == "win32":
        return _is_pid_running_windows(pid)
    try:
        os.kill(pid, 0)
        return True
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    except OSError:
        return False


def _is_pid_running_windows(pid: int) -> bool:
    try:
        import ctypes
        from ctypes import wintypes

        kernel32 = ctypes.windll.kernel32
        handle = kernel32.OpenProcess(0x1000, False, pid)
        if not handle:
            return False
        code = wintypes.DWORD()
        ok = kernel32.GetExitCodeProcess(handle, ctypes.byref(code))
        kernel32.CloseHandle(handle)
        return bool(ok) and code.value == 259
    except Exception:
        return False


__all__ = [
    "LockProcessState",
    "identity_matches",
    "is_pid_running",
    "lock_process_is_running",
    "probe_lock_process_state",
    "process_birth_identity",
    "stored_pid",
    "verification_fields",
]
