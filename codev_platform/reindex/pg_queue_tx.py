"""Pg 队列单次操作的共享 deadline、事务 timeout 与异常归一。"""
from __future__ import annotations

import math
import time
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from dataclasses import dataclass, field
from types import TracebackType
from typing import NoReturn

from codev_platform.reindex.queue_ports import QueueOperationTimeout, validate_timeout

_TIMEOUT_SQL = (
    "SELECT set_config('statement_timeout', %s, true), "
    "set_config('lock_timeout', %s, true), "
    "set_config('idle_in_transaction_session_timeout', %s, true)"
)
_MIN_SERVER_BUDGET_MS = 4.0
_PG_TIMEOUT_STATES = {"57014", "55P03", "25P03"}


class RollbackTransaction(RuntimeError):
    """请求连接 context 回滚当前事务的内部控制信号。"""


@dataclass(frozen=True, slots=True)
class PgOperationBudget:
    """一次公开操作唯一拥有的 monotonic 预算。"""

    deadline: float
    _clock: Callable[[], float] = field(repr=False, compare=False)

    @classmethod
    def start(
        cls,
        timeout_sec: float,
        *,
        clock: Callable[[], float] | None = None,
    ) -> PgOperationBudget:
        resolved_clock = clock or time.monotonic
        timeout = validate_timeout(timeout_sec)
        return cls(resolved_clock() + timeout, resolved_clock)

    def remaining(self) -> float:
        remaining = self.deadline - self._clock()
        if not math.isfinite(remaining) or remaining <= 0:
            raise QueueOperationTimeout("Pg 队列操作超过时间预算")
        return remaining

    def server_timeouts(self) -> tuple[str, str, str]:
        remaining_ms = self.remaining() * 1000.0
        if remaining_ms <= _MIN_SERVER_BUDGET_MS:
            raise QueueOperationTimeout("Pg 队列剩余预算不足以设置服务端 timeout")
        statement_ms = max(2, math.floor(remaining_ms * 0.80))
        lock_ms = max(1, math.floor(remaining_ms * 0.40))
        idle_ms = max(3, math.floor(remaining_ms * 0.90))
        return tuple(f"{value}ms" for value in (statement_ms, lock_ms, idle_ms))


def _is_pg_timeout(exc: Exception) -> bool:
    if getattr(exc, "sqlstate", None) in _PG_TIMEOUT_STATES:
        return True
    if type(exc).__name__ == "PoolTimeout":
        return True
    try:
        from psycopg_pool import PoolTimeout
    except ImportError:
        return False
    return isinstance(exc, PoolTimeout)


def configure_transaction(conn, budget: PgOperationBudget) -> None:
    """按当前剩余预算设置仅对本事务生效的三个 PG timeout。"""
    conn.execute(_TIMEOUT_SQL, budget.server_timeouts())
    budget.remaining()


def _raise_normalized(exc: Exception) -> None:
    if _is_pg_timeout(exc):
        raise QueueOperationTimeout("Pg 队列数据库操作超时") from None
    raise exc


def execute_with_budget(conn, budget: PgOperationBudget, sql: str, params=()):
    """按最新剩余预算刷新 GUC、执行一条 SQL，并在返回前复核 deadline。"""
    try:
        configure_transaction(conn, budget)
        cursor = conn.execute(sql, params)
        budget.remaining()
        return cursor
    except QueueOperationTimeout:
        raise
    except Exception as exc:
        _raise_normalized(exc)


def _reraise_transaction_failure(
    exc: BaseException,
    *,
    body_error: BaseException | None,
    body_traceback: TracebackType | None,
    budget: PgOperationBudget,
) -> NoReturn:
    if body_error is None:
        if isinstance(exc, QueueOperationTimeout):
            raise exc
        if isinstance(exc, Exception):
            _raise_normalized(exc)
        raise exc
    if exc is body_error:
        if isinstance(body_error, RollbackTransaction):
            budget.remaining()
        raise body_error.with_traceback(body_traceback)
    if isinstance(body_error, RollbackTransaction):
        if isinstance(exc, QueueOperationTimeout):
            raise exc
        if isinstance(exc, Exception):
            _raise_normalized(exc)
        raise exc
    body_error.add_note(f"Pg 事务清理异常: {type(exc).__name__}")
    raise body_error.with_traceback(body_traceback) from exc


@contextmanager
def bounded_connection(pool, budget: PgOperationBudget) -> Iterator[object]:
    """取得有界连接并保持异常即回滚的唯一事务 context。"""
    body_error: BaseException | None = None
    body_traceback = None
    try:
        connection_context = pool.connection(timeout=budget.remaining())
        with connection_context as conn:
            configure_transaction(conn, budget)
            try:
                yield conn
            except BaseException as exc:
                body_error = exc
                body_traceback = exc.__traceback__
                raise
            configure_transaction(conn, budget)
            budget.remaining()
    except BaseException as exc:
        _reraise_transaction_failure(
            exc,
            body_error=body_error,
            body_traceback=body_traceback,
            budget=budget,
        )
    if body_error is not None:
        _reraise_transaction_failure(
            body_error,
            body_error=body_error,
            body_traceback=body_traceback,
            budget=budget,
        )
    budget.remaining()


__all__ = [
    "PgOperationBudget", "RollbackTransaction", "bounded_connection",
    "configure_transaction", "execute_with_budget",
]
