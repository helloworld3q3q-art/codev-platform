"""builtin.frontend_react — 通用 React 前端栈插件 (不绑任何具体项目)。

detect: repo 有 React 迹象 (package.json 含 react 依赖, 或存在 *.tsx); 不局限目录名。
analyze:
  - 扫前端 API 调用 (services/apis 导出函数 + 内联 axios/fetch) -> frontend_api_call 节点。
  - 扫调用了 api 的页面/组件 -> frontend_route 节点 + renders 边 (页面 -> api 调用)。
  - 顺带 AST 扫同仓 FastAPI 路由 (仅用于 URL 解析, 不重复产 backend_endpoint 节点),
    把 frontend_api_call --calls_api--> backend_endpoint 边产在本结果里 (per-plugin
    ingest 模型下, 跨插件链接边须落在某一个插件的 AnalyzerResult)。链接逻辑统一走
    _stack_scan.link_api_calls (单一真值源)。

按"技术栈"组织: React 是一类前端栈, 任意采用它的仓都适用, 杜绝 per-project 脚本。
"""
from __future__ import annotations

from pathlib import Path

from codev_platform.graph.schema import AnalyzerResult
from codev_platform.plugins.base import AnalyzerPlugin
from codev_platform.plugins.builtin import _stack_scan

PLUGIN_NAME = "builtin.frontend_react"


class FrontendReactPlugin(AnalyzerPlugin):
    """React 前端 -> frontend_route / frontend_api_call 节点 + renders / calls_api 边。"""

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

        # 跨插件链接: 前端 api 调用 -> 后端 endpoint (URL 匹配)。
        # 同仓 FastAPI 节点仅作 URL 解析参照, 不并入本结果 (backend_fastapi 插件产正本)。
        backend_nodes = _stack_scan.scan_fastapi(repo, project_id)
        result.edges.extend(
            _stack_scan.link_api_calls(api_nodes, backend_nodes)
        )
        return result
