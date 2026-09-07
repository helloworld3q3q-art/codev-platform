"""P2 多租户:工具按 project_id 路由的解析逻辑测试(不触真 DB/网络)."""
from __future__ import annotations

import json

import pytest

from codev_platform.agent.tools import _project
from codev_platform.agent.tools import build_default_registry
from codev_platform.core.project_id import ProjectIdError


def test_resolve_project_id_explicit_wins():
    assert _project.resolve_project_id("proj-a") == "proj-a"


def test_resolve_project_id_explicit_wins_even_in_token_mode(monkeypatch):
    # token 模式下 explicit 仍直接走 validate(在读 config 之前 return), 不受 cwd-fallback 禁令影响
    monkeypatch.setattr(
        "codev_platform.core.config.load_config",
        lambda: {"gateway": {"auth_mode": "token"}},
    )
    assert _project.resolve_project_id("proj-a") == "proj-a"


def test_resolve_project_id_token_mode_no_cwd_fallback(monkeypatch):
    # Phase 0: token 模式 + explicit 缺失 → 禁 cwd fallback, 抛 ProjectIdError (越权扫盲防线)
    monkeypatch.setattr(
        "codev_platform.core.config.load_config",
        lambda: {"gateway": {"auth_mode": "token"}},
    )
    # resolve_local 若被调用即破坏隔离 —— 用 sentinel 断言它**不**被触达
    monkeypatch.setattr(
        "codev_platform.core.project_id.resolve_local",
        lambda *a, **k: (_ for _ in ()).throw(AssertionError("token 模式不应触达 cwd 回退")),
    )
    with pytest.raises(ProjectIdError):
        _project.resolve_project_id(None)


def test_resolve_project_id_passthrough_keeps_cwd_fallback(monkeypatch):
    # passthrough(dev 单机): explicit 缺失仍走 cwd 回退(单项目兼容不破)
    monkeypatch.setattr(
        "codev_platform.core.config.load_config",
        lambda: {"gateway": {"auth_mode": "passthrough"}},
    )
    monkeypatch.setattr(
        "codev_platform.core.project_id.resolve_local",
        lambda *a, **k: "cwd-derived-proj",
    )
    assert _project.resolve_project_id(None) == "cwd-derived-proj"


def test_repo_path_of_reads_meta(tmp_path, monkeypatch):
    # 造一个假 platform_meta/projects/<id>/meta.json
    proj = tmp_path / "demo-proj"
    proj.mkdir()
    (proj / "meta.json").write_text(
        json.dumps({"project_id": "demo-proj", "repo_path": "D:/repos/demo"}),
        encoding="utf-8",
    )
    monkeypatch.setenv("PLATFORM_META_DIR", str(tmp_path))
    rp = _project.repo_path_of("demo-proj")
    assert rp is not None and str(rp).replace("\\", "/") == "D:/repos/demo"


def test_repo_path_of_missing_returns_none(tmp_path, monkeypatch):
    monkeypatch.setenv("PLATFORM_META_DIR", str(tmp_path))
    assert _project.repo_path_of("nope") is None


def _set_token_mode(monkeypatch):
    monkeypatch.setattr(
        "codev_platform.core.config.load_config",
        lambda: {"gateway": {"auth_mode": "token"}},
    )
    # resolve_local 若被调用即破坏隔离 —— sentinel 断言不触达
    monkeypatch.setattr(
        "codev_platform.core.project_id.resolve_local",
        lambda *a, **k: (_ for _ in ()).throw(AssertionError("token 模式不应触达 cwd 回退")),
    )


def test_impact_tool_token_mode_no_cwd_fallback(monkeypatch):
    # impact 工具 None 分支同样禁 cwd fallback(此前裸 resolve_local 绕过守卫的回归)
    _set_token_mode(monkeypatch)
    from codev_platform.agent.tools.impact import TableUsageTool
    r = TableUsageTool(None).run({"table": "t_demo"})
    assert r.is_error and "token 模式" in r.content


def test_recall_tool_token_mode_no_cwd_fallback(monkeypatch):
    _set_token_mode(monkeypatch)
    from codev_platform.agent.tools.recall import CodeRecallTool
    r = CodeRecallTool(None).run({"query": "anything"})
    assert r.is_error and "token 模式" in r.content


def test_codegraph_tool_token_mode_no_cwd_fallback(monkeypatch):
    # cwd 上溯找 .codegraph 的分支在 token 模式必须被守卫拦下(防命中平台进程所在仓)
    _set_token_mode(monkeypatch)
    from codev_platform.agent.tools.codegraph import CodegraphSearchTool
    r = CodegraphSearchTool(None).run({"query": "anything"})
    assert r.is_error and "token 模式" in r.content


def test_build_registry_accepts_project_id():
    # project_id 透传不报错;工具实例带上 project_id
    reg = build_default_registry("some-project")
    names = {t.name for t in reg.all()}
    assert "table_usage" in names and "codegraph_search" in names and "search_docs" in names
    # project_id 路由的工具(codegraph/impact/search_docs)在 __init__ 绑 project_id;
    # remember 例外 —— 它从 runctx 拿 per-request 上下文(含 project_id), 不在 __init__ 绑。
    for tool in reg.all():
        if tool.name == "remember":
            continue
        assert getattr(tool, "project_id", "MISSING") == "some-project"
