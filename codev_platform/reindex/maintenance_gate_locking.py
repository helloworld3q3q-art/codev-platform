"""reindex 维护门禁的 Linux 锁存储与可信文件打开。"""

from __future__ import annotations

import os
import stat
import time
from dataclasses import dataclass
from pathlib import Path

from .file_durability import durable_write_once, fsync_directory
from .maintenance_gate_contract import (
    MaintenanceGateLockBusyError,
    _MaintenanceLockUnavailable,
)

_GLOBAL_DIRECTORY_MODE = 0o755
_GLOBAL_LOCK_MODE = 0o644
_UNSAFE_WRITE_BITS = stat.S_IWGRP | stat.S_IWOTH
_LOCK_TIMEOUT_SEC = 5.0
_LOCK_RETRY_SEC = 0.02


@dataclass(frozen=True, slots=True)
class GlobalLockFile:
    """需要在可信全局目录中预置的固定锁文件。"""

    name: str
    payload: bytes
    label: str


def _required_linux_flag(name: str) -> int:
    value = getattr(os, name, None)
    if type(value) is not int or value == 0:
        raise _MaintenanceLockUnavailable(f"当前 Linux 缺少安全打开标志 {name}")
    return value


def _linux_directory_flags() -> int:
    return (
        os.O_RDONLY
        | _required_linux_flag("O_DIRECTORY")
        | _required_linux_flag("O_NOFOLLOW")
        | getattr(os, "O_CLOEXEC", 0)
    )


def _linux_regular_file_flags() -> int:
    return os.O_RDONLY | _required_linux_flag("O_NOFOLLOW") | getattr(os, "O_CLOEXEC", 0)


def _validate_global_directory(
    descriptor: int,
    *,
    owner_uid: int,
    required_mode: int | None,
    label: str,
) -> None:
    metadata = os.fstat(descriptor)
    if not stat.S_ISDIR(metadata.st_mode):
        raise _MaintenanceLockUnavailable(f"维护门禁{label}必须是目录")
    if metadata.st_uid != owner_uid:
        raise _MaintenanceLockUnavailable(f"维护门禁{label}必须由 root 所有")
    mode = stat.S_IMODE(metadata.st_mode)
    if required_mode is None:
        if mode & _UNSAFE_WRITE_BITS:
            raise _MaintenanceLockUnavailable(f"维护门禁{label}不能被非 root 写入")
        return
    if mode != required_mode:
        raise _MaintenanceLockUnavailable(f"维护门禁{label}权限必须为 {required_mode:04o}")


def _validate_global_lock(
    descriptor: int,
    *,
    owner_uid: int,
    label: str,
) -> None:
    metadata = os.fstat(descriptor)
    if not stat.S_ISREG(metadata.st_mode):
        raise _MaintenanceLockUnavailable(f"{label}必须是普通文件")
    if metadata.st_uid != owner_uid:
        raise _MaintenanceLockUnavailable(f"{label}必须由 root 所有")
    if stat.S_IMODE(metadata.st_mode) != _GLOBAL_LOCK_MODE:
        raise _MaintenanceLockUnavailable(f"{label}权限必须为 0644")


def _open_global_gate_directory(
    *,
    global_root: Path,
    directory_name: str,
    owner_uid: int,
    create: bool,
) -> int:
    """以无跟随目录句柄取得默认门禁目录，避免路径检查与使用脱节。"""
    try:
        root_descriptor = os.open(global_root, _linux_directory_flags())
    except OSError as error:
        raise _MaintenanceLockUnavailable("维护门禁根目录不可安全读取") from error
    try:
        _validate_global_directory(
            root_descriptor,
            owner_uid=owner_uid,
            required_mode=None,
            label="根目录",
        )
        try:
            descriptor = os.open(
                directory_name,
                _linux_directory_flags(),
                dir_fd=root_descriptor,
            )
        except FileNotFoundError:
            if not create:
                raise _MaintenanceLockUnavailable("维护门禁目录尚未预置") from None
            descriptor = _create_global_gate_directory(
                root_descriptor,
                directory_name,
                owner_uid=owner_uid,
            )
        try:
            _validate_global_directory(
                descriptor,
                owner_uid=owner_uid,
                required_mode=_GLOBAL_DIRECTORY_MODE,
                label="目录",
            )
            return descriptor
        except BaseException:
            os.close(descriptor)
            raise
    finally:
        os.close(root_descriptor)


