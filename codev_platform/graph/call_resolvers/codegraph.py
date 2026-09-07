"""codegraph 调用边 resolver —— 现有 bridge_codegraph 升格为 CallResolver(跨语言兜底)。

读 codegraph 的 calls 调用图, 把 endpoint→backend_function(碰表)物化。codegraph 能解析的
跨函数普通调用都覆盖(任何语言, 只要 codegraph 索引了);codegraph 追不动的(如 Python DI
self._store.x())由各语言专门 resolver(fastapi / spring / ...)补。行为与原 _bridge_pass 一致。
"""
from __future__ import annotations

from pathlib import Path

from codev_platform.graph.schema import GraphEdge, GraphNode, NodeKind, ProvSource


class CodegraphCallResolver:
    name = "codegraph"
    prov_source = ProvSource.AST.value  # 结构化精确解析(codegraph 调用图), 影响分析可采信

    def applies(self, repo: Path, nodes: list[GraphNode]) -> bool:
        # 有 endpoint + function 节点才有可连的调用边(跨语言兜底, 不挑栈)。
        kinds = {n.kind for n in nodes}
        return (NodeKind.BACKEND_ENDPOINT.value in kinds
                and NodeKind.BACKEND_FUNCTION.value in kinds)

    def resolve(self, repo: Path, project_id: str, nodes: list[GraphNode]) -> list[GraphEdge]:
        # 复用现有桥接(fail-soft 内建: codegraph 缺失/异常 → 返回 [])。
        from codev_platform.graph.bridge_codegraph import bridge_endpoints_to_functions
        endpoints = [n for n in nodes if n.kind == NodeKind.BACKEND_ENDPOINT.value]
        functions = [n for n in nodes if n.kind == NodeKind.BACKEND_FUNCTION.value]
        return bridge_endpoints_to_functions(project_id, endpoints, functions)


def register_into() -> None:
    from codev_platform.graph.call_resolvers.base import register_resolver
    register_resolver(CodegraphCallResolver())
