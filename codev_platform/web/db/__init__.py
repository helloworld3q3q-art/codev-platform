"""web/db —— RBAC/账户 PG 库的 SQLAlchemy Core 接缝。

tables.py  7 表 MetaData 单一真值源(收口原 rbac_store_pg._SCHEMA)。
engine.py  create_engine 接缝(读写分离 + psycopg 缺失 ImportError 契约)。
"""
from __future__ import annotations

from codev_platform.web.db import engine, tables
from codev_platform.web.db.engine import make_engine, make_engines, normalize_dsn
from codev_platform.web.db.tables import metadata

__all__ = ["engine", "tables", "metadata", "make_engine", "make_engines", "normalize_dsn"]
