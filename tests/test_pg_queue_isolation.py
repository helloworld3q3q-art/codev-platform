"""Pg 队列隔离、事务预算与发布围栏的无数据库必跑测试。"""

from __future__ import annotations

import math
import threading
import time

import pytest

from codev_platform.reindex.queue_ports import (
    QueueClaimLost,
    QueueOperationTimeout,
)
from tests.pg_queue_fakes import FakeConn, FakeCursor, FakePool, unit_queue
from tests.pg_queue_test_support import _OID_A, _claim


@pytest.mark.parametrize("timeout", [True, 0.0, 0.009, math.nan, math.inf])
def test_pg_budget_rejects_unrepresentable_timeout(timeout) -> None:
    from codev_platform.reindex.pg_queue_tx import PgOperationBudget

    with pytest.raises(ValueError):
        PgOperationBudget.start(timeout)


def test_bounded_connection_sets_three_local_timeouts_below_budget() -> None:
    from codev_platform.reindex.pg_queue_tx import PgOperationBudget, bounded_connection

    pool = FakePool(FakeConn())
    budget = PgOperationBudget.start(0.2)
    with bounded_connection(pool, budget):
        pass

    assert len(pool.connection_timeouts) == 1
    assert 0 < pool.connection_timeouts[0] <= 0.2
    sql, params = pool._conn.control_calls[0]
    assert sql.count("set_config(") == 3
    values = [float(value.removesuffix("ms")) for value in params]
    assert all(0 < value < 200 for value in values)
    assert pool._conn.commits == 1


def test_schema_initialization_wait_uses_same_operation_budget() -> None:
    from codev_platform.reindex.pg_queue import PgJobQueue
    from codev_platform.reindex.pg_queue_tx import PgOperationBudget

    queue = PgJobQueue.__new__(PgJobQueue)
    queue._opened = False
    queue._ensure_lock = threading.Lock()
    queue._ensure_lock.acquire()
    started = time.monotonic()
    try:
        with pytest.raises(QueueOperationTimeout):
            queue._ensure(PgOperationBudget.start(0.02))
    finally:
        queue._ensure_lock.release()

    assert time.monotonic() - started < 0.2


@pytest.mark.parametrize("sqlstate", ["57014", "55P03", "25P03"])
def test_pg_timeout_sqlstate_is_normalized(sqlstate) -> None:
    from codev_platform.reindex.pg_queue_tx import (
        PgOperationBudget,
        bounded_connection,
        execute_with_budget,
    )

    error = RuntimeError("数据库预算耗尽")
    error.sqlstate = sqlstate
    pool = FakePool(FakeConn([error]))
    budget = PgOperationBudget.start(0.2)

    with pytest.raises(QueueOperationTimeout):
        with bounded_connection(pool, budget) as conn:
            execute_with_budget(conn, budget, "UPDATE queue")
    assert pool._conn.rollbacks == 1


def test_non_timeout_database_error_is_not_hidden() -> None:
    from codev_platform.reindex.pg_queue_tx import PgOperationBudget, bounded_connection

    error = RuntimeError("数据库结构损坏")
    pool = FakePool(FakeConn([error]))

    with pytest.raises(RuntimeError, match="数据库结构损坏"):
        with bounded_connection(pool, PgOperationBudget.start(0.2)) as conn:
            conn.execute("UPDATE queue")


def test_publish_body_timeout_error_is_preserved() -> None:
    from codev_platform.reindex.pg_queue_tx import PgOperationBudget, bounded_connection

    pool = FakePool(FakeConn())
    with pytest.raises(TimeoutError, match="外部发布超时") as captured:
        with bounded_connection(pool, PgOperationBudget.start(0.2)):
            raise TimeoutError("外部发布超时")
    assert type(captured.value) is TimeoutError
    assert pool._conn.rollbacks == 1


def test_pool_context_enter_timeout_is_normalized() -> None:
    from codev_platform.reindex.pg_queue_tx import PgOperationBudget, bounded_connection

    class PoolTimeout(RuntimeError):
        pass

    class _FailingContext:
        def __enter__(self):
            raise PoolTimeout("连接池等待超时")

        def __exit__(self, _exc_type, _exc, _tb):
            pytest.fail("进入失败后不应执行退出")

    class _Pool:
        def connection(self, *, timeout=None):
            assert timeout is not None
            return _FailingContext()

    with pytest.raises(QueueOperationTimeout, match="数据库操作超时"):
        with bounded_connection(_Pool(), PgOperationBudget.start(0.2)):
            pytest.fail("连接池进入失败后不应执行事务体")


