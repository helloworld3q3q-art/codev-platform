"""Pg 队列发布围栏与 quarantine 恢复回归。"""

from __future__ import annotations

import pytest

from codev_platform.reindex.attempts import ConfirmedProcessDeath
from codev_platform.reindex.queue_ports import (
    Job,
    JobMeta,
    QueueClaimLost,
    QueueOperationTimeout,
)
from tests.pg_queue_fakes import FakeConn, FakeCursor, unit_queue
from tests.pg_queue_test_support import _OID_A, _OID_B, _claim


def test_publish_guard_supersedes_dirty_revision_and_commits_ack() -> None:
    claim = _claim()
    pending = '{"source":"webhook","target_commit":"' + _OID_B + '"}'
    active = '{"source":"test","target_commit":"' + _OID_A + '"}'
    queue = unit_queue(
        FakeCursor(rows=[(20.0, pending, active)]),
        FakeCursor(rowcount=1),
    )

    with queue.begin_publish(
        claim,
        desired_revision=_OID_A,
        timeout_sec=0.2,
    ) as permit:
        assert permit.superseded is True
        assert permit.ack() is True
        assert permit.ack() is False

    assert queue._pool._conn.commits == 1
    assert queue._pool._conn.rollbacks == 0
    lock_sql = queue._pool._conn.calls[0][0]
    ack_sql = queue._pool._conn.calls[1][0]
    assert "FOR UPDATE" in lock_sql
    assert "quarantine_at IS NULL" in lock_sql
    assert "pending_enqueued_at = NULL" not in ack_sql
    with pytest.raises(RuntimeError):
        permit.ack()


def test_publish_guard_without_ack_rolls_back() -> None:
    active = '{"source":"test","target_commit":"' + _OID_A + '"}'
    queue = unit_queue(FakeCursor(rows=[(None, None, active)]))

    with queue.begin_publish(
        _claim(),
        desired_revision=_OID_A,
        timeout_sec=0.2,
    ) as permit:
        assert permit.superseded is False

    assert queue._pool._conn.rollbacks == 1
    assert queue._pool._conn.commits == 0


def test_publish_guard_rollback_timeout_is_normalized() -> None:
    active = '{"source":"test","target_commit":"' + _OID_A + '"}'
    error = RuntimeError("回滚阶段超时")
    error.sqlstate = "57014"

    class _RollbackTimeoutConn(FakeConn):
        def rollback(self):
            raise error

    conn = _RollbackTimeoutConn([FakeCursor(rows=[(None, None, active)])])

    class _RollbackTimeoutContext:
        def __enter__(self):
            return conn

        def __exit__(self, exc_type, _exc, _tb):
            if exc_type is not None:
                raise error
            conn.commits += 1
            return False

    class _Pool:
        def connection(self, *, timeout=None):
            assert timeout is not None
            return _RollbackTimeoutContext()

    queue = unit_queue()
    queue._pool = _Pool()
    with pytest.raises(QueueOperationTimeout, match="数据库操作超时"):
        with queue.begin_publish(
            _claim(),
            desired_revision=_OID_A,
            timeout_sec=0.2,
        ):
            pass

    assert conn.commits == 0


def test_publish_guard_ack_then_body_error_rolls_back() -> None:
    active = '{"source":"test","target_commit":"' + _OID_A + '"}'
    queue = unit_queue(
        FakeCursor(rows=[(None, None, active)]),
        FakeCursor(rowcount=1),
    )

    with pytest.raises(RuntimeError, match="发布失败"):
        with queue.begin_publish(
            _claim(),
            desired_revision=_OID_A,
            timeout_sec=0.2,
        ) as permit:
            assert permit.ack() is True
            raise RuntimeError("发布失败")

    assert queue._pool._conn.rollbacks == 1
    assert queue._pool._conn.commits == 0


def test_publish_guard_rejects_persisted_active_revision_mismatch() -> None:
    active = '{"source":"test","target_commit":"' + _OID_B + '"}'
    queue = unit_queue(FakeCursor(rows=[(None, None, active)]))

    with pytest.raises(QueueClaimLost):
        with queue.begin_publish(
            _claim(),
            desired_revision=_OID_A,
            timeout_sec=0.2,
        ):
            pytest.fail("版本不匹配不得进入发布围栏")


def test_clear_quarantine_requires_matching_death_proof() -> None:
    from codev_platform.reindex.queue_ports import QuarantineRecord

    record = QuarantineRecord(
        "demo",
        "chroma",
        "claim-1",
        "attempt-1",
        "fence-1",
        "pid:10:start:20",
        "test",
        "fixture:10",
        "死亡未确认",
        55.0,
    )
    queue = unit_queue(FakeCursor(rowcount=1))
    death = ConfirmedProcessDeath("pid:10:start:20", "test", 56.0, "已退出")

    assert queue.clear_quarantine(record, death_proof=death, timeout_sec=0.2) is True
    sql, params = queue._pool._conn.calls[0]
    assert "quarantine_attempt_id" in sql and "quarantine_fence" in sql
    assert "quarantine_at IS NOT NULL" in sql
    assert record.attempt_id in params and record.claim_token in params


def test_clear_quarantine_early_rejection_still_validates_timeout() -> None:
    queue = unit_queue()

    with pytest.raises(ValueError):
        queue.clear_quarantine(None, death_proof=None, timeout_sec=0.0)


def test_legacy_complete_reuses_publish_ack_transition() -> None:
    queue = unit_queue(FakeCursor(rows=[(None,)], rowcount=1))
    job = Job(
        "demo",
        "chroma",
        10.0,
        token="claim-1",
        meta=JobMeta(source="test", target_commit=_OID_A),
    )

    assert queue.complete(job) is True
    sql, params = queue._pool._conn.calls[0]
    assert "result_status = %s" in sql
    assert params[0] == "completed"


def test_snapshot_hides_quarantined_key_from_other_buckets() -> None:
    queue = unit_queue(
        FakeCursor(
            rows=[
                (
                    "demo",
                    "chroma",
                    10.0,
                    20.0,
                    '{"source":"webhook","target_commit":"' + _OID_B + '"}',
                    "quarantined",
                    10.0,
                    '{"source":"test","target_commit":"' + _OID_A + '"}',
                    "claim-1",
                    1.0,
                    None,
                    None,
                    None,
                    100.0,
                    "worker-1",
                    "死亡未确认",
                    "attempt-1",
                    "fence-1",
                    "pid:10:start:20",
                    "test",
                    "fixture:10",
                    55.0,
                )
            ]
        )
    )

    snapshot = queue.snapshot()

    assert len(snapshot.quarantined) == 1
    assert snapshot.pending == []
    assert snapshot.active == []
    assert snapshot.expired_active == []
