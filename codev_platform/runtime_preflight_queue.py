"""运行时预检的队列后端选择与只读探针适配。"""

from __future__ import annotations

from collections.abc import Mapping
import os
from pathlib import Path
import stat
from uuid import uuid4

from codev_platform.runtime_preflight_contract import PathRequirement, ProbeDeadline
from codev_platform.runtime_preflight_database import probe_pg_queue_state_readonly


def queue_requirement(
    cfg: dict[str, object],
    data: Path,
    environment: Mapping[str, str],
) -> PathRequirement:
    """从既有配置快照选择文件队列或 PG 只读探针。"""
    from codev_platform.core.config import get
    raw = get(cfg, "reindex.queue_backend", "file") or "file"
    if type(raw) is not str:
        raise ValueError("reindex.queue_backend 必须是字符串")
    backend = raw.strip().lower()
    if backend == "file":
        return PathRequirement(
            "file_queue_operational_rw",
            "file_queue_operational",
            data / "reindex_queue",
        )
    if backend == "pg":
        dsn = environment.get("CODEV_PLATFORM_MEMORY_DSN") or get(cfg, "memory.pg_dsn")
        if dsn is not None and type(dsn) is not str:
            raise ValueError("memory.pg_dsn 必须是字符串")
        normalized = dsn.strip() if isinstance(dsn, str) and dsn.strip() else None
        return PathRequirement(
            "queue_backend_readonly",
            "queue_readonly",
            None,
            database_dsn=normalized,
        )
    raise ValueError("reindex.queue_backend 不受支持")


def probe_queue_readonly(dsn: str | None, deadline: ProbeDeadline) -> None:
    """使用规划阶段冻结的后端上下文，避免探针二次加载配置。"""
    if type(dsn) is not str or not dsn.strip():
        raise OSError("PG queue 连接配置不可用")
    probe_pg_queue_state_readonly(dsn, deadline)


def probe_file_queue_operational(root: Path, deadline: ProbeDeadline) -> None:
    """用隐藏哨兵证明五个阶段、跨阶段替换、目录持久化与原生锁。"""
    from codev_platform.reindex.file_queue_store import (
        ACTIVE,
        LOCKS,
        PENDING,
        QUARANTINED,
        RESULTS,
    )

    phases = (PENDING, ACTIVE, RESULTS, QUARANTINED, LOCKS)
    directories = {phase: _queue_directory(Path(root) / phase) for phase in phases}
    token = uuid4().hex
    sentinels: set[Path] = set()
    try:
        for phase in phases:
            deadline.ensure()
            sentinel = directories[phase] / f".codev-preflight-{token}-{phase}.tmp"
            _create_queue_sentinel(sentinel)
            sentinels.add(sentinel)
            _fsync_queue_directory(directories[phase])
        moving = directories[PENDING] / f".codev-preflight-{token}-{PENDING}.tmp"
        for phase in (ACTIVE, RESULTS, QUARANTINED):
            deadline.ensure()
            target = directories[phase] / f".codev-preflight-{token}-transition.tmp"
            os.replace(moving, target)
            sentinels.discard(moving)
            sentinels.add(target)
            _fsync_queue_directory(moving.parent)
            _fsync_queue_directory(target.parent)
            moving = target
        _probe_queue_lock(
            directories[LOCKS] / f".codev-preflight-{token}-{LOCKS}.tmp"
        )
        deadline.ensure()
    finally:
        for sentinel in sentinels:
            sentinel.unlink(missing_ok=True)
        for directory in directories.values():
            _fsync_queue_directory(directory)


def _queue_directory(path: Path) -> Path:
    metadata = path.lstat()
    if (
        stat.S_ISLNK(metadata.st_mode)
        or not stat.S_ISDIR(metadata.st_mode)
        or not os.access(path, os.R_OK | os.W_OK | os.X_OK)
    ):
        raise OSError("File queue 阶段目录不可用")
    return path


def _create_queue_sentinel(path: Path) -> None:
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
            raise OSError("File queue 哨兵写入不完整")
        os.fsync(descriptor)
    except BaseException:
        path.unlink(missing_ok=True)
        raise
    finally:
        os.close(descriptor)


def _probe_queue_lock(path: Path) -> None:
    with path.open("r+b", buffering=0) as stream:
        stream.seek(0)
        if os.name == "nt":
            import msvcrt

            msvcrt.locking(stream.fileno(), msvcrt.LK_NBLCK, 1)
            msvcrt.locking(stream.fileno(), msvcrt.LK_UNLCK, 1)
            return
        import fcntl

        fcntl.flock(stream.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        fcntl.flock(stream.fileno(), fcntl.LOCK_UN)


def _fsync_queue_directory(path: Path) -> None:
    if os.name == "nt":
        return
    flags = os.O_RDONLY | getattr(os, "O_DIRECTORY", 0) | getattr(os, "O_NOFOLLOW", 0)
    descriptor = os.open(path, flags)
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


__all__ = [
    "probe_file_queue_operational",
    "probe_queue_readonly",
    "queue_requirement",
]
