"""运行时预检的只读数据库探针。"""

from __future__ import annotations

from codev_platform.runtime_preflight_contract import ProbeDeadline


_DATABASE_OPERATION_TIMEOUT_SEC = 5
_DATABASE_OPERATION_TIMEOUT_MS = 5_000


def probe_pg_queue_state_readonly(dsn: str, deadline: ProbeDeadline) -> None:
    """只读验证现有队列表、索引、主键与运行态 DML 权限。"""
    from psycopg import connect

    from codev_platform.reindex.pg_queue_binding import (
        freeze_pg_queue_binding,
        pg_binding_base_locator,
    )
    from codev_platform.reindex.pg_queue_schema_contract import (
        verify_queue_schema_contract,
    )

    base_locator = pg_binding_base_locator(dsn)
    deadline.ensure()
    connection = connect(
        dsn,
        connect_timeout=deadline.bounded_seconds(_DATABASE_OPERATION_TIMEOUT_SEC),
    )
    cursor = None
    try:
        deadline.ensure()
        cursor = connection.cursor()
        cursor.execute("SET TRANSACTION READ ONLY")
        timeout_ms = deadline.bounded_milliseconds(_DATABASE_OPERATION_TIMEOUT_MS)
        cursor.execute(f"SET LOCAL statement_timeout = '{timeout_ms}ms'")
        cursor.execute(f"SET LOCAL lock_timeout = '{timeout_ms}ms'")
        cursor.execute("SELECT current_schema()")
        schema_row = cursor.fetchone()
        if (
            type(schema_row) is not tuple
            or len(schema_row) != 1
            or type(schema_row[0]) is not str
            or not schema_row[0]
        ):
            raise OSError("PG queue 当前 schema 无效")
        frozen = freeze_pg_queue_binding(base_locator, schema_row[0], "reindex_jobs")

        def _execute(statement: str, params: tuple[object, ...]):
            cursor.execute(statement, params)
            deadline.ensure()
            return cursor

        verify_queue_schema_contract(
            _execute,
            schema=frozen.schema,
            table=frozen.table,
            qualified_table=frozen.qualified_table,
        )
        deadline.ensure()
    finally:
        try:
            if cursor is not None:
                cursor.close()
        finally:
            try:
                connection.rollback()
            finally:
                connection.close()
__all__ = ["probe_pg_queue_state_readonly"]
