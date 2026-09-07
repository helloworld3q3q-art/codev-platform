"""PostgreSQL 受管表、列、约束、索引与序列的规范目录指纹。"""

from __future__ import annotations

import hashlib
import json
import re
from collections.abc import Iterable

from codev_platform.web.db import tables
from codev_platform.web.db.migration_contract import (
    DatabaseMigrationError,
    SchemaFingerprint,
)


_IDENTIFIER = re.compile(r"[a-z_][a-z0-9_]{0,62}\Z")
MANAGED_TABLE_NAMES = tuple(sorted(tables.metadata.tables))


def capture_schema_fingerprint(
    connection: object,
    schema: str,
    *,
    managed_tables: Iterable[str] = MANAGED_TABLE_NAMES,
) -> SchemaFingerprint:
    """只读采集固定受管关系；schema 名不进入摘要，参考 schema 可直接比较。"""
    schema_name = require_pg_identifier(schema, "数据库 schema")
    names = _require_table_names(managed_tables)
    execute = getattr(connection, "exec_driver_sql", None)
    if not callable(execute):
        raise DatabaseMigrationError("数据库目录连接无效")
    sections: list[tuple[str, tuple[object, ...]]] = []
    try:
        for label, sql in _CATALOG_QUERIES:
            rows = execute(sql, (schema_name, list(names))).fetchall()
            sections.append((label, tuple(_normalize_row(row) for row in rows)))
    except (KeyboardInterrupt, SystemExit, MemoryError):
        raise
    except Exception:
        raise DatabaseMigrationError("数据库 schema 指纹无法读取") from None
    table_rows = sections[0][1]
    payload = json.dumps(
        sections,
        ensure_ascii=True,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return SchemaFingerprint(
        hashlib.sha256(payload).hexdigest(),
        table_count=len(table_rows),
    )


def require_pg_identifier(value: object, label: str) -> str:
    if type(value) is not str or _IDENTIFIER.fullmatch(value) is None:
        raise DatabaseMigrationError(f"{label}无效")
    return value


def quote_pg_identifier(value: object, label: str) -> str:
    return f'"{require_pg_identifier(value, label)}"'


def _require_table_names(values: Iterable[str]) -> tuple[str, ...]:
    try:
        names = tuple(values)
    except TypeError:
        raise DatabaseMigrationError("数据库受管表集合无效") from None
    if (
        not names
        or len(names) != len(set(names))
        or any(require_pg_identifier(name, "数据库受管表") != name for name in names)
    ):
        raise DatabaseMigrationError("数据库受管表集合无效")
    return names


def _normalize_row(row: object) -> tuple[object, ...]:
    try:
        return tuple(_normalize_value(value) for value in row)  # type: ignore[union-attr]
    except TypeError:
        raise DatabaseMigrationError("数据库目录返回格式无效") from None


def _normalize_value(value: object) -> object:
    if isinstance(value, (list, tuple)):
        return tuple(_normalize_value(item) for item in value)
    if value is None or type(value) in {str, int, bool, float}:
        return value
    return str(value)


_TABLES_SQL = """SELECT rel.relname, rel.relkind, rel.relrowsecurity, rel.relforcerowsecurity
FROM pg_catalog.pg_class rel
JOIN pg_catalog.pg_namespace ns ON ns.oid = rel.relnamespace
WHERE ns.nspname = %s AND rel.relname = ANY(%s)
ORDER BY rel.relname"""
_COLUMNS_SQL = """SELECT rel.relname, att.attname,
pg_catalog.format_type(att.atttypid, att.atttypmod), att.attnotnull,
COALESCE(pg_catalog.pg_get_expr(def.adbin, def.adrelid), '')
FROM pg_catalog.pg_class rel
JOIN pg_catalog.pg_namespace ns ON ns.oid = rel.relnamespace
JOIN pg_catalog.pg_attribute att ON att.attrelid = rel.oid
LEFT JOIN pg_catalog.pg_attrdef def
  ON def.adrelid = att.attrelid AND def.adnum = att.attnum
WHERE ns.nspname = %s AND rel.relname = ANY(%s)
AND att.attnum > 0 AND NOT att.attisdropped
ORDER BY rel.relname, att.attname"""
_CONSTRAINTS_SQL = """SELECT rel.relname, con.contype,
pg_catalog.pg_get_constraintdef(con.oid, true)
FROM pg_catalog.pg_constraint con
JOIN pg_catalog.pg_class rel ON rel.oid = con.conrelid
JOIN pg_catalog.pg_namespace ns ON ns.oid = rel.relnamespace
WHERE ns.nspname = %s AND rel.relname = ANY(%s)
ORDER BY rel.relname, con.contype, pg_catalog.pg_get_constraintdef(con.oid, true)"""
_INDEXES_SQL = """SELECT tbl.relname, idx.relname, ind.indisunique, ind.indisprimary,
COALESCE(pg_catalog.pg_get_expr(ind.indpred, ind.indrelid), ''),
ARRAY(SELECT pg_catalog.pg_get_indexdef(ind.indexrelid, item, true)
      FROM generate_series(1, ind.indnkeyatts) AS item ORDER BY item)
FROM pg_catalog.pg_index ind
JOIN pg_catalog.pg_class tbl ON tbl.oid = ind.indrelid
JOIN pg_catalog.pg_class idx ON idx.oid = ind.indexrelid
JOIN pg_catalog.pg_namespace ns ON ns.oid = tbl.relnamespace
WHERE ns.nspname = %s AND tbl.relname = ANY(%s)
ORDER BY tbl.relname, idx.relname"""
_SEQUENCES_SQL = """SELECT tbl.relname, att.attname, seq.relname
FROM pg_catalog.pg_depend dep
JOIN pg_catalog.pg_class seq ON seq.oid = dep.objid AND seq.relkind = 'S'
JOIN pg_catalog.pg_class tbl ON tbl.oid = dep.refobjid
JOIN pg_catalog.pg_namespace ns ON ns.oid = tbl.relnamespace
JOIN pg_catalog.pg_attribute att
  ON att.attrelid = tbl.oid AND att.attnum = dep.refobjsubid
WHERE ns.nspname = %s AND tbl.relname = ANY(%s)
AND dep.deptype IN ('a', 'i')
ORDER BY tbl.relname, att.attname, seq.relname"""
_CATALOG_QUERIES = (
    ("tables", _TABLES_SQL),
    ("columns", _COLUMNS_SQL),
    ("constraints", _CONSTRAINTS_SQL),
    ("indexes", _INDEXES_SQL),
    ("sequences", _SEQUENCES_SQL),
)


__all__ = [
    "MANAGED_TABLE_NAMES",
    "capture_schema_fingerprint",
    "quote_pg_identifier",
    "require_pg_identifier",
]
