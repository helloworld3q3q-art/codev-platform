"""共享的 reindex 写侧互斥锁(从 chroma/indexer.py 抽出, 参数化 lock_dir)。

原子 `os.open(O_CREAT|O_EXCL)` 创建 `<lock_dir>/.reindex.lock`; 拿不到即拒绝(reindex 并发无意义,
不等待)。stale lock(默认 > 30 分钟未更新)抢占, 防进程崩溃留死锁。

复用方: chroma 文档索引器(PERSIST_DIR)+ recall 代码向量索引(per-project code_vec persist 目录)。
二者写不同 sqlite, 各自独立 lock 目录, 互不阻塞; 锁只防"同一目录两个写进程"竞态。
"""
from __future__ import annotations

import logging
import os
import time
from pathlib import Path

logger = logging.getLogger(__name__)

REINDEX_LOCK_STALE_SEC = 30 * 60
_LOCK_NAME = ".reindex.lock"


def _holder_pid(lock_path: Path) -> int | None:
    """读锁文件首行的 holder pid; 读不到/非数字 → None。"""
    try:
        first = lock_path.read_text(encoding="utf-8").splitlines()[0].strip()
        return int(first)
    except Exception:  # noqa: BLE001
        return None


def _pid_alive(pid: int | None) -> bool:
    """holder 进程是否还活着。判不了一律当**活**(保守, 退回时间阈值, 绝不误抢活锁)。
    PID 复用最坏只是"不抢"(退 stale 超时), 不会误抢。"""
    if pid is None:
        return True
    try:
        os.kill(pid, 0)        # Linux(生产 worker): 不存在 → ProcessLookupError
        return True
    except ProcessLookupError:
        return False
    except OSError:            # Windows os.kill(_,0) 不可靠 / 无权限 等 → 保守当活
        return True


def try_acquire_reindex_lock(lock_dir: Path) -> tuple | None:
    """原子创建 <lock_dir>/.reindex.lock; 返回 (fd, path) 表示拿到, None 表示已被占(拒绝)。

    被占时两种抢占: (1) holder 进程已死(PID 不存在, 如 build 跑中被 kill / 重启 worker 杀子进程)
    立即抢占; (2) 时间 stale(> 30min 未更新, 兜底判不了 PID 的情况)。死锁不会卡满 30min。
    """
    lock_dir = Path(lock_dir)
    lock_dir.mkdir(parents=True, exist_ok=True)
    lock_path = lock_dir / _LOCK_NAME
    try:
        fd = os.open(str(lock_path), os.O_CREAT | os.O_EXCL | os.O_RDWR)
        os.write(fd, f"{os.getpid()}\n{time.time()}\n".encode())
        return (fd, lock_path)
    except FileExistsError:
        try:
            age = time.time() - lock_path.stat().st_mtime
            dead = not _pid_alive(_holder_pid(lock_path))
            if dead or age > REINDEX_LOCK_STALE_SEC:    # 死进程 / 时间 stale → 抢占
                logger.warning("removing %s reindex lock at %s (age=%ds)",
                               "dead-holder" if dead else "stale", lock_path, int(age))
                lock_path.unlink(missing_ok=True)
                try:
                    fd = os.open(str(lock_path), os.O_CREAT | os.O_EXCL | os.O_RDWR)
                    os.write(fd, f"{os.getpid()}\n{time.time()}\n".encode())
                    return (fd, lock_path)
                except FileExistsError:
                    pass
        except OSError:
            pass
        return None


def release_reindex_lock(lock) -> None:
    """释放锁(关 fd + 删文件)。lock=None 安全 no-op。"""
    if lock is None:
        return
    fd, path = lock
    try:
        os.close(fd)
    except OSError:
        pass
    try:
        path.unlink(missing_ok=True)
    except OSError:
        pass
