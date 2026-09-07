"""PostgreSQL reindex 队列的只读 schema 契约验证。"""
from __future__ import annotations

from collections.abc import Callable
from typing import Protocol

from codev_platform.reindex.pg_queue_sql import (
    QUEUE_CLAIM_INDEX_COLUMNS,
    QUEUE_COLUMN_CONTRACT,
    QUEUE_PRIMARY_KEY,
)


class PgQueueSchemaContractError(OSError):
    """队列表结构或运行态权限不符合固定契约。"""


class _Result(Protocol):
    def fetchone(self) -> object: ...

    def fetchall(self) -> list[object]: ...


SchemaExecutor = Callable[[str, tuple[object, ...]], _Result]


def verify_queue_schema_contract(
    execute: SchemaExecutor,
    *,
    schema: str,
    table: str,
    qualified_table: str,
) -> None:
    """仅通过目录查询和一条 SELECT 验证现有队列表。"""
    _require_true(
        execute(_SCHEMA_PRIVILEGE_SQL, (schema,)).fetchone(),
        "PG queue schema 权限不足",
    )
    actual_columns = tuple(execute(_COLUMNS_SQL, (schema, table)).fetchall())
    if actual_columns != _expected_columns():
        raise PgQueueSchemaContractError("PG queue 列契约不一致")
    primary_row = execute(_PRIMARY_KEY_SQL, (schema, table)).fetchone()
    if (
        type(primary_row) is not tuple
        or len(primary_row) != 1
        or tuple(primary_row[0] or ()) != QUEUE_PRIMARY_KEY
    ):
        raise PgQueueSchemaContractError("PG queue 主键契约不一致")
    index_row = execute(
        _CLAIM_INDEX_SQL,
        (schema, table, list(QUEUE_CLAIM_INDEX_COLUMNS)),
    ).fetchone()
    _require_true(index_row, "PG queue claim 索引契约不一致")
    _require_true(
        execute(_TABLE_SAFETY_SQL, (schema, table)).fetchone(),
        "PG queue 表类型或 RLS 契约不一致",
    )
    _require_true(
        execute(_TABLE_PRIVILEGE_SQL, (schema, table)).fetchone(),
        "PG queue DML 权限不足",
    )
    execute(f"SELECT status FROM {qualified_table} LIMIT 1", ())


def _expected_columns() -> tuple[tuple[str, str, str], ...]:
    return tuple(
        (name, data_type, "YES" if nullable else "NO")
        for name, data_type, nullable in QUEUE_COLUMN_CONTRACT
    )


def _require_true(row: object, message: str) -> None:
    if type(row) is not tuple or row != (True,):
        raise PgQueueSchemaContractError(message)


_SCHEMA_PRIVILEGE_SQL = (
    "SELECT has_schema_privilege(current_user, %s, 'USAGE')"
)
_COLUMNS_SQL = """SELECT column_name, data_type, is_nullable
FROM information_schema.columns
WHERE table_schema = %s AND table_name = %s
ORDER BY ordinal_position"""
_PRIMARY_KEY_SQL = """SELECT array_agg(att.attname ORDER BY keys.ordinality)
FROM pg_catalog.pg_index idx
JOIN pg_catalog.pg_class rel ON rel.oid = idx.indrelid
JOIN pg_catalog.pg_namespace ns ON ns.oid = rel.relnamespace
JOIN LATERAL unnest(idx.indkey) WITH ORDINALITY AS keys(attnum, ordinality) ON TRUE
JOIN pg_catalog.pg_attribute att ON att.attrelid = rel.oid AND att.attnum = keys.attnum
WHERE ns.nspname = %s AND rel.relname = %s AND idx.indisprimary"""
_CLAIM_INDEX_SQL = """SELECT EXISTS (
SELECT 1 FROM pg_catalog.pg_index idx
JOIN pg_catalog.pg_class rel ON rel.oid = idx.indrelid
JOIN pg_catalog.pg_namespace ns ON ns.oid = rel.relnamespace
WHERE ns.nspname = %s AND rel.relname = %s
AND NOT idx.indisprimary AND idx.indisvalid AND idx.indisready
AND idx.indpred IS NULL AND idx.indexprs IS NULL
AND (
    SELECT array_agg(att.attname ORDER BY keys.ordinality)
    FROM unnest(idx.indkey) WITH ORDINALITY AS keys(attnum, ordinality)
    JOIN pg_catalog.pg_attribute att
      ON att.attrelid = rel.oid AND att.attnum = keys.attnum
) = %s::text[])"""
_TABLE_SAFETY_SQL = """SELECT EXISTS (
SELECT 1 FROM pg_catalog.pg_class rel
JOIN pg_catalog.pg_namespace ns ON ns.oid = rel.relnamespace
WHERE ns.nspname = %s AND rel.relname = %s AND rel.relkind = 'r'
AND NOT rel.relrowsecurity AND NOT rel.relforcerowsecurity)"""
_TABLE_PRIVILEGE_SQL = (
    "SELECT has_table_privilege(current_user, "
    "to_regclass(format('%I.%I', %s, %s)), "
    "'SELECT,INSERT,UPDATE,DELETE')"
)


__all__ = [
    "PgQueueSchemaContractError",
    "SchemaExecutor",
    "verify_queue_schema_contract",
]
