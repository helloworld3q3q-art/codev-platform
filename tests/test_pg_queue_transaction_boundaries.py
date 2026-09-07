"""PostgreSQL 队列状态转换与 Python 结果处理的事务边界测试。"""
from __future__ import annotations

import time

import pytest

from codev_platform.reindex.queue_ports import (
    ClaimedJob,
    Job,
    JobMeta,
    QueueOperationTimeout,
)
from tests.pg_queue_fakes import FakeConn, FakeCursor, unit_queue

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


def test_quarantine_result_conversion_failure_rolls_back_transition() -> None:
    queue = unit_queue(FakeCursor(rows=[(
        "demo",
        "chroma",
        "claim-1",
        "attempt-1",
        "fence-1",
        "pid:10:start:20",
        "test",
        "fixture:10",
        "死亡未确认",
        "损坏时间",
    )]))

    with pytest.raises(ValueError):
        queue.quarantine(
            _claim(),
            attempt_id="attempt-1",
            fence="fence-1",
            process_identity="pid:10:start:20",
            containment_kind="test",
            native_ref="fixture:10",
            reason="死亡未确认",
            timeout_sec=0.2,
        )

    assert queue._pool._conn.rollbacks == 1
    assert queue._pool._conn.commits == 0


def test_publish_without_ack_cannot_hide_expired_deadline() -> None:
    active = '{"source":"test","target_commit":"' + _OID_A + '"}'
    queue = unit_queue(FakeCursor(rows=[(None, None, active)]))

    with pytest.raises(QueueOperationTimeout):
        with queue.begin_publish(
            _claim(), desired_revision=_OID_A, timeout_sec=0.01,
        ):
            time.sleep(0.03)

    assert queue._pool._conn.rollbacks == 1
    assert queue._pool._conn.commits == 0


def test_recover_owned_result_conversion_obeys_deadline(monkeypatch) -> None:
    queue = unit_queue(FakeCursor(rows=[(
        "demo",
        "chroma",
        10.0,
        "claim-1",
        "worker-1",
        200.0,
        '{"source":"test","target_commit":"' + _OID_A + '"}',
    )]))
    original_job = queue._job

    def _slow_job(*args, **kwargs):
        time.sleep(0.03)
        return original_job(*args, **kwargs)

    monkeypatch.setattr(queue, "_job", _slow_job)
    with pytest.raises(QueueOperationTimeout):
        queue.recover_owned(owner_token="worker-1", timeout_sec=0.01)


def test_peek_result_conversion_obeys_deadline(monkeypatch) -> None:
    import codev_platform.reindex.pg_queue as pg_queue_module

    queue = unit_queue(FakeCursor(rows=[("unused",)]))

    def _slow_pending(_row):
        time.sleep(0.04)
        return None

    monkeypatch.setattr(pg_queue_module, "_DEFAULT_OPERATION_TIMEOUT_SEC", 0.02)
    monkeypatch.setattr(queue, "_pending_job", _slow_pending)
    with pytest.raises(QueueOperationTimeout):
        queue.peek()


def test_snapshot_result_aggregation_obeys_deadline(monkeypatch) -> None:
    import codev_platform.reindex.pg_queue as pg_queue_module

    row = (
        "demo", "chroma", 10.0, 10.0,
        '{"source":"test","target_commit":"' + _OID_A + '"}',
        "pending", None, None, None, None, None, None, None, 20.0,
        None, None, None, None, None, None, None, None,
    )
    queue = unit_queue(FakeCursor(rows=[row]))
    original_pending = queue._pending_job

    def _slow_pending(value):
        time.sleep(0.04)
        return original_pending(value)

    monkeypatch.setattr(pg_queue_module, "_DEFAULT_OPERATION_TIMEOUT_SEC", 0.02)
    monkeypatch.setattr(queue, "_pending_job", _slow_pending)
    with pytest.raises(QueueOperationTimeout):
        queue.snapshot()


def test_body_error_is_preserved_when_transaction_cleanup_also_fails() -> None:
    from codev_platform.reindex.pg_queue_tx import PgOperationBudget, bounded_connection

    body_error = RuntimeError("发布正文失败")
    cleanup_error = RuntimeError("事务清理超时")
    cleanup_error.sqlstate = "57014"
    conn = FakeConn()

    class _CleanupFailureContext:
        def __enter__(self):
            return conn

        def __exit__(self, _exc_type, _exc, _tb):
            raise cleanup_error

    class _Pool:
        def connection(self, *, timeout=None):
            assert timeout is not None
            return _CleanupFailureContext()

    with pytest.raises(RuntimeError, match="发布正文失败") as captured:
        with bounded_connection(_Pool(), PgOperationBudget.start(0.2)):
            raise body_error

    assert captured.value is body_error
    assert captured.value.__cause__ is cleanup_error
    assert any("Pg 事务清理异常" in note for note in captured.value.__notes__)


@pytest.mark.parametrize("operation", ["retry", "reject"])
def test_transition_result_failure_rolls_back_before_commit(operation) -> None:
    class BrokenCursor:
        @property
        def rowcount(self):
            raise ValueError("转换状态转换结果失败")

    responses = (
        (FakeCursor(rowcount=0), BrokenCursor())
        if operation == "retry"
        else (BrokenCursor(),)
    )
    queue = unit_queue(*responses)

    with pytest.raises(ValueError, match="转换状态转换结果失败"):
        method = getattr(queue, operation)
        method(_claim(), reason="测试失败", timeout_sec=0.2)

    assert queue._pool._conn.rollbacks == 1
    assert queue._pool._conn.commits == 0
