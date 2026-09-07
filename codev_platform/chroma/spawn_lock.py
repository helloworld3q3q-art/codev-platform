"""Chroma 后台进程启动阶段的原生咨询锁。"""

from __future__ import annotations

import errno
import os
import time
from dataclasses import dataclass
from pathlib import Path
from typing import BinaryIO

from codev_platform.core.runtime_artifact_io import open_runtime_artifact_binary


@dataclass(slots=True)
class SpawnAdvisoryLock:
    """持有一个由操作系统管理生命周期的独占锁。"""

    path: Path
    stream: BinaryIO
    released: bool = False


def acquire_spawn_lock(path: Path) -> SpawnAdvisoryLock | None:
    """非阻塞获取独占锁；被占用时返回 ``None``。

    锁文件是稳定运行产物，不删除、不用 mtime 推断持有者是否存活。进程异常
    退出时，内核会随文件描述符关闭而释放锁，因此不会留下需要抢占的陈旧锁。
    """
    stream = open_runtime_artifact_binary(path)
    try:
        if not _try_lock(stream):
            stream.close()
            return None
        _ensure_lock_byte(stream)
        return SpawnAdvisoryLock(path=Path(path), stream=stream)
    except BaseException:
        stream.close()
        raise


def release_spawn_lock(lock: SpawnAdvisoryLock | None) -> None:
    """幂等释放锁；解锁失败时仍关闭描述符，让内核完成最终释放。"""
    if lock is None or lock.released:
        return
    lock.released = True
    try:
        _unlock(lock.stream)
    except (OSError, ValueError):
        pass
    finally:
        try:
            lock.stream.close()
        except OSError:
            pass


def acquire_spawn_lock_until(
    path: Path,
    timeout_sec: float,
    *,
    poll_interval_sec: float = 0.02,
) -> SpawnAdvisoryLock | None:
    """在固定时限内轮询原生锁；超时返回 ``None``。"""
    deadline = time.monotonic() + max(0.0, timeout_sec)
    while True:
        lock = acquire_spawn_lock(path)
        if lock is not None:
            return lock
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            return None
        time.sleep(min(poll_interval_sec, remaining))


def _ensure_lock_byte(stream: BinaryIO) -> None:
    """保证 Windows 字节区间锁有一个可锁定字节。"""
    if os.fstat(stream.fileno()).st_size == 0:
        if stream.write(b"\0") != 1:
            raise OSError("启动锁初始化写入不完整")
        stream.flush()
    stream.seek(0)


def _try_lock(stream: BinaryIO) -> bool:
    if os.name == "nt":
        import msvcrt

        stream.seek(0)
        try:
            msvcrt.locking(stream.fileno(), msvcrt.LK_NBLCK, 1)
            return True
        except OSError as exc:
            if _is_lock_busy(exc):
                return False
            raise

    import fcntl

    try:
        fcntl.flock(stream.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        return True
    except OSError as exc:
        if _is_lock_busy(exc):
            return False
        raise


def _unlock(stream: BinaryIO) -> None:
    if os.name == "nt":
        import msvcrt

        stream.seek(0)
        msvcrt.locking(stream.fileno(), msvcrt.LK_UNLCK, 1)
        return

    import fcntl

    fcntl.flock(stream.fileno(), fcntl.LOCK_UN)


def _is_lock_busy(exc: OSError) -> bool:
    return exc.errno in {errno.EACCES, errno.EAGAIN, errno.EDEADLK} or getattr(
        exc, "winerror", None
    ) in {33, 36}


__all__ = [
    "SpawnAdvisoryLock",
    "acquire_spawn_lock",
    "acquire_spawn_lock_until",
    "release_spawn_lock",
]
