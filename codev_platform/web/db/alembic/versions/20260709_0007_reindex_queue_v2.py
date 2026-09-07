"""reindex_jobs v2 的 pending/active/result 对称列。

Revision ID: 0007_reindex_queue_v2
Revises: 0006_agent_tokens
Create Date: 2026-07-09

保留每个 key 一行的结构，并允许 dirty pending 与 active lease 同时存在。
"""
from __future__ import annotations

from collections.abc import Sequence

from alembic import op

# Alembic 版本标识。
revision: str = "0007_reindex_queue_v2"
down_revision: str | None = "0006_agent_tokens"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # 迁移允许安全重跑；worker 运行期只验证契约，不执行 DDL。
    op.execute("""
        ALTER TABLE reindex_jobs
            ADD COLUMN IF NOT EXISTS pending_token TEXT,
            ADD COLUMN IF NOT EXISTS pending_enqueued_at DOUBLE PRECISION,
            ADD COLUMN IF NOT EXISTS pending_updated_at DOUBLE PRECISION,
            ADD COLUMN IF NOT EXISTS pending_meta_json TEXT,
            ADD COLUMN IF NOT EXISTS active_enqueued_at DOUBLE PRECISION,
            ADD COLUMN IF NOT EXISTS active_meta_json TEXT,
            ADD COLUMN IF NOT EXISTS active_heartbeat_at DOUBLE PRECISION,
            ADD COLUMN IF NOT EXISTS result_status TEXT,
            ADD COLUMN IF NOT EXISTS result_finished_at DOUBLE PRECISION,
            ADD COLUMN IF NOT EXISTS result_enqueued_at DOUBLE PRECISION,
            ADD COLUMN IF NOT EXISTS result_meta_json TEXT
    """)


def downgrade() -> None:
    op.execute("""
        ALTER TABLE reindex_jobs
            DROP COLUMN IF EXISTS result_meta_json,
            DROP COLUMN IF EXISTS result_enqueued_at,
            DROP COLUMN IF EXISTS result_finished_at,
            DROP COLUMN IF EXISTS result_status,
            DROP COLUMN IF EXISTS active_heartbeat_at,
            DROP COLUMN IF EXISTS active_meta_json,
            DROP COLUMN IF EXISTS active_enqueued_at,
            DROP COLUMN IF EXISTS pending_meta_json,
            DROP COLUMN IF EXISTS pending_updated_at,
            DROP COLUMN IF EXISTS pending_enqueued_at,
            DROP COLUMN IF EXISTS pending_token
    """)
