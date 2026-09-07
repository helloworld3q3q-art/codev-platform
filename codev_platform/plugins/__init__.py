"""codev_platform.plugins — 插件协议 + runtime (Phase 2).

"模块化核心 + 插件化扩展" 架构里,本包是 plugin runtime:协议 (base) + 注册表/发现
(registry) + 执行隔离/结果校验 (executor)。客户差异能力以 AnalyzerPlugin 形式接入,
统一输出 graph.schema:AnalyzerResult,核心据此存储、查询、给 Agent 使用。

铁律 (decision doc §改造原则):插件失败不拖垮核心 (executor 隔离),所有插件输出经
schema 校验 (executor 校验返回类型),插件执行可观测 (ExecutionResult 记录耗时/错误码)。

第一批只内置插件 (registry._discover_builtins 显式注册),不做第三方动态加载。
"""
from __future__ import annotations

from codev_platform.plugins.base import AnalyzerPlugin, AnalyzerPluginProtocol
from codev_platform.plugins.executor import (
    ERR_ANALYZE_FAILED,
    ERR_DETECT_FAILED,
    ERR_INVALID_RESULT,
    ERR_NOT_APPLICABLE,
    ExecutionResult,
    run_plugin,
)
from codev_platform.plugins.registry import (
    clear_registry,
    get_plugin,
    list_plugins,
    register_plugin,
    registered_names,
    run_all,
    run_applicable,
)

__all__ = [
    "AnalyzerPlugin",
    "AnalyzerPluginProtocol",
    "ExecutionResult",
    "run_plugin",
    "ERR_ANALYZE_FAILED",
    "ERR_DETECT_FAILED",
    "ERR_INVALID_RESULT",
    "ERR_NOT_APPLICABLE",
    "register_plugin",
    "get_plugin",
    "list_plugins",
    "registered_names",
    "clear_registry",
    "run_all",
    "run_applicable",
]
