"""codev_platform.graph.adapters — 适配器包 (cross_link 适配器已于 2026-06-03 退场)。

历史: 曾把 cross_link sqlite 适配成统一 AnalyzerResult (build_cross_link_result)。
全栈血缘收敛后, 跨业务链路改由 stack 插件 (节点) + 核心 linker pass (calls_api 边) 直接
产入统一 store, 不再经 cross_link 适配器 —— 故本包暂空。后续接其它工具 (如 codegraph
adapter) 时再在此登记。
"""
from __future__ import annotations

__all__: list[str] = []
