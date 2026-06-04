"""Tool 抽象 + 平台能力封装. 对 loop 暴露统一接口, backend 差异封内部.

`build_default_registry()` 集中决定"装哪些工具",从 service 解耦 —— 加新工具只改这里。
"""
from __future__ import annotations

from codev_platform.agent.tools.base import ToolRegistry


def build_default_registry(project_id: str | None = None) -> ToolRegistry:
    """组装默认工具集. project_id 给定 → 工具按该项目路由(P2 多租户);
    None → 工具按进程 cwd 推导(单项目兼容)。加新工具在此追加 register_into。"""
    reg = ToolRegistry()
    # cross_link 工具已退役: 其跨层查询由 impact (统一图谱 store 原生) 完全覆盖且更全
    # (table_usage 替 cross_link_table_refs / api_callers 替 cross_link_endpoint_callers)。
    from codev_platform.agent.tools import codegraph, fs, impact, remember, search_docs
    codegraph.register_into(reg, project_id)
    impact.register_into(reg, project_id)  # 统一图谱影响分析 (store, 取代旧 cross_link 工具)
    fs.register_into(reg, project_id)  # read_file / list_dir: 读全文 + 看结构 (codegraph 只给符号片段)
    search_docs.register_into(reg, project_id)
    remember.register_into(reg, project_id)  # M1 写侧: agent 把任务记忆写进 memory(读上下文走 runctx)
    return reg
