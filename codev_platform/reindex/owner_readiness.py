"""稳定 queue owner 的只读就绪判定。"""
from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from pathlib import Path

from .runtime_owner import (
    QueueBackendBinding,
    QueueOwnerCorruptionError,
    QueueOwnerUnavailableError,
    queue_owner_path,
    read_queue_owner_file,
)

OWNER_BOOTSTRAP_EXIT_CODE = 77
OWNER_OPERATOR_BLOCKED_EXIT_CODE = 78


class OwnerReadiness(str, Enum):
    """自动启动可依据的稳定 owner 状态。"""

    READY = "ready"
    BOOTSTRAP_REQUIRED = "bootstrap_required"
    RECOVERY_REQUIRED = "recovery_required"
    BINDING_MISMATCH = "binding_mismatch"
    UNAVAILABLE = "unavailable"


@dataclass(frozen=True, slots=True)
class OwnerReadinessReport:
    """不含 owner token 的只读判定结果。"""

    status: OwnerReadiness


def inspect_owner_readiness(
    binding: QueueBackendBinding,
    *,
    path: Path | None = None,
) -> OwnerReadinessReport:
    """在不创建目录、owner 或锁文件的前提下检查稳定 owner。"""
    if type(binding) is not QueueBackendBinding:
        raise ValueError("binding 必须是 QueueBackendBinding")
    try:
        target = queue_owner_path(create_parent=False) if path is None else Path(path)
        owner = read_queue_owner_file(target)
    except (MemoryError, KeyboardInterrupt, SystemExit):
        raise
    except QueueOwnerCorruptionError:
        return OwnerReadinessReport(OwnerReadiness.RECOVERY_REQUIRED)
    except QueueOwnerUnavailableError:
        return OwnerReadinessReport(OwnerReadiness.UNAVAILABLE)
    except Exception:
        return OwnerReadinessReport(OwnerReadiness.UNAVAILABLE)
    if owner is None:
        return OwnerReadinessReport(OwnerReadiness.BOOTSTRAP_REQUIRED)
    if owner.binding != binding:
        return OwnerReadinessReport(OwnerReadiness.BINDING_MISMATCH)
    return OwnerReadinessReport(OwnerReadiness.READY)


__all__ = [
    "OWNER_BOOTSTRAP_EXIT_CODE",
    "OWNER_OPERATOR_BLOCKED_EXIT_CODE",
    "OwnerReadiness",
    "OwnerReadinessReport",
    "inspect_owner_readiness",
]
