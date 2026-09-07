"""sessions table (backend-deep P1-3: login session persistence).

Revision ID: 0003_sessions
Revises: 0002_jobs
Create Date: 2026-06-08

Equivalent to tables.py ``sessions`` Table. Only token sha256 hashes stored (no plaintext);
access/refresh expiry as epoch float. ``upgrade`` builds sessions + 3 indexes; ``downgrade``
drops them (local/test only; production never downgrades).
"""
from __future__ import annotations

from collections.abc import Sequence

from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision: str = "0003_sessions"
down_revision: str | None = "0002_jobs"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "sessions",
        sa.Column("session_id", sa.Text(), nullable=False),
        sa.Column("username", sa.Text(), nullable=False),
        sa.Column("org_id", sa.Text(), nullable=False),
        sa.Column("access_hash", sa.Text(), nullable=False),
        sa.Column("refresh_hash", sa.Text(), nullable=False),
        sa.Column("access_expires_at", sa.Float(), nullable=False),
        sa.Column("refresh_expires_at", sa.Float(), nullable=False),
        sa.PrimaryKeyConstraint("session_id"),
    )
    op.create_index("ix_sessions_access", "sessions", ["access_hash"], unique=False)
    op.create_index("ix_sessions_refresh", "sessions", ["refresh_hash"], unique=False)
    op.create_index("ix_sessions_username", "sessions", ["username"], unique=False)


def downgrade() -> None:
    op.drop_index("ix_sessions_username", table_name="sessions")
    op.drop_index("ix_sessions_refresh", table_name="sessions")
    op.drop_index("ix_sessions_access", table_name="sessions")
    op.drop_table("sessions")
