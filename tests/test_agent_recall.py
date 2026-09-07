"""RecallService / visible_scopes / 召回注入 测试(memory M3,纯逻辑,不需 PG)。

用假 MemoryStore 喂条目,验:可见作用域计算、跨作用域合并去冲突、redline 优先、
query 排序、prompt 注入格式。真 PG 端到端在 scripts/verify_memory_pg.py。
"""
from __future__ import annotations

from codev_platform.agent.memory_store import MemoryEntry, MemoryStore, _DEFAULT_ORG
from codev_platform.agent.recall_service import (
    LocalRecallService,
    _rank_for_query,
    visible_scopes,
)
from codev_platform.agent.context_plan import build_context_plan
from codev_platform.agent.prompts import _format_context_plan, build_code_understanding_system, CODE_UNDERSTANDING_SYSTEM


class FakeStore(MemoryStore):
    """按 (org,scope,scope_ref) 存内存,模拟 SqlMemoryStore.list_scope 行为。"""

    def __init__(self, entries: list[MemoryEntry]) -> None:
        self._entries = entries

    def list_scope(self, scope, scope_ref, org_id=_DEFAULT_ORG, limit=100):
        return [e for e in self._entries
                if e.org_id == org_id and e.scope == scope and e.scope_ref == scope_ref
                and e.status == "active"][:limit]

    def write(self, entry):  # 不用
        return entry.id

    def supersede(self, old_id, new_entry):
        return new_entry.id

    def forget(self, entry_id):
        return True

    def archive(self, entry_id):
        return True

    def archive_expired(self, org_id=None):
        return 0

    def set_task_state(self, task_id, task_state, org_id=_DEFAULT_ORG, owner_user_id=None):
        n = 0
        for e in self._entries:
            if (e.org_id == org_id and e.task_id == task_id and e.status == "active"
                    and (owner_user_id is None or e.owner_user_id == owner_user_id)):
                e.task_state = task_state
                n += 1
        return n


def _e(scope, ref, content, *, topic_key=None, is_redline=False, org="default"):
    return MemoryEntry(id=content, scope=scope, scope_ref=ref, owner_user_id="u",
                       content=content, org_id=org, topic_key=topic_key, is_redline=is_redline)


# ---- visible_scopes ----

def test_visible_scopes_full_tuple():
    assert visible_scopes("acme", "alice", "proj1") == [
        ("org", "org"), ("project", "proj1"), ("personal", "alice")]


def test_visible_scopes_no_project_no_user():
    assert visible_scopes("acme", None, None) == [("org", "org")]


# ---- recall 合并 + 冲突 + 排序 ----

def test_recall_merges_visible_scopes():
    store = FakeStore([
        _e("org", "org", "组织规则A"),
        _e("project", "proj1", "项目约定B"),
        _e("personal", "alice", "个人偏好C"),
        _e("personal", "bob", "别人的D"),          # 不同 user,不该召回
        _e("project", "proj2", "别的项目E"),         # 不同 project,不该召回
    ])
    svc = LocalRecallService(store)
    out = svc.recall(org_id="default", user_id="alice", project_id="proj1")
    contents = {m.content for m in out}
    assert contents == {"组织规则A", "项目约定B", "个人偏好C"}


def test_recall_conflict_personal_wins():
    store = FakeStore([
        _e("org", "org", "组织默认风格", topic_key="style"),
        _e("personal", "alice", "我的风格", topic_key="style"),
    ])
    out = LocalRecallService(store, default_policy="personal_first").recall(
        org_id="default", user_id="alice", project_id="proj1")
    assert len(out) == 1 and out[0].content == "我的风格"


def test_recall_redline_always_first_and_wins():
    store = FakeStore([
        _e("personal", "alice", "我想随便点", topic_key="style"),
        _e("org", "org", "提交不带AI痕迹", topic_key="style", is_redline=True),
    ])
    out = LocalRecallService(store).recall(org_id="default", user_id="alice", project_id="proj1")
    assert len(out) == 1 and out[0].content == "提交不带AI痕迹" and out[0].is_redline


def test_recall_org_isolation():
    store = FakeStore([
        _e("org", "org", "A组织规则", org="acme"),
        _e("org", "org", "B组织规则", org="other"),
    ])
    out = LocalRecallService(store).recall(org_id="acme", user_id="alice", project_id=None)
    assert {m.content for m in out} == {"A组织规则"}


