"""parity 守护:web/db/tables.py 的 metadata 表/列集合 ≡ 原 rbac_store_pg._SCHEMA。

迁移 SQLAlchemy Core 前,7 表定义在 rbac_store_pg._SCHEMA 大字符串里。本测试把那份 schema 的
表名 + 列名 + PK + NOT NULL + 默认值固化为参照常量(EXPECTED_*),断言 tables.py 与之逐项一致。
改列名 / 删表 / 改默认值 / 改 NOT NULL → 测试红,防 schema 静默漂移(plan 阶段 ④)。
"""
from __future__ import annotations

import importlib.util
import os
from pathlib import Path
import subprocess
import sys

import pytest

pytest.importorskip("sqlalchemy")

from codev_platform.web.db import tables  # noqa: E402
from codev_platform.reindex.pg_queue_sql import QUARANTINE_COLUMNS  # noqa: E402

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
                     "lease_expires_at", "claim_token", "pending_token",
                     "pending_enqueued_at", "pending_updated_at", "pending_meta_json",
                     "active_enqueued_at", "active_meta_json", "active_heartbeat_at",
                     "result_status", "result_finished_at", "result_enqueued_at",
                     "result_meta_json", "quarantine_reason", "quarantine_attempt_id",
                     "quarantine_fence", "quarantine_process_identity",
                     "quarantine_containment_kind", "quarantine_native_ref",
                     "quarantine_at"},
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
    # agent_tokens: 2026-06-12 新增(multi-org server Phase 2: IDE agent PG token), 守护扩展到 16 表。
    "agent_tokens": {"token_hash", "user_id", "org_id", "projects", "label",
                     "status", "expires_at", "created_at"},
    # 0009 统一接管过去由运行期 Store 建立的 Agent 会话与记忆表。
    "agent_sessions": {"org_id", "user_id", "session_id", "project_id",
                       "created_at", "updated_at"},
    "agent_messages": {"id", "org_id", "user_id", "session_id", "role",
                       "content", "payload", "created_at"},
    "memory_entries": {"id", "org_id", "scope", "scope_ref", "owner_user_id",
                       "content", "kind", "topic_key", "is_redline", "status",
                       "supersedes", "extra", "ttl_at", "task_id", "task_state",
                       "created_at", "updated_at"},
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
    "agent_tokens": {"token_hash"},
    "agent_sessions": {"org_id", "user_id", "session_id"},
    "agent_messages": {"id"},
    "memory_entries": {"id"},
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
    # agent_tokens: PK token_hash(隐含 NOT NULL) + 显式 NOT NULL 业务列。
    "agent_tokens": {"token_hash", "user_id", "org_id", "status", "created_at"},
    "agent_sessions": {"org_id", "user_id", "session_id", "created_at", "updated_at"},
    "agent_messages": {"id", "org_id", "user_id", "session_id", "role", "payload",
                       "created_at"},
    "memory_entries": {"id", "org_id", "scope", "scope_ref", "owner_user_id",
                       "content", "is_redline", "status", "extra", "created_at",
                       "updated_at"},
}

# 索引名(原 _SCHEMA 3 个 + jobs 1 + sessions 3 + reindex_jobs 1 + graph 4 + agent_tokens 1)。
EXPECTED_INDEXES = {"ix_org_members_user", "ix_team_members_user", "ix_teams_org",
                    "ix_jobs_project", "ix_sessions_access", "ix_sessions_refresh",
                    "ix_sessions_username", "ix_reindex_jobs_claim",
                    "ix_graph_nodes_kind", "ix_graph_nodes_name",
                    "ix_graph_edges_source", "ix_graph_edges_target",
                    "ix_agent_tokens_user", "ix_agent_msg_session",
                    "ix_mem_scope", "ix_mem_topic", "ix_mem_task"}


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


def test_reindex_queue_v2_migration_chains_from_latest_revision():
    path = Path("codev_platform/web/db/alembic/versions/20260709_0007_reindex_queue_v2.py")
    spec = importlib.util.spec_from_file_location("reindex_queue_v2_migration", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    assert module.down_revision == "0006_agent_tokens"


def test_reindex_quarantine_migration_is_idempotent_and_chained():
    path = Path(
        "codev_platform/web/db/alembic/versions/"
        "20260712_0008_reindex_queue_quarantine.py"
    )
    source = path.read_text(encoding="utf-8")
    spec = importlib.util.spec_from_file_location("reindex_queue_quarantine_migration", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)

    assert module.down_revision == "0007_reindex_queue_v2"
    assert "ADD COLUMN IF NOT EXISTS" in source
    assert "DROP COLUMN IF EXISTS" in source
    for column in (
        "quarantine_reason", "quarantine_attempt_id", "quarantine_fence",
        "quarantine_process_identity", "quarantine_containment_kind",
        "quarantine_native_ref", "quarantine_at",
    ):
        assert column in source


def test_reindex_queue_v2_migration_is_idempotent():
    source = Path(
        "codev_platform/web/db/alembic/versions/20260709_0007_reindex_queue_v2.py"
    ).read_text(encoding="utf-8")
    assert "ADD COLUMN IF NOT EXISTS" in source
    assert "DROP COLUMN IF EXISTS" in source


def test_reindex_migrations_render_complete_offline_sql():
    root = Path(__file__).resolve().parents[1]
    environment = os.environ.copy()
    environment["CODEV_PLATFORM_CONFIG"] = str(root / ".missing-test-config.json")
    environment["CODEV_PLATFORM_MEMORY_DSN"] = (
        "postgresql+psycopg://test:test@localhost/test"
    )
    result = subprocess.run(
        [
            sys.executable,
            "-m",
            "alembic",
            "-c",
            "codev_platform/web/db/alembic.ini",
            "upgrade",
            "head",
            "--sql",
        ],
        cwd=root,
        env=environment,
        capture_output=True,
        timeout=30,
        check=False,
    )
    stderr = result.stderr.decode("utf-8", errors="replace")
    assert result.returncode == 0, stderr
    rendered = result.stdout.decode("utf-8", errors="replace")
    for column in QUARANTINE_COLUMNS:
        assert f"ADD COLUMN IF NOT EXISTS {column}" in rendered
