"""受管父目录的有界 POSIX 协作写锁。"""

from __future__ import annotations

from codev_platform._runtime_managed_file_fd import ManagedFileError
from codev_platform._runtime_flock import (
    RuntimeFlockTimeoutError,
    RuntimeFlockUnavailableError,
    acquire_runtime_flock,
)

_LOCK_WAIT_TIMEOUT_SEC = 30.0
_LOCK_RETRY_INTERVAL_SEC = 0.05


def lock_managed_parent(descriptor: int) -> None:
    """锁住已复证的父目录描述符；描述符关闭时由内核自动释放。"""
    try:
        acquire_runtime_flock(
            descriptor,
            shared=False,
            timeout_sec=_LOCK_WAIT_TIMEOUT_SEC,
            retry_interval_sec=_LOCK_RETRY_INTERVAL_SEC,
        )
    except RuntimeFlockTimeoutError as error:
        raise ManagedFileError("等待受管文件协作写锁超时") from error
    except RuntimeFlockUnavailableError as error:
        raise ManagedFileError("受管文件协作写锁不可用") from error
