"""运行时构建、验证与切换共享的绝对截止时间。"""
from __future__ import annotations

from collections.abc import Callable, Iterator
from contextlib import contextmanager
from contextvars import ContextVar, Token
from dataclasses import dataclass, field
from functools import wraps
import math
import time
from typing import ParamSpec, TypeVar


class RuntimeDeadlineExceeded(TimeoutError):
    """运行时操作已耗尽唯一的总时间预算。"""


@dataclass(frozen=True, slots=True)
class RuntimeDeadline:
    """基于 monotonic clock、不可延长的绝对截止时间。"""

    deadline: float
    _clock: Callable[[], float] = field(repr=False, compare=False)

    @classmethod
    def after(
        cls,
        timeout_sec: float,
        *,
        clock: Callable[[], float] | None = None,
    ) -> RuntimeDeadline:
        timeout = _positive_seconds(timeout_sec)
        resolved_clock = time.monotonic if clock is None else clock
        now = resolved_clock()
        if not math.isfinite(now):
            raise ValueError("运行时时钟无效")
        return cls(now + timeout, resolved_clock)

    def remaining(self) -> float:
        value = self.deadline - self._clock()
        if not math.isfinite(value) or value <= 0:
            raise RuntimeDeadlineExceeded("运行时操作超过总时间预算")
        return value

    def bounded_seconds(self, maximum: float) -> float:
        return min(_positive_seconds(maximum), self.remaining())

    def child(self, maximum: float) -> RuntimeDeadline:
        """派生只能缩短、不能延长父截止时间的子预算。"""
        now = self._clock()
        if not math.isfinite(now):
            raise RuntimeDeadlineExceeded("运行时时钟无效")
        return RuntimeDeadline(
            min(self.deadline, now + _positive_seconds(maximum)),
            self._clock,
        )


_CURRENT_DEADLINE: ContextVar[RuntimeDeadline | None] = ContextVar(
    "codev_platform_runtime_deadline",
    default=None,
)
_P = ParamSpec("_P")
_R = TypeVar("_R")


def current_runtime_deadline() -> RuntimeDeadline | None:
    """返回当前调用链的总预算；未处于受控操作时返回 ``None``。"""
    return _CURRENT_DEADLINE.get()


def bounded_runtime_timeout(maximum: float) -> float:
    """把局部 timeout 收紧到当前调用链的剩余总预算。"""
    active = current_runtime_deadline()
    limit = _positive_seconds(maximum)
    return limit if active is None else active.bounded_seconds(limit)


def child_runtime_deadline(maximum: float) -> RuntimeDeadline:
    """生成受当前总预算约束的局部截止时间。"""
    active = current_runtime_deadline()
    return (
        RuntimeDeadline.after(maximum)
        if active is None
        else active.child(maximum)
    )


@contextmanager
def runtime_deadline_scope(timeout_sec: float) -> Iterator[RuntimeDeadline]:
    """建立可嵌套预算域；子调用只能缩短父域。"""
    active = current_runtime_deadline()
    deadline = (
        RuntimeDeadline.after(timeout_sec)
        if active is None
        else active.child(timeout_sec)
    )
    token: Token[RuntimeDeadline | None] = _CURRENT_DEADLINE.set(deadline)
    try:
        yield deadline
    finally:
        _CURRENT_DEADLINE.reset(token)


def runtime_operation_timeout(
    timeout_sec: float,
) -> Callable[[Callable[_P, _R]], Callable[_P, _R]]:
    """为同步入口绑定唯一总预算，避免嵌套步骤重复计时。"""
    timeout = _positive_seconds(timeout_sec)

    def _decorate(function: Callable[_P, _R]) -> Callable[_P, _R]:
        @wraps(function)
        def _wrapped(*args: _P.args, **kwargs: _P.kwargs) -> _R:
            with runtime_deadline_scope(timeout):
                return function(*args, **kwargs)

        return _wrapped

    return _decorate


def _positive_seconds(value: object) -> float:
    if (
        isinstance(value, bool)
        or not isinstance(value, (int, float))
        or not math.isfinite(float(value))
        or float(value) <= 0
    ):
        raise ValueError("运行时 timeout 必须是有限正数")
    return float(value)


__all__ = [
    "RuntimeDeadline",
    "RuntimeDeadlineExceeded",
    "bounded_runtime_timeout",
    "child_runtime_deadline",
    "current_runtime_deadline",
    "runtime_deadline_scope",
    "runtime_operation_timeout",
]
