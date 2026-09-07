"""Pg 队列测试共用的无数据库事务脚本 fake。"""
from __future__ import annotations

from collections.abc import Callable

TEST_TABLE = "reindex_jobs_test"


class FakeCursor:
    def __init__(self, rows=(), rowcount=0):
        self._rows = list(rows)
        self.rowcount = rowcount

    def fetchall(self):
        return list(self._rows)

    def fetchone(self):
        return self._rows[0] if self._rows else None


class FakeConn:
    def __init__(self, responses=()):
        self.responses = list(responses)
        self.calls: list[tuple[str, tuple]] = []
        self.control_calls: list[tuple[str, tuple]] = []
        self.commits = 0
        self.rollbacks = 0
        self._explicit_rollback = False

    def execute(self, sql, params=()):
        expected = sql.count("%s")
        if expected != len(params):
            raise AssertionError(
                f"SQL 占位符数量 {expected} 与参数数量 {len(params)} 不一致"
            )
        if "set_config(" in sql:
            self.control_calls.append((sql, params))
            return FakeCursor(rowcount=1)
        self.calls.append((sql, params))
        if not self.responses:
            raise AssertionError("出现未编排的 execute 调用")
        response = self.responses.pop(0)
        if isinstance(response, BaseException):
            raise response
        if isinstance(response, Callable):
            response = response(sql, params)
        return response

    def rollback(self):
        self.rollbacks += 1
        self._explicit_rollback = True


class FakePoolConnContext:
    def __init__(self, conn):
        self._conn = conn

    def __enter__(self):
        self._conn._explicit_rollback = False
        return self._conn

    def __exit__(self, exc_type, _exc, _tb):
        if exc_type is not None:
            self._conn.rollbacks += 1
        elif not self._conn._explicit_rollback:
            self._conn.commits += 1
        return False


class FakePool:
    def __init__(self, conn):
        self._conn = conn
        self.opened = False
        self.connection_timeouts: list[float | None] = []

    def open(self, *, wait=False, timeout=None):
        self.opened = True

    def connection(self, *, timeout=None):
        self.connection_timeouts.append(timeout)
        return FakePoolConnContext(self._conn)

    def close(self):
        self.opened = False


def unit_queue(*responses):
    from codev_platform.reindex.pg_queue import PgJobQueue

    queue = PgJobQueue.__new__(PgJobQueue)
    queue._t = TEST_TABLE
    queue._pool = FakePool(FakeConn(responses))
    queue._lease_ttl = 1800.0
    queue._owner = "unit-owner"
    queue._opened = True
    queue._ensure = lambda _budget=None: None
    return queue


def schema_verification_responses(
    *,
    schema: str = "public",
    include_schema: bool = True,
) -> list[FakeCursor]:
    """生成运行期只读 schema 验证的标准响应脚本。"""
    from codev_platform.reindex.pg_queue_sql import (
        QUEUE_COLUMN_CONTRACT,
        QUEUE_PRIMARY_KEY,
    )

    responses = []
    if include_schema:
        responses.append(FakeCursor(rows=[(schema,)]))
    responses.extend(
        (
            FakeCursor(rows=[(True,)]),
            FakeCursor(rows=[
                (name, data_type, "YES" if nullable else "NO")
                for name, data_type, nullable in QUEUE_COLUMN_CONTRACT
            ]),
            FakeCursor(rows=[(list(QUEUE_PRIMARY_KEY),)]),
            FakeCursor(rows=[(True,)]),
            FakeCursor(rows=[(True,)]),
            FakeCursor(rows=[(True,)]),
            FakeCursor(),
        )
    )
    return responses


def schema_migration_responses(*, schema: str = "public") -> list[FakeCursor]:
    """生成显式 DDL 三步及迁移后只读复验的响应脚本。"""
    return [
        FakeCursor(rows=[(schema,)]),
        FakeCursor(),
        FakeCursor(),
        FakeCursor(),
        *schema_verification_responses(schema=schema, include_schema=False),
    ]


__all__ = [
    "FakeConn", "FakeCursor", "FakePool", "FakePoolConnContext", "TEST_TABLE",
    "schema_migration_responses", "schema_verification_responses", "unit_queue",
]
