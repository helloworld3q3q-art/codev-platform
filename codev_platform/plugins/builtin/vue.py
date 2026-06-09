"""builtin.vue — 通用 Vue 前端栈插件 (不绑任何具体项目)。

detect: repo 有 Vue 迹象 (package.json 含 vue 依赖, 或存在 *.vue); 不局限目录名。
analyze:
  - 扫 .vue SFC -> frontend_component 节点。
  - 扫 SFC <script> / 同仓 .js/.ts(x) 内联 axios/fetch('/api/..') -> frontend_api_call 节点。
  - 扫 Vue Router 路由表 ({ path, component, name }) -> frontend_route 节点 + renders 边
    (route -> component)。

按"技术栈/协议族"组织 (对齐 agent-provider-architecture 思路): Vue 是一类前端栈,
复用 JS/TS 基座 (_stack_scan 的文件遍历 / url 解析 规则), 框架适配只加 Vue 规则,
不复制基座逻辑。第一版轻量正则, 不追求覆盖所有写法。

注: calls_api (前端 -> 后端 endpoint) 不再由本插件产, 统一由 graph.ingest 的核心
linker pass 跨所有后端插件 (fastapi/spring/node) 产, 单一 owner builtin.linker。
"""
from __future__ import annotations

from pathlib import Path

from codev_platform.graph.schema import AnalyzerResult, ProvSource
from codev_platform.plugins.base import AnalyzerPlugin
from codev_platform.plugins.builtin import _stack_scan

PLUGIN_NAME = "builtin.vue"


class VuePlugin(AnalyzerPlugin):
    """Vue 前端 -> frontend_component / frontend_route / frontend_api_call 节点
    + renders 边 (calls_api 由核心 linker pass 跨插件统一产)。"""

    name = PLUGIN_NAME
    version = "0.1.0"
    prov_source = ProvSource.REGEX.value  # SFC + 路由表正则解析

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
        return result
