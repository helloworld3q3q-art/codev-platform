"""运行时共享截止时间与有界 flock 回归测试。"""
from __future__ import annotations

import errno
import sys
from types import SimpleNamespace

import pytest


class _Clock:
    def __init__(self) -> None:
        self.now = 0.0

    def __call__(self) -> float:
        return self.now

    def sleep(self, seconds: float) -> None:
        self.now += seconds


def test_nested_deadline_scope_never_extends_parent(monkeypatch: pytest.MonkeyPatch) -> None:
    import codev_platform.runtime_deadline as deadline

    clock = _Clock()
    monkeypatch.setattr(deadline.time, "monotonic", clock)

    with deadline.runtime_deadline_scope(10.0) as parent:
        clock.now = 4.0
        with deadline.runtime_deadline_scope(30.0) as child:
            assert child.deadline == parent.deadline == 10.0
            assert deadline.bounded_runtime_timeout(20.0) == 6.0

    assert deadline.current_runtime_deadline() is None


def test_expired_scope_fails_and_always_restores_context(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import codev_platform.runtime_deadline as deadline

    clock = _Clock()
    monkeypatch.setattr(deadline.time, "monotonic", clock)

    with pytest.raises(deadline.RuntimeDeadlineExceeded):
        with deadline.runtime_deadline_scope(1.0):
            clock.now = 1.0
            deadline.bounded_runtime_timeout(5.0)

    assert deadline.current_runtime_deadline() is None


def test_flock_retries_nonblocking_until_shared_deadline(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import codev_platform.runtime_deadline as deadline
    import codev_platform.runtime_storage as storage
    import codev_platform._runtime_flock as flock_module

    clock = _Clock()
    calls: list[int] = []

    def flock(_descriptor: int, operation: int) -> None:
        calls.append(operation)
        raise BlockingIOError(errno.EAGAIN, "busy")

    fake_fcntl = SimpleNamespace(
        LOCK_SH=1,
        LOCK_EX=2,
        LOCK_NB=4,
        flock=flock,
    )
    monkeypatch.setitem(sys.modules, "fcntl", fake_fcntl)
    monkeypatch.setattr(deadline.time, "monotonic", clock)
    monkeypatch.setattr(flock_module.time, "sleep", clock.sleep)

    with pytest.raises(storage.RuntimeStorageLockError, match="时间预算"):
        with deadline.runtime_deadline_scope(0.11):
            storage._flock(7, shared=False)

    assert len(calls) == 3
    assert all(operation == fake_fcntl.LOCK_EX | fake_fcntl.LOCK_NB for operation in calls)


def test_flock_succeeds_after_bounded_contention(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import codev_platform.runtime_deadline as deadline
    import codev_platform.runtime_storage as storage
    import codev_platform._runtime_flock as flock_module

    clock = _Clock()
    attempts = 0

    def flock(_descriptor: int, _operation: int) -> None:
        nonlocal attempts
        attempts += 1
        if attempts < 3:
            raise BlockingIOError(errno.EAGAIN, "busy")

    monkeypatch.setitem(
        sys.modules,
        "fcntl",
        SimpleNamespace(LOCK_SH=1, LOCK_EX=2, LOCK_NB=4, flock=flock),
    )
    monkeypatch.setattr(deadline.time, "monotonic", clock)
    monkeypatch.setattr(flock_module.time, "sleep", clock.sleep)

    with deadline.runtime_deadline_scope(1.0):
        storage._flock(7, shared=True)

    assert attempts == 3
    assert clock.now == pytest.approx(0.1)
