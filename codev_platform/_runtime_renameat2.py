"""Linux renameat2 条件发布的唯一 ctypes 边界。"""

from __future__ import annotations

import ctypes
import errno
import os
import sys

_RENAME_NOREPLACE = 1


def rename_noreplace_at(
    source: str,
    destination: str,
    source_parent: int,
    destination_parent: int,
) -> None:
    """原子移动目录项；目标已经存在时绝不覆盖。"""
    _rename_at2(
        source,
        destination,
        source_parent,
        destination_parent,
        flags=_RENAME_NOREPLACE,
    )


def supports_rename_noreplace() -> bool:
    """判断当前平台是否提供 libc renameat2。"""
    if not sys.platform.startswith("linux"):
        return False
    try:
        return callable(getattr(ctypes.CDLL(None), "renameat2", None))
    except OSError:
        return False


def _rename_at2(
    source: str,
    destination: str,
    source_parent: int,
    destination_parent: int,
    *,
    flags: int,
) -> None:
    if "\x00" in source or "\x00" in destination:
        raise OSError(errno.EINVAL, "renameat2 路径不能包含 NUL")
    if not sys.platform.startswith("linux"):
        raise OSError(errno.ENOSYS, "当前平台不支持 renameat2")
    library = ctypes.CDLL(None, use_errno=True)
    renameat2 = getattr(library, "renameat2", None)
    if not callable(renameat2):
        raise OSError(errno.ENOSYS, "当前系统不支持 renameat2")
    renameat2.argtypes = (
        ctypes.c_int,
        ctypes.c_char_p,
        ctypes.c_int,
        ctypes.c_char_p,
        ctypes.c_uint,
    )
    renameat2.restype = ctypes.c_int
    result = renameat2(
        source_parent,
        os.fsencode(source),
        destination_parent,
        os.fsencode(destination),
        flags,
    )
    if result != 0:
        error_number = ctypes.get_errno()
        raise OSError(error_number, os.strerror(error_number))
