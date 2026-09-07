"""运行时组件共享的有界 POSIX flock 机械层。"""

from __future__ import annotations

import errno
import time

from codev_platform.runtime_deadline import RuntimeDeadlineExceeded, child_runtime_deadline


class RuntimeFlockError(RuntimeError):
    """POSIX flock 机械操作失败。"""


class RuntimeFlockTimeoutError(RuntimeFlockError):
    """等待 flock 超过调用链时间预算。"""


class RuntimeFlockUnavailableError(RuntimeFlockError):
    """当前平台或描述符不支持 flock。"""


def acquire_runtime_flock(
    descriptor: int,
    *,
    shared: bool,
    timeout_sec: float,
    retry_interval_sec: float,
) -> None:
    """使用非阻塞重试取得 flock，并服从现有调用链的绝对截止时间。"""
    try:
        import fcntl
    except ImportError as error:
        raise RuntimeFlockUnavailableError("POSIX flock 不可用") from error

    deadline = child_runtime_deadline(timeout_sec)
    operation = (fcntl.LOCK_SH if shared else fcntl.LOCK_EX) | fcntl.LOCK_NB
    while True:
        try:
            deadline.remaining()
            fcntl.flock(descriptor, operation)
            return
        except RuntimeDeadlineExceeded as error:
            raise RuntimeFlockTimeoutError("等待 POSIX flock 超时") from error
        except BlockingIOError:
            pass
        except InterruptedError:
            continue
        except OSError as error:
            if error.errno not in {errno.EACCES, errno.EAGAIN}:
                raise RuntimeFlockUnavailableError("POSIX flock 不可用") from error
        try:
            time.sleep(deadline.bounded_seconds(retry_interval_sec))
        except RuntimeDeadlineExceeded as error:
            raise RuntimeFlockTimeoutError("等待 POSIX flock 超时") from error
