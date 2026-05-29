"""P2 多租户:工具按 project_id 路由的解析逻辑测试(不触真 DB/网络)."""
from __future__ import annotations

import json

from codev_platform.agent.tools import _project
from codev_platform.agent.tools import build_default_registry


def test_resolve_project_id_explicit_wins():
    assert _project.resolve_project_id("proj-a") == "proj-a"


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


def test_build_registry_accepts_project_id():
    # project_id 透传不报错;工具实例带上 project_id
    reg = build_default_registry("some-project")
    names = {t.name for t in reg.all()}
    assert "cross_link_table_refs" in names and "codegraph_search" in names and "search_docs" in names
    for tool in reg.all():
        assert getattr(tool, "project_id", "MISSING") == "some-project"
