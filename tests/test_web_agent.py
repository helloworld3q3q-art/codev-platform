"""Web Agent 路由片测试 (B1, plan §十五 Agent)。

覆盖: 路由通 (代理 AgentClient 返回 → envelope 投影) + 越权 project 被 require_project_access
拒 (token 模式白名单外) + 下游不可达 → 503 envelope。AgentClient 用 mock 替换 (不起真 agent)。
本 venv 未装 fastapi → skip。
"""
from __future__ import annotations

import pytest

pytest.importorskip("fastapi")

from fastapi.testclient import TestClient  # noqa: E402

from codev_platform.core.errors import ErrorCode, PlatformError  # noqa: E402
from codev_platform.core.httpkit import build_app  # noqa: E402
from codev_platform.gateway.auth import token_hash  # noqa: E402
from codev_platform.web.routes import agent  # noqa: E402

_PASSTHROUGH_CFG = {"gateway": {"auth_mode": "passthrough"}, "projects": {}}
_HEADERS = {"X-Project-Id": "demo-proj"}

# token 模式: 该 token 仅授权 allowed-proj, 用于验证越权 project 被拒。
_TOKEN = "secret-tok"
_TOKEN_CFG = {
    "gateway": {
        "auth_mode": "token",
        "tokens": {token_hash(_TOKEN): {"user_id": "u1", "org_id": "o1", "projects": ["allowed-proj"]}},
    },
    "projects": {},
}
_BEARER = {"Authorization": f"Bearer {_TOKEN}"}


class _FakeAgentClient:
    """记录入参 + 返回固定 agent /chat 形状的 mock。"""

    def __init__(self, raw: dict | None = None, exc: Exception | None = None) -> None:
        self._raw = raw or {}
        self._exc = exc
        self.calls: list[tuple] = []

    def chat(self, ident, body: dict) -> dict:
        self.calls.append((ident, body))
        if self._exc is not None:
            raise self._exc
        return self._raw

    def list_sessions(self, ident, params: dict) -> list:
        self.calls.append((ident, params))
        if self._exc is not None:
            raise self._exc
        return self._raw

    def session_messages(self, ident, params: dict) -> list:
        self.calls.append((ident, params))
        if self._exc is not None:
            raise self._exc
        return self._raw


def _client(cfg: dict) -> TestClient:
    return TestClient(build_app(title="t", routers=[agent.router], cfg=cfg))


def test_chat_proxies_and_projects_envelope():
    fake = _FakeAgentClient(raw={
        "session_id": "s1",
        "answer": "hello",
        "steps": [{"n": 1, "tool": "search", "args": {"q": "x"}, "result_summary": "ok"}],
        "usage": {"tokens": 12},
        "stop_reason": "final",
    })
    agent.agent_client = fake
    c = _client(_PASSTHROUGH_CFG)
    r = c.post("/api/v1/agent/chat", json={"question": "hi", "maxSteps": 3}, headers=_HEADERS)
    assert r.status_code == 200
    body = r.json()
    assert body["result"] == 0
    data = body["data"]
    assert data["sessionId"] == "s1"
    assert data["answer"] == "hello"
    assert data["stopReason"] == "final"
    assert data["usage"] == {"tokens": 12}
    assert data["steps"][0]["resultSummary"] == "ok"
    assert body["requestId"]
    # 鉴权后的 project_id 转发到 agent body
    _ident, fwd = fake.calls[0]
    assert fwd["project_id"] == "demo-proj"
    assert fwd["question"] == "hi"
    assert fwd["max_steps"] == 3


