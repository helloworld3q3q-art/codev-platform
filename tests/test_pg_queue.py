"""PgJobQueue 单测(多机共享 reindex 队列)。需 PG(memory.pg_dsn / CODEV_PLATFORM_MEMORY_DSN);
缺 PG / psycopg → skip。用隔离表 reindex_jobs_test, 绝不碰生产 reindex_jobs。"""
from __future__ import annotations

import os
import time

import pytest
from codev_platform.reindex.queue import Job, JobMeta, QueueSnapshot
from tests.pg_queue_fakes import (
    FakeConn as _FakeConn,
    FakeCursor as _FakeCursor,
    FakePool as _FakePool,
    TEST_TABLE as _TEST_TABLE,
    schema_verification_responses as _schema_verification_responses,
    unit_queue as _unit_queue,
)
from tests.queue_transition_contract import (
    assert_dependency_state_is_exact_and_read_only,
    assert_reject_is_token_fenced,
    assert_retry_moves_claim_to_tail,
)

_EXTRA_QUEUES: list = []   # _q_owner 造的额外实例, fixture teardown 统一关池


def _dsn():
    from codev_platform.core.config import get, load_config
    return os.environ.get("CODEV_PLATFORM_MEMORY_DSN") or get(load_config(), "memory.pg_dsn")


@pytest.fixture
def q():
    dsn = _dsn()
    if not dsn:
        pytest.skip("无 memory.pg_dsn / CODEV_PLATFORM_MEMORY_DSN")
    try:
        from codev_platform.reindex.pg_queue import PgJobQueue
    except ImportError as exc:
        pytest.skip(f"未安装 PG 可选依赖: {exc}")
    queue = PgJobQueue(dsn, table=_TEST_TABLE)
    from codev_platform.reindex.pg_queue_schema import migrate_queue_schema
    from codev_platform.reindex.pg_queue_tx import PgOperationBudget

    migrate_queue_schema(queue, PgOperationBudget.start(5.0))

    def _clean():
        with queue._pool.connection() as c:
            c.execute(f"DELETE FROM {_TEST_TABLE}")
    _clean()
    yield queue
    _clean()
    # 关本 fixture 池 + _q_owner 造的额外池(避免 psycopg_pool atexit 自 join 崩, Windows 尤甚)。
    queue.close()
    for extra in _EXTRA_QUEUES:
        extra.close()
    _EXTRA_QUEUES.clear()


def _pids(jobs):
    return {(j.project_id, j.kind) for j in jobs}


def test_pg_retry_to_tail_matches_shared_queue_contract(q):
    assert_retry_moves_claim_to_tail(q, prefix="pg-tail")


def test_pg_retry_tail_is_later_than_existing_future_pending(q):
    first = "pg-future-first"
    second = "pg-future-second"
    q.enqueue(first, "chroma", JobMeta(target_commit="a" * 40))
    q.enqueue(second, "chroma", JobMeta(target_commit="b" * 40))
    claim = q.claim(
        owner_token="future-worker",
        projects={first},
        limit=1,
        timeout_sec=0.5,
    )[0]
    future = time.time() + 3600.0
    with q._pool.connection() as conn:
        conn.execute(
            f"UPDATE {_TEST_TABLE} SET pending_enqueued_at = %s "
            "WHERE project_id = %s AND kind = %s",
            (future, second, "chroma"),
        )

    assert q.retry(claim, reason="等待依赖", timeout_sec=0.5) is True

    pending = [job for job in q.peek() if job.project_id in {first, second}]
    assert [job.project_id for job in pending] == [second, first]
    assert pending[1].enqueued_at > future


def test_pg_reject_matches_shared_queue_contract(q):
    assert_reject_is_token_fenced(q, prefix="pg")


def test_pg_dependency_state_matches_shared_queue_contract(q):
    assert_dependency_state_is_exact_and_read_only(q, prefix="pg")


def test_enqueue_accepts_v2_meta_without_schema_change_unit():
    q = _unit_queue(_FakeCursor(rowcount=1))

    q.enqueue("t-pgq-meta", "chroma", meta=JobMeta(source="manual", pull_policy="never"))

    conn = q._pool._conn
    assert len(conn.calls) == 1
    assert conn.calls[0][1][:2] == ("t-pgq-meta", "chroma")


