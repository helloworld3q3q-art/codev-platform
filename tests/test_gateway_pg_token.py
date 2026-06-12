"""PgTokenStore + PgTokenResolver 闭合"config token 与 PG 脱节"洞的真实覆盖(sqlite 内存 engine)。

核心安全性质: web 把 users.status 置 DISABLED 后, 该用户的 PG token 下一次认证即失效
(config token 做不到 —— 这正是脱节洞)。用真 SQLAlchemy SQL(join users.status)验证, 不靠 mock。
"""
from __future__ import annotations

import pytest

sqlalchemy = pytest.importorskip("sqlalchemy")
from sqlalchemy import create_engine  # noqa: E402

from codev_platform.gateway.auth import (  # noqa: E402
    CompositeTokenResolver,
    MappingTokenResolver,
    PgTokenResolver,
    TokenAuthenticator,
    Unauthorized,
    token_hash,
)
from codev_platform.gateway.token_store_pg import PgTokenStore  # noqa: E402
from codev_platform.web.db import tables  # noqa: E402


@pytest.fixture()
def engine():
    eng = create_engine("sqlite://")
    yield eng
    eng.dispose()


def _seed_user(engine, user_id: str, status: str = "ACTIVE", org: str = "acme") -> None:
    """建 org + user(满足 agent_tokens FK + lookup 的 users.status join)。"""
    tables.metadata.create_all(engine)
    with engine.begin() as conn:
        conn.execute(tables.orgs.insert().values(org_id=org, name=org, status="ACTIVE"))
        conn.execute(tables.users.insert().values(user_id=user_id, status=status))


def test_lookup_active_user_returns_meta(engine):
    _seed_user(engine, "alice")
    store = PgTokenStore(engine=engine)
    store.issue(token_hash("alice-tok"), "alice", "acme", projects=["proj-a"])
    meta = store.lookup(token_hash("alice-tok"))
    assert meta is not None
    assert meta["user_id"] == "alice" and meta["org_id"] == "acme"
    assert meta["projects"] == ["proj-a"]


def test_disabled_user_token_fails_lookup(engine):
    """脱节洞闭合: 用户 ACTIVE 时 token 通, 置 DISABLED 后同一 token lookup 立即返 None。"""
    _seed_user(engine, "bob")
    store = PgTokenStore(engine=engine)
    store.issue(token_hash("bob-tok"), "bob", "acme", projects="*")
    assert store.lookup(token_hash("bob-tok")) is not None       # ACTIVE → 通
    with engine.begin() as conn:
        conn.execute(tables.users.update().where(tables.users.c.user_id == "bob").values(status="DISABLED"))
    assert store.lookup(token_hash("bob-tok")) is None           # DISABLED → 立即失效


def test_disabled_org_token_fails_lookup(engine):
    """审计 R1: org 被停用(orgs.status=DISABLED)→ 该 org 下 token 即失效, 即便 user 仍 ACTIVE。"""
    _seed_user(engine, "frank", org="acme")
    store = PgTokenStore(engine=engine)
    store.issue(token_hash("frank-tok"), "frank", "acme", projects="*")
    assert store.lookup(token_hash("frank-tok")) is not None       # org+user ACTIVE → 通
    with engine.begin() as conn:
        conn.execute(tables.orgs.update().where(tables.orgs.c.org_id == "acme").values(status="DISABLED"))
    assert store.lookup(token_hash("frank-tok")) is None           # org DISABLED → 失效(user 还 ACTIVE)


def test_revoke_and_revoke_user(engine):
    _seed_user(engine, "carol")
    store = PgTokenStore(engine=engine)
    store.issue(token_hash("c1"), "carol", "acme", projects="*", label="laptop")
    store.issue(token_hash("c2"), "carol", "acme", projects="*", label="desktop")
    # 单个吊销(按 hash 前缀)
    assert store.revoke(token_hash("c1")) == 1
    assert store.lookup(token_hash("c1")) is None
    assert store.lookup(token_hash("c2")) is not None
    # 一键吊销该用户所有 active token
    assert store.revoke_user("carol") == 1                       # 只剩 c2 active
    assert store.lookup(token_hash("c2")) is None


def test_authenticator_with_pg_resolver(engine):
    """端到端: TokenAuthenticator 经 PgTokenResolver 认证 Bearer; 禁用用户后 401。"""
    _seed_user(engine, "dave")
    store = PgTokenStore(engine=engine)
    store.issue(token_hash("dave-tok"), "dave", "acme", projects="*")
    auth = TokenAuthenticator(PgTokenResolver(store))
    ident = auth.authenticate({"Authorization": "Bearer dave-tok"})
    assert ident.user_id == "dave" and ident.via == "token" and ident.all_projects is True
    # 禁用 → 下一次认证 Unauthorized
    with engine.begin() as conn:
        conn.execute(tables.users.update().where(tables.users.c.user_id == "dave").values(status="DISABLED"))
    with pytest.raises(Unauthorized):
        auth.authenticate({"Authorization": "Bearer dave-tok"})


def test_composite_pg_primary_config_fallback(engine):
    """组合 resolver: PG 命中优先; PG 未命中回退 config(bootstrap token 仍可用)。"""
    _seed_user(engine, "erin")
    store = PgTokenStore(engine=engine)
    store.issue(token_hash("pg-tok"), "erin", "acme", projects="*")
    config = MappingTokenResolver({token_hash("boot-tok"): {"user_id": "root", "org_id": "default", "projects": "*"}})
    auth = TokenAuthenticator(CompositeTokenResolver([PgTokenResolver(store), config]))
    # PG token
    assert auth.authenticate({"Authorization": "Bearer pg-tok"}).user_id == "erin"
    # config bootstrap token(PG 未命中 → 回退 config)
    assert auth.authenticate({"Authorization": "Bearer boot-tok"}).user_id == "root"
    # 都不命中 → 401
    with pytest.raises(Unauthorized):
        auth.authenticate({"Authorization": "Bearer nope"})


def test_pg_resolver_failclosed_on_db_error():
    """store.lookup 抛异常 → resolver 返 None(fail-closed, 拒绝优于误放行)。"""
    class _Boom:
        def lookup(self, h):
            raise RuntimeError("db down")
    assert PgTokenResolver(_Boom()).resolve("anyhash") is None
