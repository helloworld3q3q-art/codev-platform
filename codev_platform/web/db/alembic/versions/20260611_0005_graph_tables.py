"""graph store PG tables (统一图谱多机共享真值源, PgGraphStore).

5 表 = graph/store.py sqlite schema 同构(nodes/edges/evidences/findings/ingest_meta), 唯一差异:
ingest_meta 加 project_id 入 PK —— sqlite per-file 库无 project_id(文件即 project 边界), 共享 PG
库必须按 project_id 隔离否则不同 project 同 plugin 计数撞 PK。

真值源是 PgGraphStore._SCHEMA_DDL(运行期 CREATE IF NOT EXISTS 自足); 本迁移 + tables.py 登记
是为受管迁移 + alembic autogenerate 不误 DROP + parity 守护(同 reindex_jobs)。

upgrade 建 5 表 + 4 查询索引; downgrade 删(local/test only, 生产不 downgrade)。
"""
from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "0005_graph_tables"
down_revision: str | None = "0004_reindex_jobs"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "graph_nodes",
        sa.Column("id", sa.Text(), nullable=False),
        sa.Column("plugin", sa.Text(), nullable=False),
        sa.Column("kind", sa.Text(), nullable=False),
        sa.Column("name", sa.Text(), nullable=False),
        sa.Column("project_id", sa.Text(), nullable=False),
        sa.Column("file", sa.Text(), nullable=True),
        sa.Column("line", sa.Integer(), nullable=True),
        sa.Column("language", sa.Text(), nullable=True),
        sa.Column("meta_json", sa.Text(), nullable=True),
        sa.PrimaryKeyConstraint("id", "plugin"),
    )
    op.create_table(
        "graph_edges",
        sa.Column("project_id", sa.Text(), nullable=False),
        sa.Column("plugin", sa.Text(), nullable=False),
        sa.Column("source", sa.Text(), nullable=False),
        sa.Column("target", sa.Text(), nullable=False),
        sa.Column("kind", sa.Text(), nullable=False),
        sa.Column("confidence", sa.Float(), server_default=sa.text("1.0"), nullable=True),
        sa.Column("meta_json", sa.Text(), nullable=True),
        sa.PrimaryKeyConstraint("project_id", "plugin", "source", "target", "kind"),
    )
    op.create_table(
        "graph_evidences",
        sa.Column("project_id", sa.Text(), nullable=False),
        sa.Column("plugin", sa.Text(), nullable=False),
        sa.Column("seq", sa.Integer(), nullable=False),
        sa.Column("source", sa.Text(), nullable=False),
        sa.Column("detail", sa.Text(), nullable=False),
        sa.Column("file", sa.Text(), nullable=True),
        sa.Column("line", sa.Integer(), nullable=True),
        sa.Column("confidence", sa.Float(), server_default=sa.text("1.0"), nullable=True),
        sa.Column("meta_json", sa.Text(), nullable=True),
        sa.PrimaryKeyConstraint("project_id", "plugin", "seq"),
    )
    op.create_table(
        "graph_findings",
        sa.Column("project_id", sa.Text(), nullable=False),
        sa.Column("plugin", sa.Text(), nullable=False),
        sa.Column("seq", sa.Integer(), nullable=False),
        sa.Column("kind", sa.Text(), nullable=False),
        sa.Column("severity", sa.Text(), nullable=False),
        sa.Column("title", sa.Text(), nullable=False),
        sa.Column("detail", sa.Text(), nullable=True),
        sa.Column("node_ids_json", sa.Text(), nullable=True),
        sa.Column("evidence_ids_json", sa.Text(), nullable=True),
        sa.Column("meta_json", sa.Text(), nullable=True),
        sa.PrimaryKeyConstraint("project_id", "plugin", "seq"),
    )
    op.create_table(
        "graph_ingest_meta",
        sa.Column("project_id", sa.Text(), nullable=False),
        sa.Column("plugin", sa.Text(), nullable=False),
        sa.Column("plugin_version", sa.Text(), nullable=True),
        sa.Column("node_count", sa.Integer(), nullable=True),
        sa.Column("edge_count", sa.Integer(), nullable=True),
        sa.Column("evidence_count", sa.Integer(), nullable=True),
        sa.Column("finding_count", sa.Integer(), nullable=True),
        sa.Column("ingested_at", sa.Text(), nullable=True),
        sa.PrimaryKeyConstraint("project_id", "plugin"),
    )
    op.create_index("ix_graph_nodes_kind", "graph_nodes", ["project_id", "kind"], unique=False)
    op.create_index("ix_graph_nodes_name", "graph_nodes", ["project_id", "name"], unique=False)
    op.create_index("ix_graph_edges_source", "graph_edges", ["project_id", "source"], unique=False)
    op.create_index("ix_graph_edges_target", "graph_edges", ["project_id", "target"], unique=False)


def downgrade() -> None:
    op.drop_index("ix_graph_edges_target", table_name="graph_edges")
    op.drop_index("ix_graph_edges_source", table_name="graph_edges")
    op.drop_index("ix_graph_nodes_name", table_name="graph_nodes")
    op.drop_index("ix_graph_nodes_kind", table_name="graph_nodes")
    op.drop_table("graph_ingest_meta")
    op.drop_table("graph_findings")
    op.drop_table("graph_evidences")
    op.drop_table("graph_edges")
    op.drop_table("graph_nodes")