def test_enqueue_persists_pending_meta_json_unit():
    q = _unit_queue(_FakeCursor(rowcount=1))

    q.enqueue("t-pgq-meta-json", "chroma", meta=JobMeta(source="manual", pull_policy="never", target_commit="abc123"))

    sql, params = q._pool._conn.calls[0]
    assert "pending_meta_json" in sql
    assert "pending_token" in sql
    assert "pending_updated_at" in sql
    assert params[:2] == ("t-pgq-meta-json", "chroma")
    assert "manual" in params[-1]
    assert '"target_commit":"abc123"' in params[-1]


def test_pending_unit_binds_projects_limit_owner_and_lease_in_sql_order():
    q = _unit_queue(_FakeCursor(rows=[]))

    assert q.pending({"t-pgq-affinity"}, limit=1) == []

    sql, params = q._pool._conn.calls[0]
    assert "project_id = ANY(%s)" in sql
    assert "LIMIT %s" in sql
    assert params == (["t-pgq-affinity"], 1, "unit-owner", 1800)


def test_ensure_only_verifies_existing_schema_without_ddl_unit():
    from codev_platform.reindex.pg_queue import PgJobQueue

    conn = _FakeConn(_schema_verification_responses())
    q = PgJobQueue.__new__(PgJobQueue)
    q._bare_table = _TEST_TABLE
    q._binding_base = "pg-v2-base|host=db.example|port=5432|db=reindex"
    q._pool = _FakePool(conn)
    q._lease_ttl = 1800
    q._owner = "unit-owner"
    q._opened = False

    q._ensure()

    statements = [call[0] for call in conn.calls]
    assert any("information_schema.columns" in sql for sql in statements)
    assert any("has_table_privilege" in sql for sql in statements)
    assert not any(
        sql.lstrip().upper().startswith(("CREATE ", "ALTER "))
        for sql in statements
    )


def test_snapshot_returns_pending_active_and_results_shape_unit():
    q = _unit_queue(_FakeCursor(rows=[
        ("t-pgq-snap-p", "chroma", 10.0, 10.0, '{"source":"manual","pull_policy":"never"}',
         "pending", None, None, None, None, None, None, None, time.time()),
        ("t-pgq-snap-a", "chroma", 20.0, None, None, "running", 20.0,
         '{"source":"worker","pull_policy":"ff_only"}', "token-1", time.time() + 60,
         None, None, None, time.time()),
        ("t-pgq-snap-exp", "chroma", 30.0, None, None, "running", 30.0,
         '{"source":"legacy","pull_policy":null}', "token-expired", time.time() - 60,
         None, None, None, time.time()),
    ]))

    snap = q.snapshot()

    assert isinstance(snap, QueueSnapshot)
    assert ("t-pgq-snap-p", "chroma") in _pids(snap.pending)
    assert ("t-pgq-snap-a", "chroma") in _pids(snap.active)
    assert ("t-pgq-snap-exp", "chroma") in _pids(snap.pending)
    assert ("t-pgq-snap-exp", "chroma") in _pids(snap.expired_active)
    assert snap.results == []
    active = next(j for j in snap.active if j.project_id == "t-pgq-snap-a")
    assert active.token == "token-1"
    assert active.meta == JobMeta(source="worker", pull_policy="ff_only")
    assert active.lease_expires_at is not None
    pending = next(j for j in snap.pending if j.project_id == "t-pgq-snap-p")
    assert pending.meta == JobMeta(source="manual", pull_policy="never")


def test_enqueue_pending_complete(q):
    q.enqueue("t-pgq-a", "chroma")
    jobs = q.pending()
    assert ("t-pgq-a", "chroma") in _pids(jobs)
    job = next(j for j in jobs if j.project_id == "t-pgq-a")
    assert q.complete(job) is True
    assert not any(j.project_id == "t-pgq-a" for j in q.pending())   # 完成即消失


def test_enqueue_merges_same_key(q):
    q.enqueue("t-pgq-m", "chroma")
    q.enqueue("t-pgq-m", "chroma")
    jobs = [j for j in q.pending() if j.project_id == "t-pgq-m"]
    assert len(jobs) == 1   # 同 (pid,kind) 合并成一条


def test_claim_skip_locked_no_double_claim(q):
    q.enqueue("t-pgq-c", "chroma")
    first = q.pending()                       # 认领(写 lease)
    assert any(j.project_id == "t-pgq-c" for j in first)
    second = q.pending()                      # 已 running + 租约未过期 → 不再认领
    assert not any(j.project_id == "t-pgq-c" for j in second)


def test_dirty_reentry_keeps_job(q):
    q.enqueue("t-pgq-d", "chroma")
    job = next(j for j in q.pending() if j.project_id == "t-pgq-d")
    time.sleep(0.01)
    q.enqueue("t-pgq-d", "chroma")            # 运行期被重新触发(enqueued_at 变新)
    assert q.complete(job) is False           # 不删, 保留重跑
    again = [j for j in q.pending() if j.project_id == "t-pgq-d"]
    assert len(again) == 1                    # 可被重新认领


