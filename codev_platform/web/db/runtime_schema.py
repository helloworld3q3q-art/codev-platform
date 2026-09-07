"""PG 运行期只读 schema 探针；业务 Store 永不在请求路径执行 DDL。"""

from __future__ import annotations

from collections.abc import Iterable

from sqlalchemy import select
from sqlalchemy.engine import Engine
from sqlalchemy.sql.schema import Table


class RuntimeSchemaUnavailable(RuntimeError):
    """当前连接无法证明业务所需表与列均可读取。"""


def verify_sqlalchemy_tables(engine: Engine, required: Iterable[Table]) -> None:
    """对固定 Core Table 执行零行 SELECT，验证关系和全部已声明列。"""
    try:
        tables = tuple(required)
    except TypeError:
        raise RuntimeSchemaUnavailable("PG 运行时 schema 探针无效") from None
    if not tables or not all(isinstance(table, Table) for table in tables):
        raise RuntimeSchemaUnavailable("PG 运行时 schema 探针无效")
    try:
        with engine.connect() as connection:
            for table in tables:
                connection.execute(select(*table.c).limit(0))
    except (KeyboardInterrupt, SystemExit, MemoryError):
        raise
    except Exception:
        raise RuntimeSchemaUnavailable("PG 运行时 schema 未迁移或不可读") from None


def verify_psycopg_probes(connection: object, probes: tuple[str, ...]) -> None:
    """对原生 psycopg 连接执行固定零行探针，不接受动态标识符。"""
    if (
        not probes
        or not all(type(sql) is str and sql.startswith("SELECT ") and sql.endswith(" LIMIT 0") for sql in probes)
        or not callable(getattr(connection, "execute", None))
    ):
        raise RuntimeSchemaUnavailable("PG 运行时 schema 探针无效")
    try:
        for sql in probes:
            connection.execute(sql)
    except (KeyboardInterrupt, SystemExit, MemoryError):
        raise
    except Exception:
        raise RuntimeSchemaUnavailable("PG 运行时 schema 未迁移或不可读") from None


__all__ = [
    "RuntimeSchemaUnavailable",
    "verify_psycopg_probes",
    "verify_sqlalchemy_tables",
]
