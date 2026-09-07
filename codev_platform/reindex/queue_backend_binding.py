"""把实际 queue 后端映射为不含秘密的稳定 owner 绑定。"""
from __future__ import annotations

from pathlib import Path

from .file_queue import FileSpoolQueue
from .pg_queue import PgJobQueue, PgQueueInitializationError
from .queue_ports import QueueOperationTimeout
from .runtime_owner import QueueBackendBinding, fingerprint_backend


class QueueBackendBindingError(RuntimeError):
    """无法从当前 queue 安全建立稳定 owner 绑定。"""


def queue_backend_binding(queue: object, cfg: dict | None = None) -> QueueBackendBinding:
    """建立稳定 owner 绑定；PG 会受控完成首次 schema 冻结。"""
    if isinstance(queue, FileSpoolQueue):
        return _binding("file", _canonical_file_location(queue.location))
    if isinstance(queue, PgJobQueue):
        try:
            return _binding("pg", queue.ensure_owner_binding_locator())
        except (PgQueueInitializationError, QueueOperationTimeout, TypeError, ValueError):
            raise QueueBackendBindingError("PG queue 无法完成安全后端绑定") from None
    raise QueueBackendBindingError("当前 queue 后端不支持稳定 owner 绑定")


def _canonical_file_location(value: object) -> str:
    path = Path(value)
    try:
        resolved = path.resolve(strict=False)
    except OSError as error:
        raise QueueBackendBindingError("File queue 位置无法规范化") from error
    if not resolved.is_absolute():
        raise QueueBackendBindingError("File queue 位置不是绝对路径")
    return str(resolved)


def _binding(kind: str, locator: str) -> QueueBackendBinding:
    try:
        return QueueBackendBinding(kind, fingerprint_backend(kind, locator))
    except (TypeError, ValueError) as error:
        raise QueueBackendBindingError("queue 后端绑定无法生成") from error


__all__ = ["QueueBackendBindingError", "queue_backend_binding"]