def _create_global_gate_directory(
    root_descriptor: int,
    name: str,
    *,
    owner_uid: int,
) -> int:
    try:
        os.mkdir(name, _GLOBAL_DIRECTORY_MODE, dir_fd=root_descriptor)
    except FileExistsError:
        pass
    except OSError as error:
        raise _MaintenanceLockUnavailable("无法创建维护门禁目录") from error
    try:
        descriptor = os.open(name, _linux_directory_flags(), dir_fd=root_descriptor)
    except OSError as error:
        raise _MaintenanceLockUnavailable("维护门禁目录不可安全读取") from error
    try:
        metadata = os.fstat(descriptor)
        if not stat.S_ISDIR(metadata.st_mode) or metadata.st_uid != owner_uid:
            raise _MaintenanceLockUnavailable("维护门禁目录创建后状态不安全")
        os.fchmod(descriptor, _GLOBAL_DIRECTORY_MODE)
        os.fsync(descriptor)
        os.fsync(root_descriptor)
        return descriptor
    except BaseException:
        os.close(descriptor)
        raise


def _write_lock_payload(descriptor: int, payload: bytes) -> None:
    offset = 0
    while offset < len(payload):
        written = os.write(descriptor, payload[offset:])
        if written <= 0:
            raise OSError("维护门禁锁写入未取得进展")
        offset += written


def _ensure_global_lock(
    directory_descriptor: int,
    lock: GlobalLockFile,
    *,
    owner_uid: int,
) -> None:
    flags = (
        os.O_WRONLY
        | os.O_CREAT
        | os.O_EXCL
        | _required_linux_flag("O_NOFOLLOW")
        | getattr(os, "O_CLOEXEC", 0)
    )
    try:
        descriptor = os.open(
            lock.name,
            flags,
            _GLOBAL_LOCK_MODE,
            dir_fd=directory_descriptor,
        )
    except FileExistsError:
        return
    except OSError as error:
        raise _MaintenanceLockUnavailable(f"无法创建{lock.label}") from error
    try:
        _write_lock_payload(descriptor, lock.payload)
        os.fchmod(descriptor, _GLOBAL_LOCK_MODE)
        os.fsync(descriptor)
        _validate_global_lock(descriptor, owner_uid=owner_uid, label=lock.label)
        os.fsync(directory_descriptor)
    except BaseException:
        os.close(descriptor)
        raise
    os.close(descriptor)


def provision_global_locks(
    *,
    global_root: Path,
    directory_name: str,
    owner_uid: int,
    effective_uid: int,
    locks: tuple[GlobalLockFile, ...],
) -> None:
    """在可信 root 目录内幂等预置固定全局锁原像。"""
    if effective_uid != owner_uid:
        raise _MaintenanceLockUnavailable("默认维护门禁只能由 root 预置")
    directory_descriptor = _open_global_gate_directory(
        global_root=global_root,
        directory_name=directory_name,
        owner_uid=owner_uid,
        create=True,
    )
    try:
        for lock in locks:
            _ensure_global_lock(directory_descriptor, lock, owner_uid=owner_uid)
    finally:
        os.close(directory_descriptor)


