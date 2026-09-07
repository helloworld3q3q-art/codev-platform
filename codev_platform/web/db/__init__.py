"""平台 PG 的表定义、连接、只读运行期探针与受控 Alembic 迁移接缝。

生产 Store 不执行 DDL；存量无版本库只能由迁移协调器做参考指纹分类后接管，禁止人工
``stamp head``。SQLite ``metadata.create_all`` 仅供测试准备阶段使用。
"""
from __future__ import annotations

from codev_platform.web.db import engine, tables
from codev_platform.web.db.engine import make_engine, make_engines, normalize_dsn
from codev_platform.web.db.tables import metadata

__all__ = ["engine", "tables", "metadata", "make_engine", "make_engines", "normalize_dsn"]
