"""Pg queue 的物理目标冻结、运行时验证与显式 DDL 迁移。"""
from __future__ import annotations

from codev_platform.reindex.pg_queue_binding import (
    FrozenPgQueueBinding,
    freeze_pg_queue_binding,
)
from codev_platform.reindex.pg_queue_schema_contract import (
    verify_queue_schema_contract,
)
from codev_platform.reindex.pg_queue_sql import alter_table_sql, create_table_sql
from codev_platform.reindex.pg_queue_tx import (
    PgOperationBudget,
    bounded_connection,
    execute_with_budget,
)
from codev_platform.reindex.queue_ports import QueueOperationTimeout


class PgQueueInitializationError(RuntimeError):
    """PG queue 初始化或物理目标冻结失败，且不携带连接串。"""


def build_queue_pool(
    connection_pool_factory,
    dsn: str,
    *,
    max_size: int,
    pool_timeout: float,
):
    """构造连接池，并隔离底层可能回显 DSN 的异常。"""
    pool_created = False
    pool = None
    try:
        pool = connection_pool_factory(
            dsn,
            min_size=1,
            max_size=max_size,
            open=False,
            timeout=pool_timeout,
        )
        pool_created = True
    except Exception:  # noqa: BLE001 - 连接池异常可能回显原始 DSN
        pass
    if not pool_created:
        raise PgQueueInitializationError("PG queue 连接池创建失败") from None
    return pool


def _frozen_index_sql(table_reference: str, index_reference: str) -> str:
    """用已冻结的安全限定标识建立索引，不依赖 search_path。"""
    return (
        f"CREATE INDEX IF NOT EXISTS {index_reference} "
        f"ON {table_reference} (status, enqueued_at)"
    )


def _freeze_physical_target(queue: object, conn, operation: PgOperationBudget):
    """在首次已连接事务读取 current_schema 并一次性冻结物理目标。"""
    frozen = getattr(queue, "_frozen_binding", None)
    if frozen is not None:
        return frozen
    row = execute_with_budget(conn, operation, "SELECT current_schema()").fetchone()
    if type(row) is not tuple or len(row) != 1 or type(row[0]) is not str:
        raise ValueError("PG queue 当前 schema 读取结果无效")
    frozen = freeze_pg_queue_binding(
        getattr(queue, "_binding_base", None),
        row[0],
        getattr(queue, "_bare_table", None),
    )
    queue._frozen_binding = frozen
    queue._t = frozen.qualified_table
    queue._backend_locator = frozen.locator
    return frozen


def _open_pool(queue: object, operation: PgOperationBudget) -> None:
    if not getattr(queue, "_pool_started", False):
        queue._pool.open(wait=False, timeout=operation.remaining())
        queue._pool_started = True


def _verify_frozen_schema(
    queue: object,
    conn: object,
    operation: PgOperationBudget,
    frozen: FrozenPgQueueBinding,
) -> None:
    def _execute(sql: str, params: tuple[object, ...]):
        return execute_with_budget(conn, operation, sql, params)

    verify_queue_schema_contract(
        _execute,
        schema=frozen.schema,
        table=frozen.table,
        qualified_table=frozen.qualified_table,
    )


def open_and_verify_queue_schema(queue: object, operation: PgOperationBudget) -> None:
    """打开连接并只读验证既有表；运行时绝不执行 DDL。"""
    failed = False
    try:
        _open_pool(queue, operation)
        with bounded_connection(queue._pool, operation) as conn:
            frozen = _freeze_physical_target(queue, conn, operation)
            _verify_frozen_schema(queue, conn, operation, frozen)
        queue._opened = True
    except QueueOperationTimeout:
        raise
    except Exception:  # noqa: BLE001 - 仅向调用方暴露无秘密的初始化错误
        failed = True
    if failed:
        raise PgQueueInitializationError("PG queue schema 验证失败") from None


def migrate_queue_schema(queue: object, operation: PgOperationBudget) -> None:
    """显式创建/补齐队列表；仅供迁移或隔离测试准备阶段调用。"""
    failed = False
    try:
        _open_pool(queue, operation)
        with bounded_connection(queue._pool, operation) as conn:
            frozen = _freeze_physical_target(queue, conn, operation)
            execute_with_budget(conn, operation, create_table_sql(queue._t))
            execute_with_budget(conn, operation, alter_table_sql(queue._t))
            execute_with_budget(
                conn,
                operation,
                _frozen_index_sql(queue._t, frozen.qualified_index),
            )
            _verify_frozen_schema(queue, conn, operation, frozen)
        queue._opened = True
    except QueueOperationTimeout:
        raise
    except Exception:  # noqa: BLE001 - 仅向调用方暴露无秘密的初始化错误
        failed = True
    if failed:
        raise PgQueueInitializationError("PG queue schema 显式迁移失败") from None


__all__ = [
    "PgQueueInitializationError",
    "build_queue_pool",
    "migrate_queue_schema",
    "open_and_verify_queue_schema",
]
