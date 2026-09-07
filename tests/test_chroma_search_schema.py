"""chroma search_docs schema 护栏: module 字段不得退回硬 enum(P0 回归守护)。

2026-06-04 实测: module 写死 enum=[stock-*] 把 codev-platform 的 module="web-ui" 当非法拒掉。
修复 = 去掉硬 enum, 改自由字符串 + _build_where 对未知 module 优雅返空。本测试钉住该行为,
防有人手滑给 module 加回 enum 而 CI 不红。
"""
from __future__ import annotations

from codev_platform.chroma._schema import _build_where, tool_definitions


def _search_docs_module_prop() -> dict:
    sd = next(t for t in tool_definitions() if t.name == "search_docs")
    return sd.inputSchema["properties"]["module"]


def test_module_field_has_no_hard_enum():
    # module 是逐项目动态值, 不能用 schema 静态 enum 校验(否则非默认项目的合法 module 被拒)。
    prop = _search_docs_module_prop()
    assert "enum" not in prop, "search_docs.module 不得有硬 enum(逐项目动态, 见 P0 回归)"
    assert prop["type"] == "string"
    assert "文件路径归属" in prop["description"]
    assert "docs/**" in prop["description"]


def test_build_where_unknown_module_is_graceful():
    # 未知 module → 正常构造过滤子句(交给 Chroma 返空集), 不抛错。
    assert _build_where("all", "web-ui") == {"module": "web-ui"}
    # module="all" / 空 → 不过滤(None)。
    assert _build_where("all", "all") is None
    assert _build_where("all", "") is None
    # category + module 同给 → $and 组合。
    assert _build_where("rule", "web-ui") == {"$and": [{"category": "rule"}, {"module": "web-ui"}]}
