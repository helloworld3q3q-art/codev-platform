"""运行时预检的文件系统、身份与导入来源探针。"""

from __future__ import annotations

import os
import stat
from collections.abc import Callable, Sequence
from pathlib import Path
from types import ModuleType
from uuid import uuid4

from codev_platform.runtime_preflight_contract import ProbeDeadline


_SENTINEL_PREFIX = ".codev-preflight-"


def probe_runtime_identity(current: Path) -> None:
    """证明当前进程来自 formal current 邻接的完整 release。"""
    from codev_platform.core.runtime_identity import runtime_identity

    identity = runtime_identity()
    prefix = getattr(identity, "environment_prefix", None)
    interpreter = getattr(identity, "interpreter_realpath", None)
    if (
        getattr(identity, "mode", None) != "release"
        or type(prefix) is not str
        or type(interpreter) is not str
    ):
        raise OSError("当前进程不是正式 release")
    expected_prefix = (current / "venv").resolve(strict=True)
    expected_python = (current / "venv" / "bin" / "python").resolve(strict=True)
    if (
        Path(prefix).resolve(strict=True) != expected_prefix
        or Path(interpreter).resolve(strict=True) != expected_python
    ):
        raise OSError("当前进程不邻接 formal current")


def probe_directory_rx(path: Path) -> None:
    resolved = path.resolve(strict=True)
    if not resolved.is_dir() or not os.access(path, os.R_OK | os.X_OK):
        raise OSError("目录不可读或不可遍历")


def probe_directory_traverse(path: Path) -> None:
    """证明目录仅可安全穿越，不要求服务账号列举父命名空间。"""
    resolved = path.resolve(strict=True)
    if not resolved.is_dir() or not os.access(path, os.X_OK):
        raise OSError("目录不可遍历")


def probe_file_rx(path: Path) -> None:
    resolved = path.resolve(strict=True)
    if not resolved.is_file() or not os.access(path, os.R_OK | os.X_OK):
        raise OSError("文件不可读或不可执行")


def probe_import_sources(
    release: Path,
    forbidden_root: Path | None,
    modules: Sequence[str],
    importer: Callable[[str], ModuleType | object],
    deadline: ProbeDeadline,
) -> None:
    """逐个导入受管入口并证明其来源均属于 current release。"""
    release_root = release.resolve(strict=True)
    forbidden = None if forbidden_root is None else forbidden_root.resolve(strict=True)
    for module_name in modules:
        deadline.ensure()
        loaded = importer(module_name)
        raw = getattr(loaded, "__file__", None)
        if type(raw) is not str or not raw:
            raise OSError("应用导入来源不可证明")
        actual = Path(raw).resolve(strict=True)
        if (
            not actual.is_file()
            or _is_mnt_path(actual)
            or _inside_git_checkout(actual)
            or not actual.is_relative_to(release_root)
            or (forbidden is not None and actual.is_relative_to(forbidden))
        ):
            raise OSError("应用导入来源不属于当前 release")
    deadline.ensure()


def _is_mnt_path(path: Path) -> bool:
    return len(path.parts) >= 3 and path.parts[:2] == ("/", "mnt")


def _inside_git_checkout(path: Path) -> bool:
    for parent in path.parents:
        try:
            (parent / ".git").lstat()
        except FileNotFoundError:
            continue
        except OSError:
            return True
        return True
    return False


def _require_probe_directory(directory: Path) -> Path:
    metadata = directory.lstat()
    if stat.S_ISLNK(metadata.st_mode) or not stat.S_ISDIR(metadata.st_mode):
        raise OSError("探针目录不受信任")
    if not os.access(directory, os.R_OK | os.W_OK | os.X_OK):
        raise OSError("探针目录不可读写")
    return directory


def _sentinel(directory: Path, suffix: str) -> Path:
    return directory / f"{_SENTINEL_PREFIX}{uuid4().hex}{suffix}"


def _create_sentinel(path: Path) -> int:
    flags = (
        os.O_RDWR
        | os.O_CREAT
        | os.O_EXCL
        | getattr(os, "O_CLOEXEC", 0)
        | getattr(os, "O_NOFOLLOW", 0)
    )
    descriptor = os.open(path, flags, 0o600)
    try:
        if os.write(descriptor, b"preflight\n") != len(b"preflight\n"):
            raise OSError("哨兵写入不完整")
        os.fsync(descriptor)
        return descriptor
    except BaseException:
        os.close(descriptor)
        raise


def _fsync_directory(directory: Path) -> None:
    flags = os.O_RDONLY | getattr(os, "O_DIRECTORY", 0) | getattr(os, "O_NOFOLLOW", 0)
    descriptor = os.open(directory, flags)
    try:
        if not stat.S_ISDIR(os.fstat(descriptor).st_mode):
            raise OSError("fsync 目标不是目录")
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def _cleanup_sentinels(directory: Path, *paths: Path) -> None:
    for path in paths:
        path.unlink(missing_ok=True)
    _fsync_directory(directory)


def probe_directory_write(directory: Path) -> None:
    parent = _require_probe_directory(directory)
    sentinel = _sentinel(parent, ".rw")
    descriptor: int | None = None
    try:
        descriptor = _create_sentinel(sentinel)
        os.close(descriptor)
        descriptor = None
        _fsync_directory(parent)
    finally:
        if descriptor is not None:
            os.close(descriptor)
        _cleanup_sentinels(parent, sentinel)


def probe_atomic_directory(directory: Path) -> None:
    parent = _require_probe_directory(directory)
    source = _sentinel(parent, ".source")
    target = _sentinel(parent, ".target")
    descriptor: int | None = None
    try:
        descriptor = _create_sentinel(source)
        os.close(descriptor)
        descriptor = None
        os.replace(source, target)
        _fsync_directory(parent)
    finally:
        if descriptor is not None:
            os.close(descriptor)
        _cleanup_sentinels(parent, source, target)


def _flock_exclusive(descriptor: int) -> None:
    import fcntl

    fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
    fcntl.flock(descriptor, fcntl.LOCK_UN)


def probe_flock_directory(directory: Path) -> None:
    parent = _require_probe_directory(directory)
    sentinel = _sentinel(parent, ".lock")
    descriptor: int | None = None
    try:
        descriptor = _create_sentinel(sentinel)
        _flock_exclusive(descriptor)
        _fsync_directory(parent)
    finally:
        if descriptor is not None:
            os.close(descriptor)
        _cleanup_sentinels(parent, sentinel)


__all__ = [
    "probe_atomic_directory",
    "probe_directory_rx",
    "probe_directory_traverse",
    "probe_directory_write",
    "probe_file_rx",
    "probe_flock_directory",
    "probe_import_sources",
    "probe_runtime_identity",
]
