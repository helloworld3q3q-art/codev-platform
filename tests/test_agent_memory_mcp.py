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
    def __init__(self, *, forget_ok=True, supersede_raises=False):
        self.calls = []
        self.written = []
        self.forgot = []
        self.superseded = []
        self._forget_ok = forget_ok
        self._supersede_raises = supersede_raises

    def list_scope(self, scope, scope_ref, org_id="default", limit=100):
        self.calls.append((scope, scope_ref, org_id, limit))
        return [_entry(scope=scope, scope_ref=scope_ref)]

    def write(self, entry):
        self.written.append(entry)
        return "new-id-1"

    def forget(self, entry_id, *, owner_user_id=None, org_id=None, protect_redline=False):
        self.forgot.append((entry_id, owner_user_id, org_id, protect_redline))
        return self._forget_ok

    def supersede(self, old_id, new_entry, *, owner_user_id=None, protect_redline=False):
        if self._supersede_raises:
            raise ValueError("目标不存在/非本人")
        self.superseded.append((old_id, new_entry, owner_user_id, protect_redline))
        return "new-id-2"


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


# ---- remember (write) ----

def test_remember_defaults_personal_self(monkeypatch):
    store = _FakeStore()
    _bind(monkeypatch, store=store)
    out = _run("remember", {"content": "我偏好深色", "topic_key": "Dark Mode"})
    assert out["ok"] and out["scope"] == "personal" and out["scope_ref"] == "alice"
    e = store.written[0]
    assert e.scope == "personal" and e.scope_ref == "alice" and e.owner_user_id == "alice"
    assert e.org_id == "acme" and e.is_redline is False
    assert e.topic_key == "dark-mode"          # 归一
    assert e.content == "我偏好深色"


def test_remember_project_in_allowlist(monkeypatch):
    store = _FakeStore()
    ident = SimpleNamespace(via="token", org_id="acme", user_id="alice",
                            projects=frozenset({"proj1"}), all_projects=False)
    _bind(monkeypatch, store=store, ident=ident, auth_mode="token")
    out = _run("remember", {"content": "团队约定", "scope": "project", "scope_ref": "proj1"})
    assert out["ok"] and store.written[0].scope == "project"


def test_remember_project_denied_when_not_allowlisted(monkeypatch):
    store = _FakeStore()
    ident = SimpleNamespace(via="token", org_id="acme", user_id="alice",
                            projects=frozenset({"other"}), all_projects=False)
    _bind(monkeypatch, store=store, ident=ident, auth_mode="token")
    out = _run("remember", {"content": "x", "scope": "project", "scope_ref": "proj1"})
    assert "error" in out and not store.written


def test_remember_non_personal_requires_scope_ref(monkeypatch):
    store = _FakeStore()
    _bind(monkeypatch, store=store)
    out = _run("remember", {"content": "x", "scope": "project"})
    assert "error" in out and not store.written


def test_remember_empty_content(monkeypatch):
    store = _FakeStore()
    _bind(monkeypatch, store=store)
    out = _run("remember", {"content": "   "})
    assert "error" in out


# ---- forget (write) ----

def test_forget_owner_scoped(monkeypatch):
    store = _FakeStore(forget_ok=True)
    _bind(monkeypatch, store=store)
    out = _run("forget", {"entry_id": "e9"})
    assert out["ok"] is True
    # 传 owner + org + redline 保护给 store(防删他人 / 删 org 硬约束)
    assert store.forgot[0] == ("e9", "alice", "acme", True)


def test_forget_not_found(monkeypatch):
    store = _FakeStore(forget_ok=False)
    _bind(monkeypatch, store=store)
    out = _run("forget", {"entry_id": "nope"})
    assert out["ok"] is False and "未找到" in out["note"]


# ---- supersede (write) ----

def test_supersede_owner_scoped(monkeypatch):
    store = _FakeStore()
    _bind(monkeypatch, store=store)
    out = _run("supersede", {"old_id": "e1", "content": "改用浅色", "topic_key": "Dark Mode"})
    assert out["ok"] and out["supersedes"] == "e1"
    old_id, new_entry, owner, protect = store.superseded[0]
    assert old_id == "e1" and owner == "alice" and protect is True  # redline 受保护
    assert new_entry.owner_user_id == "alice" and new_entry.topic_key == "dark-mode"


def test_supersede_target_not_owned(monkeypatch):
    store = _FakeStore(supersede_raises=True)
    _bind(monkeypatch, store=store)
    out = _run("supersede", {"old_id": "someone-else", "content": "x"})
    assert "error" in out


def test_unknown_tool(monkeypatch):
    _bind(monkeypatch, recall=_FakeRecall(), store=_FakeStore())
    out = _run("nope", {})
    assert "error" in out
