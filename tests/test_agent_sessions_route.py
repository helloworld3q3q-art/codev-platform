"""agent 会话域路由测试:GET /sessions(列会话)/ GET /sessions/messages(取消息)。

锁三件事:
- 形状:SessionOut(title 派生 / message_count)+ MessageOut(只回 user/assistant 有正文轮)。
- 隔离红线:store 按 (org_id, user_id) 取,路由把 X-User-Id / X-Org-Id 透传给 store。
- 中间纯 tool-call 轮(content 空)+ tool 角色被过滤,不进历史。

用真实 InMemorySessionStore 注入(经 deps.get_sessions),不连 PG。fastapi 缺失则跳过。
"""
from __future__ import annotations

import pytest

pytest.importorskip("fastapi")
from fastapi import FastAPI  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

from codev_platform.agent.brain import Message  # noqa: E402
from codev_platform.agent.routes import sessions as sessions_route  # noqa: E402
from codev_platform.agent.session import InMemorySessionStore  # noqa: E402


def _client(monkeypatch, store: InMemorySessionStore) -> TestClient:
    monkeypatch.setattr(sessions_route.deps, "get_sessions", lambda: store)
    app = FastAPI()
    app.include_router(sessions_route.router)
    return TestClient(app, raise_server_exceptions=True)


def _seed(store: InMemorySessionStore, user="alice", org="default") -> str:
    sid = store.new(user, org_id=org)
    store.append(sid, user,
                 Message(role="user", content="第一条问题就是标题"),
                 Message(role="assistant", content="", tool_calls=[]),  # 纯 tool 轮(无正文)
                 Message(role="tool", content="工具结果", tool_call_id="c1"),
                 Message(role="assistant", content="最终回答"),
                 org_id=org)
    return sid


def test_list_sessions_shape_and_title(monkeypatch):
    store = InMemorySessionStore()
    _seed(store)
    c = _client(monkeypatch, store)
    r = c.get("/sessions", headers={"X-User-Id": "alice"})
    assert r.status_code == 200
    body = r.json()
    assert len(body) == 1
    item = body[0]
    assert item["title"] == "第一条问题就是标题"
    assert item["message_count"] == 4
    assert item["session_id"] and item["created_at"] and item["updated_at"]


def test_messages_filters_empty_and_tool_rows(monkeypatch):
    store = InMemorySessionStore()
    sid = _seed(store)
    c = _client(monkeypatch, store)
    r = c.get("/sessions/messages", params={"session_id": sid}, headers={"X-User-Id": "alice"})
    assert r.status_code == 200
    msgs = r.json()
    # 4 条入库 → 只回 user + 最终 assistant(空 assistant 轮 / tool 轮被过滤)
    assert [m["role"] for m in msgs] == ["user", "assistant"]
    assert msgs[1]["content"] == "最终回答"


def test_org_user_isolation(monkeypatch):
    store = InMemorySessionStore()
    _seed(store, user="alice", org="orgA")
    c = _client(monkeypatch, store)
    # 另一个 user 列不到 alice 的会话
    assert c.get("/sessions", headers={"X-User-Id": "bob"}).json() == []
    # 另一个 org 同名 user 也列不到
    r = c.get("/sessions", headers={"X-User-Id": "alice", "X-Org-Id": "orgB"})
    assert r.json() == []
    # 本人本 org 能列到
    r = c.get("/sessions", headers={"X-User-Id": "alice", "X-Org-Id": "orgA"})
    assert len(r.json()) == 1


def test_messages_requires_session_id(monkeypatch):
    store = InMemorySessionStore()
    c = _client(monkeypatch, store)
    assert c.get("/sessions/messages", headers={"X-User-Id": "alice"}).status_code == 422
