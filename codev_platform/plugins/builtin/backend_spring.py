"""builtin.backend_spring — 通用 Spring MVC 后端栈插件 (不绑任何具体项目)。

detect: repo 内有 .java 含 @RestController/@Controller 或 *Mapping 注解。
analyze: 正则扫 Controller 的类级 @RequestMapping(base) + 方法级 @GetMapping/.../@RequestMapping
         -> backend_endpoint 节点 (language=java)。

按"协议族/技术栈"组织 (对齐 agent-provider-architecture + stack-adapter-taxonomy):
Spring 是 Java 后端的一类框架, 任意采用它的仓都适用, 不为单个项目写脚本。扫描逻辑在
_stack_scan.scan_spring (与 FastAPI/Node 插件共享同一套 repo-内容判定 + 稳定 node id 规则,
node id "<pid>:backend_endpoint:<METHOD>:<url>" 跨 java/py 可被 link_api_calls 命中)。

填补 cross_link 退场后 Java 端点的产出缺口 (见 unified-graph-lineage-2026-06-03 plan P1)。
"""
from __future__ import annotations

from pathlib import Path

from codev_platform.graph.schema import AnalyzerResult, NodeKind
from codev_platform.plugins.base import AnalyzerPlugin
from codev_platform.plugins.builtin import _stack_scan

PLUGIN_NAME = "builtin.backend_spring"


class SpringPlugin(AnalyzerPlugin):
    """Spring MVC Controller -> backend_endpoint 统一图谱节点。"""

    name = PLUGIN_NAME
    version = "0.1.0"
    produces = (NodeKind.BACKEND_ENDPOINT.value,)

    def detect(self, repo_path: Path) -> bool:
        return _stack_scan.spring_detect(Path(repo_path))

    def analyze(self, repo_path: Path, project_id: str) -> AnalyzerResult:
        repo = Path(repo_path)
        nodes = _stack_scan.scan_spring(repo, project_id)
        return AnalyzerResult(nodes=nodes, plugin=PLUGIN_NAME)
