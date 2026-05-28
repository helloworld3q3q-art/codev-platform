"""Tool 抽象 + 平台能力封装. 对 loop 暴露统一接口, backend 差异封内部.

`build_default_registry()` 集中决定"装哪些工具",从 service 解耦 —— 加新工具只改这里。
"""
from __future__ import annotations

from codev_platform.agent.tools.base import ToolRegistry


def build_default_registry() -> ToolRegistry:
    """组装默认工具集. 加 codegraph / search_docs 工具时在此追加 register_into。"""
    reg = ToolRegistry()
    from codev_platform.agent.tools import codegraph, cross_link, search_docs
    cross_link.register_into(reg)
    codegraph.register_into(reg)
    search_docs.register_into(reg)
    return reg
