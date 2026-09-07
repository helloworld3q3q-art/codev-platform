"""运行时 fd tree 的路径归一化与 fd-relative 原语。"""

from __future__ import annotations

import os
from pathlib import Path

from codev_platform._runtime_fd_tree_contracts import RuntimeFdTreeError


def _normalize_root(root: Path) -> Path:
    candidate = Path(root)
    if not candidate.is_absolute():
        raise RuntimeFdTreeError("运行时对象根必须是规范绝对路径")
    normalized = Path(os.path.abspath(os.fspath(candidate)))
    try:
        resolved = candidate.resolve(strict=True)
    except (OSError, RuntimeError):
        raise RuntimeFdTreeError("运行时对象根目录不可用") from None
    if candidate != normalized or resolved != normalized or normalized == Path(normalized.anchor):
        raise RuntimeFdTreeError("运行时对象根必须是规范非链接目录")
    return normalized


def _open_directory(path: Path | str, *, dir_fd: int | None = None) -> int:
    flags = (
        os.O_RDONLY
        | getattr(os, "O_CLOEXEC", 0)
        | getattr(os, "O_DIRECTORY", 0)
        | getattr(os, "O_NOFOLLOW", 0)
        | getattr(os, "O_NOATIME", 0)
    )
    try:
        return os.open(path, flags, dir_fd=dir_fd)
    except OSError:
        raise RuntimeFdTreeError("运行时目录不可安全打开") from None


def _open_regular(name: str, *, dir_fd: int) -> int:
    flags = (
        os.O_RDONLY
        | getattr(os, "O_CLOEXEC", 0)
        | getattr(os, "O_NOFOLLOW", 0)
        | getattr(os, "O_NOATIME", 0)
        | getattr(os, "O_NONBLOCK", 0)
    )
    try:
        return os.open(name, flags, dir_fd=dir_fd)
    except OSError:
        raise RuntimeFdTreeError("运行时普通文件不可安全打开") from None


def _read_bounded(descriptor: int, max_bytes: int) -> bytes:
    blocks: list[bytes] = []
    total = 0
    while True:
        try:
            block = os.read(descriptor, max_bytes + 1 - total)
        except InterruptedError:
            continue
        except OSError:
            raise RuntimeFdTreeError("运行时对象未完成标记不可读") from None
        if not block:
            return b"".join(blocks)
        blocks.append(block)
        total += len(block)
        if total > max_bytes:
            raise RuntimeFdTreeError("运行时对象未完成标记不受信任")


def _stat_at(
    parent_fd: int,
    name: str,
    *,
    missing_ok: bool = False,
) -> os.stat_result | None:
    try:
        return os.stat(name, dir_fd=parent_fd, follow_symlinks=False)
    except FileNotFoundError:
        if missing_ok:
            return None
        raise RuntimeFdTreeError("运行时对象目录项发生漂移") from None
    except OSError:
        raise RuntimeFdTreeError("运行时对象目录项不可安全检查") from None


__all__ = [
    "_normalize_root",
    "_open_directory",
    "_open_regular",
    "_read_bounded",
    "_stat_at",
]
