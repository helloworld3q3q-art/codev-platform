"""File 队列跨进程状态切换使用的原生 advisory gate。"""

from __future__ import annotations

import errno
import os
import time
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import Protocol

_WAIT_SEC = 0.005
_BUSY_ERRNOS = {errno.EACCES, errno.EAGAIN, errno.EDEADLK}
_ERROR_LOCK_VIOLATION = 33


class AdvisoryDeadline(Protocol):
    def check(self) -> None: ...
    def remaining(self) -> float: ...


def _ensure_lock_byte(stream) -> None:
    stream.seek(0, os.SEEK_END)
    if stream.tell() == 0:
        stream.write(b"\0")
        stream.flush()


def _try_lock(stream) -> bool:
    stream.seek(0)
    try:
        if os.name == "nt":
            import msvcrt

            msvcrt.locking(stream.fileno(), msvcrt.LK_NBLCK, 1)
        else:
            import fcntl

            fcntl.flock(stream.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError as exc:
        if exc.errno in _BUSY_ERRNOS or getattr(exc, "winerror", None) == _ERROR_LOCK_VIOLATION:
            return False
        raise
    return True


def _unlock(stream) -> None:
    stream.seek(0)
    if os.name == "nt":
        import msvcrt

        msvcrt.locking(stream.fileno(), msvcrt.LK_UNLCK, 1)
        return
    import fcntl

    fcntl.flock(stream.fileno(), fcntl.LOCK_UN)


@contextmanager
def advisory_lock(
    path: Path,
    deadline: AdvisoryDeadline,
    *,
    blocking: bool,
) -> Iterator[bool]:
    """在 deadline 内取得 OS 自动随进程退出释放的单字节锁。"""
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a+b", buffering=0) as stream:
        acquired = False
        while not acquired:
            deadline.check()
            acquired = _try_lock(stream)
            if acquired:
                break
            if not blocking:
                yield False
                return
            remaining = deadline.remaining()
            if remaining <= 0:
                deadline.check()
                continue
            time.sleep(min(_WAIT_SEC, remaining))
        try:
            _ensure_lock_byte(stream)
            yield True
        finally:
            _unlock(stream)


__all__ = ["AdvisoryDeadline", "advisory_lock"]
