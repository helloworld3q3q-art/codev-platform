"""SQLAlchemy 仓储共享基座与方言适配。"""

from __future__ import annotations

from sqlalchemy.engine import Engine
from sqlalchemy.sql.schema import Table


def build_upsert_statement(
    table: Table,
    values: dict[str, object],
    *,
    index_elements: list[str],
    update_cols: list[str],
    dialect_name: str,
):
    """按 PG 或 SQLite 方言构造无 DDL 副作用的 upsert。"""
    if dialect_name == "postgresql":
        from sqlalchemy.dialects.postgresql import insert
    elif dialect_name == "sqlite":
        from sqlalchemy.dialects.sqlite import insert
    else:
        from sqlalchemy.dialects.postgresql import insert
    statement = insert(table).values(**values)
    updates = {column: statement.excluded[column] for column in update_cols}
    return statement.on_conflict_do_update(
        index_elements=index_elements,
        set_=updates,
    )


class SqlAlchemyRepositoryBase:
    """只负责 engine 生命周期与子类声明的最小 schema 只读探针。"""

    _required_tables: tuple[Table, ...] = ()

    def __init__(
        self,
        dsn: str | None = None,
        *,
        min_size: int = 1,
        max_size: int = 4,
        engine: Engine | None = None,
    ) -> None:
        if engine is not None:
            self._engine = engine
        else:
            if dsn is None:
                raise ValueError("SQLAlchemy 仓储需要 dsn 或 engine 之一")
            from codev_platform.web.db.engine import make_engine

            self._engine = make_engine(dsn, pool_size=max_size)
        self._schema_verified = False

    @property
    def _dialect(self) -> str:
        return self._engine.dialect.name

    def _ensure(self) -> None:
        """每个仓储实例只验证一次自己显式声明的表与列。"""
        if self._schema_verified:
            return
        from codev_platform.web.db.runtime_schema import verify_sqlalchemy_tables

        verify_sqlalchemy_tables(self._engine, self._required_tables)
        self._schema_verified = True


__all__ = ["SqlAlchemyRepositoryBase", "build_upsert_statement"]
