"""PgJobReadRepo/PgJobWriteRepo(sqlite 内存 exercise)+ bind_job_service 回退/failfast(codex P2)。

不连真 PG: _PgBase 支持注入 engine, 用 sqlite StaticPool(所有连接共享同一内存库)跑改写后的
SQL 逻辑。bind 回退测试照 test_account_store_pg_failfast 范式 monkeypatch。
"""
from __future__ import annotations

import pytest

pytest.importorskip("sqlalchemy")

from sqlalchemy import create_engine
from sqlalchemy.pool import StaticPool

from codev_platform.web.db import tables
from codev_platform.web.domain.job import create_job
from codev_platform.web.repositories.job_read_repo import InMemoryJobReadRepo, PgJobReadRepo
from codev_platform.web.repositories.job_write_repo import PgJobWriteRepo
from codev_platform.web.services import job_service as js


def _shared_sqlite_engine():
    # StaticPool: 所有连接共享同一内存库(否则每连接独立, 读写仓互不可见)。
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    # 测试准备阶段只建 jobs，反证 Job 仓储不依赖账户 schema，也不在请求期执行 DDL。
    tables.jobs.create(engine)
    return engine


def test_pg_job_crud_roundtrip():
    eng = _shared_sqlite_engine()
    r, w = PgJobReadRepo(engine=eng), PgJobWriteRepo(engine=eng)
    job = create_job("proj-a", "index_rebuild:chroma")
    w.create(job)
    got = r.get(job.job_id)
    assert got is not None
    assert got.project_id == "proj-a" and got.job_type == "index_rebuild:chroma"
    assert got.status == "Pending"
    assert got.created_at == job.created_at  # float 时间戳无损往返(非 datetime)
    # save = upsert(状态流转覆盖 status/updated_at/error)
    w.save(job.to_running())
    assert r.get(job.job_id).status == "Running"
    # list + project 过滤
    w.create(create_job("proj-b", "x"))
    assert len(r.list()) == 2
    assert [j.project_id for j in r.list(project_id="proj-a")] == ["proj-a"]


def test_pg_job_persists_across_repo_instances():
    # 模拟多 worker: 新 read repo 实例(同 engine)读到另一实例写的 job —— 非进程内 dict, 跨实例一致。
    eng = _shared_sqlite_engine()
    job = create_job("p", "x")
    PgJobWriteRepo(engine=eng).create(job)
    assert PgJobReadRepo(engine=eng).get(job.job_id) is not None


def test_bind_job_service_no_dsn_is_memory():
    svc = js.bind_job_service({})  # 无 pg_dsn → 内存(行为同前)
    assert isinstance(svc._read, InMemoryJobReadRepo)


_PROD = {"memory": {"pg_dsn": "postgres://x"}, "deployment": {"mode": "prod"}}
_DEV = {"memory": {"pg_dsn": "postgres://x"}, "deployment": {"mode": "dev"}}


def _raise_import(*_a, **_k):
    raise ImportError("psycopg 未安装 (模拟)")


def test_bind_prod_pg_failure_fails_fast(monkeypatch):
    # prod 配 PG 却缺 psycopg → fail-fast, 不静默回退内存(防运维误以为用 PG, 重启丢 job 历史)。
    monkeypatch.setattr(js, "build_pg_job_service", _raise_import)
    with pytest.raises(ImportError):
        js.bind_job_service(_PROD)


def test_bind_dev_pg_failure_falls_back_to_memory(monkeypatch):
    monkeypatch.setattr(js, "build_pg_job_service", _raise_import)
    assert isinstance(js.bind_job_service(_DEV)._read, InMemoryJobReadRepo)
