"""D15 bootstrap 纯 plan 单测 —— 只验"按 config 产出哪些有序步骤", 不真跑子命令。

绝不在测试期触发重活: dry_run 路径断言 subprocess.run 未被调用。
"""
from __future__ import annotations

import sys

from codev_platform.ops import bootstrap as bs


def _names(steps):
    return [s.name for s in steps]


def _by_name(steps, name):
    return next((s for s in steps if s.name == name), None)


# ---- venv check 恒在且在最前 ----

def test_venv_check_always_first():
    steps = bs.plan_bootstrap({"projects": {}, "memory": {}})
    assert steps[0].name == "venv"
    assert steps[0].kind == "check"


# ---- serve-mcp start 恒在 ----

def test_serve_mcp_always_present():
    steps = bs.plan_bootstrap({"projects": {}, "memory": {}})
    sm = _by_name(steps, "serve-mcp")
    assert sm is not None
    assert sm.kind == "cmd"
    assert sm.cmd == [sys.executable, "-m", "codev_platform.cli", "serve-mcp", "start"]


# ---- codegraph link: projects 空 → 无 cmd; 非空 → 有 ----

def test_codegraph_link_absent_when_no_projects():
    steps = bs.plan_bootstrap({"projects": {}, "memory": {}})
    assert _by_name(steps, "codegraph-link") is None


def test_codegraph_link_present_when_projects():
    steps = bs.plan_bootstrap({"projects": {"p": {"repo_path": "/x"}}, "memory": {}})
    cg = _by_name(steps, "codegraph-link")
    assert cg is not None and cg.kind == "cmd"
    assert cg.cmd == [sys.executable, "-m", "codev_platform.cli", "codegraph", "link", "--all"]


# ---- memory init-db: dsn 空 → guide (无 cmd); 非空 → cmd ----

def test_memory_guide_when_dsn_empty():
    steps = bs.plan_bootstrap({"projects": {}, "memory": {"pg_dsn": ""}})
    m = _by_name(steps, "memory-init-db")
    assert m is not None
    assert m.kind == "guide"
    assert m.cmd is None


def test_memory_guide_when_dsn_missing():
    steps = bs.plan_bootstrap({"projects": {}})
    m = _by_name(steps, "memory-init-db")
    assert m is not None and m.kind == "guide" and m.cmd is None


def test_memory_cmd_when_dsn_set():
    steps = bs.plan_bootstrap({"projects": {}, "memory": {"pg_dsn": "postgresql://u@h/db"}})
    m = _by_name(steps, "memory-init-db")
    assert m is not None and m.kind == "cmd"
    assert m.cmd == [sys.executable, "-m", "codev_platform.cli", "memory", "init-db"]


# ---- dry_run 无副作用 (subprocess.run 不被调用) ----

def test_dry_run_no_subprocess(monkeypatch):
    called = {"n": 0}

    def _fake_run(*a, **k):  # pragma: no cover - 不应被调用
        called["n"] += 1
        raise AssertionError("dry_run 不应执行 subprocess.run")

    monkeypatch.setattr(bs.subprocess, "run", _fake_run)
    rc = bs.run_bootstrap({"projects": {"p": {"repo_path": "/x"}}, "memory": {"pg_dsn": "x"}}, dry_run=True)
    assert rc == 0
    assert called["n"] == 0
