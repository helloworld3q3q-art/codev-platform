"""builtin.frontend_react — 通用 React 前端栈插件 (不绑任何具体项目)。

detect: repo 有 React 迹象 (package.json 含 react 依赖, 或存在 *.tsx); 不局限目录名。
analyze:
  - 扫前端 API 调用 (services/apis 导出函数 + 内联 axios/fetch) -> frontend_api_call 节点。
  - 扫调用了 api 的页面/组件 -> frontend_route 节点 + renders 边 (页面 -> api 调用)。

按"技术栈"组织: React 是一类前端栈, 任意采用它的仓都适用, 杜绝 per-project 脚本。

注: 前端 calls_api -> 后端 endpoint 的跨层链接**不再由本插件产** (旧实现各前端插件
只扫同仓 FastAPI 做参照, 链不到 Spring/Node/Java 端点)。统一由 graph.ingest 末尾的
**核心 linker pass** 跨所有插件 (fastapi/spring/node) 产 calls_api, 单一 owner builtin.linker。
"""
from __future__ import annotations

from pathlib import Path

from codev_platform.graph.schema import AnalyzerResult
from codev_platform.plugins.base import AnalyzerPlugin
from codev_platform.plugins.builtin import _stack_scan

PLUGIN_NAME = "builtin.frontend_react"


class FrontendReactPlugin(AnalyzerPlugin):
    """React 前端 -> frontend_route / frontend_api_call 节点 + renders 边
    (calls_api 由核心 linker pass 跨插件统一产)。"""

    name = PLUGIN_NAME
    version = "0.1.0"

    def detect(self, repo_path: Path) -> bool:
        return _stack_scan.react_detect(Path(repo_path))

    def analyze(self, repo_path: Path, project_id: str) -> AnalyzerResult:
        repo = Path(repo_path)
        result = AnalyzerResult(plugin=PLUGIN_NAME)

        api_nodes = _stack_scan.scan_react(repo, project_id)
        result.nodes.extend(api_nodes)

        page_nodes, page_edges = _stack_scan.scan_react_pages(
            repo, project_id, api_nodes
        )
        result.nodes.extend(page_nodes)
        result.edges.extend(page_edges)
        return result