def test_kind_rank_codegraph_before_code_vec_on_tie(q):
    t = time.time()
    with q._pool.connection() as c:           # 同 enqueued_at 强制 tie → 依赖序
        for k in ("code_vec", "codegraph"):
            c.execute(
                f"INSERT INTO {_TEST_TABLE} (project_id, kind, enqueued_at, status) "
                "VALUES (%s, %s, %s, 'pending')", ("t-pgq-o", k, t))
    kinds = [j.kind for j in q.pending() if j.project_id == "t-pgq-o"]
    assert kinds.index("codegraph") < kinds.index("code_vec")   # 依赖序; 字母序会反


def test_expired_lease_reclaimable(q):
    q.enqueue("t-pgq-e", "chroma")
    q.pending()                               # 认领(lease_ttl 默认 1800s)
    with q._pool.connection() as c:           # 手动把租约设为已过期
        c.execute(f"UPDATE {_TEST_TABLE} SET lease_expires_at = %s WHERE project_id='t-pgq-e'",
                  (time.time() - 1,))
    again = [j for j in q.pending() if j.project_id == "t-pgq-e"]
    assert len(again) == 1                    # 租约过期(worker 崩)→ 另一 worker 接管


def test_lease_takeover_stale_complete_does_not_delete(q):
    # 审计 P0 回归: A 认领 → 租约过期 → B 接管(新 token)→ A 迟到 complete 不能删 B 在跑的行。
    q.enqueue("t-pgq-t", "chroma")
    job_a = next(j for j in q.pending() if j.project_id == "t-pgq-t")   # A 认领, token A
    with q._pool.connection() as c:                                    # A 卡住, 租约过期
        c.execute(f"UPDATE {_TEST_TABLE} SET lease_expires_at = %s WHERE project_id='t-pgq-t'",
                  (time.time() - 1,))
    job_b = next(j for j in q.pending() if j.project_id == "t-pgq-t")  # B 接管, token B
    assert job_b.token and job_b.token != job_a.token
    assert q.complete(job_a) is False         # A 的 stale complete: token 不匹配 → 删不掉 B 在跑的行
    with q._pool.connection() as c:           # B 的行仍在(没被 A 误删)
        n = c.execute(f"SELECT count(*) FROM {_TEST_TABLE} WHERE project_id='t-pgq-t'").fetchone()[0]
    assert n == 1
    assert q.complete(job_b) is True          # B 正常完成自己那次认领


def test_peek_no_side_effect(q):
    q.enqueue("t-pgq-pk", "chroma")
    p1 = [j for j in q.peek() if j.project_id == "t-pgq-pk"]
    p2 = [j for j in q.peek() if j.project_id == "t-pgq-pk"]
    assert len(p1) == 1 and len(p2) == 1      # peek 不消费, 多次都在
    with q._pool.connection() as c:           # 仍是 pending(未被 peek 认领)
        st = c.execute(f"SELECT status FROM {_TEST_TABLE} WHERE project_id='t-pgq-pk'").fetchone()[0]
    assert st == "pending"
    assert any(j.project_id == "t-pgq-pk" for j in q.pending())   # 之后 worker 仍能认领


def test_enqueue_accepts_v2_meta_without_schema_change(q):
    q.enqueue("t-pgq-meta", "chroma", meta=JobMeta(source="manual", pull_policy="never"))

    jobs = [j for j in q.peek() if j.project_id == "t-pgq-meta"]

    assert len(jobs) == 1
    assert jobs[0].meta == JobMeta(source="manual", pull_policy="never")


def test_snapshot_returns_pending_active_and_results_shape(q):
    q.enqueue("t-pgq-snap-p", "chroma")
    q.enqueue("t-pgq-snap-a", "chroma", meta=JobMeta(source="worker", pull_policy="ff_only"))
    claimed = next(j for j in q.pending() if j.project_id == "t-pgq-snap-a")

    snap = q.snapshot()

    assert isinstance(snap, QueueSnapshot)
    assert ("t-pgq-snap-p", "chroma") in _pids(snap.pending)
    assert ("t-pgq-snap-a", "chroma") in _pids(snap.active)
    assert snap.results == []
    active = next(j for j in snap.active if j.project_id == "t-pgq-snap-a")
    assert active.token == claimed.token
    assert active.meta == JobMeta(source="worker", pull_policy="ff_only")