def test_recall_limit_caps():
    store = FakeStore([_e("personal", "alice", f"pref{i}") for i in range(20)])
    out = LocalRecallService(store).recall(
        org_id="default", user_id="alice", project_id=None, limit=5)
    assert len(out) == 5


def test_recall_task_id_priority():
    # M1: 传 task_id 时, 当前任务的记忆置顶(任务上下文优先), 其余仍召回不丢。
    store = FakeStore([
        MemoryEntry(id="m1", scope="personal", scope_ref="alice", owner_user_id="u",
                    content="别的任务记忆", org_id="default", task_id="other-task"),
        MemoryEntry(id="m2", scope="personal", scope_ref="alice", owner_user_id="u",
                    content="当前任务目标", org_id="default", task_id="task-A"),
        MemoryEntry(id="m3", scope="personal", scope_ref="alice", owner_user_id="u",
                    content="无任务记忆", org_id="default", task_id=None),
    ])
    svc = LocalRecallService(store)
    out = svc.recall(org_id="default", user_id="alice", project_id=None, task_id="task-A")
    assert out[0].content == "当前任务目标"   # task-A 置顶
    assert len(out) == 3                       # 其余不被丢弃
    # 不传 task_id 时不加权(保原相对序: m1 在前)
    out_no = svc.recall(org_id="default", user_id="alice", project_id=None)
    assert out_no[0].content == "别的任务记忆"


def test_recall_redline_beats_task_match():
    # M1 不变量: redline(组织硬约束)仍压过 task 匹配的普通记忆, 任务加权不得破坏红线优先。
    store = FakeStore([
        MemoryEntry(id="t", scope="personal", scope_ref="alice", owner_user_id="u",
                    content="task 匹配普通记忆", org_id="default", task_id="task-A"),
        MemoryEntry(id="r", scope="org", scope_ref="org", owner_user_id="u",
                    content="组织红线", org_id="default", is_redline=True, task_id=None),
    ])
    out = LocalRecallService(store).recall(
        org_id="default", user_id="alice", project_id=None, task_id="task-A")
    assert out[0].content == "组织红线" and out[0].is_redline  # redline 压过 task 匹配


def test_set_task_state_owner_scoped():
    # M1: set_task_state 只改本人(owner 限定)+ 本 org 的该 task active 记忆, 防改他人任务。
    store = FakeStore([
        MemoryEntry(id="a", scope="personal", scope_ref="alice", owner_user_id="alice",
                    content="alice 任务", org_id="default", task_id="t1", task_state="active"),
        MemoryEntry(id="b", scope="personal", scope_ref="bob", owner_user_id="bob",
                    content="bob 任务", org_id="default", task_id="t1", task_state="active"),
    ])
    n = store.set_task_state("t1", "done", org_id="default", owner_user_id="alice")
    assert n == 1
    assert store._entries[0].task_state == "done"    # alice 的改了
    assert store._entries[1].task_state == "active"  # bob 的没动(防改他人)


def test_rank_redline_first_then_query_match():
    es = [
        _e("personal", "u", "无关内容"),
        _e("personal", "u", "讲 commit 规范的"),
        _e("org", "org", "硬约束", is_redline=True),
    ]
    ranked = _rank_for_query(es, "commit")
    assert ranked[0].content == "硬约束"            # redline 永远最前
    assert ranked[1].content == "讲 commit 规范的"   # query 命中其次


# ---- prompt 注入 ----

def test_format_context_plan_groups_and_redline():
    plan = build_context_plan([
        _e("org", "org", "提交不带AI痕迹", is_redline=True),
        _e("personal", "alice", "喜欢简洁"),
    ])
    block = _format_context_plan(plan)
    assert "提交不带AI痕迹" in block and "喜欢简洁" in block
    assert "redline" in block and "组织硬约束" in block
    assert "个人偏好" in block  # 分组小标题


def test_build_system_injects_memories():
    sys = build_code_understanding_system(
        project_id="proj1", user_id="alice", org_id="acme",
        context_plan=build_context_plan([_e("personal", "alice", "喜欢简洁")]))
    assert "【当前会话上下文】" in sys
    assert "【已知记忆" in sys and "喜欢简洁" in sys
    assert CODE_UNDERSTANDING_SYSTEM in sys


def test_build_system_no_memories_no_block():
    sys = build_code_understanding_system(project_id="proj1", user_id="alice", org_id="acme")
    assert "【已知记忆" not in sys
    assert "【当前会话上下文】" in sys


def test_build_system_empty_returns_base():
    assert build_code_understanding_system() == CODE_UNDERSTANDING_SYSTEM
