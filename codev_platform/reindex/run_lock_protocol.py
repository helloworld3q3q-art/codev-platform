"""reindex worker 运行锁的跨进程原子发布协议。"""
from __future__ import annotations

import json
import os
from collections.abc import Callable, Iterator, Mapping
from contextlib import contextmanager
from enum import Enum
from pathlib import Path
from typing import Any

from .file_advisory_lock import advisory_lock
from .file_durability import durable_unlink, read_regular_file_bounded

_MAX_LOCK_BYTES = 16 * 1024


class RunLockUnavailableError(RuntimeError):
    """无法安全证明运行锁的独占状态，调用方必须拒绝启动。"""


class RunLockDisposition(Enum):
    """运行锁现状的三态结论；只有 RECLAIMABLE 才允许删除。"""

    ACTIVE = "active"
    RECLAIMABLE = "reclaimable"
    UNKNOWN = "unknown"


class _ImmediateDeadline:
    """运行锁只做一次非阻塞尝试，避免第二 worker 在锁忙时等待。"""

    def check(self) -> None:
        return None

    def remaining(self) -> float:
        return 0.0


LockAssessment = Callable[[dict[str, Any]], RunLockDisposition]
LockOwnedByCurrentProcess = Callable[[dict[str, Any]], bool]


def guard_path(path: Path) -> Path:
    """返回永久 guard 文件；它绝不能随业务运行锁一起删除。"""
    return path.with_name(f"{path.stem}.guard")


def read_lock_payload(path: Path) -> dict[str, Any]:
    """供状态展示使用的 fail-soft 读取；破坏性路径必须走严格读取。"""
    try:
        _, payload = _read_lock_payload_strict(path)
    except RunLockUnavailableError:
        return {}
    return payload


def _read_lock_payload_strict(path: Path) -> tuple[bool, dict[str, Any]]:
    """仅缺失可当作无锁；权限、链接、目录或超限均必须失败关闭。"""
    try:
        raw = read_regular_file_bounded(path, max_bytes=_MAX_LOCK_BYTES)
    except FileNotFoundError:
        return False, {}
    except (OSError, ValueError) as error:
        raise RunLockUnavailableError("运行锁读取无法证明") from error
    try:
        parsed = json.loads(raw.decode("utf-8"))
    except (UnicodeError, json.JSONDecodeError):
        return True, {}
    return True, parsed if isinstance(parsed, dict) else {}


@contextmanager
def _exclusive_guard(path: Path) -> Iterator[None]:
    """串行化同一运行锁的读取、回收、创建、写入和删除。"""
    with advisory_lock(
        guard_path(path),
        _ImmediateDeadline(),
        blocking=False,
    ) as acquired:
        if acquired is not True:
            raise RunLockUnavailableError("运行锁 guard 正忙，拒绝猜测锁状态")
        yield


