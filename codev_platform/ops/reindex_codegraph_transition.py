"""CodeGraph systemd 维护动作的共享转换锁适配器。"""
from __future__ import annotations

from collections.abc import Callable, Iterator
from contextlib import AbstractContextManager, contextmanager


TransitionLockFactory = Callable[[], AbstractContextManager[None]]


@contextmanager
def codegraph_systemd_transition(
    *,
    lock_factory: TransitionLockFactory | None = None,
) -> Iterator[None]:
    """在既有全局转换锁内执行单个 CodeGraph systemd 临界区，不新增锁真相。"""
    factory = _default_transition_lock if lock_factory is None else lock_factory
    if not callable(factory):
        raise RuntimeError("CodeGraph systemd 转换锁适配器不可用")
    lock = factory()
    if not hasattr(lock, "__enter__") or not hasattr(lock, "__exit__"):
        raise RuntimeError("CodeGraph systemd 转换锁无效")
    with lock:
        yield


def _default_transition_lock() -> AbstractContextManager[None]:
    """惰性复用 reindex 的唯一跨进程转换锁，避免导入环。"""
    from codev_platform.reindex.maintenance_gate import maintenance_systemd_transition_lock

    return maintenance_systemd_transition_lock()


__all__ = ["codegraph_systemd_transition"]
