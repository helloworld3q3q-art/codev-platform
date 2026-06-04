"""agent memory MCP 前门 (_dispatch) 单测 —— recall / list_scope 工具逻辑 + ACL。

纯逻辑: mock recall service / store + 直接设 contextvar, 不起 SSE / 不碰真 PG。
"""
from __future__ import annotations

import asyncio
import json
from types import SimpleNamespace

from codev_platform.agent import memory_mcp as mm
from codev_platform.agent.memory_store import MemoryEntry


def _run(name, args):
    """设好 contextvar 后跑 _dispatch, 返回解析后的 JSON dict。"""
    res = asyncio.run(mm._dispatch(name, args))
    return json.loads(res[0].text)


def _entry(**kw):
    base = dict(id="e1", scope="personal", scope_ref="alice", owner_user_id="alice",
                content="x", org_id="acme")
    base.update(kw)
    return MemoryEntry(**base)


class _FakeRecall:
    def __init__(self):
        self.calls = []

    def recall(self, **kw):
        self.calls.append(kw)
        return [_entry(content="记忆A"), _entry(id="e2", content="记忆B")]


class _FakeStore:
    def __init__(self):
        self.calls = []

    def list_scope(self, scope, scope_ref, org_id="default", limit=100):
        self.calls.append((scope, scope_ref, org_id, limit))
        return [_entry(scope=scope, scope_ref=scope_ref)]


def _bind(monkeypatch, *, org="acme", user="alice", project="proj1", ident=None,
          recall=None, store=None, auth_mode="passthrough", projects=None):
    import codev_platform.agent.deps as deps
    import codev_platform.core.config as cfgmod
    if recall is not None:
        monkeypatch.setattr(deps, "get_recall_service", lambda: recall)
    if store is not None:
        monkeypatch.setattr(deps, "get_memory_store", lambda: store)
    monkeypatch.setattr(deps, "get_rbac_store", lambda: None)
    monkeypatch.setattr(cfgmod, "load_config",
                        lambda: {"gateway": {"auth_mode": auth_mode}, "projects": projects or {}})
    mm._ctx_org.set(org)
    mm._ctx_user.set(user)
    mm._ctx_project.set(project)
    mm._ctx_identity.set(ident if ident is not None
                         else SimpleNamespace(via="passthrough", org_id=org, user_id=user,
                                              all_projects=True))


# ---- recall ----

def test_recall_passes_context_and_serializes(monkeypatch):
    fake = _FakeRecall()
    _bind(monkeypatch, recall=fake)
    out = _run("recall", {"query": "深色", "limit": 5})
    assert out["count"] == 2 and len(out["entries"]) == 2
    assert out["entries"][0]["content"] == "记忆A"
    # 身份/项目来自 contextvar, 不是 client 入参
    c = fake.calls[0]
    assert c["org_id"] == "acme" and c["user_id"] == "alice" and c["project_id"] == "proj1"
    assert c["query"] == "深色" and c["limit"] == 5


def test_recall_clamps_limit_and_validates_policy(monkeypatch):
    fake = _FakeRecall()
    _bind(monkeypatch, recall=fake)
    _run("recall", {"limit": 999, "policy": "bogus"})
    c = fake.calls[0]
    assert c["limit"] == 50           # 上限钳到 50
    assert c["policy"] is None        # 非法 policy 落回默认(None)


def test_recall_memory_disabled(monkeypatch):
    _bind(monkeypatch, recall=None)
    import codev_platform.agent.deps as deps
    monkeypatch.setattr(deps, "get_recall_service", lambda: None)
    out = _run("recall", {"query": "x"})
    assert "error" in out


# ---- list_scope ----

def test_list_scope_personal_forces_self(monkeypatch):
    store = _FakeStore()
    _bind(monkeypatch, store=store)
    # client 传别人的 scope_ref, 必须被本人覆盖
    out = _run("list_scope", {"scope": "personal", "scope_ref": "bob"})
    assert out["scope_ref"] == "alice" and out["count"] == 1
    assert store.calls[0][0] == "personal" and store.calls[0][1] == "alice"


def test_list_scope_project_in_allowlist(monkeypatch):
    store = _FakeStore()
    ident = SimpleNamespace(via="token", org_id="acme", user_id="alice",
                            projects=frozenset({"proj1"}), all_projects=False)
    _bind(monkeypatch, store=store, ident=ident, auth_mode="token")
    out = _run("list_scope", {"scope": "project", "scope_ref": "proj1"})
    assert out["count"] == 1


def test_list_scope_project_denied_when_not_allowlisted(monkeypatch):
    store = _FakeStore()
    ident = SimpleNamespace(via="token", org_id="acme", user_id="alice",
                            projects=frozenset({"other"}), all_projects=False)
    _bind(monkeypatch, store=store, ident=ident, auth_mode="token")
    out = _run("list_scope", {"scope": "project", "scope_ref": "proj1"})
    assert "error" in out and not store.calls  # ACL 拒绝, 未触达 store


def test_list_scope_bad_scope(monkeypatch):
    store = _FakeStore()
    _bind(monkeypatch, store=store)
    out = _run("list_scope", {"scope": "galaxy"})
    assert "error" in out


def test_unknown_tool(monkeypatch):
    _bind(monkeypatch, recall=_FakeRecall(), store=_FakeStore())
    out = _run("nope", {})
    assert "error" in out
