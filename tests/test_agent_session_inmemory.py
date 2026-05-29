"""InMemorySessionStore 隔离测试 —— 按 (org_id, user_id, session_id) 三层隔离。"""
from __future__ import annotations

from codev_platform.agent.brain import Message
from codev_platform.agent.session import InMemorySessionStore


def test_user_isolation():
    s = InMemorySessionStore()
    sid = s.new("alice")
    s.append(sid, "alice", Message(role="user", content="hi"))
    assert s.has(sid, "alice") and not s.has(sid, "bob")   # 别的 user 看不到
    assert s.get(sid, "bob") == []


def test_org_isolation():
    s = InMemorySessionStore()
    sid = s.new("alice", org_id="orgA")
    s.append(sid, "alice", Message(role="user", content="hi"), org_id="orgA")
    # 同 user 同 session_id,但不同 org → 互不可见(多 org 期同名 user 不撞)
    assert s.has(sid, "alice", org_id="orgA")
    assert not s.has(sid, "alice", org_id="orgB")
    assert s.get(sid, "alice", org_id="orgB") == []
    assert len(s.get(sid, "alice", org_id="orgA")) == 1


def test_default_org_when_omitted():
    s = InMemorySessionStore()
    sid = s.new("alice")  # 默认 org='default'
    assert s.has(sid, "alice", org_id="default")
