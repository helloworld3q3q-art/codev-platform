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


def test_empty_input():
    assert resolve_conflicts([]) == []


def test_same_priority_keeps_first_seen():
    # 同 topic + 同 scope + 同 redline 标志 = 完全同优先级 → 保留列表中先出现的一条(稳定契约)
    es = [_e("personal", "先到", topic_key="t"), _e("personal", "后到", topic_key="t")]
    out = resolve_conflicts(es, policy="personal_first")
    assert len(out) == 1 and out[0].content == "先到"


def test_redline_vs_redline_org_wins_regardless_of_policy():
    # 两条都 redline 时,按 org 治理序裁决,不受 policy 影响(plan §3.5 红线不可配)
    es = [
        _e("personal", "个人红线", topic_key="t", is_redline=True),
        _e("org", "组织红线", topic_key="t", is_redline=True),
    ]
    # 即便 personal_first,org 红线仍胜(否则个人红线压组织合规红线 = 违规)
    out_pf = resolve_conflicts(es, policy="personal_first")
    assert len(out_pf) == 1 and out_pf[0].content == "组织红线"
    out_of = resolve_conflicts(es, policy="org_first")
    assert len(out_of) == 1 and out_of[0].content == "组织红线"


def test_unknown_policy_falls_back_to_default():
    es = [_e("org", "org 值", topic_key="t"), _e("personal", "个人值", topic_key="t")]
    out = resolve_conflicts(es, policy="bogus_policy")  # 未知 policy → personal_first 兜底
    assert len(out) == 1 and out[0].content == "个人值"


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
