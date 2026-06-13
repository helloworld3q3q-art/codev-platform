"""builtin.node — 通用 Node/Express 系后端栈插件 (不绑任何具体项目)。

taxonomy 定位 (对齐 agent-provider-architecture 思路, 按基座/适配/方言分):
  - Layer1 语言基座 = JS/TS (.js/.jsx/.ts/.tsx/.mjs/.cjs), 与 React 共用 _stack_scan
    的文件遍历 + 稳定 node id + 解析原语 + link_api_calls 单一真值源。
  - Layer2 框架适配 = 本文件, detect 基于 repo 内容 (package.json deps 含
    express/koa/fastify), analyze 只调基座扫描函数 + Node-Express 路由规则,
    不复制基座逻辑。

detect: package.json 的 dependencies / devDependencies 含 express / koa / fastify
        (纯内容判定, 不靠目录名; 多框架同仓可共存, 不与 frontend_react 冲突)。
analyze: 扫 app.<method>(...) / router.<method>(...) / api.use(...) 路由注册
         -> 统一 graph/schema 的 backend_endpoint 节点 (本插件产 backend 正本)。

第一版轻量正则即可 (plan 允许不追求覆盖所有写法): 不解析 router 挂载前缀拼接、
不跨文件追 app.use('/api', router) 的路径组合。后续可增强而不破契约。
"""
from __future__ import annotations

from pathlib import Path

from codev_platform.graph.schema import AnalyzerResult, NodeKind
from codev_platform.plugins.base import AnalyzerPlugin
from codev_platform.plugins.builtin import _stack_scan

PLUGIN_NAME = "builtin.node"


class NodeBackendPlugin(AnalyzerPlugin):
    """Express/Koa/Fastify 路由 -> backend_endpoint 统一图谱节点。"""

    name = PLUGIN_NAME
    version = "0.1.0"
    produces = (NodeKind.BACKEND_ENDPOINT.value,)

    def detect(self, repo_path: Path) -> bool:
        return _stack_scan.node_detect(Path(repo_path))

    def analyze(self, repo_path: Path, project_id: str) -> AnalyzerResult:
        repo = Path(repo_path)
        nodes = _stack_scan.scan_node_express(repo, project_id)
        return AnalyzerResult(nodes=nodes, plugin=PLUGIN_NAME)
