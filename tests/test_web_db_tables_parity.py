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
    # jobs/sessions: 2026-06-08 新增(codex P2 job 历史 + backend-deep P1-3 登录态持久化), 非原
    # _SCHEMA, 守护扩展到 9 表。
    "jobs": {"job_id", "project_id", "job_type", "status", "created_at", "updated_at", "error"},
    "sessions": {"session_id", "username", "org_id", "access_hash", "refresh_hash",
                 "access_expires_at", "refresh_expires_at"},
    # reindex_jobs: 2026-06-11 新增(PgJobQueue 多机共享 reindex 队列), 守护扩展到 10 表。
    "reindex_jobs": {"project_id", "kind", "enqueued_at", "status", "claimed_by",
                     "lease_expires_at", "claim_token"},
    # graph_*: 2026-06-11 新增(PgGraphStore 统一图谱 PG 后端), 守护扩展到 15 表。
    "graph_nodes": {"id", "plugin", "kind", "name", "project_id", "file", "line",
                    "language", "meta_json"},
    "graph_edges": {"project_id", "plugin", "source", "target", "kind", "confidence",
                    "meta_json"},
    "graph_evidences": {"project_id", "plugin", "seq", "source", "detail", "file",
                        "line", "confidence", "meta_json"},
    "graph_findings": {"project_id", "plugin", "seq", "kind", "severity", "title",
                       "detail", "node_ids_json", "evidence_ids_json", "meta_json"},
    "graph_ingest_meta": {"project_id", "plugin", "plugin_version", "node_count",
                          "edge_count", "evidence_count", "finding_count", "ingested_at"},
}

# 原 _SCHEMA 的主键列(PRIMARY KEY / PRIMARY KEY(...) 复合) + jobs。
EXPECTED_PK: dict[str, set[str]] = {
    "orgs": {"org_id"},
    "users": {"user_id"},
    "org_members": {"org_id", "user_id"},
    "teams": {"team_id"},
    "team_members": {"team_id", "user_id"},
    "projects": {"project_id"},
    "project_access": {"project_id", "principal"},
    "jobs": {"job_id"},
    "sessions": {"session_id"},
    "reindex_jobs": {"project_id", "kind"},
    "graph_nodes": {"id", "plugin"},
    "graph_edges": {"project_id", "plugin", "source", "target", "kind"},
    "graph_evidences": {"project_id", "plugin", "seq"},
    "graph_findings": {"project_id", "plugin", "seq"},
    "graph_ingest_meta": {"project_id", "plugin"},
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
    "jobs": {"job_id", "project_id", "job_type", "status", "created_at", "updated_at"},
    "sessions": {"session_id", "username", "org_id", "access_hash", "refresh_hash",
                 "access_expires_at", "refresh_expires_at"},
    "reindex_jobs": {"project_id", "kind", "enqueued_at", "status"},
    # graph_*: PK 列(PG 隐含 NOT NULL) + 显式 NOT NULL 业务列。
    "graph_nodes": {"id", "plugin", "kind", "name", "project_id"},
    "graph_edges": {"project_id", "plugin", "source", "target", "kind"},
    "graph_evidences": {"project_id", "plugin", "seq", "source", "detail"},
    "graph_findings": {"project_id", "plugin", "seq", "kind", "severity", "title"},
    "graph_ingest_meta": {"project_id", "plugin"},
}

# 索引名(原 _SCHEMA 3 个 + jobs 1 + sessions 3 + reindex_jobs 1, 2026-06-11)。
EXPECTED_INDEXES = {"ix_org_members_user", "ix_team_members_user", "ix_teams_org",
                    "ix_jobs_project", "ix_sessions_access", "ix_sessions_refresh",
                    "ix_sessions_username", "ix_reindex_jobs_claim",
                    "ix_graph_nodes_kind", "ix_graph_nodes_name",
                    "ix_graph_edges_source", "ix_graph_edges_target"}


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
