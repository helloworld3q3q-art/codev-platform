"""Tool 抽象 + 平台能力封装. 对 loop 暴露统一接口, backend 差异封内部.

`build_default_registry()` 集中决定"装哪些工具",从 service 解耦 —— 加新工具只改这里。
"""
from __future__ import annotations

from codev_platform.agent.tools.base import ToolRegistry


def build_default_registry(project_id: str | None = None) -> ToolRegistry:
    """组装默认工具集. project_id 给定 → 工具按该项目路由(P2 多租户);
    None → 工具按进程 cwd 推导(单项目兼容)。加新工具在此追加 register_into。"""
    reg = ToolRegistry()
    from codev_platform.agent.tools import codegraph, cross_link, impact, search_docs
    cross_link.register_into(reg, project_id)
    codegraph.register_into(reg, project_id)
    impact.register_into(reg, project_id)  # 统一图谱影响分析 (cross_link 工具的替代, A4)
    search_docs.register_into(reg, project_id)
    return reg
