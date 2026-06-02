"""codev_platform.plugins.builtin — 平台内置 analyzer 插件 (Phase 2 第一批)。

decision doc §5: 第一批只做内置插件 (显式 register, 不做第三方动态加载)。
本包收纳由平台自带、随核心一起发布的 AnalyzerPlugin 实现;registry._discover_builtins
逐一 import + register。当前仅 cross-link 适配器包成的 builtin.cross_link。
"""
from __future__ import annotations

from codev_platform.plugins.builtin.cross_link import CrossLinkPlugin

__all__ = ["CrossLinkPlugin"]