def test_chat_unauthorized_project_is_denied(monkeypatch):
    # require_project_access 内部读 load_config() (非注入 cfg), 故 patch 其真值源为 token cfg。
    monkeypatch.setattr(
        "codev_platform.core.httpkit.permissions.load_config", lambda: _TOKEN_CFG
    )
    fake = _FakeAgentClient(raw={"session_id": "s", "answer": "", "stop_reason": "final"})
    agent.agent_client = fake
    c = _client(_TOKEN_CFG)
    # token 仅授权 allowed-proj, 请求 other-proj → require_project_access 拒
    r = c.post(
        "/api/v1/agent/chat",
        json={"question": "hi"},
        headers={**_BEARER, "X-Project-Id": "other-proj"},
    )
    assert r.status_code == 403
    assert r.json()["errors"][0]["errorCode"] == "access_denied"
    # 越权未触达下游
    assert fake.calls == []


def test_chat_downstream_unavailable_is_503():
    fake = _FakeAgentClient(exc=PlatformError(ErrorCode.UPSTREAM_UNAVAILABLE, "agent 后端不可达"))
    agent.agent_client = fake
    c = _client(_PASSTHROUGH_CFG)
    r = c.post("/api/v1/agent/chat", json={"question": "hi"}, headers=_HEADERS)
    assert r.status_code == 503
    body = r.json()
    assert body["result"] == 1
    assert body["errors"][0]["errorCode"] == "upstream_unavailable"
    assert body["requestId"]


def test_sessions_list_proxies_and_envelope():
    fake = _FakeAgentClient(raw=[
        {"session_id": "s1", "title": "问题一", "message_count": 4,
         "created_at": "2026-06-04T00:00:00Z", "updated_at": "2026-06-04T01:00:00Z"},
    ])
    agent.agent_client = fake
    c = _client(_PASSTHROUGH_CFG)
    r = c.get("/api/v1/agent/sessions", headers=_HEADERS)
    assert r.status_code == 200
    body = r.json()
    assert body["result"] == 0
    data = body["data"]
    assert data[0]["sessionId"] == "s1"
    assert data[0]["title"] == "问题一"
    assert data[0]["messageCount"] == 4
    assert data[0]["updatedAt"] == "2026-06-04T01:00:00Z"


def test_session_messages_proxies_and_envelope():
    fake = _FakeAgentClient(raw=[
        {"role": "user", "content": "hi"},
        {"role": "assistant", "content": "hello"},
    ])
    agent.agent_client = fake
    c = _client(_PASSTHROUGH_CFG)
    r = c.get("/api/v1/agent/sessions/messages", params={"sessionId": "s1"}, headers=_HEADERS)
    assert r.status_code == 200
    body = r.json()
    assert body["result"] == 0
    assert [m["role"] for m in body["data"]] == ["user", "assistant"]
    # sessionId 透传给下游 (snake_case)
    _ident, params = fake.calls[0]
    assert params["session_id"] == "s1"


def test_session_messages_maps_steps():
    fake = _FakeAgentClient(raw=[
        {"role": "user", "content": "查", "steps": []},
        {"role": "assistant", "content": "ok", "steps": [
            {"n": 1, "thought": "t", "tool": "search", "args": {"q": "x"}, "result_summary": "done"}]},
    ])
    agent.agent_client = fake
    c = _client(_PASSTHROUGH_CFG)
    r = c.get("/api/v1/agent/sessions/messages", params={"sessionId": "s1"}, headers=_HEADERS)
    assert r.status_code == 200
    data = r.json()["data"]
    assert data[0]["steps"] == []
    step = data[1]["steps"][0]
    assert step["tool"] == "search"
    assert step["resultSummary"] == "done"  # snake_case → camelCase 投影


def test_sessions_downstream_unavailable_is_503():
    fake = _FakeAgentClient(exc=PlatformError(ErrorCode.UPSTREAM_UNAVAILABLE, "agent 后端不可达"))
    agent.agent_client = fake
    c = _client(_PASSTHROUGH_CFG)
    r = c.get("/api/v1/agent/sessions", headers=_HEADERS)
    assert r.status_code == 503
    assert r.json()["errors"][0]["errorCode"] == "upstream_unavailable"
