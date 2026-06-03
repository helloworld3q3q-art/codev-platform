"""codev_platform.plugins.builtin — 平台内置 analyzer 插件 (Phase 2 第一批)。

decision doc §5: 第一批只做内置插件 (显式 register, 不做第三方动态加载)。
本包收纳由平台自带、随核心一起发布的 AnalyzerPlugin 实现;registry._discover_builtins
扫本包非下划线模块自动注册 (新增插件文件即生效, 无需改本 __init__)。当前按技术栈/
协议族复用的通用栈插件: frontend_react / vue (前端) · backend_fastapi / backend_spring /
node (后端) · sql (DB + 表读写血缘), detect 探测 repo 内容自动适配任意仓。

注 (2026-06-03 全栈血缘收敛): cross-link 适配器插件已退场 —— 跨业务链路统一由 stack
插件 (节点) + graph.ingest 的核心 linker pass (calls_api 边) 产出, 单一真值源走统一 store,
不再由 cross_link 适配器重复灌库。适配器代码 (graph/adapters/cross_link.py) 仍保留供
web /cross-link 路由按需读 legacy cross_layer.sqlite, 但不再接入 ingest。
"""
from __future__ import annotations

from codev_platform.plugins.builtin.backend_fastapi import FastApiPlugin
from codev_platform.plugins.builtin.frontend_react import FrontendReactPlugin

__all__ = ["FrontendReactPlugin", "FastApiPlugin"]
