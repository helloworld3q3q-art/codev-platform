"""parity 守护:web/db/tables.py 的 metadata 表/列集合 ≡ 原 rbac_store_pg._SCHEMA。

迁移 SQLAlchemy Core 前,7 表定义在 rbac_store_pg._SCHEMA 大字符串里。本测试把那份 schema 的
表名 + 列名 + PK + NOT NULL + 默认值固化为参照常量(EXPECTED_*),断言 tables.py 与之逐项一致。
改列名 / 删表 / 改默认值 / 改 NOT NULL → 测试红,防 schema 静默漂移(plan 阶段 ④)。
"""
from __future__ import annotations

import pytest

pytest.importorskip("sqlalchemy")

from codev_platform.web.db import tables  # noqa: E402

# 原 _SCHEMA 的表→列集合(逐字对齐删除前的 CREATE TABLE 定义)。
EXPECTED_COLUMNS: dict[str, set[str]] = {
    "orgs": {"org_id", "name", "status", "created_at"},
    "users": {"user_id", "display_name", "password_hash", "status", "email", "created_at"},
    "org_members": {"org_id", "user_id", "org_role"},
    "teams": {"team_id", "org_id", "name"},
    "team_members": {"team_id", "user_id", "role"},
    "projects": {"project_id", "org_id", "display_name"},
    "project_access": {"project_id", "principal", "principal_kind", "role"},
}

# 原 _SCHEMA 的主键列(PRIMARY KEY / PRIMARY KEY(...) 复合)。
EXPECTED_PK: dict[str, set[str]] = {
    "orgs": {"org_id"},
    "users": {"user_id"},
    "org_members": {"org_id", "user_id"},
    "teams": {"team_id"},
    "team_members": {"team_id", "user_id"},
    "projects": {"project_id"},
    "project_access": {"project_id", "principal"},
}

# 原 _SCHEMA 的 NOT NULL 列(PK 列在 PG 隐含 NOT NULL,这里只列显式声明 / 业务约束列)。
EXPECTED_NOT_NULL: dict[str, set[str]] = {
    "orgs": {"org_id", "status", "created_at"},
    "users": {"user_id", "status", "created_at"},
    "org_members": {"org_id", "user_id", "org_role"},
    "teams": {"team_id", "org_id"},
    "team_members": {"team_id", "user_id", "role"},
    "projects": {"project_id", "org_id"},
    "project_access": {"project_id", "principal", "principal_kind", "role"},
}

# 原 _SCHEMA 的索引名(3 个 CREATE INDEX)。
EXPECTED_INDEXES = {"ix_org_members_user", "ix_team_members_user", "ix_teams_org"}


def test_table_names_match():
    assert set(tables.metadata.tables) == set(EXPECTED_COLUMNS)


@pytest.mark.parametrize("table_name", sorted(EXPECTED_COLUMNS))
def test_columns_match(table_name):
    t = tables.metadata.tables[table_name]
    assert set(t.c.keys()) == EXPECTED_COLUMNS[table_name]


@pytest.mark.parametrize("table_name", sorted(EXPECTED_PK))
def test_primary_keys_match(table_name):
    t = tables.metadata.tables[table_name]
    pk = {c.name for c in t.primary_key.columns}
    assert pk == EXPECTED_PK[table_name]


@pytest.mark.parametrize("table_name", sorted(EXPECTED_NOT_NULL))
def test_not_null_columns_match(table_name):
    t = tables.metadata.tables[table_name]
    not_null = {c.name for c in t.c if not c.nullable}
    assert not_null == EXPECTED_NOT_NULL[table_name]


def test_indexes_match():
    found = {ix.name for t in tables.metadata.tables.values() for ix in t.indexes}
    assert found == EXPECTED_INDEXES


def test_status_default_active():
    # status DEFAULT 'ACTIVE' 保留(orgs + users)
    for tname in ("orgs", "users"):
        col = tables.metadata.tables[tname].c["status"]
        assert col.server_default is not None
        assert "ACTIVE" in str(col.server_default.arg)
