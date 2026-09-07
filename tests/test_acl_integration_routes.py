"""ACL 入口级集成测试 — agent FastAPI 路由入口 (audit #3)。

纯函数测试只覆盖 can_access / memory_scope_access 本身, 易"入口漏接闸"。本测真挂
chat + memory router + AuthMiddleware, 用 TestClient 打 /chat 与 /memory, 验证 token
模式下 ACL 真在请求路径上 403。

本 venv 未装 fastapi → importorskip 自动 skip; 生产/CI 装了 fastapi 即真跑。
"""
from __future__ import annotations

from types import SimpleNamespace

import pytest

pytest.importorskip("fastapi")

from fastapi import FastAPI  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

from codev_platform.agent.routes import chat as chat_route  # noqa: E402
from codev_platform.agent.routes import memory as memory_route  # noqa: E402
from codev_platform.gateway import AuthMiddleware  # noqa: E402
from codev_platform.gateway.auth import TokenAuthenticator, token_hash  # noqa: E402

_TOK = "secret-token-routes"
_TOK_CFG = {"gateway": {"auth_mode": "token"}, "projects": {}}
_AUTH = {"Authorization": f"Bearer {_TOK}"}


def _token_auth(projects):
    return TokenAuthenticator({token_hash(_TOK): {"user_id": "u1", "org_id": "orgA", "projects": projects}})


def _app(authenticator):
    app = FastAPI()
    app.include_router(chat_route.router)
    app.include_router(memory_route.router)
    app.add_middleware(AuthMiddleware, authenticator=authenticator, public_paths={"/health"})
    return app


@pytest.fixture
def token_cfg(monkeypatch):
    # 路由内 can_access/memory_scope_access 读 load_config() → 注入 token cfg
    monkeypatch.setattr(chat_route, "load_config", lambda: _TOK_CFG)
    monkeypatch.setattr(memory_route, "load_config", lambda: _TOK_CFG)


# ---- /chat ----

def test_chat_token_no_project_id_is_403(token_cfg, monkeypatch):
    # token 模式无 project_id → can_access deny → 403 (ACL 闸先于 chat_service)
    monkeypatch.setattr(chat_route.deps, "get_chat_service",
                        lambda: (_ for _ in ()).throw(AssertionError("不应到达 service")))
    client = TestClient(_app(_token_auth(["openclaw-stock"])))
    r = client.post("/chat", json={"question": "hi"}, headers=_AUTH)
    assert r.status_code == 403


def test_chat_token_project_not_in_allowlist_is_403(token_cfg):
    client = TestClient(_app(_token_auth(["other-proj"])))
    r = client.post("/chat", json={"question": "hi"}, headers={**_AUTH, "X-Project-Id": "openclaw-stock"})
    assert r.status_code == 403


def test_chat_token_whitelisted_passes_acl(token_cfg, monkeypatch):
    # 白名单内: ACL 放行, 进 chat_service (mock 掉避免真跑 LLM) → 非 403
    outcome = SimpleNamespace(session_id="s1", result=SimpleNamespace(
        answer="ok", steps=[], usage={}, stop_reason="done"))
    fake = SimpleNamespace(ask=lambda *a, **k: outcome)
    monkeypatch.setattr(chat_route.deps, "get_chat_service", lambda: fake)
    client = TestClient(_app(_token_auth(["openclaw-stock"])))
    r = client.post("/chat", json={"question": "hi"}, headers={**_AUTH, "X-Project-Id": "openclaw-stock"})
    assert r.status_code == 200
    assert r.json()["answer"] == "ok"


# ---- /memory ----

def test_memory_write_token_org_scope_is_403(token_cfg, monkeypatch):
    # token 模式 org scope → memory_scope_access deny (M5 前不放行) → 403, 不触达 store
    monkeypatch.setattr(memory_route.deps, "get_memory_store", lambda: SimpleNamespace(
        write=lambda e: (_ for _ in ()).throw(AssertionError("不应写库"))))
    client = TestClient(_app(_token_auth(["openclaw-stock"])))
    r = client.post("/memory", json={"scope": "org", "scope_ref": "orgA", "content": "x"}, headers=_AUTH)
    assert r.status_code == 403


def test_memory_write_token_team_scope_is_403(token_cfg, monkeypatch):
    monkeypatch.setattr(memory_route.deps, "get_memory_store", lambda: SimpleNamespace(write=lambda e: "id1"))
    client = TestClient(_app(_token_auth(["openclaw-stock"])))
    r = client.post("/memory", json={"scope": "team", "scope_ref": "team1", "content": "x"}, headers=_AUTH)
    assert r.status_code == 403


def test_memory_read_personal_other_is_403(token_cfg, monkeypatch):
    # personal 读他人 (scope_ref != 自己 user_id=u1) → 403, recall ≠ read
    monkeypatch.setattr(memory_route.deps, "get_memory_store", lambda: SimpleNamespace(
        list_scope=lambda *a, **k: (_ for _ in ()).throw(AssertionError("不应读库"))))
    client = TestClient(_app(_token_auth(["openclaw-stock"])))
    r = client.get("/memory", params={"scope": "personal", "scope_ref": "someone-else"}, headers=_AUTH)
    assert r.status_code == 403
