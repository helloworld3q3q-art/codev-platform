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

from codev_platform.graph.schema import (
    EdgeKind,
    GraphNode,
    NodeKind,
    edge_provenance,
    is_soft_edge_kind,
    is_soft_node_kind,
)
from codev_platform.graph.store import load_graph

_MAX_DEPTH = 10

# 影响分析"确定依赖 vs 候选"的置信阈值(对齐 audit._LOW_CONF): confidence < 此值的边
# 视作候选(低置信 / 名称启发式推断), 默认不进高风险改动的确定结论, 只作候选提示。
# provenance src(ast/framework/bridge/regex)随 brief 一并返回, 作来源解释。
_CERTAIN_CONF = 0.7


def _is_certain(confidence: float | None) -> bool:
    """边是否"确定依赖": 置信 ≥ 阈值。低置信(fuzzy/regex 推断)= 候选。"""
    c = 1.0 if confidence is None else confidence
    return c >= _CERTAIN_CONF

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
    # 边属性旁路表(confidence + provenance src), 键 (source,target,kind)。与 fwd/rev 的
    # (id,kind) 邻接分开存 —— 不改邻接元组 arity, 既有遍历(find_node_domain/arch 等)零改;
    # 只在出影响 brief 时按方向算键回查, 给路径标来源 + 置信。
    edge_attr: dict[tuple[str, str, str], dict] = field(default_factory=dict)

    def find_nodes_by_name(self, name: str, kind: str | None = None) -> list[GraphNode]:
        """按 name (可选 kind) 找**全部**同名节点 (大小写不敏感)。供歧义检测。"""
        low = name.strip().lower()
        return [
            n for n in self.nodes.values()
            if (kind is None or n.kind == kind) and (n.name or "").lower() == low
        ]


def build_impact_graph(conn, project_id: str, *, include_soft: bool = False) -> ImpactGraph:
    """构建内存影响图。

    include_soft=False(默认): 过滤软节点(BUSINESS_DOMAIN)+ 软边(BELONGS_TO_DOMAIN)——
    "查依赖 / 影响面"走确定性硬骨架, 不被分析器/LLM 软产物污染(护城河保护)。
    include_soft=True: 含软产物, 供"查理解"(业务域归属)类查询放开。
    """
    merged = load_graph(conn, project_id)
    if include_soft:
        nodes, edges = merged.nodes, merged.edges
    else:
        nodes = [n for n in merged.nodes if not is_soft_node_kind(n.kind)]
        edges = [e for e in merged.edges if not is_soft_edge_kind(e.kind)]
    g = ImpactGraph(nodes={n.id: n for n in nodes})
    for e in edges:
        g.fwd.setdefault(e.source, []).append((e.target, e.kind))
        g.rev.setdefault(e.target, []).append((e.source, e.kind))
        prov = edge_provenance(e.meta)
        g.edge_attr[(e.source, e.target, e.kind)] = {
            "confidence": e.confidence, "src": prov.get("src"),
        }
    return g


def _traverse(g: ImpactGraph, start_id: str, *, reverse: bool,
              max_depth: int = _MAX_DEPTH) -> list[tuple[GraphNode, int, str, dict]]:
    """从 start_id 做方向感知 BFS, 返回 [(node, depth, 到达它的边 kind, 边属性), ...] (不含起点)。

    边属性 = {confidence, src}, 从 g.edge_attr 按方向算键回查(reverse 时边方向 nbr→nid)。
    """
    adj = g.rev if reverse else g.fwd
    visited = {start_id}
    out: list[tuple[GraphNode, int, str, dict]] = []
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
                key = (nbr, nid, kind) if reverse else (nid, nbr, kind)  # 边永远 source→target
                out.append((node, depth + 1, kind, g.edge_attr.get(key, {})))
            queue.append((nbr, depth + 1))
    return out


def _node_brief(n: GraphNode, depth: int | None = None, via: str | None = None,
                attr: dict | None = None) -> dict:
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
    if attr:  # 到达该节点那条边的来源 + 置信(Phase 3 provenance): 让影响路径可审计
        conf = attr.get("confidence")
        d["confidence"] = conf
        if attr.get("src") is not None:
            d["src"] = attr["src"]
        d["certain"] = _is_certain(conf)
    return d


