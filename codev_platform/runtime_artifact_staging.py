"""wheelhouse 的独占暂存、可续跑与原子发布边界。"""

from __future__ import annotations

import os
import stat
import threading
import time
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path

from codev_platform.runtime_deadline import bounded_runtime_timeout


class RuntimeArtifactStagingError(RuntimeError):
    """制品暂存目录或跨进程锁不受信任。"""


_WINDOWS_LOCKS_GUARD = threading.Lock()
_WINDOWS_LOCKS: dict[str, threading.Lock] = {}


@dataclass(slots=True)
class ArtifactWorkspace:
    """一次独占准备任务持有的最终目录与工作目录。"""

    final: Path
    working: Path
    publish_required: bool
    _published: bool = False

    def publish(self) -> Path:
        """只把已由调用方完整验证的暂存目录提交为最终 wheelhouse。"""
        if self._published:
            return self.final
        if not self.publish_required:
            self._published = True
            return self.final
        _require_directory(self.working, "wheelhouse 暂存目录")
        if self.final.exists() or self.final.is_symlink():
            raise RuntimeArtifactStagingError("wheelhouse 最终目录已被并发占用")
        try:
            os.replace(self.working, self.final)
            _fsync_directory(self.final.parent)
        except OSError:
            raise RuntimeArtifactStagingError("wheelhouse 无法原子发布") from None
        self._published = True
        return self.final


@contextmanager
def locked_artifact_workspace(final: Path) -> Iterator[ArtifactWorkspace]:
    """取得固定伴随锁；失败保留 `.incomplete` 供下一次精确续跑。"""
    target = _absolute_target(final)
    stage = target.with_name(f".{target.name}.incomplete")
    lock_path = target.with_name(f".{target.name}.lock")
    target.parent.mkdir(parents=True, exist_ok=True)
    with _exclusive_lock(lock_path):
        target_exists = target.exists() or target.is_symlink()
        stage_exists = stage.exists() or stage.is_symlink()
        if target_exists and stage_exists:
            raise RuntimeArtifactStagingError("wheelhouse 最终目录与暂存目录同时存在")
        if target_exists:
            _require_directory(target, "wheelhouse 最终目录")
            workspace = ArtifactWorkspace(target, target, False)
        else:
            if not stage_exists:
                try:
                    stage.mkdir(mode=0o700)
                    _fsync_directory(stage.parent)
                except OSError:
                    raise RuntimeArtifactStagingError("wheelhouse 暂存目录无法创建") from None
            _require_directory(stage, "wheelhouse 暂存目录")
            workspace = ArtifactWorkspace(target, stage, True)
        yield workspace


@contextmanager
def locked_artifact_publication(output: Path) -> Iterator[Path]:
    """按最终锁文件身份串行发布；必须先于 wheelhouse 锁获取。"""
    target = _absolute_target(output)
    lock_path = target.with_name(f".{target.name}.publication.lock")
    target.parent.mkdir(parents=True, exist_ok=True)
    _require_directory(target.parent, "制品发布目录")
    with _exclusive_lock(lock_path):
        yield target


def _absolute_target(value: Path) -> Path:
    path = Path(value)
    if not path.is_absolute() or path == Path(path.anchor) or path.name in {"", ".", ".."}:
        raise RuntimeArtifactStagingError("wheelhouse 最终路径必须是安全绝对路径")
    normalized = Path(os.path.abspath(path))
    if normalized != path or path.is_symlink():
        raise RuntimeArtifactStagingError("wheelhouse 最终路径不受信任")
    return normalized


def _require_directory(path: Path, label: str) -> None:
    try:
        metadata = path.lstat()
    except OSError:
        raise RuntimeArtifactStagingError(f"{label}不可用") from None
    if not stat.S_ISDIR(metadata.st_mode) or stat.S_ISLNK(metadata.st_mode):
        raise RuntimeArtifactStagingError(f"{label}不受信任")


@contextmanager
def _exclusive_lock(path: Path) -> Iterator[None]:
    """Linux 使用真实 flock；Windows 单测用进程内互斥，不冒充生产锁。"""
    if os.name != "posix":
        lock = _windows_lock(path)
        acquired = lock.acquire(timeout=bounded_runtime_timeout(30.0))
        if not acquired:
            raise RuntimeArtifactStagingError("制品准备锁超时")
        try:
            yield
        finally:
            lock.release()
        return
    import fcntl

    flags = os.O_RDWR | os.O_CREAT | getattr(os, "O_CLOEXEC", 0) | getattr(os, "O_NOFOLLOW", 0)
    try:
        descriptor = os.open(path, flags, 0o600)
        metadata = os.fstat(descriptor)
        if (
            not stat.S_ISREG(metadata.st_mode)
            or metadata.st_nlink != 1
            or metadata.st_uid != os.geteuid()
            or stat.S_IMODE(metadata.st_mode) != 0o600
        ):
            raise RuntimeArtifactStagingError("wheelhouse 准备锁不受信任")
        deadline = time.monotonic() + bounded_runtime_timeout(30.0)
        while True:
            try:
                fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
                break
            except BlockingIOError:
                if time.monotonic() >= deadline:
                    raise RuntimeArtifactStagingError("制品准备锁超时") from None
                time.sleep(min(0.05, max(0.0, deadline - time.monotonic())))
        yield
    except RuntimeArtifactStagingError:
        raise
    except OSError:
        raise RuntimeArtifactStagingError("制品准备锁不可用") from None
    finally:
        if "descriptor" in locals():
            try:
                fcntl.flock(descriptor, fcntl.LOCK_UN)
            except OSError:
                pass
            os.close(descriptor)


def _windows_lock(path: Path) -> threading.Lock:
    key = os.path.normcase(os.path.abspath(path))
    with _WINDOWS_LOCKS_GUARD:
        return _WINDOWS_LOCKS.setdefault(key, threading.Lock())


def _fsync_directory(path: Path) -> None:
    if os.name != "posix":
        return
    descriptor = os.open(path, os.O_RDONLY | getattr(os, "O_DIRECTORY", 0))
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


__all__ = [
    "ArtifactWorkspace",
    "RuntimeArtifactStagingError",
    "locked_artifact_publication",
    "locked_artifact_workspace",
]