def open_global_named_lock(
    *,
    global_root: Path,
    directory_name: str,
    lock_name: str,
    owner_uid: int,
    exclusive: bool,
    label: str,
) -> int:
    """从可信全局目录取得并锁定固定普通文件。"""
    directory_descriptor = _open_global_gate_directory(
        global_root=global_root,
        directory_name=directory_name,
        owner_uid=owner_uid,
        create=False,
    )
    try:
        try:
            descriptor = os.open(
                lock_name,
                _linux_regular_file_flags(),
                dir_fd=directory_descriptor,
            )
        except OSError as error:
            raise _MaintenanceLockUnavailable(f"{label}不可安全读取") from error
    finally:
        os.close(directory_descriptor)
    try:
        _validate_global_lock(descriptor, owner_uid=owner_uid, label=label)
        _flock_linux(descriptor, exclusive=exclusive)
        return descriptor
    except BaseException:
        os.close(descriptor)
        raise


def same_file(before: os.stat_result, after: os.stat_result) -> bool:
    """只接受同一个稳定普通文件原像。"""
    return (
        stat.S_ISREG(before.st_mode)
        and stat.S_ISREG(after.st_mode)
        and before.st_dev == after.st_dev
        and before.st_ino == after.st_ino
    )


def open_linux_lock(path: Path, *, exclusive: bool) -> int:
    """无跟随打开自定义锁文件并核对打开前后身份。"""
    try:
        before = path.lstat()
    except FileNotFoundError:
        raise
    except OSError as error:
        raise _MaintenanceLockUnavailable("维护门禁锁不可读取") from error
    if not stat.S_ISREG(before.st_mode):
        raise _MaintenanceLockUnavailable("维护门禁锁必须是普通文件")
    flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_CLOEXEC", 0)
    try:
        descriptor = os.open(path, flags)
    except OSError as error:
        raise _MaintenanceLockUnavailable("维护门禁锁不可读取") from error
    try:
        if not same_file(before, os.fstat(descriptor)):
            raise _MaintenanceLockUnavailable("维护门禁锁在读取期间变化")
        _flock_linux(descriptor, exclusive=exclusive)
        return descriptor
    except BaseException:
        os.close(descriptor)
        raise


def _flock_linux(descriptor: int, *, exclusive: bool) -> None:
    import fcntl

    mode = fcntl.LOCK_EX if exclusive else fcntl.LOCK_SH
    deadline = time.monotonic() + _LOCK_TIMEOUT_SEC
    while True:
        try:
            fcntl.flock(descriptor, mode | fcntl.LOCK_NB)
            return
        except BlockingIOError:
            if time.monotonic() >= deadline:
                raise MaintenanceGateLockBusyError("维护门禁锁等待超时") from None
            time.sleep(_LOCK_RETRY_SEC)
        except OSError as error:
            raise _MaintenanceLockUnavailable("维护门禁锁不可用") from error


def release_linux_lock(descriptor: int) -> None:
    """释放 flock 后关闭描述符，任一失败都不伪装成功。"""
    release_error: OSError | None = None
    try:
        import fcntl

        fcntl.flock(descriptor, fcntl.LOCK_UN)
    except OSError as error:
        release_error = error
    try:
        os.close(descriptor)
    except OSError as error:
        raise _MaintenanceLockUnavailable("维护门禁锁描述符无法关闭") from error
    if release_error is not None:
        raise _MaintenanceLockUnavailable("维护门禁锁无法释放") from release_error


def prepare_local_linux_lock(marker: Path, lock: Path, *, payload: bytes) -> Path:
    """为非默认测试路径幂等创建本地锁文件。"""
    try:
        marker.parent.mkdir(parents=True, exist_ok=True)
        try:
            durable_write_once(lock, payload)
        except FileExistsError:
            pass
        metadata = lock.lstat()
        if not stat.S_ISREG(metadata.st_mode):
            raise _MaintenanceLockUnavailable("维护门禁锁必须是普通文件")
        os.chmod(lock, 0o644)
        fsync_directory(marker.parent)
        return lock
    except _MaintenanceLockUnavailable:
        raise
    except (OSError, ValueError) as error:
        raise _MaintenanceLockUnavailable("无法准备维护门禁锁") from error


__all__ = [
    "GlobalLockFile",
    "open_global_named_lock",
    "open_linux_lock",
    "prepare_local_linux_lock",
    "provision_global_locks",
    "release_linux_lock",
    "same_file",
]
