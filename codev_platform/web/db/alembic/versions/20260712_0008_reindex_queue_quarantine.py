"""为 reindex_jobs 增加进程死亡未确认时的持久隔离围栏。

Revision ID: 0008_reindex_queue_quarantine
Revises: 0007_reindex_queue_v2
Create Date: 2026-07-12
"""
from __future__ import annotations

from collections.abc import Sequence

from alembic import op

# Alembic 版本标识。
revision: str = "0008_reindex_queue_quarantine"
down_revision: str | None = "0007_reindex_queue_v2"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute("""
        ALTER TABLE reindex_jobs
            ADD COLUMN IF NOT EXISTS quarantine_reason TEXT,
            ADD COLUMN IF NOT EXISTS quarantine_attempt_id TEXT,
            ADD COLUMN IF NOT EXISTS quarantine_fence TEXT,
            ADD COLUMN IF NOT EXISTS quarantine_process_identity TEXT,
            ADD COLUMN IF NOT EXISTS quarantine_containment_kind TEXT,
            ADD COLUMN IF NOT EXISTS quarantine_native_ref TEXT,
            ADD COLUMN IF NOT EXISTS quarantine_at DOUBLE PRECISION
    """)


def downgrade() -> None:
    op.execute("""
        ALTER TABLE reindex_jobs
            DROP COLUMN IF EXISTS quarantine_at,
            DROP COLUMN IF EXISTS quarantine_native_ref,
            DROP COLUMN IF EXISTS quarantine_containment_kind,
            DROP COLUMN IF EXISTS quarantine_process_identity,
            DROP COLUMN IF EXISTS quarantine_fence,
            DROP COLUMN IF EXISTS quarantine_attempt_id,
            DROP COLUMN IF EXISTS quarantine_reason
    """)