def test_pool_context_exit_timeout_is_normalized() -> None:
    from codev_platform.reindex.pg_queue_tx import PgOperationBudget, bounded_connection

    error = RuntimeError("提交阶段超时")
    error.sqlstate = "57014"
    conn = FakeConn()

    class _FailingContext:
        def __enter__(self):
            return conn

        def __exit__(self, _exc_type, _exc, _tb):
            raise error

    class _Pool:
        def connection(self, *, timeout=None):
            assert timeout is not None
            return _FailingContext()

    with pytest.raises(QueueOperationTimeout, match="数据库操作超时"):
        with bounded_connection(_Pool(), PgOperationBudget.start(0.2)):
            pass


def test_successful_commit_crossing_deadline_cannot_return_success() -> None:
    from codev_platform.reindex.pg_queue_tx import PgOperationBudget, bounded_connection

    now = [10.0]
    conn = FakeConn()

    class _SlowCommitContext:
        def __enter__(self):
            return conn

        def __exit__(self, exc_type, _exc, _tb):
            if exc_type is None:
                now[0] += 0.02
                conn.commits += 1
            else:
                conn.rollbacks += 1
            return False

    class _Pool:
        def connection(self, *, timeout=None):
            assert timeout is not None
            return _SlowCommitContext()

    budget = PgOperationBudget.start(0.2, clock=lambda: now[0])
    with pytest.raises(QueueOperationTimeout):
        with bounded_connection(_Pool(), budget):
            now[0] = 10.19

    assert conn.commits == 1


def test_timeout_refresh_crossing_deadline_does_not_issue_business_sql() -> None:
    from codev_platform.reindex.pg_queue_tx import (
        PgOperationBudget,
        bounded_connection,
        execute_with_budget,
    )

    now = [10.0]

    class _SlowControlConn(FakeConn):
        def __init__(self):
            super().__init__([FakeCursor(rowcount=1)])
            self._control_count = 0

        def execute(self, sql, params=()):
            if "set_config(" in sql:
                self._control_count += 1
                if self._control_count == 2:
                    now[0] += 0.21
            return super().execute(sql, params)

    conn = _SlowControlConn()
    budget = PgOperationBudget.start(0.2, clock=lambda: now[0])
    with pytest.raises(QueueOperationTimeout):
        with bounded_connection(FakePool(conn), budget) as active_conn:
            execute_with_budget(active_conn, budget, "UPDATE queue")

    assert conn.calls == []


def test_sql_and_commit_cannot_cross_shared_deadline() -> None:
    from codev_platform.reindex.pg_queue_tx import (
        PgOperationBudget,
        bounded_connection,
        execute_with_budget,
    )

    now = [10.0]

    def _slow_sql(_sql, _params):
        now[0] += 0.21
        return FakeCursor(rowcount=1)

    pool = FakePool(FakeConn([_slow_sql]))
    budget = PgOperationBudget.start(0.2, clock=lambda: now[0])
    with pytest.raises(QueueOperationTimeout):
        with bounded_connection(pool, budget) as conn:
            execute_with_budget(conn, budget, "UPDATE queue")

    assert pool._conn.rollbacks == 1
    assert pool._conn.commits == 0


def test_claim_returns_explicit_owner_token_and_persisted_lease() -> None:
    queue = unit_queue(
        FakeCursor(
            rows=[
                (
                    "demo",
                    "chroma",
                    10.0,
                    "claim-1",
                    "worker-7",
                    123.5,
                    '{"source":"test","target_commit":"' + _OID_A + '"}',
                )
            ]
        )
    )

    claims = queue.claim(
        owner_token="worker-7",
        projects={"demo"},
        limit=1,
        timeout_sec=0.2,
    )

    assert len(claims) == 1
    assert claims[0].owner_token == "worker-7"
    assert claims[0].claim_token == "claim-1"
    assert claims[0].lease_expires_at == 123.5
    assert claims[0].job.token is None
    sql, params = queue._pool._conn.calls[0]
    assert "quarantine_at IS NULL" in sql
    assert "lease_expires_at" in sql
    assert "worker-7" in params


