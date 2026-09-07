"""数据库迁移协调器的 PostgreSQL/Alembic 生产适配器。"""

from __future__ import annotations

from contextlib import contextmanager
from pathlib import Path
import secrets
from collections.abc import Iterator

from alembic import command
from alembic.config import Config
from alembic.script import ScriptDirectory
from sqlalchemy.engine import Connection, Engine

from codev_platform.reindex.pg_queue_schema_contract import (
    verify_queue_schema_contract,
)
from codev_platform.web.db.migration_contract import (
    DatabaseMigrationError,
    DatabaseMigrationPorts,
    DatabaseVerificationPorts,
    DatabaseVerificationReport,
    MigrationPlan,
    MigrationReport,
    RevisionState,
    SchemaFingerprint,
)
from codev_platform.web.db.migration_coordinator import (
    migrate_database,
    verify_database_current,
)
from codev_platform.web.db.migration_fingerprint import (
    MANAGED_TABLE_NAMES,
    capture_schema_fingerprint,
    quote_pg_identifier,
    require_pg_identifier,
)


_ADVISORY_LOCK_KEY = "codev-platform:database-migration:v1"
_VERSION_TABLE = "alembic_version"
_QUEUE_TABLE = "reindex_jobs"


class PostgresMigrationAdapter:
    """把一条外层事务连接绑定到纯迁移状态机端口。"""

    def __init__(self, engine: Engine, plan: MigrationPlan) -> None:
        if not isinstance(engine, Engine) or engine.dialect.name != "postgresql":
            raise DatabaseMigrationError("数据库迁移只支持 PostgreSQL engine")
        if type(plan) is not MigrationPlan:
            raise DatabaseMigrationError("数据库迁移计划无效")
        self._engine = engine
        self._plan = plan
        self._connection: Connection | None = None
        self._schema: str | None = None

    def ports(self) -> DatabaseMigrationPorts:
        return DatabaseMigrationPorts(
            transaction=self.transaction,
            acquire_lock=self.acquire_lock,
            read_revision=self.read_revision,
            read_fingerprint=self.read_fingerprint,
            build_reference_fingerprints=self.build_reference_fingerprints,
            stamp_revision=self.stamp_revision,
            upgrade_revision=self.upgrade_revision,
            verify_runtime_contract=self.verify_runtime_contract,
        )

    def verification_ports(self) -> DatabaseVerificationPorts:
        """只暴露迁移后独立验收所需的只读能力。"""
        return DatabaseVerificationPorts(
            readonly_transaction=self.readonly_transaction,
            read_revision=self.read_revision,
            read_fingerprint=self.read_fingerprint,
            verify_runtime_contract=self.verify_runtime_contract,
        )

    @contextmanager
    def transaction(self) -> Iterator[None]:
        if self._connection is not None:
            raise DatabaseMigrationError("数据库迁移事务不可重入")
        with self._engine.connect() as connection, connection.begin():
            try:
                schema = connection.exec_driver_sql("SELECT current_schema()").scalar_one()
                self._schema = require_pg_identifier(schema, "数据库当前 schema")
                self._connection = connection
                connection.exec_driver_sql("SET LOCAL lock_timeout = '30s'")
                connection.exec_driver_sql("SET LOCAL statement_timeout = '15min'")
                connection.exec_driver_sql("SET LOCAL idle_in_transaction_session_timeout = '15min'")
                self._set_search_path(self._schema)
                yield
            finally:
                self._connection = None
                self._schema = None

    @contextmanager
    def readonly_transaction(self) -> Iterator[None]:
        """以 PostgreSQL 强制只读事务复验，不取得迁移锁也不执行 DDL。"""
        if self._connection is not None:
            raise DatabaseMigrationError("数据库验收事务不可重入")
        with self._engine.connect() as connection, connection.begin():
            try:
                connection.exec_driver_sql("SET TRANSACTION READ ONLY")
                connection.exec_driver_sql("SET LOCAL lock_timeout = '5s'")
                connection.exec_driver_sql("SET LOCAL statement_timeout = '2min'")
                schema = connection.exec_driver_sql("SELECT current_schema()").scalar_one()
                self._schema = require_pg_identifier(schema, "数据库当前 schema")
                self._connection = connection
                self._set_search_path(self._schema)
                yield
            finally:
                self._connection = None
                self._schema = None

    def acquire_lock(self) -> None:
        connection = self._conn()
        row = connection.exec_driver_sql(
            "SELECT pg_try_advisory_xact_lock(hashtext(%s))",
            (_ADVISORY_LOCK_KEY,),
        ).one()
        if tuple(row) != (True,):
            raise DatabaseMigrationError("数据库迁移锁正被另一事务持有")
        # Advisory lock 只约束本平台迁移器；对所有既有受管表再取得 NOWAIT
        # 排他锁，关闭未接入协议的旧进程在停写证明后的竞态窗口。
        schema = self._schema_name()
        managed = (*MANAGED_TABLE_NAMES, _VERSION_TABLE)
        rows = connection.exec_driver_sql(
            """SELECT rel.relname FROM pg_catalog.pg_class rel
            JOIN pg_catalog.pg_namespace ns ON ns.oid = rel.relnamespace
            WHERE ns.nspname = %s AND rel.relkind IN ('r', 'p')
              AND rel.relname = ANY(%s::text[]) ORDER BY rel.relname""",
            (schema, list(managed)),
        ).fetchall()
        names = tuple(str(item[0]) for item in rows)
        if len(names) != len(set(names)) or any(name not in managed for name in names):
            raise DatabaseMigrationError("数据库迁移写屏障目标无效")
        if names:
            qualified = ", ".join(
                f"{quote_pg_identifier(schema, '数据库 schema')}."
                f"{quote_pg_identifier(name, '数据库受管表')}"
                for name in names
            )
            try:
                connection.exec_driver_sql(
                    f"LOCK TABLE {qualified} IN ACCESS EXCLUSIVE MODE NOWAIT"
                )
            except Exception:
                raise DatabaseMigrationError("数据库仍存在并发访问，拒绝迁移") from None

    def read_revision(self) -> RevisionState:
        connection = self._conn()
        schema = self._schema_name()
        present = connection.exec_driver_sql(
            """SELECT EXISTS (
            SELECT 1 FROM pg_catalog.pg_class rel
            JOIN pg_catalog.pg_namespace ns ON ns.oid = rel.relnamespace
            WHERE ns.nspname = %s AND rel.relname = %s AND rel.relkind = 'r')""",
            (schema, _VERSION_TABLE),
        ).scalar_one()
        if present is not True:
            return RevisionState(False, None)
        table = f"{quote_pg_identifier(schema, '数据库 schema')}.{_VERSION_TABLE}"
        rows = connection.exec_driver_sql(f"SELECT version_num FROM {table}").fetchall()
        if len(rows) != 1 or type(rows[0][0]) is not str:
            raise DatabaseMigrationError("数据库 Alembic 版本表状态无效")
        return RevisionState(True, rows[0][0])

    def read_fingerprint(self) -> SchemaFingerprint:
        schema = self._schema_name()
        self._set_search_path(schema)
        return capture_schema_fingerprint(self._conn(), schema)

    def build_reference_fingerprints(
        self,
        revisions: tuple[str, ...],
    ) -> dict[str, SchemaFingerprint]:
        requested = tuple(revisions)
        if (
            not requested
            or len(requested) != len(set(requested))
            or any(revision not in self._plan.revisions for revision in requested)
            or tuple(sorted(requested, key=self._plan.revisions.index)) != requested
        ):
            raise DatabaseMigrationError("数据库参考 revision 集合无效")
        reference = require_pg_identifier(
            f"codev_ref_{secrets.token_hex(8)}",
            "数据库参考 schema",
        )
        connection = self._conn()
        quoted = quote_pg_identifier(reference, "数据库参考 schema")
        connection.exec_driver_sql(f"CREATE SCHEMA {quoted}")
        snapshots: dict[str, SchemaFingerprint] = {}
        try:
            for revision in requested:
                self._set_search_path(reference)
                command.upgrade(self._alembic_config(reference), revision)
                snapshots[revision] = capture_schema_fingerprint(connection, reference)
        finally:
            self._set_search_path(self._schema_name())
            connection.exec_driver_sql(f"DROP SCHEMA {quoted} CASCADE")
        return snapshots

    def stamp_revision(self, revision: str) -> None:
        if revision not in self._plan.revisions:
            raise DatabaseMigrationError("数据库 stamp revision 无效")
        schema = self._schema_name()
        self._set_search_path(schema)
        command.stamp(self._alembic_config(schema), revision)

    def upgrade_revision(self, revision: str) -> None:
        if revision != self._plan.head:
            raise DatabaseMigrationError("数据库升级目标不是当前 head")
        schema = self._schema_name()
        self._set_search_path(schema)
        command.upgrade(self._alembic_config(schema), revision)

    def verify_runtime_contract(self) -> None:
        connection = self._conn()
        schema = self._schema_name()
        schema_access = connection.exec_driver_sql(
            "SELECT has_schema_privilege(current_user, %s, 'USAGE')",
            (schema,),
        ).one()
        table_access = connection.exec_driver_sql(
            """SELECT bool_and(has_table_privilege(
            current_user, format('%I.%I', %s, name), 'SELECT,INSERT,UPDATE,DELETE'))
            FROM unnest(%s::text[]) AS name""",
            (schema, list(MANAGED_TABLE_NAMES)),
        ).one()
        if tuple(schema_access) != (True,) or tuple(table_access) != (True,):
            raise DatabaseMigrationError("数据库运行角色 DML 权限不足")

        def execute(sql: str, params: tuple[object, ...]):
            return _TupleResult(connection.exec_driver_sql(sql, params))

        verify_queue_schema_contract(
            execute,
            schema=schema,
            table=_QUEUE_TABLE,
            qualified_table=(
                f"{quote_pg_identifier(schema, '数据库 schema')}."
                f"{quote_pg_identifier(_QUEUE_TABLE, '数据库队列表')}"
            ),
        )

    def _alembic_config(self, schema: str) -> Config:
        config = _base_alembic_config()
        config.attributes["connection"] = self._conn()
        config.attributes["version_table_schema"] = schema
        config.attributes["target_schema"] = schema
        return config

    def _set_search_path(self, schema: str) -> None:
        quoted = quote_pg_identifier(schema, "数据库 schema")
        self._conn().exec_driver_sql(f"SET LOCAL search_path TO {quoted}, pg_catalog")

    def _conn(self) -> Connection:
        if self._connection is None:
            raise DatabaseMigrationError("数据库迁移连接不在事务内")
        return self._connection

    def _schema_name(self) -> str:
        if self._schema is None:
            raise DatabaseMigrationError("数据库迁移 schema 未冻结")
        return self._schema


