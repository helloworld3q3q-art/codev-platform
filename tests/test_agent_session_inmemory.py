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


def test_list_sessions_title_count_and_isolation():
    s = InMemorySessionStore()
    sid = s.new("alice")
    s.append(sid, "alice",
             Message(role="user", content="标题问题"),
             Message(role="assistant", content="答"))
    s.new("bob")  # 别人会话不混入
    rows = s.list_sessions("alice")
    assert len(rows) == 1
    assert rows[0].session_id == sid
    assert rows[0].title == "标题问题"
    assert rows[0].message_count == 2
    assert rows[0].created_at is not None and rows[0].updated_at is not None
    # 跨 user / 跨 org 列不到
    assert s.list_sessions("carol") == []
    assert s.list_sessions("alice", org_id="orgX") == []


def test_list_sessions_project_isolation():
    s = InMemorySessionStore()
    a = s.new("u", project_id="projA")
    s.append(a, "u", Message(role="user", content="a"))
    b = s.new("u", project_id="projB")
    s.append(b, "u", Message(role="user", content="b"))
    assert [r.session_id for r in s.list_sessions("u", project_id="projA")] == [a]
    assert [r.session_id for r in s.list_sessions("u", project_id="projB")] == [b]
    assert len(s.list_sessions("u")) == 2  # 不传 project_id = 不过滤


def test_get_messages_project_isolation():
    # P1 修复(codex bug-edge-audit): get 必须按 project_id 隔离 —— 否则同 org/user 下知道别项目
    # session_id, 就能在本项目上下文读别项目对话历史(对齐 list_sessions 的项目过滤)。
    s = InMemorySessionStore()
    a = s.new("u", project_id="projA")
    s.append(a, "u", Message(role="user", content="secret-a"))
    b = s.new("u", project_id="projB")
    s.append(b, "u", Message(role="user", content="secret-b"))
    # 带 projA 读 session_b(别项目) → 空(跨项目隔离闸)
    assert s.get(b, "u", project_id="projA") == []
    # 带正确 project 能读
    assert [m.content for m in s.get(b, "u", project_id="projB")] == ["secret-b"]
    # 不传 project_id(向后兼容, 内部调用) → 照旧能读
    assert len(s.get(b, "u")) == 1


def test_list_sessions_recent_first():
    s = InMemorySessionStore()
    a = s.new("u")
    b = s.new("u")
    s.append(a, "u", Message(role="user", content="a"))  # a 后活跃 → 排前
    rows = s.list_sessions("u")
    assert [r.session_id for r in rows] == [a, b]


def test_project_isolation_has_and_get():
    # P1#1(安全审计): 会话绑 project A, 用 project B 校验 → has 拒绝复用 + get 返空,
    # 防项目 A 的 session_id 在项目 B 请求里被复用导致历史跨项目泄漏/污染。
    s = InMemorySessionStore()
    sid = s.new("alice", project_id="A")
    s.append(sid, "alice", Message(role="user", content="secret-A"))
    assert s.has(sid, "alice", project_id="A") is True
    assert s.has(sid, "alice", project_id="B") is False        # 不属 B → 拒绝复用
    assert s.has(sid, "alice") is True                          # 不传 project_id → 不过滤(兼容)
    assert s.get(sid, "alice", project_id="A")                  # A 能读自己历史
    assert s.get(sid, "alice", project_id="B") == []            # B 读不到 A 的历史
