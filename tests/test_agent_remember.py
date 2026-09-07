"""remember 工具 + runctx 测试(纯逻辑, mock store)。真 PG 写入由冒烟脚本另验。

覆盖: runctx set/get/reset、entry 构造(project/personal scope + task 字段)、
无上下文/空内容报错、注册到默认 registry。
P0(dev-agent-memory 前置修复)新增: topic_key 归一参与去重、工具恒不写 redline、
scope_decision 鉴权(token 越权被拒)、统一 audit 不旁路。
"""
from __future__ import annotations

from types import SimpleNamespace

from codev_platform.agent.runctx import (
    RunContext, get_run_context, reset_run_context, set_run_context,
)
from codev_platform.agent.tools.remember import RememberTool


class _FakeStore:
    def __init__(self) -> None:
        self.written: list = []

    def write(self, entry):
        self.written.append(entry)
        return "fake-id-12345678"


def _setup(monkeypatch, store, *, auth_mode: str = "passthrough", projects=None):
    """隔离 ambient config / 真 PG: memory store 用 fake、rbac store 置 None(走纯逻辑兜底)、
    config 固定(默认 passthrough, 不依赖本机 ~/.codev-platform/config.json)。"""
    import codev_platform.agent.deps as deps
    import codev_platform.core.config as cfgmod
    monkeypatch.setattr(deps, "get_memory_store", lambda: store)
    monkeypatch.setattr(deps, "get_rbac_store", lambda: None)
    cfg = {"gateway": {"auth_mode": auth_mode}, "projects": projects or {}}
    monkeypatch.setattr(cfgmod, "load_config", lambda: cfg)


def test_runctx_set_get_reset():
    assert get_run_context() is None
    tok = set_run_context(RunContext(user_id="u", org_id="o", project_id="p", task_id="t1"))
    ctx = get_run_context()
    assert ctx.user_id == "u" and ctx.org_id == "o" and ctx.task_id == "t1"
    reset_run_context(tok)
    assert get_run_context() is None  # reset 后不泄漏


def test_remember_writes_project_scope(monkeypatch):
    fake = _FakeStore()
    _setup(monkeypatch, fake)
    tok = set_run_context(RunContext(user_id="alice", org_id="acme", project_id="proj1", task_id="task-9"))
    try:
        r = RememberTool().run({"content": "任务目标 X", "task_state": "active"})
    finally:
        reset_run_context(tok)
    assert not r.is_error and len(fake.written) == 1
    e = fake.written[0]
    assert e.scope == "project" and e.scope_ref == "proj1" and e.owner_user_id == "alice"
    assert e.org_id == "acme" and e.task_id == "task-9" and e.task_state == "active"
    assert e.kind == "task" and e.content == "任务目标 X"


def test_remember_personal_scope_when_no_project(monkeypatch):
    fake = _FakeStore()
    _setup(monkeypatch, fake)
    tok = set_run_context(RunContext(user_id="bob", org_id="acme", project_id=None, task_id=None))
    try:
        r = RememberTool().run({"content": "记一条", "kind": "fact"})
    finally:
        reset_run_context(tok)
    assert not r.is_error
    e = fake.written[0]
    assert e.scope == "personal" and e.scope_ref == "bob" and e.kind == "fact" and e.task_id is None


def test_remember_normalizes_topic_key(monkeypatch):
    # 缺口 1: 工具写带 topic_key 且归一成 slug, 同主题方能参与去重/冲突消解。
    fake = _FakeStore()
    _setup(monkeypatch, fake)
    tok = set_run_context(RunContext(user_id="bob", org_id="acme"))
    try:
        r = RememberTool().run({"content": "深色主题", "kind": "preference",
                                "topic_key": "Dark Mode 偏好"})
    finally:
        reset_run_context(tok)
    assert not r.is_error
    assert fake.written[0].topic_key == "dark-mode-偏好"


def test_remember_never_writes_redline(monkeypatch):
    # 缺口 3: IDE/工具路径恒不写 redline(即便 client 想塞, schema 不收 + 构造恒 False)。
    fake = _FakeStore()
    _setup(monkeypatch, fake)
    tok = set_run_context(RunContext(user_id="bob", org_id="acme", project_id="proj1"))
    try:
        r = RememberTool().run({"content": "x", "is_redline": True})  # 多余字段被忽略
    finally:
        reset_run_context(tok)
    assert not r.is_error
    assert fake.written[0].is_redline is False


def test_remember_token_project_not_in_allowlist_denied(monkeypatch):
    # 缺口 2: 工具写走 scope_decision —— token 模式下越权项目被拒, 不再无脑直写。
    fake = _FakeStore()
    _setup(monkeypatch, fake, auth_mode="token", projects={})
    ident = SimpleNamespace(via="token", org_id="acme", user_id="alice",
                            projects=frozenset({"other-proj"}), all_projects=False)
    tok = set_run_context(RunContext(user_id="alice", org_id="acme",
                                     project_id="proj1", identity=ident))
    try:
        r = RememberTool().run({"content": "x"})
    finally:
        reset_run_context(tok)
    assert r.is_error and not fake.written  # 被拒 + 未写入


def test_remember_token_project_in_allowlist_allowed(monkeypatch):
    fake = _FakeStore()
    _setup(monkeypatch, fake, auth_mode="token", projects={})
    ident = SimpleNamespace(via="token", org_id="acme", user_id="alice",
                            projects=frozenset({"proj1"}), all_projects=False)
    tok = set_run_context(RunContext(user_id="alice", org_id="acme",
                                     project_id="proj1", identity=ident))
    try:
        r = RememberTool().run({"content": "x"})
    finally:
        reset_run_context(tok)
    assert not r.is_error and len(fake.written) == 1


def test_remember_no_context_errors():
    # 无 runctx(非 agent 请求路径)→ 报错不崩、不写
    assert get_run_context() is None
    r = RememberTool().run({"content": "x"})
    assert r.is_error


def test_remember_empty_content_errors():
    r = RememberTool().run({"content": "   "})
    assert r.is_error


def test_remember_store_disabled_errors(monkeypatch):
    import codev_platform.agent.deps as deps
    monkeypatch.setattr(deps, "get_memory_store", lambda: None)  # memory 未启用
    tok = set_run_context(RunContext(user_id="u", org_id="o"))
    try:
        r = RememberTool().run({"content": "x"})
    finally:
        reset_run_context(tok)
    assert r.is_error


def test_remember_registered_in_default_registry():
    from codev_platform.agent.tools import build_default_registry
    reg = build_default_registry()
    assert reg.get("remember") is not None
    assert "remember" in {t.name for t in reg.all()}