def _grouped(reached: list[tuple[GraphNode, int, str, dict]]) -> dict:
    """把 BFS 结果按层分组 + 计数(含确定/候选拆分)。"""
    by_layer: dict[str, list[dict]] = {"frontend": [], "backend": [], "database": [], "other": []}
    certain = 0
    for node, depth, via, attr in reached:
        brief = _node_brief(node, depth, via, attr)
        if brief.get("certain"):
            certain += 1
        by_layer[layer_of(node.kind)].append(brief)
    counts = {k: len(v) for k, v in by_layer.items() if v}
    return {"byLayer": {k: v for k, v in by_layer.items() if v},
            "counts": counts, "total": len(reached),
            # 确定依赖(高置信结构边) vs 候选(低置信 / 名称启发式): 高风险结论只采信确定部分。
            "certainCount": certain, "candidateCount": len(reached) - certain}


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
        _node_brief(n, d, v, a) for (n, d, v, a) in reached
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
    callers = [(n, d, v, a) for (n, d, v, a) in reached if layer_of(n.kind) == "frontend"]
    return {"found": True, "endpoint": _node_brief(node),
            "callers": [_node_brief(n, d, v, a) for (n, d, v, a) in callers],
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
    certain_n = impact.get("certainCount", total)
    candidate_n = impact.get("candidateCount", 0)
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
        # provenance 拆分: 高风险结论只采信确定依赖, 候选边(低置信 / 名称启发式)仅作提示。
        lines.append(f"- 其中 确定依赖 {certain_n}(高置信结构边) / 候选 {candidate_n}"
                     "(低置信或名称启发式推断, 需人工确认)")

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
        "certainCount": certain_n, "candidateCount": candidate_n,
        "summary": "\n".join(lines),
    }


# ---------------------------------------------------------------- 业务域查询(A1 软节点消费前门)

def find_node_domain(conn, project_id: str, node_ref: str) -> dict:
    """查 endpoint/表属于哪个业务域(A1 软节点)。读已标好的软边, **不调 LLM**。

    放开软边(include_soft=True)—— 这是"查理解"类查询, 与"查依赖"(默认过滤软边)分开,
    互不污染: 查依赖走确定性硬骨架, 查理解才放开 LLM 标的软节点。
    """
    g = build_impact_graph(conn, project_id, include_soft=True)
    node, ambig = _resolve(g, node_ref, None)
    if node is None:
        return _not_found("ref", node_ref, ambig)
    domains = []
    for tgt, kind in g.fwd.get(node.id, []):
        if kind == EdgeKind.BELONGS_TO_DOMAIN.value:
            dom = g.nodes.get(tgt)
            if dom is not None:
                domains.append(dom.name)
    return {"found": True, "node": _node_brief(node), "domains": sorted(set(domains))}


def search_nodes(conn, project_id: str, query: str, kind: str = "all",
                 limit: int = 50) -> dict:
    """模糊搜节点(name 含 query, 可选 kind 过滤)。读已落库, 不调 LLM。

    含软节点(include_soft=True), 业务域名也能搜到。对齐 cross-link `search_nodes` 语义
    (退役 cross-link 后由本工具承接)。
    """
    g = build_impact_graph(conn, project_id, include_soft=True)
    low = (query or "").strip().lower()
    if not low:
        return {"query": query, "kind": kind, "hits": [], "count": 0}
    hits = [
        _node_brief(n) for n in g.nodes.values()
        if low in (n.name or "").lower() and (kind in ("all", "") or n.kind == kind)
    ]
    hits.sort(key=lambda h: (h["kind"], h["name"]))
    capped = hits[: max(0, int(limit))]
    return {"query": query, "kind": kind, "hits": capped, "count": len(capped)}


def list_domain_members(conn, project_id: str, domain_name: str) -> dict:
    """查某业务域下有哪些 endpoint/表(反向软边)。读已标好的软节点, **不调 LLM**。"""
    g = build_impact_graph(conn, project_id, include_soft=True)
    doms = g.find_nodes_by_name(domain_name, NodeKind.BUSINESS_DOMAIN.value)
    if not doms:
        return {"found": False, "domain": domain_name}
    dom = doms[0]
    members = []
    for src, kind in g.rev.get(dom.id, []):
        if kind == EdgeKind.BELONGS_TO_DOMAIN.value:
            m = g.nodes.get(src)
            if m is not None:
                members.append(_node_brief(m))
    members.sort(key=lambda x: x["name"])
    return {"found": True, "domain": dom.name, "members": members, "count": len(members)}


