"""Pg 队列 retry/reject SQL 与审计契约。"""

from __future__ import annotations

from codev_platform.reindex.queue_ports import ClaimedJob, Job, JobMeta
from codev_platform.reindex.pg_queue_sql import retry_sql
from tests.pg_queue_fakes import FakeCursor, unit_queue

_OID_A = "a" * 40


def _claim() -> ClaimedJob:
    return ClaimedJob(
        Job(
            "demo",
            "chroma",
            10.0,
            meta=JobMeta(source="test", target_commit=_OID_A),
            lease_expires_at=200.0,
        ),
        "claim-1",
        "worker-1",
        200.0,
    )


def test_pg_retry_updates_reentry_time_and_order_to_current_tail() -> None:
    queue = unit_queue(FakeCursor(rowcount=0), FakeCursor(rowcount=1))

    assert queue.retry(_claim(), reason="等待依赖", timeout_sec=0.2) is True

    sql, params = queue._pool._conn.calls[1]
    assert "pending_enqueued_at = valid_tail.tail_value" in sql
    assert "pending_updated_at = EXTRACT(EPOCH FROM clock_timestamp())" in sql
    assert "pending_token = md5(" in sql
    assert params == ("demo", "chroma", "claim-1", "worker-1")


def test_pg_retry_tail_is_strictly_later_than_global_pending_maximum() -> None:
    sql = retry_sql("queue_table")

    assert "MAX(pending_enqueued_at)" in sql
    assert "tail_value > tail_base" in sql
    assert "tail_value < 'Infinity'::double precision" in sql


def test_pg_retry_serializes_tail_scan_and_update_in_one_transaction() -> None:
    queue = unit_queue(FakeCursor(rowcount=0), FakeCursor(rowcount=1))

    assert queue.retry(_claim(), reason="等待依赖", timeout_sec=0.2) is True

    calls = queue._pool._conn.calls
    assert calls[0][0] == "LOCK TABLE reindex_jobs_test IN SHARE ROW EXCLUSIVE MODE"
    assert "MAX(pending_enqueued_at)" in calls[1][0]
    assert queue._pool._conn.commits == 1
    assert queue._pool._conn.rollbacks == 0


def test_pg_reject_is_token_fenced_and_persists_failure_reason() -> None:
    queue = unit_queue(FakeCursor(rowcount=1))

    assert queue.reject(
        _claim(),
        reason="非法目标版本",
        timeout_sec=0.2,
    ) is True

    sql, params = queue._pool._conn.calls[0]
    assert "claim_token = %s" in sql and "claimed_by = %s" in sql
    assert "quarantine_at IS NULL" in sql
    assert "result_status = 'failed'" in sql
    assert "%s::text AS failure_reason" in sql
    assert "jsonb_build_object('failure_reason', matched.failure_reason)" in sql
    assert params == ("非法目标版本", "demo", "chroma", "claim-1", "worker-1")


def test_pg_rejection_audit_reuses_result_meta_without_schema_fork() -> None:
    from codev_platform.reindex.pg_queue_sql import (
        alter_table_sql,
        create_table_sql,
        reject_sql,
    )

    assert "result_reason" not in create_table_sql("queue_table")
    assert "result_reason" not in alter_table_sql("queue_table")
    assert "failure_reason" in reject_sql("queue_table")