class _TupleResult:
    """把 SQLAlchemy Row 适配成队列契约要求的精确 tuple。"""

    def __init__(self, result: object) -> None:
        self._result = result

    def fetchone(self) -> tuple[object, ...] | None:
        row = self._result.fetchone()  # type: ignore[union-attr]
        return None if row is None else tuple(row)

    def fetchall(self) -> list[tuple[object, ...]]:
        return [tuple(row) for row in self._result.fetchall()]  # type: ignore[union-attr]


def load_migration_plan() -> MigrationPlan:
    """从当前不可变包读取并验证单 head、无分叉 revision 链。"""
    script = ScriptDirectory.from_config(_base_alembic_config())
    heads = tuple(script.get_heads())
    if len(heads) != 1:
        raise DatabaseMigrationError("数据库迁移必须只有一个 head")
    ordered = tuple(reversed(tuple(script.walk_revisions(base="base", head=heads[0]))))
    previous: str | None = None
    revisions: list[str] = []
    for item in ordered:
        if item.down_revision != previous:
            raise DatabaseMigrationError("数据库迁移链存在分叉或断点")
        revisions.append(item.revision)
        previous = item.revision
    return MigrationPlan(tuple(revisions), heads[0])


def migrate_postgres(engine: Engine) -> MigrationReport:
    """使用当前 release 内迁移脚本对一个 PostgreSQL engine 执行受控升级。"""
    plan = load_migration_plan()
    adapter = PostgresMigrationAdapter(engine, plan)
    return migrate_database(plan, adapter.ports())


def verify_postgres_current(engine: Engine) -> DatabaseVerificationReport:
    """独立只读证明 PostgreSQL 已处于当前 release 的可用 schema。"""
    plan = load_migration_plan()
    adapter = PostgresMigrationAdapter(engine, plan)
    return verify_database_current(
        plan,
        adapter.verification_ports(),
        expected_table_count=len(MANAGED_TABLE_NAMES),
    )


def _base_alembic_config() -> Config:
    script_location = Path(__file__).with_name("alembic")
    if not script_location.is_dir():
        raise DatabaseMigrationError("不可变 release 缺少 Alembic 迁移包")
    config = Config()
    config.set_main_option("script_location", str(script_location))
    config.set_main_option("sqlalchemy.url", "driver://")
    return config


__all__ = [
    "PostgresMigrationAdapter",
    "load_migration_plan",
    "migrate_postgres",
    "verify_postgres_current",
]
