"""PgJobQueue 单测(多机共享 reindex 队列)。需 PG(memory.pg_dsn / CODEV_PLATFORM_MEMORY_DSN);
缺 PG / psycopg → skip。用隔离表 reindex_jobs_test, 绝不碰生产 reindex_jobs。"""
from __future__ import annotations

import os
import time

import pytest

_TEST_TABLE = "reindex_jobs_test"


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
