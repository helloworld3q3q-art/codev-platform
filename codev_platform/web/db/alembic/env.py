"""Alembic environment for codev-platform web business DB (RBAC/account 7 tables).

Schema single source of truth = codev_platform/web/db/tables.py (`metadata`).
``target_metadata`` points at it so ``--autogenerate`` / ``check`` diff against
the live Table definitions (no ORM declarative Base).

DSN resolution (mirrors the rest of the platform):
    env CODEV_PLATFORM_MEMORY_DSN  >  config memory.pg_dsn  >  (none)

When no DSN is configured we fall back to Alembic *offline* mode (emit SQL to
stdout, never connect). That makes `history` / `heads` / script inspection work
on machines without PG, and lets offline `upgrade --sql` render the baseline DDL
for review without touching a real database.

Stamp strategy (see also alembic.ini header):
  - **Existing live PG** already built by the old rbac_store_pg._SCHEMA:
        python -m alembic -c codev_platform/web/db/alembic.ini stamp head
    Marks the DB as already-at-baseline; does NOT recreate the 7 tables.
  - **Dev / test (SQLite or throwaway PG)**: code may still call
        tables.metadata.create_all(engine)
    (unchanged runtime behaviour) OR `alembic upgrade head` — both yield the
    same schema (baseline migration is equivalent to the metadata).
  - **Fresh prod PG**: `alembic upgrade head` builds all 7 tables, then future
    columns go via new forward migrations only (never edit the baseline).
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
    """Run migrations in 'offline' mode (emit SQL, no DB connection)."""
    url = _resolve_dsn()
    context.configure(
        url=url,
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
        compare_type=True,
        compare_server_default=True,
    )
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    """Run migrations against a live DB.

    If no real DSN is configured (only the placeholder driver:// remains), we
    transparently degrade to offline mode so CLI inspection never blows up on a
    machine without PG.
    """
    url = _resolve_dsn()
    if not url or url.startswith("driver://"):
        run_migrations_offline()
        return

    section = config.get_section(config.config_ini_section) or {}
    section["sqlalchemy.url"] = url
    connectable = engine_from_config(
        section,
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
    )
    with connectable.connect() as connection:
        context.configure(
            connection=connection,
            target_metadata=target_metadata,
            compare_type=True,
            compare_server_default=True,
        )
        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
