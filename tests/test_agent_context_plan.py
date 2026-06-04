"""context_plan(M2)测试: 分组 + budget 裁剪 + redline 永不裁 + 可调试。纯逻辑, 不需 PG。"""
from __future__ import annotations

from codev_platform.agent.context_plan import build_context_plan
from codev_platform.agent.memory_store import MemoryEntry


def _e(content, *, scope="personal", ref="alice", is_redline=False, task_id=None, org="default"):
    return MemoryEntry(id=content, scope=scope, scope_ref=ref, owner_user_id="u",
                       content=content, org_id=org, is_redline=is_redline, task_id=task_id)


def test_group_by_priority():
    plan = build_context_plan([
        _e("红线", is_redline=True),
        _e("任务", task_id="t1"),
        _e("项目", scope="project"),
        _e("个人", scope="personal"),
        _e("组织", scope="org"),
    ], task_id="t1")
    assert plan.groups["redline"][0].content == "红线"
    assert plan.groups["task"][0].content == "任务"
    assert plan.groups["project"][0].content == "项目"
    assert plan.groups["personal"][0].content == "个人"
    assert plan.groups["org"][0].content == "组织"
    assert plan.kept == 5 and plan.dropped == 0


def test_budget_caps_low_priority():
    # budget=2: redline + task 保留, project/personal 被裁(低优先级)
    plan = build_context_plan([
        _e("红线", is_redline=True), _e("任务", task_id="t1"),
        _e("项目", scope="project"), _e("个人", scope="personal"),
    ], task_id="t1", budget_max=2)
    assert plan.kept == 2 and plan.dropped == 2
    assert "redline" in plan.groups and "task" in plan.groups
    assert "project" not in plan.groups and "personal" not in plan.groups


def test_redline_never_dropped_even_over_budget():
    # 3 条 redline + budget=1: redline 全留(组织硬约束不可裁), 非红线被挤掉
    plan = build_context_plan([
        _e("红线1", is_redline=True), _e("红线2", is_redline=True),
        _e("红线3", is_redline=True), _e("个人", scope="personal"),
    ], budget_max=1)
    assert len(plan.groups["redline"]) == 3   # redline 全留, 不受 budget
    assert "personal" not in plan.groups       # budget 已被 redline 占满, 非红线挤掉
    assert plan.kept == 3 and plan.dropped == 1


def test_iter_kept_priority_order():
    plan = build_context_plan([
        _e("个人", scope="personal"), _e("红线", is_redline=True), _e("任务", task_id="t1"),
    ], task_id="t1")
    order = [m.content for m in plan.iter_kept()]
    assert order == ["红线", "任务", "个人"]   # redline > task > personal


def test_explain_debuggable():
    plan = build_context_plan([_e("红线", is_redline=True), _e("个人")], budget_max=8)
    s = plan.explain()
    assert "recalled=2" in s and "kept=2" in s and "redline=1" in s


def test_empty():
    plan = build_context_plan([])
    assert plan.is_empty() and plan.kept == 0
    assert list(plan.iter_kept()) == []