def _encoded_payload(payload: Mapping[str, Any]) -> bytes:
    try:
        return json.dumps(
            dict(payload),
            ensure_ascii=False,
            allow_nan=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
    except (TypeError, ValueError) as error:
        raise RunLockUnavailableError("运行锁身份记录无法编码") from error


def _verified_predicate(
    predicate: Callable[[dict[str, Any]], bool],
    payload: dict[str, Any],
    *,
    failure: str,
) -> bool:
    """探针异常或返回非布尔值均视为无法证明，不能继续回收或删除。"""
    try:
        result = predicate(payload)
    except (KeyboardInterrupt, SystemExit, MemoryError):
        raise
    except Exception as error:
        raise RunLockUnavailableError(failure) from error
    if type(result) is not bool:
        raise RunLockUnavailableError(failure)
    return result


def _assess_lock(
    assessment: LockAssessment,
    payload: dict[str, Any],
) -> RunLockDisposition:
    """只有明确 RECLAIMABLE 才允许删除，探针异常一律保留原锁。"""
    try:
        result = assessment(payload)
    except (KeyboardInterrupt, SystemExit, MemoryError):
        raise
    except Exception as error:
        raise RunLockUnavailableError("运行锁活跃状态无法证明") from error
    if type(result) is not RunLockDisposition:
        raise RunLockUnavailableError("运行锁活跃状态无法证明")
    return result


def _write_all(descriptor: int, payload: bytes) -> None:
    """处理短写和 EINTR，只有完整同步后才允许释放 guard。"""
    offset = 0
    while offset < len(payload):
        try:
            written = os.write(descriptor, payload[offset:])
        except InterruptedError:
            continue
        if written <= 0:
            raise OSError("运行锁身份记录写入不完整")
        offset += written


def _cleanup_incomplete_lock(path: Path) -> None:
    """仅清理由本次创建且尚未完成发布的锁；清理失败交由下次严格回收。"""
    try:
        durable_unlink(path)
    except OSError:
        return


def _create_payload_file(path: Path, payload: Mapping[str, Any]) -> None:
    descriptor: int | None = None
    created = False
    try:
        flags = os.O_CREAT | os.O_EXCL | os.O_WRONLY | getattr(os, "O_BINARY", 0)
        descriptor = os.open(str(path), flags, 0o600)
        created = True
        _write_all(descriptor, _encoded_payload(payload))
        os.fsync(descriptor)
    except BaseException:
        if descriptor is not None:
            try:
                os.close(descriptor)
            except OSError:
                pass
            descriptor = None
        if created:
            _cleanup_incomplete_lock(path)
        raise
    if descriptor is not None:
        os.close(descriptor)


def _remove_stale_payload(path: Path) -> None:
    try:
        durable_unlink(path)
    except OSError as error:
        raise RunLockUnavailableError("陈旧运行锁无法安全回收") from error


def inspect(
    path: Path,
    *,
    lock_assessment: LockAssessment,
) -> RunLockDisposition:
    """严格只读检查运行锁，供 launcher 在未知时保守抑制重复启动。"""
    try:
        with _exclusive_guard(path):
            exists, existing = _read_lock_payload_strict(path)
            if not exists:
                return RunLockDisposition.RECLAIMABLE
            return _assess_lock(lock_assessment, existing)
    except (KeyboardInterrupt, SystemExit, MemoryError):
        raise
    except RunLockUnavailableError:
        raise
    except OSError as error:
        raise RunLockUnavailableError("运行锁原子协议不可用") from error


def acquire(
    path: Path,
    payload: Mapping[str, Any],
    *,
    lock_assessment: LockAssessment,
) -> bool:
    """在永久 guard 内原子取得运行锁；活锁返回 False，其余不确定性抛错。"""
    try:
        with _exclusive_guard(path):
            exists, existing = _read_lock_payload_strict(path)
            if exists:
                disposition = _assess_lock(lock_assessment, existing)
                if disposition is RunLockDisposition.ACTIVE:
                    return False
                if disposition is RunLockDisposition.UNKNOWN:
                    raise RunLockUnavailableError("运行锁活跃状态无法证明")
                _remove_stale_payload(path)
            try:
                _create_payload_file(path, payload)
            except FileExistsError as error:
                raise RunLockUnavailableError("运行锁创建发生并发冲突") from error
    except (KeyboardInterrupt, SystemExit, MemoryError):
        raise
    except RunLockUnavailableError:
        raise
    except OSError as error:
        raise RunLockUnavailableError("运行锁原子协议不可用") from error
    return True


def reclaim_stale(
    path: Path,
    *,
    lock_assessment: LockAssessment,
) -> bool:
    """仅在严格身份未证明活跃时回收遗留运行锁。"""
    try:
        with _exclusive_guard(path):
            exists, existing = _read_lock_payload_strict(path)
            if exists:
                disposition = _assess_lock(lock_assessment, existing)
                if disposition is RunLockDisposition.ACTIVE:
                    return False
                if disposition is RunLockDisposition.UNKNOWN:
                    raise RunLockUnavailableError("运行锁活跃状态无法证明")
                _remove_stale_payload(path)
    except (KeyboardInterrupt, SystemExit, MemoryError):
        raise
    except RunLockUnavailableError:
        raise
    except OSError as error:
        raise RunLockUnavailableError("运行锁原子协议不可用") from error
    return True


def release(
    path: Path,
    owner_token: str,
    *,
    owned_by_current_process: LockOwnedByCurrentProcess,
) -> None:
    """只删除仍属于当前进程和当前 owner 的运行锁。"""
    try:
        with _exclusive_guard(path):
            exists, existing = _read_lock_payload_strict(path)
            if not exists:
                return
            if (
                existing.get("owner_token") != owner_token
                or not _verified_predicate(
                    owned_by_current_process,
                    existing,
                    failure="运行锁归属无法证明",
                )
            ):
                return
            _remove_stale_payload(path)
    except (KeyboardInterrupt, SystemExit, MemoryError):
        raise
    except RunLockUnavailableError:
        raise
    except OSError as error:
        raise RunLockUnavailableError("运行锁原子协议不可用") from error


__all__ = [
    "RunLockDisposition",
    "RunLockUnavailableError",
    "acquire",
    "guard_path",
    "inspect",
    "read_lock_payload",
    "reclaim_stale",
    "release",
]
