"""remember 工具 + runctx 测试(纯逻辑, mock store)。真 PG 写入由冒烟脚本另验。

覆盖: runctx set/get/reset、entry 构造(project/personal scope + task 字段)、
无上下文/空内容报错、注册到默认 registry。
"""
from __future__ import annotations

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


def test_runctx_set_get_reset():
    assert get_run_context() is None
    tok = set_run_context(RunContext(user_id="u", org_id="o", project_id="p", task_id="t1"))
    ctx = get_run_context()
    assert ctx.user_id == "u" and ctx.org_id == "o" and ctx.task_id == "t1"
    reset_run_context(tok)
    assert get_run_context() is None  # reset 后不泄漏


def test_remember_writes_project_scope(monkeypatch):
    fake = _FakeStore()
    import codev_platform.agent.deps as deps
    monkeypatch.setattr(deps, "get_memory_store", lambda: fake)
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
    import codev_platform.agent.deps as deps
    monkeypatch.setattr(deps, "get_memory_store", lambda: fake)
    tok = set_run_context(RunContext(user_id="bob", org_id="acme", project_id=None, task_id=None))
    try:
        r = RememberTool().run({"content": "记一条", "kind": "fact"})
    finally:
        reset_run_context(tok)
    assert not r.is_error
    e = fake.written[0]
    assert e.scope == "personal" and e.scope_ref == "bob" and e.kind == "fact" and e.task_id is None


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
