"""agent 工具注册 + spec 形状测试(不触网 / 不查真 db)."""
from __future__ import annotations

from codev_platform.agent.tools import build_default_registry
from codev_platform.agent.tools.base import Tool, ToolRegistry


def test_default_registry_has_expected_tools():
    reg = build_default_registry()
    names = {t.name for t in reg.all()}
    # A 能力的三类 backend 都在 (cross_link 退役 -> impact 统一图谱工具)
    assert "table_usage" in names          # 统一图谱版, 取代旧 cross_link_table_refs
    assert "codegraph_search" in names
    assert "search_docs" in names
    # 退役工具不应再注册
    assert "cross_link_table_refs" not in names
    assert "cross_link_endpoint_callers" not in names


def test_specs_shape():
    reg = build_default_registry()
    for spec in reg.specs():
        assert set(spec) == {"name", "description", "input_schema"}
        assert spec["input_schema"]["type"] == "object"


def test_find_db_uses_centralized_platform_path(tmp_path, monkeypatch):
    # codegraph 数据 2026-05-30 起集中到平台: _find_db 应走
    # data_root/codegraph_ext/<pid>/codegraph/codegraph.db, 不依赖 meta.json repo_path
    # (跨机绝对路径会失效 —— WSL 跑的 agent 读到 Windows 'D:/...' 路径解析不到 → 误报未建索引)。
    monkeypatch.setenv("PLATFORM_DATA_DIR", str(tmp_path))
    from codev_platform.agent.tools.codegraph import _find_db
    pid = "demo-proj"
    db = tmp_path / "codegraph_ext" / pid / "codegraph" / "codegraph.db"
    db.parent.mkdir(parents=True)
    db.write_bytes(b"")
    assert _find_db(pid) == db


def test_registry_rejects_nameless_tool():
    class Bad(Tool):
        name = ""
        def run(self, args):  # pragma: no cover
            raise NotImplementedError

    r = ToolRegistry()
    try:
        r.register(Bad())
    except ValueError:
        return
    raise AssertionError("应拒绝空 name 工具")
