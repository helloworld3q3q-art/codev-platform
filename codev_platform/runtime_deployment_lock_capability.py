"""deployment flock 持有期内可验证的短生命周期能力。"""

from __future__ import annotations

import os
from dataclasses import dataclass
from contextlib import contextmanager
from collections.abc import Iterator
from dataclasses import field
from threading import RLock
from weakref import WeakKeyDictionary

from codev_platform.runtime_root_binding import (
    BoundRuntimeRoot,
    RuntimeRootBindingError,
)


class RuntimeDeploymentLockCapabilityError(RuntimeError):
    """deployment-lock capability 未由持锁临界区签发或已经失效。"""


@dataclass(slots=True)
class _DeploymentLockState:
    """私有注册表中的单次签发状态。"""

    root: BoundRuntimeRoot
    descriptor: int
    issuer_pid: int
    active: bool = True
    lifecycle_lock: RLock = field(default_factory=RLock)


class BoundDeploymentLock:
    """仅由 ``deployment_lock_at()`` 在真实 flock 临界区签发的能力。"""

    __slots__ = ("__weakref__",)

    def __init__(self) -> None:
        raise TypeError("BoundDeploymentLock 只能由 deployment_lock_at() 签发")

    def require_active(self, root: BoundRuntimeRoot) -> None:
        """确认能力尚未吊销且仍绑定同一个存活根租约。"""
        state = _state_for(self, root)
        with state.lifecycle_lock:
            _require_active_state(state, root)

    @contextmanager
    def hold_active(self, root: BoundRuntimeRoot) -> Iterator[None]:
        """覆盖完整领域操作，阻止吊销与 flock 释放插入中间。"""
        state = _state_for(self, root)
        with state.lifecycle_lock:
            _require_active_state(state, root)
            yield


_CAPABILITIES: WeakKeyDictionary[BoundDeploymentLock, _DeploymentLockState] = WeakKeyDictionary()
_CAPABILITIES_LOCK = RLock()


def _state_for(
    capability: BoundDeploymentLock,
    root: BoundRuntimeRoot,
) -> _DeploymentLockState:
    if type(root) is not BoundRuntimeRoot:
        raise RuntimeDeploymentLockCapabilityError("根租约类型无效")
    with _CAPABILITIES_LOCK:
        state = _CAPABILITIES.get(capability)
    if state is None:
        raise RuntimeDeploymentLockCapabilityError(
            "deployment-lock capability 已失效或未经签发",
        )
    if state.root is not root:
        raise RuntimeDeploymentLockCapabilityError(
            "deployment-lock capability 与根租约不一致",
        )
    return state


def _require_active_state(
    state: _DeploymentLockState,
    root: BoundRuntimeRoot,
) -> None:
    if not state.active:
        raise RuntimeDeploymentLockCapabilityError(
            "deployment-lock capability 已失效或未经签发",
        )
    if state.issuer_pid != os.getpid():
        raise RuntimeDeploymentLockCapabilityError(
            "deployment-lock capability 不能跨进程继承",
        )
    try:
        os.fstat(state.descriptor)
    except OSError as error:
        raise RuntimeDeploymentLockCapabilityError(
            "deployment-lock capability 的锁文件描述符已失效",
        ) from error
    try:
        root.verify_visible()
    except RuntimeRootBindingError as error:
        raise RuntimeDeploymentLockCapabilityError(
            "deployment-lock capability 的根租约已失效",
        ) from error


def _issue_bound_deployment_lock(
    root: BoundRuntimeRoot,
    descriptor: int,
) -> BoundDeploymentLock:
    """仅供持锁的 storage 私有临界区签发一次能力。"""
    if type(root) is not BoundRuntimeRoot:
        raise RuntimeDeploymentLockCapabilityError("根租约类型无效")
    if type(descriptor) is not int or descriptor < 0:
        raise RuntimeDeploymentLockCapabilityError("锁文件描述符无效")
    try:
        os.fstat(descriptor)
    except OSError as error:
        raise RuntimeDeploymentLockCapabilityError("锁文件描述符不可用") from error
    capability = object.__new__(BoundDeploymentLock)
    with _CAPABILITIES_LOCK:
        _CAPABILITIES[capability] = _DeploymentLockState(
            root=root,
            descriptor=descriptor,
            issuer_pid=os.getpid(),
        )
    return capability


def _revoke_bound_deployment_lock(capability: BoundDeploymentLock) -> None:
    """由 storage 在 flock 释放前吊销能力，阻止离开 gate 后重放。"""
    if type(capability) is not BoundDeploymentLock:
        raise RuntimeDeploymentLockCapabilityError("deployment-lock capability 类型无效")
    with _CAPABILITIES_LOCK:
        state = _CAPABILITIES.get(capability)
    if state is None:
        raise RuntimeDeploymentLockCapabilityError(
            "deployment-lock capability 未处于可吊销状态",
        )
    with state.lifecycle_lock:
        if not state.active:
            raise RuntimeDeploymentLockCapabilityError(
                "deployment-lock capability 未处于可吊销状态",
            )
        state.active = False
    with _CAPABILITIES_LOCK:
        if _CAPABILITIES.get(capability) is state:
            del _CAPABILITIES[capability]


def _clear_capabilities_after_fork() -> None:
    """子进程不得继承父进程的活跃 flock capability 或锁文件 fd。"""
    global _CAPABILITIES, _CAPABILITIES_LOCK
    for state in tuple(_CAPABILITIES.values()):
        if not state.active:
            continue
        state.active = False
        try:
            os.close(state.descriptor)
        except OSError:
            pass
    _CAPABILITIES = WeakKeyDictionary()
    _CAPABILITIES_LOCK = RLock()


if hasattr(os, "register_at_fork"):
    os.register_at_fork(after_in_child=_clear_capabilities_after_fork)


__all__ = [
    "BoundDeploymentLock",
    "RuntimeDeploymentLockCapabilityError",
]
