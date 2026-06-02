"""账户存储后端绑定 + 首管理员 bootstrap 测试 (内存路径; PG 路径由真机验证)。

PG 实现(account_store_pg)需 psycopg + 真库, 不在此连;本测覆盖 provider 选择/优雅回退 +
bootstrap 幂等 + 播种后可登录 —— 与现有 *_pg 测试同范式(纯逻辑, 不连 PG)。
"""
from __future__ import annotations

import pytest

from codev_platform.web.repositories.account_store import (
    bind_account_stores,
    get_member_store,
    get_org_store,
    get_user_store,
    reset_account_stores,
)
from codev_platform.web.security.bootstrap import ensure_seed_admin
from codev_platform.web.security.passwords import verify_password
from codev_platform.web.security.sessions import session_store


@pytest.fixture(autouse=True)
def _clean():
    reset_account_stores()
    session_store.clear()
    yield
    reset_account_stores()
    session_store.clear()


# ---- provider 绑定 / 优雅回退 ----

def test_bind_no_dsn_is_memory():
    assert bind_account_stores({"gateway": {}}) == "memory"


def test_bind_pg_dsn_graceful_fallback():
    res = bind_account_stores({"memory": {"pg_dsn": "postgresql://nope/db"}})
    try:
        import psycopg_pool  # noqa: F401
        assert res == "pg"  # 有 psycopg → 绑 PG (ConnectionPool open=False, 不连库)
    except ImportError:
        assert res == "memory"  # 平台 venv 无 psycopg → 优雅回退, 不崩
    bind_account_stores({})  # 复位内存, 防后续 test 触 PG 连接


# ---- bootstrap 播种 ----

def test_seed_admin_creates_user_org_member():
    cfg = {"platform_admins": ["root"], "web": {"bootstrap_org": "orgA", "bootstrap_password": "s3cret"}}
    bind_account_stores(cfg)  # memory
    assert ensure_seed_admin(cfg) == "root"
    u = get_user_store().get("root")
    assert u is not None and u.org_id == "orgA" and u.status == "ACTIVE"
    assert verify_password("s3cret", u.password_hash) is True
    assert get_org_store().exists("orgA")
    m = get_member_store().get("orgA", "root")
    assert m is not None and m.role == "admin"


def test_seed_admin_idempotent():
    cfg = {"platform_admins": ["root"], "web": {"bootstrap_password": "x"}}
    bind_account_stores(cfg)
    assert ensure_seed_admin(cfg) == "root"
    assert ensure_seed_admin(cfg) is None  # 已存在 → 幂等跳过


def test_seed_admin_no_config_is_noop():
    assert ensure_seed_admin({"gateway": {}}) is None
    assert get_user_store().list() == []


def test_login_works_after_seed():
    from codev_platform.core.errors import PlatformError
    from codev_platform.web.services.auth_service import AuthService

    cfg = {"platform_admins": ["root"], "web": {"bootstrap_password": "pw123"}}
    bind_account_stores(cfg)
    ensure_seed_admin(cfg)
    pair = AuthService().login(username="root", password="pw123")
    assert pair.accessToken and pair.refreshToken
    with pytest.raises(PlatformError):
        AuthService().login(username="root", password="wrong")
