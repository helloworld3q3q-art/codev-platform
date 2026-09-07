"""attempt 子进程直接继承日志的安全文件描述符叶子。"""

from __future__ import annotations

import errno
import os
import stat
from pathlib import Path


def open_direct_log(path: Path):
    """以非阻塞方式校验普通文件，再恢复阻塞追加模式。"""
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    flags = os.O_WRONLY | os.O_CREAT | os.O_APPEND | getattr(os, "O_CLOEXEC", 0)
    nonblocking = getattr(os, "O_NONBLOCK", 0)
    flags |= getattr(os, "O_NOFOLLOW", 0) | nonblocking
    descriptor = os.open(target, flags, 0o600)
    try:
        if not stat.S_ISREG(os.fstat(descriptor).st_mode):
            raise OSError(errno.EINVAL, "attempt 日志必须是普通文件", target)
        if nonblocking:
            os.set_blocking(descriptor, True)
        return os.fdopen(descriptor, "ab", buffering=0)
    except BaseException:
        os.close(descriptor)
        raise


__all__ = ["open_direct_log"]
