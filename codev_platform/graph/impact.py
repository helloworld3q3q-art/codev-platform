"""影响分析引擎 (Track A4) —— 在连通统一图谱 store 上做跨层影响遍历。

A1 桥接后 store 连通 (边向 = 消费方→提供方):
    前端 --calls_api--> 端点 --calls--> 函数 --reads/writes/updates_table--> 表
    (contains/renders 把 route/component/api_call 串成前端子树)

"改一处影响谁" = **反向** BFS (谁依赖它 / 谁会被波及);
"这一处依赖什么" = **正向** BFS。两个方向都按层 (frontend / backend / database) 分组返回。

4 个 Agent 工具 (find_impact / find_table_usage / find_page_dependencies / find_api_callers)
都是本引擎的薄封装。纯读 store, 不写;深度受限防爆炸。
"""
from __future__ import annotations

from dataclasses import dataclass, field

from codev_platform.graph.schema import GraphNode, NodeKind
from codev_platform.graph.store import load_graph

_MAX_DEPTH = 10

# 节点 kind → 层。未知 kind 归 "other"。
_LAYER: dict[str, str] = {
    NodeKind.FRONTEND_ROUTE.value: "frontend",
    NodeKind.FRONTEND_COMPONENT.value: "frontend",
    NodeKind.FRONTEND_API_CALL.value: "frontend",
    NodeKind.BACKEND_ENDPOINT.value: "backend",
    NodeKind.BACKEND_FUNCTION.value: "backend",
    NodeKind.DB_TABLE.value: "database",
    NodeKind.DB_COLUMN.value: "database",
}


def layer_of(kind: str) -> str:
    return _LAYER.get(kind, "other")


@dataclass
class ImpactGraph:
    """连通图的内存视图: 节点索引 + 正/反向邻接 (含边 kind)。"""

    nodes: dict[str, GraphNode] = field(default_factory=dict)
    fwd: dict[str, list[tuple[str, str]]] = field(default_factory=dict)   # id -> [(target, kind)]
    rev: dict[str, list[tuple[str, str]]] = field(default_factory=dict)   # id -> [(source, kind)]

    def find_by_name(self, name: str, kind: str | None = None) -> GraphNode | None:
        """按 name (可选 kind) 找节点。表名大小写不敏感。"""
        low = name.strip().lower()
        for n in self.nodes.values():
            if kind is not None and n.kind != kind:
                continue
            if (n.name or "").lower() == low:
                return n
        return None


def build_impact_graph(conn, project_id: str) -> ImpactGraph:
    merged = load_graph(conn, project_id)
    g = ImpactGraph(nodes={n.id: n for n in merged.nodes})
    for e in merged.edges:
        g.fwd.setdefault(e.source, []).append((e.target, e.kind))
        g.rev.setdefault(e.target, []).append((e.source, e.kind))
    return g


def _traverse(g: ImpactGraph, start_id: str, *, reverse: bool,
              max_depth: int = _MAX_DEPTH) -> list[tuple[GraphNode, int, str]]:
    """从 start_id 做方向感知 BFS, 返回 [(node, depth, 到达它的边 kind), ...] (不含起点)。"""
    adj = g.rev if reverse else g.fwd
    visited = {start_id}
    out: list[tuple[GraphNode, int, str]] = []
    queue: list[tuple[str, int]] = [(start_id, 0)]
    while queue:
        nid, depth = queue.pop(0)
        if depth >= max_depth:
            continue
        for nbr, kind in adj.get(nid, ()):  # noqa: E501
            if nbr in visited:
                continue
            visited.add(nbr)
            node = g.nodes.get(nbr)
            if node is not None:
                out.append((node, depth + 1, kind))
            queue.append((nbr, depth + 1))
    return out


def _node_brief(n: GraphNode, depth: int | None = None, via: str | None = None) -> dict:
    d = {
        "id": n.id, "kind": n.kind, "name": n.name, "layer": layer_of(n.kind),
        "file": n.file, "line": n.line,
    }
    if depth is not None:
        d["depth"] = depth
    if via is not None:
        d["via_edge"] = via
    return d


def _grouped(reached: list[tuple[GraphNode, int, str]]) -> dict:
    """把 BFS 结果按层分组 + 计数。"""
    by_layer: dict[str, list[dict]] = {"frontend": [], "backend": [], "database": [], "other": []}
    for node, depth, via in reached:
        by_layer[layer_of(node.kind)].append(_node_brief(node, depth, via))
    counts = {k: len(v) for k, v in by_layer.items() if v}
    return {"byLayer": {k: v for k, v in by_layer.items() if v},
            "counts": counts, "total": len(reached)}


def _resolve(g: ImpactGraph, ref: str, kind: str | None) -> GraphNode | None:
    """ref 既可是节点 id, 也可是 name (后者按 kind 找)。"""
    if ref in g.nodes:
        return g.nodes[ref]
    return g.find_by_name(ref, kind)


# ---------------------------------------------------------------- 4 个查询入口

def find_impact(conn, project_id: str, node_ref: str) -> dict:
    """改 node_ref (id 或 name) → 跨层**被波及**集合 (反向 BFS, 谁依赖它)。"""
    g = build_impact_graph(conn, project_id)
    node = _resolve(g, node_ref, None)
    if node is None:
        return {"found": False, "ref": node_ref}
    reached = _traverse(g, node.id, reverse=True)
    return {"found": True, "target": _node_brief(node), "impact": _grouped(reached)}


def find_table_usage(conn, project_id: str, table: str) -> dict:
    """给表名 → 哪些函数/端点/前端用它 (反向 BFS, 从 db_table 出发)。"""
    g = build_impact_graph(conn, project_id)
    node = _resolve(g, table, NodeKind.DB_TABLE.value)
    if node is None:
        return {"found": False, "table": table}
    reached = _traverse(g, node.id, reverse=True)
    return {"found": True, "table": _node_brief(node), "usage": _grouped(reached)}


def find_page_dependencies(conn, project_id: str, page_ref: str) -> dict:
    """给前端页/组件 → 它依赖的端点/函数/表 (正向 BFS)。"""
    g = build_impact_graph(conn, project_id)
    node = _resolve(g, page_ref, None)
    if node is None:
        return {"found": False, "page": page_ref}
    reached = _traverse(g, node.id, reverse=False)
    return {"found": True, "page": _node_brief(node), "dependsOn": _grouped(reached)}


def find_api_callers(conn, project_id: str, endpoint_ref: str) -> dict:
    """给端点 → 哪些前端调它 (反向 BFS, 仅取 frontend 层)。"""
    g = build_impact_graph(conn, project_id)
    node = _resolve(g, endpoint_ref, NodeKind.BACKEND_ENDPOINT.value)
    if node is None:
        return {"found": False, "endpoint": endpoint_ref}
    reached = _traverse(g, node.id, reverse=True)
    callers = [(n, d, v) for (n, d, v) in reached if layer_of(n.kind) == "frontend"]
    return {"found": True, "endpoint": _node_brief(node),
            "callers": [_node_brief(n, d, v) for (n, d, v) in callers],
            "count": len(callers)}
