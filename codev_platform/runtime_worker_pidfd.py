"""root-fd worker 的 Linux pidfd 身份、状态与终止机械原语。"""

from __future__ import annotations

import os
import select
import signal


_PIDFD_LINK = "anon_inode:[pidfd]"


class RuntimeWorkerPidfdError(RuntimeError):
    """root-fd worker 无法证明或操作 Linux pidfd。"""


def require_pidfd_capability() -> None:
    """pidfd 是父死亡与主进程回收的必要能力，不允许静默降级。"""
    if not callable(getattr(os, "pidfd_open", None)) or not callable(
        getattr(signal, "pidfd_send_signal", None)
    ):
        raise RuntimeWorkerPidfdError("root-fd worker 缺少 Linux pidfd 能力")


def open_current_pidfd() -> int:
    """打开当前进程的不可继承 pidfd。"""
    return open_process_pidfd(os.getpid(), "当前进程")


def open_process_pidfd(process_id: int, label: str) -> int:
    """以不可复用的内核对象绑定指定 PID；失败时不返回弱身份。"""
    require_pidfd_capability()
    if type(process_id) is not int or process_id <= 0 or type(label) is not str or not label:
        raise RuntimeWorkerPidfdError("root-fd worker pidfd 目标无效")
    descriptor = -1
    try:
        descriptor = os.pidfd_open(process_id)
        require_pidfd_descriptor(descriptor)
        os.set_inheritable(descriptor, False)
        return descriptor
    except RuntimeWorkerPidfdError:
        _close_quietly(descriptor)
        raise
    except (OSError, ValueError) as error:
        _close_quietly(descriptor)
        raise RuntimeWorkerPidfdError(f"root-fd worker 无法打开{label} pidfd") from error


def require_pidfd_descriptor(descriptor: int) -> None:
    """拒绝 pipe、普通文件或伪造 descriptor 充当进程身份。"""
    if type(descriptor) is not int or descriptor < 3:
        raise RuntimeWorkerPidfdError("root-fd worker pidfd descriptor 无效")
    try:
        target = os.readlink(f"/proc/self/fd/{descriptor}")
    except OSError as error:
        raise RuntimeWorkerPidfdError("root-fd worker pidfd descriptor 不可读取") from error
    if target != _PIDFD_LINK:
        raise RuntimeWorkerPidfdError("root-fd worker descriptor 不是 pidfd")


def pidfd_is_ready(descriptor: int) -> bool:
    """pidfd 就绪即表示其绑定的精确进程已退出。"""
    require_pidfd_descriptor(descriptor)
    try:
        readable, _writable, _exceptional = select.select((descriptor,), (), (), 0)
    except (OSError, ValueError) as error:
        raise RuntimeWorkerPidfdError("root-fd worker pidfd 状态不可读取") from error
    return bool(readable)


def require_pidfd_not_ready(descriptor: int, label: str) -> None:
    """交接期间拒绝已退出的 peer，避免 PID 重用伪造 cgroup 证明。"""
    if type(label) is not str or not label:
        raise RuntimeWorkerPidfdError("root-fd worker pidfd 标签无效")
    if pidfd_is_ready(descriptor):
        raise RuntimeWorkerPidfdError(f"root-fd worker {label} 已退出")


def terminate_pidfd(descriptor: int) -> bool:
    """尽力终止精确进程；目标已退出等价于所需终态。"""
    require_pidfd_descriptor(descriptor)
    sender = getattr(signal, "pidfd_send_signal", None)
    if not callable(sender):
        return False
    try:
        sender(descriptor, signal.SIGKILL)
    except ProcessLookupError:
        return True
    except OSError:
        return False
    return True


def _close_quietly(descriptor: int) -> None:
    if descriptor < 0:
        return
    try:
        os.close(descriptor)
    except OSError:
        pass


__all__ = [
    "RuntimeWorkerPidfdError",
    "open_current_pidfd",
    "open_process_pidfd",
    "pidfd_is_ready",
    "require_pidfd_capability",
    "require_pidfd_descriptor",
    "require_pidfd_not_ready",
    "terminate_pidfd",
]
