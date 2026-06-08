"""PgSessionStore(sqlite exercise)+ bind_session_store 回退/failfast(backend-deep P1-3)。

登录态 PG 化: 重启不丢 / 多 worker 共享 / revoke 跨进程。sqlite StaticPool exercise 改写后的
SQL 逻辑, 不连真 PG; bind 回退测试照 account/job 范式 monkeypatch。
"""
from __future__ import annotations

import pytest

pytest.importorskip("sqlalchemy")

from sqlalchemy import create_engine
from sqlalchemy.pool import StaticPool

from codev_platform.web.security import sessions as S


def _eng():
    return create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)


def test_pg_session_create_resolve():
    st = S.PgSessionStore(engine=_eng())
    tok = st.create("alice", "acme")
    sess = st.resolve(tok.access_token)
    assert sess is not None and sess.username == "alice" and sess.org_id == "acme"
    assert st.resolve("wrong-token") is None


def test_pg_session_expired_resolve_none():
    st = S.PgSessionStore(engine=_eng())
    tok = st.create("alice", "acme", now=1000.0)
    assert st.resolve(tok.access_token, now=1000.0 + 999999) is None  # access 过期


def test_pg_session_refresh_rotates():
    st = S.PgSessionStore(engine=_eng())
    tok = st.create("alice", "acme", now=1000.0)
    new = st.refresh(tok.refresh_token, now=1000.0)
    assert new is not None
    assert st.resolve(tok.access_token, now=1000.0) is None      # 旧 access 轮换失效
    assert st.resolve(new.access_token, now=1000.0) is not None  # 新 access 有效


def test_pg_session_revoke():
    st = S.PgSessionStore(engine=_eng())
    tok = st.create("alice", "acme")
    st.revoke(tok.refresh_token)
    assert st.resolve(tok.access_token) is None


def test_pg_session_revoke_user():
    st = S.PgSessionStore(engine=_eng())
    t1 = st.create("alice", "acme")
    st.create("alice", "acme")
    tb = st.create("bob", "acme")
    assert st.revoke_user("alice") == 2          # alice 2 个 session 全撤
    assert st.resolve(t1.access_token) is None   # alice 失效
    assert st.resolve(tb.access_token) is not None  # bob 不受影响


def test_pg_session_persists_across_instances():
    # 模拟多 worker: 新 store 实例(同 engine)能 resolve → 落库非进程内, 重启/跨 worker 一致。
    eng = _eng()
    tok = S.PgSessionStore(engine=eng).create("alice", "acme")
    assert S.PgSessionStore(engine=eng).resolve(tok.access_token) is not None


def test_bind_session_store_no_dsn_is_memory():
    assert isinstance(S.bind_session_store({}), S.SessionStore)


def _raise_import(*_a, **_k):
    raise ImportError("psycopg 未安装 (模拟)")


def test_bind_prod_pg_failure_fails_fast(monkeypatch):
    monkeypatch.setattr(S, "PgSessionStore", _raise_import)
    with pytest.raises(ImportError):
        S.bind_session_store({"memory": {"pg_dsn": "x"}, "deployment": {"mode": "prod"}})


def test_bind_dev_pg_failure_falls_back_to_memory(monkeypatch):
    monkeypatch.setattr(S, "PgSessionStore", _raise_import)
    out = S.bind_session_store({"memory": {"pg_dsn": "x"}, "deployment": {"mode": "dev"}})
    assert isinstance(out, S.SessionStore)
