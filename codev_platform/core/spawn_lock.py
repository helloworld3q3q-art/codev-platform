"""跨进程 spawn lock — 防 N 个 user / 脚本同时跑长时间操作互踩。

原子 O_EXCL 创建 lock 文件 (含 PID + timestamp), 进程退出时 unlink. stale 检测
按 mtime, 超过阈值视为遗弃 (上次进程崩没清), 抢占重建。

复用场景:
- chroma daemon spawn (避免多 launcher 并发 spawn 浪费 GPU)
- cross_link build_index (避免多 user 并发 build 撞 tmp.sqlite)
- 未来的 reindex / rebuild 等长时操作

用法 (context manager):
    from codev_platform.core.spawn_lock import acquire_lock
    with acquire_lock("/path/to/.work.lock", stale_after_sec=600):
        # 长时间操作 ...
"""
from __future__ import annotations

import os
import time
from contextlib import contextmanager
from pathlib import Path


class LockHeld(RuntimeError):
    """另一进程持锁且未 stale, 调用方决定等 / 跳过 / 报错。"""


@contextmanager
def acquire_lock(lock_path: str | Path, stale_after_sec: int = 600, label: str = "lock"):
    """获锁 + yield + release (含 stale 抢占).

    Args:
        lock_path: lock 文件绝对路径 (自动创建父目录).
        stale_after_sec: 超过此秒数视为遗弃, 抢占 (默认 600).
        label: 日志友好名 (RuntimeError 时显示).
    Raises:
        LockHeld: 已有未 stale 锁持有者.
    """
    p = Path(lock_path)
    p.parent.mkdir(parents=True, exist_ok=True)
    fd: int | None = None
    try:
        try:
            fd = os.open(str(p), os.O_CREAT | os.O_EXCL | os.O_RDWR)
            os.write(fd, f"{os.getpid()}\n{int(time.time())}\n".encode())
        except FileExistsError:
            try:
                age = time.time() - p.stat().st_mtime
            except OSError:
                age = 0
            if age > stale_after_sec:
                try:
                    p.unlink(missing_ok=True)
                    fd = os.open(str(p), os.O_CREAT | os.O_EXCL | os.O_RDWR)
                    os.write(fd, f"{os.getpid()}\n{int(time.time())}\n".encode())
                except FileExistsError:
                    raise LockHeld(f"{label}: race condition on stale claim {p}") from None
            else:
                try:
                    holder = p.read_text(encoding="utf-8").strip()
                except OSError:
                    holder = "?"
                raise LockHeld(
                    f"{label} already running (lock {p}, holder {holder}, age={int(age)}s, "
                    f"stale_after={stale_after_sec}s). 等其完成或确认 stale 后手动 Remove-Item '{p}'"
                ) from None
        try:
            yield
        finally:
            if fd is not None:
                try:
                    os.close(fd)
                except OSError:
                    pass
            try:
                p.unlink(missing_ok=True)
            except OSError:
                pass
    except Exception:
        if fd is not None:
            try:
                os.close(fd)
            except OSError:
                pass
        raise
