"""统一接管 Agent 会话与分层记忆表。

Revision ID: 0009_agent_memory
Revises: 0008_reindex_queue_quarantine
Create Date: 2026-07-17

历史版本曾由运行期 Store 幂等建表。本迁移只补齐缺失对象；迁移协调器会在同一事务内
对升级后的完整 schema 做参考指纹复证，类型、约束或索引漂移时整体回滚并失败关闭。
"""

from __future__ import annotations

from collections.abc import Sequence

from alembic import op


revision: str = "0009_agent_memory"
down_revision: str | None = "0008_reindex_queue_quarantine"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute(
        """CREATE TABLE IF NOT EXISTS agent_sessions (
        org_id TEXT NOT NULL DEFAULT 'default',
        user_id TEXT NOT NULL,
        session_id TEXT NOT NULL,
        project_id TEXT,
        created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
        updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
        PRIMARY KEY (org_id, user_id, session_id)
        )"""
    )
    op.execute("ALTER TABLE agent_sessions ADD COLUMN IF NOT EXISTS project_id TEXT")
    op.execute(
        """CREATE TABLE IF NOT EXISTS agent_messages (
        id BIGSERIAL PRIMARY KEY,
        org_id TEXT NOT NULL DEFAULT 'default',
        user_id TEXT NOT NULL,
        session_id TEXT NOT NULL,
        role TEXT NOT NULL,
        content TEXT,
        payload JSONB NOT NULL DEFAULT '{}'::jsonb,
        created_at TIMESTAMPTZ NOT NULL DEFAULT now()
        )"""
    )
    op.execute(
        """CREATE INDEX IF NOT EXISTS ix_agent_msg_session
        ON agent_messages (org_id, user_id, session_id, id)"""
    )
    op.execute(
        """CREATE TABLE IF NOT EXISTS memory_entries (
        id UUID PRIMARY KEY,
        org_id TEXT NOT NULL DEFAULT 'default',
        scope TEXT NOT NULL,
        scope_ref TEXT NOT NULL,
        owner_user_id TEXT NOT NULL,
        content TEXT NOT NULL,
        kind TEXT,
        topic_key TEXT,
        is_redline BOOLEAN NOT NULL DEFAULT FALSE,
        status TEXT NOT NULL DEFAULT 'active',
        supersedes UUID REFERENCES memory_entries(id),
        extra JSONB NOT NULL DEFAULT '{}'::jsonb,
        ttl_at TIMESTAMPTZ,
        task_id TEXT,
        task_state TEXT,
        created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
        updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
        )"""
    )
    op.execute("ALTER TABLE memory_entries ADD COLUMN IF NOT EXISTS task_id TEXT")
    op.execute("ALTER TABLE memory_entries ADD COLUMN IF NOT EXISTS task_state TEXT")
    op.execute(
        """CREATE INDEX IF NOT EXISTS ix_mem_scope
        ON memory_entries (org_id, scope, scope_ref, status)"""
    )
    op.execute(
        """CREATE INDEX IF NOT EXISTS ix_mem_topic
        ON memory_entries (org_id, topic_key, status)"""
    )
    op.execute(
        """CREATE INDEX IF NOT EXISTS ix_mem_task
        ON memory_entries (org_id, task_id, status)"""
    )


def downgrade() -> None:
    op.drop_index("ix_mem_task", table_name="memory_entries")
    op.drop_index("ix_mem_topic", table_name="memory_entries")
    op.drop_index("ix_mem_scope", table_name="memory_entries")
    op.drop_table("memory_entries")
    op.drop_index("ix_agent_msg_session", table_name="agent_messages")
    op.drop_table("agent_messages")
    op.drop_table("agent_sessions")
