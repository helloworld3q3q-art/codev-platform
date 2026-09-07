"""不可变 wheel 内 Alembic 迁移链和包数据测试。"""

from __future__ import annotations

from codev_platform.web.db.migration_postgres import load_migration_plan


def test_packaged_migration_chain_has_single_expected_head() -> None:
    plan = load_migration_plan()

    assert plan.head == "0009_agent_memory"
    assert plan.revisions[0] == "0001_baseline"
    assert plan.revisions[-2:] == (
        "0008_reindex_queue_quarantine",
        "0009_agent_memory",
    )
