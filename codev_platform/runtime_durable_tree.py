"""将已完成的运行时对象树封存到持久存储。"""

from __future__ import annotations

import os
from pathlib import Path
import stat


_MAX_TREE_ENTRIES = 2_000_000
_MAX_TREE_BYTES = 256 * 1024 * 1024 * 1024


class RuntimeDurabilityError(RuntimeError):
    """运行时对象树无法形成完整持久化屏障。"""


def seal_durable_tree(root: Path) -> None:
    """同步普通文件和目录项；不跟随链接并拒绝特殊文件。"""
    tree_root = Path(root)
    _require_plain_directory(tree_root)
    directories: list[tuple[Path, os.stat_result]] = []
    entry_count = 0
    total_bytes = 0
    try:
        for current, names, filenames in os.walk(
            tree_root,
            topdown=True,
            onerror=_raise_walk_error,
            followlinks=False,
        ):
            current_path = Path(current)
            current_metadata = _require_plain_directory(current_path)
            directories.append((current_path, current_metadata))
            for name in (*names, *filenames):
                path = current_path / name
                metadata = path.lstat()
                entry_count += 1
                if entry_count > _MAX_TREE_ENTRIES:
                    raise RuntimeDurabilityError("运行时对象树条目超过持久化安全预算")
                if stat.S_ISLNK(metadata.st_mode):
                    _verify_stable_link(path, metadata)
                elif stat.S_ISDIR(metadata.st_mode):
                    if name in filenames:
                        raise RuntimeDurabilityError("运行时对象树遍历结果发生漂移")
                elif stat.S_ISREG(metadata.st_mode):
                    total_bytes += metadata.st_size
                    if total_bytes > _MAX_TREE_BYTES:
                        raise RuntimeDurabilityError("运行时对象树大小超过持久化安全预算")
                    _fsync_regular_file(path, metadata)
                else:
                    raise RuntimeDurabilityError("运行时对象树包含特殊文件")
    except RuntimeDurabilityError:
        raise
    except OSError:
        raise RuntimeDurabilityError("运行时对象树无法完整封存") from None
    for directory, metadata in reversed(directories):
        _fsync_directory(directory, metadata)


def _require_plain_directory(path: Path) -> os.stat_result:
    try:
        metadata = path.lstat()
    except OSError:
        raise RuntimeDurabilityError("运行时对象目录不可用") from None
    if not stat.S_ISDIR(metadata.st_mode) or stat.S_ISLNK(metadata.st_mode):
        raise RuntimeDurabilityError("运行时对象目录必须是非链接目录")
    return metadata


def _raise_walk_error(_error: OSError) -> None:
    raise RuntimeDurabilityError("运行时对象树无法完整枚举")


def _verify_stable_link(path: Path, before: os.stat_result) -> None:
    try:
        os.readlink(path)
        after = path.lstat()
    except OSError:
        raise RuntimeDurabilityError("运行时对象链接不可稳定读取") from None
    if (
        not stat.S_ISLNK(after.st_mode)
        or before.st_dev != after.st_dev
        or before.st_ino != after.st_ino
        or before.st_mtime_ns != after.st_mtime_ns
    ):
        raise RuntimeDurabilityError("运行时对象链接读取期间发生漂移")


def _fsync_regular_file(path: Path, before: os.stat_result) -> None:
    access = os.O_RDWR if os.name == "nt" else os.O_RDONLY
    flags = access | getattr(os, "O_CLOEXEC", 0) | getattr(os, "O_NOFOLLOW", 0)
    flags |= getattr(os, "O_BINARY", 0)
    try:
        descriptor = os.open(path, flags)
    except OSError:
        raise RuntimeDurabilityError("运行时普通文件不可安全打开") from None
    try:
        opened = os.fstat(descriptor)
        if (
            not stat.S_ISREG(opened.st_mode)
            or before.st_dev != opened.st_dev
            or before.st_ino != opened.st_ino
            or before.st_size != opened.st_size
            or before.st_mtime_ns != opened.st_mtime_ns
        ):
            raise RuntimeDurabilityError("运行时普通文件打开期间发生漂移")
        os.fsync(descriptor)
        after = os.fstat(descriptor)
        if opened.st_size != after.st_size or opened.st_mtime_ns != after.st_mtime_ns:
            raise RuntimeDurabilityError("运行时普通文件同步期间发生漂移")
    except RuntimeDurabilityError:
        raise
    except OSError:
        raise RuntimeDurabilityError("运行时普通文件无法持久化") from None
    finally:
        os.close(descriptor)


def _fsync_directory(path: Path, before: os.stat_result) -> None:
    if os.name == "nt":
        return
    flags = (
        os.O_RDONLY
        | getattr(os, "O_CLOEXEC", 0)
        | getattr(os, "O_DIRECTORY", 0)
        | getattr(os, "O_NOFOLLOW", 0)
    )
    try:
        descriptor = os.open(path, flags)
    except OSError:
        raise RuntimeDurabilityError("运行时目录不可安全打开") from None
    try:
        opened = os.fstat(descriptor)
        if _directory_identity(before) != _directory_identity(opened):
            raise RuntimeDurabilityError("运行时目录打开期间发生漂移")
        os.fsync(descriptor)
        after = os.fstat(descriptor)
        linked = path.lstat()
        if (
            _directory_identity(opened) != _directory_identity(after)
            or _directory_identity(opened) != _directory_identity(linked)
        ):
            raise RuntimeDurabilityError("运行时目录同步期间发生漂移")
    except RuntimeDurabilityError:
        raise
    except OSError:
        raise RuntimeDurabilityError("运行时目录无法持久化") from None
    finally:
        os.close(descriptor)


def _directory_identity(metadata: os.stat_result) -> tuple[int, ...]:
    return (
        metadata.st_dev,
        metadata.st_ino,
        metadata.st_mode,
        metadata.st_uid,
        metadata.st_gid,
        metadata.st_nlink,
        metadata.st_size,
        metadata.st_mtime_ns,
    )


__all__ = ["RuntimeDurabilityError", "seal_durable_tree"]
