"""reindex_jobs table (多机共享 reindex 队列, PgJobQueue).

替代单机 FileSpoolQueue: per (project_id,kind) 合并(复合主键 = FileSpool 同 key 合并语义);
lease(claimed_by/lease_expires_at)+ FOR UPDATE SKIP LOCKED 做**跨机原子认领**(防多 worker
抢同 job); enqueued_at(epoch float, 对齐 domain Job time.time())做行版本, 保 dirty 重入
(运行期被重新 enqueue → enqueued_at 变新 → complete 不删 → 下轮重跑)。

upgrade 建 reindex_jobs + 认领索引; downgrade 删(local/test only, 生产不 downgrade)。
"""
from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "0004_reindex_jobs"
down_revision: str | None = "0003_sessions"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "reindex_jobs",
        sa.Column("project_id", sa.Text(), nullable=False),
        sa.Column("kind", sa.Text(), nullable=False),
        sa.Column("enqueued_at", sa.Float(), nullable=False),
        sa.Column("status", sa.Text(), server_default=sa.text("'pending'"), nullable=False),
        sa.Column("claimed_by", sa.Text(), nullable=True),
        sa.Column("lease_expires_at", sa.Float(), nullable=True),
        # claim_token: 每次认领的唯一戳, complete 据此精确删自己那次认领(防 lease 接管误删他人在跑行)。
        sa.Column("claim_token", sa.Text(), nullable=True),
        sa.PrimaryKeyConstraint("project_id", "kind"),
    )
    # 认领扫描: status='pending' 或 租约过期的 running, 按 enqueued_at(FIFO)
    op.create_index("ix_reindex_jobs_claim", "reindex_jobs", ["status", "enqueued_at"], unique=False)


def downgrade() -> None:
    op.drop_index("ix_reindex_jobs_claim", table_name="reindex_jobs")
    op.drop_table("reindex_jobs")
