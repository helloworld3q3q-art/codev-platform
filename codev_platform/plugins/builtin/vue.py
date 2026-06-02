"""builtin.vue — 通用 Vue 前端栈插件 (不绑任何具体项目)。

detect: repo 有 Vue 迹象 (package.json 含 vue 依赖, 或存在 *.vue); 不局限目录名。
analyze:
  - 扫 .vue SFC -> frontend_component 节点。
  - 扫 SFC <script> / 同仓 .js/.ts(x) 内联 axios/fetch('/api/..') -> frontend_api_call 节点。
  - 扫 Vue Router 路由表 ({ path, component, name }) -> frontend_route 节点 + renders 边
    (route -> component)。
  - 跨层 calls_api 边: frontend_api_call -> 同仓 FastAPI backend_endpoint (URL 匹配),
    统一走 _stack_scan.link_api_calls (单一真值源)。同仓后端节点仅作 URL 参照, 不并入
    本结果 (backend 插件产正本)。

按"技术栈/协议族"组织 (对齐 agent-provider-architecture 思路): Vue 是一类前端栈,
复用 JS/TS 基座 (_stack_scan 的文件遍历 / url 解析 / 链接逻辑), 框架适配只加 Vue 规则,
不复制基座逻辑。第一版轻量正则, 不追求覆盖所有写法。
"""
from __future__ import annotations

from pathlib import Path

from codev_platform.graph.schema import AnalyzerResult
from codev_platform.plugins.base import AnalyzerPlugin
from codev_platform.plugins.builtin import _stack_scan

PLUGIN_NAME = "builtin.vue"


class VuePlugin(AnalyzerPlugin):
    """Vue 前端 -> frontend_component / frontend_route / frontend_api_call 节点
    + renders / calls_api 边。"""

    name = PLUGIN_NAME
    version = "0.1.0"

    def detect(self, repo_path: Path) -> bool:
        return _stack_scan.vue_detect(Path(repo_path))

    def analyze(self, repo_path: Path, project_id: str) -> AnalyzerResult:
        repo = Path(repo_path)
        result = AnalyzerResult(plugin=PLUGIN_NAME)

        # 1) SFC 组件 + 内联 api 调用 (JS/TS 基座复用)。
        scan_nodes = _stack_scan.scan_vue(repo, project_id)
        result.nodes.extend(scan_nodes)

        # 2) Vue Router 路由 + renders 边 (route -> SFC 组件)。
        route_nodes, route_edges = _stack_scan.scan_vue_routes(
            repo, project_id, scan_nodes
        )
        result.nodes.extend(route_nodes)
        result.edges.extend(route_edges)

        # 3) 跨插件链接: 前端 api 调用 -> 同仓 FastAPI endpoint (URL 匹配)。
        #    同仓后端节点仅作 URL 解析参照, 不并入本结果 (backend 插件产正本)。
        api_nodes = [
            n for n in scan_nodes
            if n.kind == "frontend_api_call"
        ]
        backend_nodes = _stack_scan.scan_fastapi(repo, project_id)
        result.edges.extend(
            _stack_scan.link_api_calls(api_nodes, backend_nodes)
        )
        return result
