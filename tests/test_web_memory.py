"""Web Memory 路由 (B1) —— 代理到 codev-agent /memory, 鉴权前门红线。

覆盖: 写 / 列通路 (camelCase ↔ agent snake_case) + org_id 不信 client (取已认证身份) +
personal scopeRef 强制本人 + agent 不可达转 503。AgentClient 用 FakeClient mock, 不起真 agent。
本 venv 未装 fastapi → skip。
"""
from __future__ import annotations

import pytest

pytest.importorskip("fastapi")

from fastapi.testclient import TestClient  # noqa: E402

from codev_platform.core.errors import ErrorCode, PlatformError  # noqa: E402
from codev_platform.core.httpkit import build_app  # noqa: E402
from codev_platform.web.routes import memory  # noqa: E402

_CFG = {"gateway": {"auth_mode": "passthrough"}, "projects": {}}
_HEADERS = {"X-Project-Id": "demo-proj", "X-User-Id": "alice", "X-Org-Id": "acme"}


class FakeClient:
    """记录代理调用 + 回放固定结果; raises 设为真则抛 UPSTREAM_UNAVAILABLE (模拟 agent 不可达)。"""

    def __init__(self, *, raises: bool = False) -> None:
        self.raises = raises
        self.last_ident = None
        self.last_body: dict | None = None
        self.last_params: dict | None = None

    def _maybe_raise(self) -> None:
        if self.raises:
            raise PlatformError(ErrorCode.UPSTREAM_UNAVAILABLE, "agent 后端不可达")

    def memory_write(self, ident, body: dict) -> dict:
        self.last_ident, self.last_body = ident, body
        self._maybe_raise()
        return {
            "id": "m1", "scope": body["scope"], "scope_ref": body["scope_ref"],
            "owner_user_id": ident.user_id, "content": body["content"],
            "org_id": ident.org_id, "kind": body.get("kind"), "status": "active",
        }

    def memory_list(self, ident, params: dict) -> dict:
        self.last_ident, self.last_params = ident, params
        self._maybe_raise()
        return [{
            "id": "m1", "scope": params["scope"], "scope_ref": params["scope_ref"],
            "owner_user_id": ident.user_id, "content": "hi", "org_id": ident.org_id,
        }]


@pytest.fixture
def fake(monkeypatch):
    fc = FakeClient()
    monkeypatch.setattr(memory, "agent_client", fc)
    return fc


def _client() -> TestClient:
    return TestClient(build_app(title="t", routers=[memory.router], cfg=_CFG))


def test_write_proxies_and_translates_camel_to_snake(fake):
    c = _client()
    r = c.post("/api/v1/memory", headers=_HEADERS, json={
        "scope": "project", "scopeRef": "demo-proj", "content": "note", "topicKey": "k1",
    })
    assert r.status_code == 200
    data = r.json()["data"]
    assert data["scope"] == "project" and data["content"] == "note"
    # camelCase 已翻成 agent snake_case body
    assert fake.last_body["scope_ref"] == "demo-proj"
    assert fake.last_body["topic_key"] == "k1"


def test_write_org_id_taken_from_identity_not_client(fake):
    c = _client()
    # body 无 orgId 字段 (schema 不暴露); 代理身份的 org_id 必来自已认证 X-Org-Id。
    c.post("/api/v1/memory", headers=_HEADERS, json={
        "scope": "org", "scopeRef": "org", "content": "x",
    })
    assert fake.last_ident.org_id == "acme"


def test_write_personal_scope_ref_forced_to_self(fake):
    c = _client()
    # client 尝试以 'bob' 名义写个人记忆 → 强制改回本人 'alice'。
    r = c.post("/api/v1/memory", headers=_HEADERS, json={
        "scope": "personal", "scopeRef": "bob", "content": "secret",
    })
    assert r.status_code == 200
    assert fake.last_body["scope_ref"] == "alice"
    assert r.json()["data"]["scopeRef"] == "alice"


def test_list_proxies_with_params(fake):
    c = _client()
    r = c.get("/api/v1/memory", headers=_HEADERS,
              params={"scope": "project", "scopeRef": "demo-proj", "limit": 5})
    assert r.status_code == 200
    items = r.json()["data"]
    assert items[0]["scope"] == "project"
    assert fake.last_params == {"scope": "project", "scope_ref": "demo-proj", "limit": 5}


def test_list_personal_scope_ref_forced_to_self(fake):
    c = _client()
    c.get("/api/v1/memory", headers=_HEADERS,
          params={"scope": "personal", "scopeRef": "bob", "limit": 10})
    assert fake.last_params["scope_ref"] == "alice"


def test_list_personal_empty_scope_ref_ok(fake):
    # 复现线上 bug: personal + 空 scopeRef (前端不让填) 不应 400, 路由用本人覆盖。
    c = _client()
    r = c.get("/api/v1/memory", headers=_HEADERS,
              params={"scope": "personal", "scopeRef": "", "limit": 50})
    assert r.status_code == 200
    assert fake.last_params["scope_ref"] == "alice"


def test_list_nonpersonal_empty_scope_ref_returns_empty(fake):
    # 非 personal 未选 ref → 优雅返空, 不下发空 ref 给 agent (不 400)。
    c = _client()
    r = c.get("/api/v1/memory", headers=_HEADERS, params={"scope": "org", "scopeRef": ""})
    assert r.status_code == 200
    assert r.json()["data"] == []
    assert fake.last_params is None  # agent 未被调用


def test_write_personal_empty_scope_ref_ok(fake):
    # personal 写入不传 scopeRef (前端隐藏该字段) → 路由用本人, 不 400。
    c = _client()
    r = c.post("/api/v1/memory", headers=_HEADERS, json={"scope": "personal", "content": "x"})
    assert r.status_code == 200
    assert fake.last_body["scope_ref"] == "alice"


def test_write_nonpersonal_empty_scope_ref_400(fake):
    # 非 personal 写入缺 scopeRef → 显式 invalid_params (而非静默写错 ref)。
    c = _client()
    r = c.post("/api/v1/memory", headers=_HEADERS, json={"scope": "org", "content": "x"})
    assert r.status_code == 400
    assert r.json()["errors"][0]["errorCode"] == "invalid_params"


def test_write_project_scope_ref_forced_to_header_project(fake):
    # P1 修复(codex bug-edge-audit): client 传 scope=project + scopeRef=别项目 → 强制用鉴权
    # X-Project-Id(demo-proj), 防越权写别项目 memory(agent 侧 via=internal 全信任不复核)。
    c = _client()
    r = c.post("/api/v1/memory", headers=_HEADERS, json={
        "scope": "project", "scopeRef": "other-proj", "content": "x",
    })
    assert r.status_code == 200
    assert fake.last_body["scope_ref"] == "demo-proj"   # 不是 client 传的 other-proj


def test_list_project_scope_ref_forced_to_header_project(fake):
    c = _client()
    c.get("/api/v1/memory", headers=_HEADERS,
          params={"scope": "project", "scopeRef": "other-proj", "limit": 5})
    assert fake.last_params["scope_ref"] == "demo-proj"   # 不是 other-proj


def test_agent_unreachable_returns_503(monkeypatch):
    monkeypatch.setattr(memory, "agent_client", FakeClient(raises=True))
    c = _client()
    r = c.post("/api/v1/memory", headers=_HEADERS, json={
        "scope": "project", "scopeRef": "demo-proj", "content": "x",
    })
    assert r.status_code == 503
    assert r.json()["errors"][0]["errorCode"] == "upstream_unavailable"
