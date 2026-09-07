"""Pg 队列 quarantine schema 与 SQL 围栏回归。"""

from __future__ import annotations

from tests.pg_queue_fakes import FakeConn, FakePool, schema_migration_responses


def test_explicit_migration_ddl_contains_all_quarantine_columns() -> None:
    from codev_platform.reindex.pg_queue import PgJobQueue
    from codev_platform.reindex.pg_queue_schema import migrate_queue_schema
    from codev_platform.reindex.pg_queue_sql import QUARANTINE_COLUMNS
    from codev_platform.reindex.pg_queue_tx import PgOperationBudget

    conn = FakeConn(schema_migration_responses())
    queue = PgJobQueue.__new__(PgJobQueue)
    queue._bare_table = "reindex_jobs_test"
    queue._binding_base = "pg-v2-base|host=db.example|port=5432|db=reindex"
    queue._pool = FakePool(conn)
    queue._lease_ttl = 1800.0
    queue._owner = "unit-owner"
    queue._opened = False

    migrate_queue_schema(queue, PgOperationBudget.start(2.0))

    _schema_sql, create_sql, alter_sql, _index_sql = (
        call[0] for call in conn.calls[:4]
    )
    for column in QUARANTINE_COLUMNS:
        assert column in create_sql
        assert column in alter_sql


def test_all_claim_mutations_are_fenced_by_quarantine_predicate() -> None:
    from codev_platform.reindex import pg_queue_sql as sql

    statements = (
        sql.claim_sql("queue_table", project_filter=False, limited=True),
        sql.renew_sql("queue_table"),
        sql.retry_sql("queue_table"),
        sql.retry_sql("queue_table", require_owner=False),
        sql.publish_lock_sql("queue_table"),
        sql.publish_ack_sql("queue_table"),
        sql.discard_sql("queue_table"),
        sql.reclaim_owned_sql("queue_table"),
        sql.recover_owned_sql("queue_table"),
    )
    assert all("quarantine_at IS NULL" in statement for statement in statements)
    enqueue = sql.enqueue_sql("queue_table")
    assert "CASE WHEN queue_table.quarantine_at IS NULL" in enqueue
    assert "quarantine_at = NULL" not in enqueue
    clear = sql.clear_quarantine_sql("queue_table")
    assert "CASE WHEN pending_enqueued_at IS NULL" in clear
    assert "COALESCE(pending_meta_json, active_meta_json)" not in clear