# ---------------------------------------------------------------- 架构分层查询(A2 软节点消费前门)

# 分层偏序(rank 越小越"上层"; 允许上层依赖下层, 下层依赖上层 = 逆向违规)。
# util/config/domain_model 之外的横切角色不入 rank → 不参与违规判定(谁都能用)。
_LAYER_RANK: dict[str, int] = {
    "controller": 0, "gateway": 0, "adapter": 1, "service": 2, "repository": 3,
}
# 违规检测遍历的硬边(确定性血缘; calls 是 function→function, imports 是 file→file)。
_DEP_EDGES = frozenset({EdgeKind.CALLS.value, EdgeKind.IMPORTS.value})


def find_arch_role(conn, project_id: str, node_ref: str) -> dict:
    """查某节点(function/endpoint/module)演哪个架构层角色(A2 软节点)。读 PLAYS_ROLE 软边, **不调 LLM**。

    graph 无 FILE kind 节点, 故角色落到**节点级**(同一 file 的节点共享其 file 的角色); 传 endpoint/
    function/module 的 name 或 id 均可。
    """
    g = build_impact_graph(conn, project_id, include_soft=True)
    node, ambig = _resolve(g, node_ref, None)
    if node is None:
        return _not_found("node", node_ref, ambig)
    roles = [
        g.nodes[tgt].name for tgt, kind in g.fwd.get(node.id, [])
        if kind == EdgeKind.PLAYS_ROLE.value and tgt in g.nodes
    ]
    return {"found": True, "node": _node_brief(node), "roles": sorted(set(roles))}


def list_layer_members(conn, project_id: str, role: str) -> dict:
    """查某架构层角色下有哪些 file(反向 PLAYS_ROLE 软边)。读已标好的软节点, **不调 LLM**。"""
    g = build_impact_graph(conn, project_id, include_soft=True)
    layers = g.find_nodes_by_name(role, NodeKind.ARCH_LAYER.value)
    if not layers:
        return {"found": False, "role": role}
    layer = layers[0]
    members = [
        _node_brief(g.nodes[src]) for src, kind in g.rev.get(layer.id, [])
        if kind == EdgeKind.PLAYS_ROLE.value and src in g.nodes
    ]
    members.sort(key=lambda x: x["name"])
    return {"found": True, "role": layer.name, "members": members, "count": len(members)}


def find_arch_violations(conn, project_id: str, limit: int = 200) -> dict:
    """跨层违规检测(**确定性**: layer 软标签 × calls/imports 硬边 × 偏序规则, 不调 LLM)。

    逆向依赖 = 下层角色(rank 大)经 calls/imports 依赖上层角色(rank 小), 如 repository→controller。
    LLM 只提供 layer 标签这一个软输入; 违规判定全确定性(硬边 + rank), 给 agent 重构/PR 自检用。
    """
    g = build_impact_graph(conn, project_id, include_soft=True)
    # node id → role(直接从 PLAYS_ROLE 软边; A2 节点级, 同 file 的每个节点各带一条软边到其 layer)
    node_role: dict[str, str] = {}
    for src, nbrs in g.fwd.items():
        for tgt, kind in nbrs:
            if kind == EdgeKind.PLAYS_ROLE.value and tgt in g.nodes:
                node_role[src] = g.nodes[tgt].name

    violations = []
    for src, nbrs in g.fwd.items():
        sr = node_role.get(src)
        if sr is None or sr not in _LAYER_RANK:
            continue
        for tgt, kind in nbrs:
            if kind not in _DEP_EDGES:
                continue
            tr = node_role.get(tgt)
            if tr is None or tr not in _LAYER_RANK or _LAYER_RANK[sr] <= _LAYER_RANK[tr]:
                continue  # 同层 / 正向(上→下)依赖合法
            violations.append({
                "from": _node_brief(g.nodes[src]), "fromRole": sr,
                "to": _node_brief(g.nodes[tgt]), "toRole": tr, "via": kind,
                "detail": f"{sr} 逆向依赖 {tr}: {g.nodes[src].name} --{kind}--> {g.nodes[tgt].name}",
            })
    violations.sort(key=lambda v: (v["fromRole"], v["toRole"], v["from"]["name"]))
    capped = violations[: max(0, int(limit))]
    return {"project_id": project_id, "violations": capped, "count": len(capped)}
