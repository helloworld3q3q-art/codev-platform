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
    NodeKind.FRONTEND_MODULE.value: "frontend",
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

    def find_nodes_by_name(self, name: str, kind: str | None = None) -> list[GraphNode]:
        """按 name (可选 kind) 找**全部**同名节点 (大小写不敏感)。供歧义检测。"""
        low = name.strip().lower()
        return [
            n for n in self.nodes.values()
            if (kind is None or n.kind == kind) and (n.name or "").lower() == low
        ]


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
    if n.meta and "is_page" in n.meta:  # 前端模块: 标注是否页面(供"影响哪些页面"区分)
        d["is_page"] = bool(n.meta["is_page"])
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


def _resolve(g: ImpactGraph, ref: str, kind: str | None) -> tuple[GraphNode | None, list[GraphNode]]:
    """解析 ref (id 优先, 否则按 name 找)。返回 (命中节点 | None, 歧义候选)。

    id 精确命中 → (node, [])。按 name 唯一命中 → (node, [])。按 name 多个命中 →
    (None, candidates) (歧义, 让调用方用 id 消歧)。无命中 → (None, [])。
    """
    if ref in g.nodes:
        return g.nodes[ref], []
    matches = g.find_nodes_by_name(ref, kind)
    if len(matches) == 1:
        return matches[0], []
    if len(matches) > 1:
        return None, matches  # 歧义
    return None, []


def _not_found(ref_key: str, ref: str, ambiguous: list[GraphNode]) -> dict:
    """统一的未命中/歧义返回。歧义时回候选 (含 file) 让调用方用 id 消歧。"""
    out: dict = {"found": False, ref_key: ref}
    if ambiguous:
        out["ambiguous"] = [_node_brief(n) for n in ambiguous]
    return out


# ---------------------------------------------------------------- 4 个查询入口

def find_impact(conn, project_id: str, node_ref: str) -> dict:
    """改 node_ref (id 或 name) → 跨层**被波及**集合 (反向 BFS, 谁依赖它)。"""
    g = build_impact_graph(conn, project_id)
    node, ambig = _resolve(g, node_ref, None)
    if node is None:
        return _not_found("ref", node_ref, ambig)
    reached = _traverse(g, node.id, reverse=True)
    return {"found": True, "target": _node_brief(node), "impact": _grouped(reached)}


def find_table_usage(conn, project_id: str, table: str) -> dict:
    """给表名 → 哪些函数/端点/前端用它 (反向 BFS, 从 db_table 出发)。"""
    g = build_impact_graph(conn, project_id)
    node, ambig = _resolve(g, table, NodeKind.DB_TABLE.value)
    if node is None:
        return _not_found("table", table, ambig)
    reached = _traverse(g, node.id, reverse=True)
    return {"found": True, "table": _node_brief(node), "usage": _grouped(reached)}


def find_page_dependencies(conn, project_id: str, page_ref: str) -> dict:
    """给前端页/组件 → 它依赖的端点/函数/表 (正向 BFS)。"""
    g = build_impact_graph(conn, project_id)
    node, ambig = _resolve(g, page_ref, None)
    if node is None:
        return _not_found("page", page_ref, ambig)
    reached = _traverse(g, node.id, reverse=False)
    return {"found": True, "page": _node_brief(node), "dependsOn": _grouped(reached)}


def find_impacted_pages(conn, project_id: str, component_ref: str) -> dict:
    """改前端组件 component_ref(id 或 name) → 哪些**页面**受影响。

    反向 BFS(谁 import 它, 含传递: 组件→barrel→页面), 只取 is_page 的 frontend_module 节点。
    解锁"改这个公共组件影响哪些页面"(codegraph 盲区, 数据由 dependency-cruiser 经 frontend_deps 产)。
    """
    g = build_impact_graph(conn, project_id)
    node, ambig = _resolve(g, component_ref, None)
    if node is None:
        return _not_found("component", component_ref, ambig)
    reached = _traverse(g, node.id, reverse=True)
    pages = [
        _node_brief(n, d, v) for (n, d, v) in reached
        if (n.meta or {}).get("is_page")
    ]
    pages.sort(key=lambda p: (p.get("depth", 0), p["file"] or ""))
    return {"found": True, "component": _node_brief(node),
            "pages": pages, "count": len(pages)}


def find_api_callers(conn, project_id: str, endpoint_ref: str) -> dict:
    """给端点 → 哪些前端调它 (反向 BFS, 仅取 frontend 层)。"""
    g = build_impact_graph(conn, project_id)
    node, ambig = _resolve(g, endpoint_ref, NodeKind.BACKEND_ENDPOINT.value)
    if node is None:
        return _not_found("endpoint", endpoint_ref, ambig)
    reached = _traverse(g, node.id, reverse=True)
    callers = [(n, d, v) for (n, d, v) in reached if layer_of(n.kind) == "frontend"]
    return {"found": True, "endpoint": _node_brief(node),
            "callers": [_node_brief(n, d, v) for (n, d, v) in callers],
            "count": len(callers)}


def generate_impact_report(conn, project_id: str, node_ref: str) -> dict:
    """改 node_ref → 一份可读跨层影响报告 (A5)。

    含: 目标节点 + 按层受影响清单 + 风险等级 + 人类可读 summary (markdown)。
    风险口径: 触及前端且跨 ≥2 层 = high;有下游 = medium;无下游 = low。
    """
    r = find_impact(conn, project_id, node_ref)
    if not r["found"]:
        ambig = r.get("ambiguous", [])
        if ambig:
            summary = f"节点名 '{node_ref}' 有 {len(ambig)} 个同名候选, 请用 id 指定 (见 ambiguous)"
        else:
            summary = f"未找到节点: {node_ref}"
        return {"found": False, "ref": node_ref, "summary": summary, "ambiguous": ambig}
    target, impact = r["target"], r["impact"]
    counts, total = impact["counts"], impact["total"]
    layers_hit = [lyr for lyr in ("frontend", "backend", "database") if counts.get(lyr)]

    lines = [f"改动 **{target['name']}** ({target['kind']} / {target['layer']} 层) 的跨层影响:"]
    if total == 0:
        lines.append("- 无下游依赖 (孤立节点或叶子, 改动影响面局限本身)。")
    else:
        for lyr in ("frontend", "backend", "database"):
            items = impact["byLayer"].get(lyr)
            if items:
                names = sorted({i["name"] for i in items})
                preview = ", ".join(names[:10]) + (" ..." if len(names) > 10 else "")
                lines.append(f"- {lyr} 层 {len(items)} 个受影响: {preview}")

    if "frontend" in layers_hit and len(layers_hit) >= 2:
        risk = "high"
    elif total > 0:
        risk = "medium"
    else:
        risk = "low"
    lines.append(f"\n风险: **{risk}** (跨 {len(layers_hit)} 层: {', '.join(layers_hit) or '无'})")

    return {
        "found": True, "target": target, "impact": impact,
        "risk": risk, "layersAffected": layers_hit, "total": total,
        "summary": "\n".join(lines),
    }
