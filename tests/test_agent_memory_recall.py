"""冲突消解纯逻辑测试 —— 红线最高 + policy(personal_first / org_first)+ topic_key 去重。"""
from __future__ import annotations

from codev_platform.agent.memory_store import MemoryEntry
from codev_platform.agent.memory_recall import resolve_conflicts


def _e(scope, content, topic_key=None, is_redline=False):
    return MemoryEntry(id=content, scope=scope, scope_ref="x", owner_user_id="u",
                       content=content, topic_key=topic_key, is_redline=is_redline)


def test_no_topic_key_all_passthrough():
    es = [_e("personal", "a"), _e("project", "b")]
    out = resolve_conflicts(es)
    assert {m.content for m in out} == {"a", "b"}  # 无 topic_key 不冲突,全留


def test_redline_always_wins():
    es = [
        _e("personal", "个人值", topic_key="t"),
        _e("org", "红线值", topic_key="t", is_redline=True),
    ]
    out = resolve_conflicts(es, policy="personal_first")  # 即便 personal_first,红线仍压
    assert len(out) == 1 and out[0].content == "红线值"


def test_personal_first_personal_wins_nonredline():
    es = [
        _e("org", "org 值", topic_key="t"),
        _e("team", "team 值", topic_key="t"),
        _e("personal", "个人值", topic_key="t"),
    ]
    out = resolve_conflicts(es, policy="personal_first")
    assert len(out) == 1 and out[0].content == "个人值"


def test_org_first_org_wins_nonredline():
    es = [
        _e("personal", "个人值", topic_key="t"),
        _e("org", "org 值", topic_key="t"),
    ]
    out = resolve_conflicts(es, policy="org_first")
    assert len(out) == 1 and out[0].content == "org 值"


def test_mixed_topics_grouped_independently():
    es = [
        _e("personal", "p-style", topic_key="style"),
        _e("org", "o-style", topic_key="style"),
        _e("org", "o-rule", topic_key="rule", is_redline=True),
        _e("personal", "p-rule", topic_key="rule"),
        _e("project", "free-note"),  # 无 topic_key
    ]
    out = resolve_conflicts(es, policy="personal_first")
    by_content = {m.content for m in out}
    assert "p-style" in by_content      # style 组 personal 胜
    assert "o-rule" in by_content        # rule 组红线胜
    assert "free-note" in by_content     # 无 topic_key 保留
    assert "o-style" not in by_content and "p-rule" not in by_content  # 被压的不在
    assert len(out) == 3
