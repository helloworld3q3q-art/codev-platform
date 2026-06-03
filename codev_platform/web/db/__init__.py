"""web/db —— RBAC/账户 PG 库的 SQLAlchemy Core 接缝。

tables.py  7 表 MetaData 单一真值源(收口原 rbac_store_pg._SCHEMA)。
engine.py  create_engine 接缝(读写分离 + psycopg 缺失 ImportError 契约)。
alembic/   PG 生产 schema 迁移(baseline = tables.metadata 等价)。存量真机库走
           `alembic stamp head` 标记 baseline 不重建;dev/test 仍可
           `metadata.create_all`;fresh prod 走 `alembic upgrade head`。
           配置 alembic.ini,env.py 的 target_metadata = tables.metadata,
           DSN 从 core.config 取(env CODEV_PLATFORM_MEMORY_DSN > memory.pg_dsn)。
"""
from __future__ import annotations

from codev_platform.web.db import engine, tables
from codev_platform.web.db.engine import make_engine, make_engines, normalize_dsn
from codev_platform.web.db.tables import metadata

__all__ = ["engine", "tables", "metadata", "make_engine", "make_engines", "normalize_dsn"]
