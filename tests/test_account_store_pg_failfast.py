"""bind_account_stores: prod 模式 PG 初始化失败 fail-fast; dev 回退内存; psycopg 缺失(ImportError)始终回退。"""
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


def test_prod_psycopg_missing_falls_back_to_memory(monkeypatch):
    monkeypatch.setattr(account_store_pg, "PgOrgStore", _raise_import)
    assert account_store.bind_account_stores(_PROD_CFG) == "memory"
