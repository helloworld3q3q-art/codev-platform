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

import json
from pathlib import Path

from codev_platform.graph.schema import AnalyzerResult, NodeKind, ProvSource
from codev_platform.plugins.base import AnalyzerPlugin
from codev_platform.plugins.builtin import _stack_scan

PLUGIN_NAME = "builtin.vue"


def _declared_url_sources(repo: Path) -> list[str] | None:
    """读 <repo>/.claude/project.json 的 frontend_url_sources(项目声明的 URL 注册文件, 相对 repo)。
    无声明返 None → scan_url_registry 走启发式自动识别。低耦合: 声明式扩展点, 不硬编码文件名。"""
    pj = repo / ".claude" / "project.json"
    if not pj.is_file():
        return None
    try:
        data = json.loads(pj.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    v = data.get("frontend_url_sources")
    return [str(x) for x in v] if isinstance(v, list) and v else None


class VuePlugin(AnalyzerPlugin):
    """Vue 前端 -> frontend_component / frontend_route / frontend_api_call 节点
    + renders 边 (calls_api 由核心 linker pass 跨插件统一产)。"""

    name = PLUGIN_NAME
    version = "0.1.0"
    prov_source = ProvSource.REGEX.value  # SFC + 路由表正则解析
    produces = (NodeKind.FRONTEND_COMPONENT.value, NodeKind.FRONTEND_ROUTE.value,
                NodeKind.FRONTEND_API_CALL.value)

    def detect(self, repo_path: Path) -> bool:
        return _stack_scan.vue_detect(Path(repo_path))

    def analyze(self, repo_path: Path, project_id: str) -> AnalyzerResult:
        repo = Path(repo_path)
        result = AnalyzerResult(plugin=PLUGIN_NAME)

        # 1) SFC 组件 + 内联 api 调用 (JS/TS 基座复用)。
        scan_nodes = _stack_scan.scan_vue(repo, project_id)
        result.nodes.extend(scan_nodes)

        # 1b) URL 注册文件提取 (代码基础层): 企业前端常把 API 路径集中声明成常量, 业务代码引用
        # 常量名调用 → 内联扫描漏掉。从注册文件抽路径产 frontend_api_call, _link 据此连后端。
        # 项目可在 .claude/project.json 声明 frontend_url_sources; 无则启发式自动识别。
        result.nodes.extend(_stack_scan.scan_url_registry(
            repo, project_id, declared_files=_declared_url_sources(repo)))

        # 2) Vue Router 路由 + renders 边 (route -> SFC 组件)。
        route_nodes, route_edges = _stack_scan.scan_vue_routes(
            repo, project_id, scan_nodes
        )
        result.nodes.extend(route_nodes)
        result.edges.extend(route_edges)
        return result
