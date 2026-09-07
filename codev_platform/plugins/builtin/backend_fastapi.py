"""builtin.backend_fastapi — 通用 FastAPI 后端栈插件 (不绑任何具体项目)。

detect: repo 内有 FastAPI 迹象 (import fastapi / from fastapi / @router.<method>(...))。
analyze: AST 扫 @router.(get|post|put|delete|patch)("/...") -> backend_endpoint 节点。

按"协议族/技术栈"组织 (对齐 agent-provider-architecture 思路): FastAPI 是一类后端栈,
任意采用它的仓都适用, 不为单个项目写脚本。扫描逻辑在 _stack_scan.scan_fastapi (与
FrontendReact 插件共享同一套 repo-内容判定 + 稳定 node id 规则)。
"""
from __future__ import annotations

from pathlib import Path

from codev_platform.graph.schema import AnalyzerResult, NodeKind
from codev_platform.plugins.base import AnalyzerPlugin
from codev_platform.plugins.builtin import _stack_scan

PLUGIN_NAME = "builtin.backend_fastapi"


class FastApiPlugin(AnalyzerPlugin):
    """FastAPI 路由 -> backend_endpoint 统一图谱节点。"""

    name = PLUGIN_NAME
    version = "0.1.0"
    produces = (NodeKind.BACKEND_ENDPOINT.value,)

    def detect(self, repo_path: Path) -> bool:
        return _stack_scan.fastapi_detect(Path(repo_path))

    def analyze(self, repo_path: Path, project_id: str) -> AnalyzerResult:
        repo = Path(repo_path)
        nodes = _stack_scan.scan_fastapi(repo, project_id)
        return AnalyzerResult(nodes=nodes, plugin=PLUGIN_NAME)
