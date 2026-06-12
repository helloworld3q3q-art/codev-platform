"""agent_tokens table (multi-org server Phase 2: IDE agent PG token).

Revision ID: 0006_agent_tokens
Revises: 0005_graph_tables
Create Date: 2026-06-12

Equivalent to tables.py ``agent_tokens`` Table. Only token sha256 hashes stored (no plaintext).
Closes the "config token vs PG user disconnect" hole: token->user lives in PG, and lookup joins
users.status so disabling a user in web invalidates their tokens on the next request.
``upgrade`` builds agent_tokens + user index; ``downgrade`` drops them (local/test only).
"""
from __future__ import annotations

from collections.abc import Sequence

from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision: str = "0006_agent_tokens"
down_revision: str | None = "0005_graph_tables"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "agent_tokens",
        sa.Column("token_hash", sa.Text(), nullable=False),
        sa.Column("user_id", sa.Text(), nullable=False),
        sa.Column("org_id", sa.Text(), nullable=False),
        sa.Column("projects", sa.Text(), nullable=True),
        sa.Column("label", sa.Text(), nullable=True),
        sa.Column("status", sa.Text(), nullable=False, server_default=sa.text("'ACTIVE'")),
        sa.Column("expires_at", sa.Float(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.ForeignKeyConstraint(["user_id"], ["users.user_id"]),
        sa.ForeignKeyConstraint(["org_id"], ["orgs.org_id"]),
        sa.PrimaryKeyConstraint("token_hash"),
    )
    op.create_index("ix_agent_tokens_user", "agent_tokens", ["user_id"], unique=False)


def downgrade() -> None:
    op.drop_index("ix_agent_tokens_user", table_name="agent_tokens")
    op.drop_table("agent_tokens")
