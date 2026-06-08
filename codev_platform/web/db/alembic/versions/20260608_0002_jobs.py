"""jobs table (codex P2: job history persistence).

Revision ID: 0002_jobs
Revises: 0001_baseline
Create Date: 2026-06-08

Equivalent to codev_platform/web/db/tables.py ``jobs`` Table. created_at/updated_at are
epoch float (sa.Float, NOT TIMESTAMPTZ) to match domain.Job ``time.time()`` semantics --
repo does no datetime conversion. ``upgrade`` builds jobs + ix_jobs_project; ``downgrade``
drops them (local/test only; production never downgrades).
"""
from __future__ import annotations

from collections.abc import Sequence

from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision: str = "0002_jobs"
down_revision: str | None = "0001_baseline"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "jobs",
        sa.Column("job_id", sa.Text(), nullable=False),
        sa.Column("project_id", sa.Text(), nullable=False),
        sa.Column("job_type", sa.Text(), nullable=False),
        sa.Column("status", sa.Text(), server_default=sa.text("'Pending'"), nullable=False),
        sa.Column("created_at", sa.Float(), nullable=False),
        sa.Column("updated_at", sa.Float(), nullable=False),
        sa.Column("error", sa.Text(), nullable=True),
        sa.PrimaryKeyConstraint("job_id"),
    )
    op.create_index("ix_jobs_project", "jobs", ["project_id"], unique=False)


def downgrade() -> None:
    op.drop_index("ix_jobs_project", table_name="jobs")
    op.drop_table("jobs")
