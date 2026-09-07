"""平台 PG 的 Alembic 环境。

生产迁移由协调器注入同一条在线连接和目标 schema，在外层事务与 advisory lock 内执行。
在线模式没有真实 DSN 时必须失败关闭，绝不伪装成离线成功；存量无版本库也禁止人工
``stamp head``，只能由协调器在参考 schema 指纹精确匹配后 stamp 到已证明的字面 revision。
"""
from __future__ import annotations

from logging.config import fileConfig

from alembic import context
from sqlalchemy import engine_from_config, pool

# --- target metadata: the single source of truth (tables.py) -----------------
from codev_platform.web.db.tables import metadata as target_metadata

# Alembic Config object, provides access to values in alembic.ini.
config = context.config

# Configure Python logging from the ini (if a config file was supplied).
if config.config_file_name is not None:
    try:
        fileConfig(config.config_file_name)
    except Exception:  # noqa: BLE001 - logging setup is best-effort
        pass


def _resolve_dsn() -> str | None:
    """env CODEV_PLATFORM_MEMORY_DSN > config memory.pg_dsn > None.

    Normalised to SQLAlchemy's ``postgresql+psycopg://`` form so a bare
    ``postgres://`` / ``postgresql://`` from user config still works.
    """
    try:
        from codev_platform.core.config import env_or_config, load_config
        from codev_platform.web.db.engine import normalize_dsn

        cfg = load_config()
        dsn = env_or_config("CODEV_PLATFORM_MEMORY_DSN", cfg, "memory.pg_dsn")
        if dsn and str(dsn).strip():
            return normalize_dsn(str(dsn).strip())
    except Exception:  # noqa: BLE001 - never fail env import; fall back to ini/offline
        pass
    # Fall back to whatever the ini carries (placeholder driver:// for offline).
    url = config.get_main_option("sqlalchemy.url")
    return url or None


def run_migrations_offline() -> None:
    """显式 ``--sql`` 才进入离线渲染，不参与生产成功判定。"""
    url = _resolve_dsn()
    if not url or url.startswith("driver://"):
        url = "postgresql+psycopg://"
    context.configure(
        url=url,
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
        compare_type=True,
        compare_server_default=True,
        version_table_schema=config.attributes.get("version_table_schema"),
    )
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    """只接受真实在线连接；协调器连接优先于配置 DSN。"""
    supplied_connection = config.attributes.get("connection")
    if supplied_connection is not None:
        _run_online_connection(supplied_connection)
        return
    url = _resolve_dsn()
    if not url or url.startswith("driver://"):
        raise RuntimeError("Alembic 在线迁移缺少真实数据库连接")

    section = config.get_section(config.config_ini_section) or {}
    section["sqlalchemy.url"] = url
    connectable = engine_from_config(
        section,
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
    )
    with connectable.connect() as connection:
        _run_online_connection(connection)


def _run_online_connection(connection) -> None:
    context.configure(
        connection=connection,
        target_metadata=target_metadata,
        compare_type=True,
        compare_server_default=True,
        transactional_ddl=True,
        version_table_schema=config.attributes.get("version_table_schema"),
    )
    with context.begin_transaction():
        context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
