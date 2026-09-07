"""agent 路由错误响应契约 (refactor plan D3 / B.6 押后项)。

断言 chat / memory 路由各错误分支:
- HTTP status_code 与改造前一致 (additive, 不破坏现有客户端);
- detail 体含机器可读 `code` (8 类 ErrorCode) + `error` 文案 + 过渡兼容 `detail` 串;
- **不透出 raw str(e)**: 内部异常原文 (project_id 原值 / RuntimeError 文案 / DB 异常) 只进日志,
  对外只给稳定 message。

fastapi 缺失则跳过 (纯路由集成测试)。
"""
from __future__ import annotations

from types import SimpleNamespace

import pytest

pytest.importorskip("fastapi")
from fastapi import FastAPI  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

from codev_platform.agent.routes import chat as chat_route  # noqa: E402
from codev_platform.agent.routes import memory as memory_route  # noqa: E402
from codev_platform.core.acl import AccessDecision  # noqa: E402
from codev_platform.core.errors import ErrorCode  # noqa: E402


# ---- helpers ----------------------------------------------------------------

def _detail(resp):
    """FastAPI HTTPException(detail=<dict>) → body["detail"] 是我们构造的 {error,code,detail}。"""
    body = resp.json()
    assert "detail" in body, body
    return body["detail"]


def _assert_code(resp, status, code: ErrorCode):
    assert resp.status_code == status, resp.text
    d = _detail(resp)
    assert isinstance(d, dict), d
    assert d["code"] == code.value, d
    # 机器码 + 文案 + 过渡 detail 三字段齐
    assert d["error"] and d["detail"], d
    return d


# ---- chat -------------------------------------------------------------------

def _chat_client(monkeypatch, *, allowed=True, ask=None):
    cfg = {"gateway": {"auth_mode": "passthrough"}, "projects": {}}
    monkeypatch.setattr(chat_route, "load_config", lambda: cfg)
    monkeypatch.setattr(chat_route, "can_access",
                        lambda *a, **k: AccessDecision(allowed=allowed, reason="test"))
    if ask is not None:
        monkeypatch.setattr(chat_route.deps, "get_chat_service",
                            lambda: SimpleNamespace(ask=ask))
    app = FastAPI()
    app.include_router(chat_route.router)
    return TestClient(app, raise_server_exceptions=True)


def test_chat_invalid_project_id_400_no_raw_leak(monkeypatch):
    c = _chat_client(monkeypatch)
    secret_pid = "Bad_PID!evil"  # 非法格式 → ProjectIdError, str(e) 含此原值
    r = c.post("/chat", headers={"X-Project-Id": secret_pid}, json={"question": "hi"})
    _assert_code(r, 400, ErrorCode.INVALID_PARAMS)
    # raw project_id 原值不得出现在对外体 (只进日志)
    assert secret_pid not in r.text


def test_chat_access_denied_403(monkeypatch):
    c = _chat_client(monkeypatch, allowed=False)
    r = c.post("/chat", json={"question": "hi"})
    _assert_code(r, 403, ErrorCode.ACCESS_DENIED)


def test_chat_upstream_unavailable_503_no_raw_leak(monkeypatch):
    secret = "model-key-sample missing in config"

    def _boom(*a, **k):
        raise RuntimeError(secret)

    c = _chat_client(monkeypatch, ask=_boom)
    r = c.post("/chat", json={"question": "hi"})
    _assert_code(r, 503, ErrorCode.UPSTREAM_UNAVAILABLE)
    assert "sk-deadbeef" not in r.text  # provider 异常原文不外泄


# ---- memory -----------------------------------------------------------------

class _FakeStore:
    def write(self, entry):
        return "mem-1"

    def list_scope(self, scope, scope_ref, org_id=None, limit=100):
        return []


class _BoomStore:
    def __init__(self, secret):
        self._secret = secret

    def write(self, entry):
        raise RuntimeError(self._secret)

    def list_scope(self, scope, scope_ref, org_id=None, limit=100):
        raise RuntimeError(self._secret)


_STORE_UNSET = object()


def _mem_client(monkeypatch, *, store=_STORE_UNSET, allowed=True):
    if store is _STORE_UNSET:
        store = _FakeStore()
    monkeypatch.setattr(memory_route.deps, "get_memory_store", lambda: store)
    monkeypatch.setattr(memory_route.deps, "get_rbac_store", lambda: None)
    cfg = {"gateway": {"auth_mode": "passthrough"}, "projects": {}}
    monkeypatch.setattr(memory_route, "load_config", lambda: cfg)
    # 直接锚定 ACL 决策, 隔离 scope 判定细节 (本测专注错误响应契约)。
    monkeypatch.setattr(memory_route, "_scope_decision",
                        lambda *a, **k: AccessDecision(allowed=allowed, reason="test"))
    app = FastAPI()
    app.include_router(memory_route.router)
    return TestClient(app, raise_server_exceptions=True)


def test_memory_store_missing_503(monkeypatch):
    c = _mem_client(monkeypatch, store=None)
    r = c.post("/memory", json={"scope": "personal", "scope_ref": "userA", "content": "x"})
    _assert_code(r, 503, ErrorCode.DEPENDENCY_MISSING)


def test_memory_bad_scope_400(monkeypatch):
    c = _mem_client(monkeypatch)
    r = c.post("/memory", json={"scope": "nope", "scope_ref": "userA", "content": "x"})
    _assert_code(r, 400, ErrorCode.INVALID_PARAMS)


def test_memory_access_denied_403(monkeypatch):
    c = _mem_client(monkeypatch, allowed=False)
    r = c.post("/memory", json={"scope": "personal", "scope_ref": "userA", "content": "x"})
    _assert_code(r, 403, ErrorCode.ACCESS_DENIED)


def test_memory_write_store_error_503_no_raw_leak(monkeypatch):
    secret = "psql host=10.0.0.5 password=hunter2 timeout"
    c = _mem_client(monkeypatch, store=_BoomStore(secret))
    r = c.post("/memory", json={"scope": "personal", "scope_ref": "userA", "content": "x"})
    _assert_code(r, 503, ErrorCode.UPSTREAM_UNAVAILABLE)
    assert "hunter2" not in r.text  # DB 异常原文/拓扑不外泄


def test_memory_list_store_error_503_no_raw_leak(monkeypatch):
    secret = "psql host=10.0.0.5 password=hunter2 timeout"
    c = _mem_client(monkeypatch, store=_BoomStore(secret))
    r = c.get("/memory", params={"scope": "personal", "scope_ref": "userA"})
    _assert_code(r, 503, ErrorCode.UPSTREAM_UNAVAILABLE)
    assert "hunter2" not in r.text


def test_memory_list_store_missing_503(monkeypatch):
    c = _mem_client(monkeypatch, store=None)
    r = c.get("/memory", params={"scope": "personal", "scope_ref": "userA"})
    _assert_code(r, 503, ErrorCode.DEPENDENCY_MISSING)