def test_claim_conversion_failure_rolls_back_active_transition() -> None:
    queue = unit_queue(
        FakeCursor(
            rows=[
                (
                    "demo",
                    "chroma",
                    "损坏时间",
                    "claim-1",
                    "worker-7",
                    123.5,
                    '{"source":"test","target_commit":"' + _OID_A + '"}',
                )
            ]
        )
    )

    with pytest.raises(ValueError):
        queue.claim(
            owner_token="worker-7",
            projects={"demo"},
            limit=1,
            timeout_sec=0.2,
        )

    assert queue._pool._conn.rollbacks == 1
    assert queue._pool._conn.commits == 0


def test_claim_sorting_timeout_rolls_back_active_transition(monkeypatch) -> None:
    import codev_platform.reindex.pg_queue as pg_queue_module

    queue = unit_queue(
        FakeCursor(
            rows=[
                (
                    "demo",
                    "chroma",
                    10.0,
                    "claim-1",
                    "worker-7",
                    123.5,
                    '{"source":"test","target_commit":"' + _OID_A + '"}',
                )
            ]
        )
    )

    def _slow_rank():
        time.sleep(0.03)
        return {"chroma": 0}

    monkeypatch.setattr(pg_queue_module, "_kind_rank", _slow_rank)
    with pytest.raises(QueueOperationTimeout):
        queue.claim(
            owner_token="worker-7",
            projects={"demo"},
            limit=1,
            timeout_sec=0.01,
        )

    assert queue._pool._conn.rollbacks == 0
    assert queue._pool._conn.commits == 0
    assert queue._pool._conn.calls == []


def test_empty_project_filter_still_validates_pg_timeout() -> None:
    queue = unit_queue()

    with pytest.raises(ValueError):
        queue.claim(
            owner_token="worker-1",
            projects=set(),
            limit=1,
            timeout_sec=0.0,
        )


def test_recover_owned_is_read_only_and_preserves_lease() -> None:
    queue = unit_queue(
        FakeCursor(
            rows=[
                (
                    "demo",
                    "chroma",
                    10.0,
                    "claim-1",
                    "worker-1",
                    123.5,
                    '{"source":"test","target_commit":"' + _OID_A + '"}',
                )
            ]
        )
    )

    recovered = queue.recover_owned(owner_token="worker-1", timeout_sec=0.2)

    assert recovered[0].lease_expires_at == 123.5
    sql, params = queue._pool._conn.calls[0]
    assert sql.lstrip().upper().startswith("SELECT")
    assert "UPDATE" not in sql.upper()
    assert params == ("worker-1",)


def test_renew_and_retry_match_token_owner_and_exclude_quarantine() -> None:
    claim = _claim()
    renew_queue = unit_queue(FakeCursor(rowcount=1))
    retry_queue = unit_queue(FakeCursor(rowcount=0), FakeCursor(rowcount=1))

    assert renew_queue.renew(claim, ttl_sec=30.0, timeout_sec=0.2) is True
    assert retry_queue.retry(claim, reason="重试", timeout_sec=0.2) is True

    for queue in (renew_queue, retry_queue):
        sql, params = queue._pool._conn.calls[-1]
        assert "claim_token" in sql and "claimed_by" in sql
        assert "quarantine_at IS NULL" in sql
        assert claim.claim_token in params and claim.owner_token in params


def test_quarantine_is_persisted_and_stale_claim_fails_closed() -> None:
    claim = _claim()
    persisted = (
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
    queue = unit_queue(FakeCursor(rows=[persisted], rowcount=1))

    record = queue.quarantine(
        claim,
        attempt_id="attempt-1",
        fence="fence-1",
        process_identity="pid:10:start:20",
        containment_kind="test",
        native_ref="fixture:10",
        reason="死亡未确认",
        timeout_sec=0.2,
    )

    assert record.quarantined_at == 55.0
    sql, _params = queue._pool._conn.calls[0]
    assert "quarantine_at IS NULL" in sql
    assert "claimed_by" in sql and "claim_token" in sql

    stale = unit_queue(FakeCursor(rows=[], rowcount=0))
    with pytest.raises(QueueClaimLost):
        stale.quarantine(
            claim,
            attempt_id="attempt-1",
            fence="fence-1",
            process_identity="pid:10:start:20",
            containment_kind="test",
            native_ref="fixture:10",
            reason="死亡未确认",
            timeout_sec=0.2,
        )