def test_enqueue_during_active_snapshot_shows_active_and_pending(q):
    q.enqueue("t-pgq-both", "chroma", meta=JobMeta(source="manual"))
    claimed = next(j for j in q.pending() if j.project_id == "t-pgq-both")
    q.enqueue("t-pgq-both", "chroma", meta=JobMeta(source="webhook", pull_policy="never"))

    snap = q.snapshot()

    active = next(j for j in snap.active if j.project_id == "t-pgq-both")
    pending = next(j for j in snap.pending if j.project_id == "t-pgq-both")
    assert active.token == claimed.token
    assert active.meta == JobMeta(source="manual")
    assert pending.meta == JobMeta(source="webhook", pull_policy="never")


def test_release_active_only_restores_pending_for_retry(q):
    q.enqueue("t-pgq-release", "chroma", meta=JobMeta(source="manual"))
    job = next(j for j in q.pending() if j.project_id == "t-pgq-release")

    assert q.release(job) is True

    snap = q.snapshot()
    assert ("t-pgq-release", "chroma") not in _pids(snap.active)
    pending = next(j for j in snap.pending if j.project_id == "t-pgq-release")
    assert pending.meta == JobMeta(source="manual")


def test_release_active_plus_pending_keeps_pending(q):
    q.enqueue("t-pgq-release-dirty", "chroma", meta=JobMeta(source="manual"))
    job = next(j for j in q.pending() if j.project_id == "t-pgq-release-dirty")
    q.enqueue("t-pgq-release-dirty", "chroma", meta=JobMeta(source="webhook", pull_policy="never"))

    assert q.release(job) is True

    pending = next(j for j in q.peek() if j.project_id == "t-pgq-release-dirty")
    assert pending.meta == JobMeta(source="webhook", pull_policy="never")


def test_renew_extends_lease_and_updates_heartbeat_unit():
    q = _unit_queue(_FakeCursor(rowcount=1))
    job = Job("t-pgq-renew", "chroma", 1.0, token="token-1")

    assert q.renew(job) is True

    sql, params = q._pool._conn.calls[0]
    assert "active_heartbeat_at" in sql
    assert "lease_expires_at" in sql
    assert params[-2:] == ("token-1", "unit-owner")


def test_break_lease_clears_active_and_preserves_dirty_pending_unit():
    q = _unit_queue(_FakeCursor(rowcount=0), _FakeCursor(rowcount=1))
    job = Job("t-pgq-break", "chroma", 1.0, token="token-1")

    assert q.break_lease(job) is True

    assert q._pool._conn.calls[0][0] == (
        "LOCK TABLE reindex_jobs_test IN SHARE ROW EXCLUSIVE MODE"
    )
    sql, params = q._pool._conn.calls[1]
    assert "claim_token = %s" in sql
    assert "pending_enqueued_at = valid_tail.tail_value" in sql
    assert "ELSE matched.pending_meta_json END" in sql
    assert "active_enqueued_at = NULL" in sql
    assert params == ("t-pgq-break", "chroma", "token-1")


def test_break_lease_without_token_returns_false_and_skips_sql_unit():
    q = _unit_queue(_FakeCursor(rowcount=1))

    assert q.break_lease(Job("t-pgq-break", "chroma", 1.0)) is False
    assert q._pool._conn.calls == []


def test_break_lease_clears_active_and_preserves_dirty_pending(q):
    q.enqueue("t-pgq-break-live", "chroma", meta=JobMeta(source="manual"))
    job = next(j for j in q.pending() if j.project_id == "t-pgq-break-live")
    q.enqueue("t-pgq-break-live", "chroma", meta=JobMeta(source="webhook", pull_policy="never"))

    assert q.break_lease(job) is True

    snap = q.snapshot()
    assert ("t-pgq-break-live", "chroma") not in _pids(snap.active)
    pending = next(j for j in snap.pending if j.project_id == "t-pgq-break-live")
    assert pending.meta == JobMeta(source="webhook", pull_policy="never")


def test_discard_pending_job(q):
    q.enqueue("t-pgq-drop", "chroma")
    job = next(j for j in q.peek() if j.project_id == "t-pgq-drop")
    assert q.discard(job) is True
    assert not any(j.project_id == "t-pgq-drop" for j in q.peek())


def test_discard_keeps_reenqueued_job(q):
    q.enqueue("t-pgq-redrop", "chroma")
    job = next(j for j in q.peek() if j.project_id == "t-pgq-redrop")
    time.sleep(0.01)
    q.enqueue("t-pgq-redrop", "chroma")
    assert q.discard(job) is False
    assert any(j.project_id == "t-pgq-redrop" for j in q.peek())


