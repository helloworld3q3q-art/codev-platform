"""baseline: RBAC/account 7 tables (orgs, users, org_members, teams,
team_members, projects, project_access).

Revision ID: 0001_baseline
Revises:
Create Date: 2026-06-03

Equivalent to codev_platform/web/db/tables.py ``metadata`` (the single source of
truth) and to the legacy rbac_store_pg._SCHEMA it superseded. ``upgrade`` builds
all 7 tables + 3 indexes; ``downgrade`` drops them in FK-safe order.

存量无版本库禁止人工 stamp；迁移协调器会先与隔离参考 schema 做精确指纹匹配，再只标记
已证明的字面 revision，并在同一事务内升级与复证。
"""
from __future__ import annotations

from collections.abc import Sequence

from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision: str = "0001_baseline"
down_revision: str | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "orgs",
        sa.Column("org_id", sa.Text(), nullable=False),
        sa.Column("name", sa.Text(), nullable=True),
        sa.Column("status", sa.Text(), server_default=sa.text("'ACTIVE'"), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint("org_id"),
    )
    op.create_table(
        "users",
        sa.Column("user_id", sa.Text(), nullable=False),
        sa.Column("display_name", sa.Text(), nullable=True),
        sa.Column("password_hash", sa.Text(), nullable=True),
        sa.Column("status", sa.Text(), server_default=sa.text("'ACTIVE'"), nullable=False),
        sa.Column("email", sa.Text(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint("user_id"),
    )
    op.create_table(
        "org_members",
        sa.Column("org_id", sa.Text(), nullable=False),
        sa.Column("user_id", sa.Text(), nullable=False),
        sa.Column("org_role", sa.Text(), server_default=sa.text("'member'"), nullable=False),
        sa.ForeignKeyConstraint(["org_id"], ["orgs.org_id"]),
        sa.ForeignKeyConstraint(["user_id"], ["users.user_id"]),
        sa.PrimaryKeyConstraint("org_id", "user_id"),
    )
    op.create_table(
        "teams",
        sa.Column("team_id", sa.Text(), nullable=False),
        sa.Column("org_id", sa.Text(), nullable=False),
        sa.Column("name", sa.Text(), nullable=True),
        sa.ForeignKeyConstraint(["org_id"], ["orgs.org_id"]),
        sa.PrimaryKeyConstraint("team_id"),
    )
    op.create_table(
        "team_members",
        sa.Column("team_id", sa.Text(), nullable=False),
        sa.Column("user_id", sa.Text(), nullable=False),
        sa.Column("role", sa.Text(), server_default=sa.text("'member'"), nullable=False),
        sa.ForeignKeyConstraint(["team_id"], ["teams.team_id"]),
        sa.ForeignKeyConstraint(["user_id"], ["users.user_id"]),
        sa.PrimaryKeyConstraint("team_id", "user_id"),
    )
    op.create_table(
        "projects",
        sa.Column("project_id", sa.Text(), nullable=False),
        sa.Column("org_id", sa.Text(), nullable=False),
        sa.Column("display_name", sa.Text(), nullable=True),
        sa.ForeignKeyConstraint(["org_id"], ["orgs.org_id"]),
        sa.PrimaryKeyConstraint("project_id"),
    )
    op.create_table(
        "project_access",
        sa.Column("project_id", sa.Text(), nullable=False),
        sa.Column("principal", sa.Text(), nullable=False),
        sa.Column("principal_kind", sa.Text(), nullable=False),
        sa.Column("role", sa.Text(), server_default=sa.text("'member'"), nullable=False),
        sa.ForeignKeyConstraint(["project_id"], ["projects.project_id"]),
        sa.PrimaryKeyConstraint("project_id", "principal"),
    )

    op.create_index("ix_org_members_user", "org_members", ["user_id"], unique=False)
    op.create_index("ix_team_members_user", "team_members", ["user_id"], unique=False)
    op.create_index("ix_teams_org", "teams", ["org_id"], unique=False)


def downgrade() -> None:
    # Drop in reverse FK-dependency order. NOTE: production must NOT downgrade
    # (drops tables); this is for local/test only -- mirrors the "published
    # migration never reversed" discipline.
    op.drop_index("ix_teams_org", table_name="teams")
    op.drop_index("ix_team_members_user", table_name="team_members")
    op.drop_index("ix_org_members_user", table_name="org_members")
    op.drop_table("project_access")
    op.drop_table("projects")
    op.drop_table("team_members")
    op.drop_table("teams")
    op.drop_table("org_members")
    op.drop_table("users")
    op.drop_table("orgs")
