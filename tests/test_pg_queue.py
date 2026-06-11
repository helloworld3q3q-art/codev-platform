"""PgJobQueue 单测(多机共享 reindex 队列)。需 PG(memory.pg_dsn / CODEV_PLATFORM_MEMORY_DSN);
缺 PG / psycopg → skip。用隔离表 reindex_jobs_test, 绝不碰生产 reindex_jobs。"""
from __future__ import annotations

import os
import time

import pytest

_TEST_TABLE = "reindex_jobs_test"
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
        queue = PgJobQueue(dsn, table=_TEST_TABLE)
        queue._ensure()
    except Exception as exc:  # noqa: BLE001 — 缺 psycopg / 连不上 → skip
        pytest.skip(f"PgJobQueue 不可用: {exc}")

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