# ---- 多 org 亲和: pending(projects=...) 白名单认领 ----

def test_affinity_claims_only_whitelisted_projects(q):
    # 多 org: worker 只认领自己 config 有 repo_path 的 project, 不抢别机/别 org 的 job。
    q.enqueue("t-pgq-mine", "chroma")
    q.enqueue("t-pgq-other", "chroma")        # 别 org/别机的 job
    claimed = q.pending({"t-pgq-mine"})       # 只认领白名单内的
    cl = _pids(claimed)
    assert ("t-pgq-mine", "chroma") in cl
    assert ("t-pgq-other", "chroma") not in cl
    with q._pool.connection() as c:           # other 仍 pending(没被本 worker 抢/删)
        st = c.execute(
            f"SELECT status FROM {_TEST_TABLE} WHERE project_id='t-pgq-other'").fetchone()[0]
    assert st == "pending"
    other = q.pending({"t-pgq-other"})        # 别 worker(其白名单含 other)仍能认领
    assert ("t-pgq-other", "chroma") in _pids(other)


def test_affinity_none_claims_all(q):
    # 兼容现状: projects=None(默认)认领全部。
    q.enqueue("t-pgq-na", "chroma")
    q.enqueue("t-pgq-nb", "chroma")
    cl = _pids(q.pending())
    assert ("t-pgq-na", "chroma") in cl and ("t-pgq-nb", "chroma") in cl


def test_affinity_empty_set_claims_nothing(q):
    # 空白名单(本 worker 无可处理 project)→ 不认领任何 job, 不误删。
    q.enqueue("t-pgq-empty", "chroma")
    assert q.pending(set()) == []
    with q._pool.connection() as c:
        st = c.execute(
            f"SELECT status FROM {_TEST_TABLE} WHERE project_id='t-pgq-empty'").fetchone()[0]
    assert st == "pending"                     # 仍待领, 没被空白名单 worker 动


# ---- 崩溃恢复: reclaim_stale_own 复位自己卡 running 的行 ----

def _q_owner(owner):
    """造一个指定 owner 的 PgJobQueue(模拟特定 host 的进程); 注册到 _EXTRA_QUEUES 由 fixture 关池。"""
    from codev_platform.reindex.pg_queue import PgJobQueue
    qx = PgJobQueue(_dsn(), table=_TEST_TABLE, owner=owner)
    _EXTRA_QUEUES.append(qx)
    return qx


def test_reclaim_across_real_restart_same_host(q):
    # 审计 P0 回归: **真实崩溃重启** = 新进程新 pid。owner=hostname 稳定 → 新实例 reclaim 能复位
    # 崩前的行。旧实现 owner=host:pid → 重启 owner 变 → reclaim 返 0, 崩溃恢复 no-op(本测会失败)。
    host = "audit-host-X"
    crashed = _q_owner(host)                     # 崩前进程
    crashed.enqueue("t-pgq-rec", "chroma")
    assert any(j.project_id == "t-pgq-rec" for j in crashed.pending())   # running, claimed_by=host
    restarted = _q_owner(host)                   # 重启后新进程(同 host, 真实新 pid; owner 仍=host)
    assert restarted.reclaim_stale_own() >= 1    # 关键: 复位崩前的行(旧 pid-owner 实现会返 0)
    with q._pool.connection() as c:
        row = c.execute(
            f"SELECT status, lease_expires_at, claim_token FROM {_TEST_TABLE} "
            "WHERE project_id='t-pgq-rec'").fetchone()
    assert row[0] == "pending" and row[1] is None and row[2] is None
    assert any(j.project_id == "t-pgq-rec" for j in restarted.pending())  # 立即重领, 不等 lease


def test_reclaim_does_not_touch_other_host(q):
    # 跨机不误伤: 别机(别 hostname)reclaim 不该复位本机 host-A 正在跑的行。
    a = _q_owner("audit-host-A")
    a.enqueue("t-pgq-ro", "chroma")
    assert any(j.project_id == "t-pgq-ro" for j in a.pending())          # host-A running
    other = _q_owner("audit-host-B")
    other.reclaim_stale_own()                    # host-B reclaim: 不碰 host-A 的行
    with q._pool.connection() as c:
        st = c.execute(
            f"SELECT status FROM {_TEST_TABLE} WHERE project_id='t-pgq-ro'").fetchone()[0]
    assert st == "running"                        # host-A 在跑的行原封不动(无跨机误伤)
