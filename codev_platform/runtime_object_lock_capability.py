"""对象 ID flock 持有期内可验证的短生命周期能力。"""

from __future__ import annotations

import os
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass, field
from threading import RLock
from weakref import WeakKeyDictionary

from codev_platform.runtime_root_binding import (
    BoundRuntimeRoot,
    RuntimeRootBindingError,
)


class RuntimeObjectLockCapabilityError(RuntimeError):
    """对象锁 capability 未由真实 flock 临界区签发或已经失效。"""


@dataclass(slots=True)
class _ObjectLockState:
    """私有注册表中的单次对象锁签发状态。"""

    root: BoundRuntimeRoot
    kind: str
    object_id: str
    descriptor: int
    shared: bool
    issuer_pid: int
    active: bool = True
    lifecycle_lock: RLock = field(default_factory=RLock)


class BoundRuntimeObjectLock:
    """仅由 ``id_lock_at()`` 在真实 flock 临界区签发的对象能力。"""

    __slots__ = ("__weakref__",)

    def __init__(self) -> None:
        raise TypeError("BoundRuntimeObjectLock 只能由 id_lock_at() 签发")

    @contextmanager
    def hold_active(
        self,
        *,
        kind: str,
        object_id: str,
        exclusive: bool,
    ) -> Iterator[BoundRuntimeRoot]:
        """覆盖完整受管操作，校验目标身份、根租约与排他性。"""
        state = _state_for(self)
        with state.lifecycle_lock:
            _require_active_state(state)
            _require_target(state, kind, object_id, exclusive=exclusive)
            yield state.root


_CAPABILITIES: WeakKeyDictionary[BoundRuntimeObjectLock, _ObjectLockState] = WeakKeyDictionary()
_CAPABILITIES_LOCK = RLock()


def _state_for(capability: BoundRuntimeObjectLock) -> _ObjectLockState:
    if type(capability) is not BoundRuntimeObjectLock:
        raise RuntimeObjectLockCapabilityError("对象锁 capability 类型无效")
    with _CAPABILITIES_LOCK:
        state = _CAPABILITIES.get(capability)
    if state is None:
        raise RuntimeObjectLockCapabilityError("对象锁 capability 未经签发或已失效")
    return state


def _require_active_state(state: _ObjectLockState) -> None:
    if not state.active:
        raise RuntimeObjectLockCapabilityError("对象锁 capability 未经签发或已失效")
    if state.issuer_pid != os.getpid():
        raise RuntimeObjectLockCapabilityError("对象锁 capability 不能跨进程继承")
    try:
        os.fstat(state.descriptor)
    except OSError as error:
        raise RuntimeObjectLockCapabilityError("对象锁文件 descriptor 已失效") from error
    try:
        state.root.verify_visible()
    except RuntimeRootBindingError as error:
        raise RuntimeObjectLockCapabilityError("对象锁 capability 的根租约已失效") from error


def _require_target(
    state: _ObjectLockState,
    kind: str,
    object_id: str,
    *,
    exclusive: bool,
) -> None:
    if type(kind) is not str or type(object_id) is not str:
        raise RuntimeObjectLockCapabilityError("对象锁 capability 身份无效")
    if (state.kind, state.object_id) != (kind, object_id):
        raise RuntimeObjectLockCapabilityError("对象锁 capability 与目标身份不一致")
    if type(exclusive) is not bool:
        raise RuntimeObjectLockCapabilityError("对象锁 capability 排他性请求无效")
    if exclusive and state.shared:
        raise RuntimeObjectLockCapabilityError("对象隔离必须持有排他对象锁")


def _issue_bound_runtime_object_lock(
    root: BoundRuntimeRoot,
    kind: str,
    object_id: str,
    descriptor: int,
    *,
    shared: bool,
) -> BoundRuntimeObjectLock:
    """仅供 storage 在真实对象 flock 已取得后签发一次能力。"""
    if type(root) is not BoundRuntimeRoot:
        raise RuntimeObjectLockCapabilityError("根租约类型无效")
    if type(kind) is not str or type(object_id) is not str or type(shared) is not bool:
        raise RuntimeObjectLockCapabilityError("对象锁 capability 参数无效")
    if type(descriptor) is not int or descriptor < 0:
        raise RuntimeObjectLockCapabilityError("对象锁文件 descriptor 无效")
    try:
        os.fstat(descriptor)
    except OSError as error:
        raise RuntimeObjectLockCapabilityError("对象锁文件 descriptor 不可用") from error
    capability = object.__new__(BoundRuntimeObjectLock)
    with _CAPABILITIES_LOCK:
        _CAPABILITIES[capability] = _ObjectLockState(
            root=root,
            kind=kind,
            object_id=object_id,
            descriptor=descriptor,
            shared=shared,
            issuer_pid=os.getpid(),
        )
    return capability


def _revoke_bound_runtime_object_lock(capability: BoundRuntimeObjectLock) -> None:
    """由 storage 在 flock 释放前吊销能力，阻止离开临界区后重放。"""
    state = _state_for(capability)
    with state.lifecycle_lock:
        if not state.active:
            raise RuntimeObjectLockCapabilityError("对象锁 capability 未处于可吊销状态")
        state.active = False
    with _CAPABILITIES_LOCK:
        if _CAPABILITIES.get(capability) is state:
            del _CAPABILITIES[capability]


def _clear_capabilities_after_fork() -> None:
    """子进程不得继承父进程活跃的对象锁 capability 或锁 descriptor。"""
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
    "BoundRuntimeObjectLock",
    "RuntimeObjectLockCapabilityError",
]
