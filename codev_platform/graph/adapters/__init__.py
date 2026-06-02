"""codev_platform.graph.adapters — 把现有工具输出适配到统一 graph schema.

每个适配器只读消费现有数据 (cross-link sqlite / codegraph db ...), 产出
AnalyzerResult (graph/schema.py 的中性类型), 不改被适配工具的代码与 schema。
"""
from __future__ import annotations

from codev_platform.graph.adapters.cross_link import (
    PLUGIN_NAME as CROSS_LINK_PLUGIN_NAME,
    build_cross_link_result,
)

__all__ = [
    "CROSS_LINK_PLUGIN_NAME",
    "build_cross_link_result",
]
