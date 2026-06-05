"""bind_account_stores: prod 模式 PG 初始化失败/依赖缺失(ImportError)均 fail-fast; dev 回退内存; 无 dsn 用内存。"""
from __future__ import annotations

import pytest

from codev_platform.web.repositories import account_store
from codev_platform.web.repositories import account_store_pg


@pytest.fixture(autouse=True)
def _restore_stores():
    yield
    account_store.reset_account_stores()


def _raise_runtime(*_args, **_kwargs):
    raise RuntimeError("PG 初始化失败 (模拟)")


def _raise_import(*_args, **_kwargs):
    raise ImportError("psycopg 未安装 (模拟)")


_PROD_CFG = {"memory": {"pg_dsn": "postgres://x"}, "deployment": {"mode": "prod"}}
_DEV_CFG = {"memory": {"pg_dsn": "postgres://x"}, "deployment": {"mode": "dev"}}


def test_prod_pg_init_failure_fails_fast(monkeypatch):
    monkeypatch.setattr(account_store_pg, "PgOrgStore", _raise_runtime)
    with pytest.raises(RuntimeError):
        account_store.bind_account_stores(_PROD_CFG)


def test_dev_pg_init_failure_falls_back_to_memory(monkeypatch):
    monkeypatch.setattr(account_store_pg, "PgOrgStore", _raise_runtime)
    assert account_store.bind_account_stores(_DEV_CFG) == "memory"


def test_prod_psycopg_missing_fails_fast(monkeypatch):
    # P0-3: prod 配了 PG dsn 却缺 psycopg → fail-fast, 不静默回退内存 (防运维误以为用 PG 实际走内存丢数据)。
    monkeypatch.setattr(account_store_pg, "PgOrgStore", _raise_import)
    with pytest.raises(ImportError):
        account_store.bind_account_stores(_PROD_CFG)


def test_dev_psycopg_missing_falls_back_to_memory(monkeypatch):
    # dev / 平台 venv 常态: psycopg 未装 → 回退内存 (开发便利)。
    monkeypatch.setattr(account_store_pg, "PgOrgStore", _raise_import)
    assert account_store.bind_account_stores(_DEV_CFG) == "memory"
