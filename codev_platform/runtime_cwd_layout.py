"""root-fd worker 当前目录内的运行时直接布局证明。"""

from __future__ import annotations

import os
from pathlib import Path
import stat


_MANAGED_DIRECTORIES = ("bases", "releases")
_UNSAFE_WRITE_BITS = stat.S_IWGRP | stat.S_IWOTH


class RuntimeCwdLayoutError(RuntimeError):
    """继承 cwd 不再满足 runtime 直接布局的最小安全契约。"""


def verify_runtime_layout_from_cwd() -> None:
    """证明 root-fd worker 的 cwd、bases 与 releases 均为可信非链接目录。"""
    if os.name != "posix" or os.geteuid() != 0:
        raise RuntimeCwdLayoutError("cwd 运行时布局证明仅支持 Linux root worker")
    _require_secure_directory(Path("."), "运行时根")
    for name in _MANAGED_DIRECTORIES:
        _require_secure_directory(Path(name), f"运行时 {name} 目录")


def _require_secure_directory(path: Path, label: str) -> None:
    try:
        metadata = path.lstat()
    except OSError:
        raise RuntimeCwdLayoutError(f"{label}不可用") from None
    if not stat.S_ISDIR(metadata.st_mode) or stat.S_ISLNK(metadata.st_mode):
        raise RuntimeCwdLayoutError(f"{label}必须是非链接目录")
    if metadata.st_uid != 0:
        raise RuntimeCwdLayoutError(f"{label}必须由 root 所有")
    if stat.S_IMODE(metadata.st_mode) & _UNSAFE_WRITE_BITS:
        raise RuntimeCwdLayoutError(f"{label}不得对非所有者可写")


__all__ = ["RuntimeCwdLayoutError", "verify_runtime_layout_from_cwd"]
