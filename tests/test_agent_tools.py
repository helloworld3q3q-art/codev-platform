"""agent 工具注册 + spec 形状测试(不触网 / 不查真 db)."""
from __future__ import annotations

from codev_platform.agent.tools import build_default_registry
from codev_platform.agent.tools.base import Tool, ToolRegistry


def test_default_registry_has_expected_tools():
    reg = build_default_registry()
    names = {t.name for t in reg.all()}
    # A 能力的三类 backend 都在
    assert "cross_link_table_refs" in names
    assert "codegraph_search" in names
    assert "search_docs" in names


def test_specs_shape():
    reg = build_default_registry()
    for spec in reg.specs():
        assert set(spec) == {"name", "description", "input_schema"}
        assert spec["input_schema"]["type"] == "object"


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
