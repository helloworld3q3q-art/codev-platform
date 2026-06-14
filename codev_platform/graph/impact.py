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

import heapq
import re
from dataclasses import dataclass, field

from codev_platform.graph.schema import (
    EdgeKind,
    GraphNode,
    NodeKind,
    dedup_nodes_by_id,
    edge_provenance,
    is_soft_edge_kind,
    is_soft_node_kind,
)
from codev_platform.graph.edge_resolve import resolve_duplicate_edges

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
    # 节点 → 结构社区 id 映射(Phase 4 软层 in_community 边提取)。**软边被 BFS 图过滤掉, 但社区
    # 映射随图一次加载备查** —— Phase 5 路径评分据此给跨社区边打 ≤1 惩罚(同社区不罚)。空=无社区数据。
    community: dict[str, str] = field(default_factory=dict)

    def find_nodes_by_name(self, name: str, kind: str | None = None) -> list[GraphNode]:
        """按 name (可选 kind) 找**全部**同名节点 (大小写不敏感)。供歧义检测。"""
        low = name.strip().lower()
        return [
            n for n in self.nodes.values()
            if (kind is None or n.kind == kind) and (n.name or "").lower() == low
        ]


def build_impact_graph(store, project_id: str, *, include_soft: bool = False,
                       certain_only: bool = False,
                       with_community: bool = False) -> ImpactGraph:
    """构建内存影响图。

    include_soft=False(默认): 过滤软节点(BUSINESS_DOMAIN)+ 软边(BELONGS_TO_DOMAIN)——
    "查依赖 / 影响面"走确定性硬骨架, 不被分析器/LLM 软产物污染(护城河保护)。
    include_soft=True: 含软产物, 供"查理解"(业务域归属)类查询放开。

    certain_only=True: 进一步**滤掉低置信硬边**(confidence < _CERTAIN_CONF, 即名称启发式
    等候选边)—— 高风险改动结论(Phase 3 Gate)只走确定依赖, 候选不参与遍历。单一过滤点,
    所有 BFS 自动尊重(与 include_soft 同处, 决定"哪些边在图里")。
    """
    merged = store.load_graph(project_id)
    if include_soft:
        nodes, edges = merged.nodes, merged.edges
    else:
        nodes = [n for n in merged.nodes if not is_soft_node_kind(n.kind)]
        edges = [e for e in merged.edges if not is_soft_edge_kind(e.kind)]
    nodes = dedup_nodes_by_id(nodes)   # 同 id 多插件节点去重(优先级让位, 非 dict last-wins 看顺序)
    if certain_only:
        edges = [e for e in edges if _is_certain(e.confidence)]
    edges = resolve_duplicate_edges(edges)   # 冲突消解: 同边多 plugin 重复 → 保最优 provenance
    # url_registry 常量(共享 URL.js 集中声明)的 contains 入边: 在影响图里**丢弃** —— 否则
    # "页面 import 整个注册模块"会经 contains 泛连成"调用其每个接口"(实测一端点假命中 97 页)。
    # 这些常量的精确调用方改由 builtin.frontend_api_usage 的 uses_api 边承载。**只对 url_registry
    # 生效**(内联 api_call 的 contains 父组件=真调用方, 不动 → 量化等纯内联项目零影响)。store 仍
    # 保留 contains(显示/其它消费方不变), 只是 impact 内存图不走它。
    _registry_api = {
        n.id for n in nodes
        if n.kind == NodeKind.FRONTEND_API_CALL.value and (n.meta or {}).get("url_registry")
    }
    g = ImpactGraph(nodes={n.id: n for n in nodes})
    for e in edges:
        if e.kind == EdgeKind.CONTAINS.value and e.target in _registry_api:
            continue
        g.fwd.setdefault(e.source, []).append((e.target, e.kind))
        g.rev.setdefault(e.target, []).append((e.source, e.kind))
        prov = edge_provenance(e.meta)
        g.edge_attr[(e.source, e.target, e.kind)] = {
            "confidence": e.confidence, "src": prov.get("src"),
        }
    # 社区映射(Phase 5 评分用): 仅当 with_community=True 才从 merged.edges 提 in_community 软边。
    # **默认关**(measure-first: 0.8 跨社区惩罚未经 A/B 证实增益, 比照 planner_llm/rerank/A1 默认关
    # 纪律, 不静默改活工具排序)。关 → g.community 空 → _community_factor 恒 1.0 → 严格等于未接因子
    # baseline。社区软层 + agent 查询工具(find_node_community/list_communities)不受此 gate, 始终可用。
    if with_community:
        g.community = {
            e.source: e.target for e in merged.edges
            if e.kind == EdgeKind.IN_COMMUNITY.value
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
    # 实体类名 → 表 fallback (ORM 映射桥): 人/agent 自然用**实体类名** (OmsInboundOrder) 指代表,
    # 而库里是表名 (OMS_INBOUND_ORDER)。db_table 节点 meta["entity_class"] 由 ORM 抽取器 (hbm /
    # mybatis-plus) 写入, 名字未命中时据此把类名解析到表节点 (根治"拿类名查影响面 found:false")。
    if kind in (None, NodeKind.DB_TABLE.value):
        low = ref.strip().lower()
        by_entity = [
            n for n in g.nodes.values()
            if n.kind == NodeKind.DB_TABLE.value
            and str((n.meta or {}).get("entity_class") or "").lower() == low
        ]
        if len(by_entity) == 1:
            return by_entity[0], []
        if len(by_entity) > 1:
            return None, by_entity  # 多实体映射同名 (罕见) → 歧义, 让调用方用表名/id 消歧
    return None, []


def _not_found(ref_key: str, ref: str, ambiguous: list[GraphNode]) -> dict:
    """统一的未命中/歧义返回。歧义时回候选 (含 file) 让调用方用 id 消歧。"""
    out: dict = {"found": False, ref_key: ref}
    if ambiguous:
        out["ambiguous"] = [_node_brief(n) for n in ambiguous]
    return out


_METHOD_PREFIX = re.compile(r"^(?:GET|POST|PUT|DELETE|PATCH|HEAD|OPTIONS)[\s:]+", re.I)


def _norm_endpoint_path(ref: str) -> str:
    """端点 ref 归一成可比裸路径: 剥 'GET '/'GET:' 等 method 前缀 + ?query/#frag + 尾斜杠。
    让 '/pda/x' / 'GET /pda/x' / 'GET:/pda/x' 都归到同一形, 与 meta.url 同形可比。"""
    s = _METHOD_PREFIX.sub("", ref.strip())
    s = s.split("?", 1)[0].split("#", 1)[0].strip()
    return s.rstrip("/")


_PATH_PARAM = re.compile(r"^(?:\{[^}]*\}|:[^/]+)$")   # 路径参数占位: {id} (Spring/FastAPI) / :id (express)


def _endpoint_path_match(stored: str, query: str) -> bool:
    """裸路径模板感知匹配: 段数相等 + 逐段(相等 或 任一侧是路径参数占位)。

    治"查具体值漏报模板端点": 后端端点存为模板 `/users/{id}`, 人/agent 从日志/network 自然敲具体
    `/users/123` → 旧的纯字符串相等命不中(漏报)。占位**只对单段通配**故不过报: `/users/active`
    不会命中 `/users/123`(段 'active' != '123' 且都非占位)。对称处理两侧写法(查询侧也可能是模板)。"""
    if stored == query:
        return True
    sa, sb = stored.split("/"), query.split("/")
    if len(sa) != len(sb):
        return False
    return all(
        a == b or _PATH_PARAM.match(a) is not None or _PATH_PARAM.match(b) is not None
        for a, b in zip(sa, sb)
    )


def _resolve_endpoint(g: ImpactGraph, ref: str) -> tuple[list[GraphNode], list[GraphNode]]:
    """端点解析: 先按 id/name(_resolve), 再按 **URL 路径** 匹配 meta.url。返回 (命中列表, 歧义候选)。

    人 / agent 自然用 URL 路径(`/pda/task/check/container`)指端点, 而端点 id 带 method 前缀
    (`...:GET:/pda/...`)、name 是 handler 名(`checkContainer`)→ 旧 _resolve 两招都不命中 →
    found:false(本次 ideas-v2 PDA 实证)。补 meta.url 路径匹配填这洞。同路径多 method(GET/POST)
    全返回, 调用方聚合其前端调用方(问"谁调这个 URL"不该被 method 切碎)。"""
    node, ambig = _resolve(g, ref, NodeKind.BACKEND_ENDPOINT.value)
    if node is not None:
        return [node], []
    if ambig:
        return [], ambig
    path = _norm_endpoint_path(ref)
    if not path:
        return [], []
    matches = [
        n for n in g.nodes.values()
        if n.kind == NodeKind.BACKEND_ENDPOINT.value
        and _endpoint_path_match(_norm_endpoint_path((n.meta or {}).get("url") or ""), path)
    ]
    return matches, []


# ---------------------------------------------------------------- 4 个查询入口

def find_impact(store, project_id: str, node_ref: str, *,
                certain_only: bool = False) -> dict:
    """改 node_ref (id 或 name) → 跨层**被波及**集合 (反向 BFS, 谁依赖它)。

    certain_only=True: 只走确定依赖(滤低置信候选边), 供高风险改动结论用(Phase 3 Gate)。
    """
    g = build_impact_graph(store, project_id, certain_only=certain_only)
    node, ambig = _resolve(g, node_ref, None)
    if node is None:
        return _not_found("ref", node_ref, ambig)
    reached = _traverse(g, node.id, reverse=True)
    return {"found": True, "target": _node_brief(node), "impact": _grouped(reached),
            "certainOnly": certain_only}


def find_table_usage(store, project_id: str, table: str, *,
                     certain_only: bool = False) -> dict:
    """给表名 → 哪些函数/端点/前端用它 (反向 BFS, 从 db_table 出发)。

    certain_only=True: 只走确定依赖(滤低置信候选边)。
    """
    g = build_impact_graph(store, project_id, certain_only=certain_only)
    node, ambig = _resolve(g, table, NodeKind.DB_TABLE.value)
    if node is None:
        return _not_found("table", table, ambig)
    reached = _traverse(g, node.id, reverse=True)
    return {"found": True, "table": _node_brief(node), "usage": _grouped(reached),
            "certainOnly": certain_only}


def find_page_dependencies(store, project_id: str, page_ref: str) -> dict:
    """给前端页/组件 → 它依赖的端点/函数/表 (正向 BFS)。"""
    g = build_impact_graph(store, project_id)
    node, ambig = _resolve(g, page_ref, None)
    if node is None:
        return _not_found("page", page_ref, ambig)
    reached = _traverse(g, node.id, reverse=False)
    return {"found": True, "page": _node_brief(node), "dependsOn": _grouped(reached)}


def find_impacted_pages(store, project_id: str, component_ref: str) -> dict:
    """改前端组件 component_ref(id 或 name) → 哪些**页面**受影响。

    反向 BFS(谁 import 它, 含传递: 组件→barrel→页面), 只取 is_page 的 frontend_module 节点。
    解锁"改这个公共组件影响哪些页面"(codegraph 盲区, 数据由 dependency-cruiser 经 frontend_deps 产)。
    """
    g = build_impact_graph(store, project_id)
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


def find_api_callers(store, project_id: str, endpoint_ref: str, *,
                     certain_only: bool = False) -> dict:
    """给端点 → 哪些前端调它 (反向 BFS, 仅取 frontend 层)。

    certain_only=True: 只走确定依赖(滤低置信候选边)。
    """
    g = build_impact_graph(store, project_id, certain_only=certain_only)
    eps, ambig = _resolve_endpoint(g, endpoint_ref)
    if not eps:
        return _not_found("endpoint", endpoint_ref, ambig)
    # 同 URL 路径多 method(GET/POST) → 聚合各端点的前端调用方, 按 id 去重(取最近距离那条 brief)。
    agg: dict[str, tuple] = {}
    for ep in eps:
        for (n, d, v, a) in _traverse(g, ep.id, reverse=True):
            if layer_of(n.kind) != "frontend":
                continue
            cur = agg.get(n.id)
            if cur is None or d < cur[1]:
                agg[n.id] = (n, d, v, a)
    callers = list(agg.values())
    out = {"found": True, "endpoint": _node_brief(eps[0]),
           "callers": [_node_brief(n, d, v, a) for (n, d, v, a) in callers],
           "count": len(callers), "certainOnly": certain_only}
    if len(eps) > 1:   # 按 URL 命中多 method, 标出全部让调用方知道聚合范围
        out["matchedEndpoints"] = [_node_brief(e) for e in eps]
    return out


def generate_impact_report(store, project_id: str, node_ref: str, *,
                           certain_only: bool = False) -> dict:
    """改 node_ref → 一份可读跨层影响报告 (A5)。

    含: 目标节点 + 按层受影响清单 + 风险等级 + 人类可读 summary (markdown)。
    风险口径: 触及前端且跨 ≥2 层 = high;有下游 = medium;无下游 = low。
    certain_only=True: 只走确定依赖(滤低置信候选边), 给高风险结论更保守的影响面。
    """
    r = find_impact(store, project_id, node_ref, certain_only=certain_only)
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
        "certainOnly": certain_only,
        "summary": "\n".join(lines),
    }


# ---------------------------------------------------------------- 多跳路径评分(Phase 5)

# 边来源权重: 结构化精确(ast/framework/bridge)= 满权; 名称启发式(regex)/ LLM 软边降权;
# 未盖戳居中。路径评分按 confidence × src 权重的**乘积** —— 数据驱动, 不内置 kind 偏好。
_SRC_WEIGHT: dict[str, float] = {
    "ast": 1.0, "framework": 1.0, "bridge": 1.0, "regex": 0.7, "llm": 0.5, "manual": 1.0,
}
_SRC_DEFAULT_WEIGHT = 0.85   # 未盖 provenance 的硬边(插件直产 reads_table 等)

# 边**关系强度**权重(与 src 权重正交: src=解析确定性, kind=该关系本身多强地表征"依赖")。
# 只列**降权**的弱关系: import ≠ 重度依赖其行为 / renders 结构性 / mentions·relates_to 松关联 /
# changed_by 历史关联。强依赖边(calls/calls_api/uses_api/reads/writes/updates/contains/...)取默认
# 1.0 —— 其确定性已由 src 权重表达, 此处不二次惩罚(防双重降权)。
_KIND_WEIGHT: dict[str, float] = {
    "imports": 0.85, "renders": 0.9, "mentions": 0.6, "relates_to": 0.6, "changed_by": 0.7,
}
_KIND_DEFAULT_WEIGHT = 1.0
# 深度衰减: 每多一跳乘一次 → 同等单边质量下**更短的依赖路径得分更高**(近依赖优先)。≤1 保 Dijkstra
# 出堆即终态的单调性。1.0=不衰减(纯按边质量乘积)。
_DEPTH_DECAY = 0.9
_PATH_MAX_FANOUT = 60        # 单节点出边上限(防高出度爆炸)
# 跨社区惩罚(Phase 4 社区因子): 同社区边 = 1.0(不罚), 跨社区边乘此(<1)。**必须 ≤1** —— 若做成
# "同社区 >1 加成"会破坏 _best_paths 的 ≤1 单调性 → Dijkstra 出堆即终态失效 → 排序错(排序上
# "跨社区罚"与"同社区奖"等价, 但前者保单调)。无社区数据时退化为 1.0(见 _community_factor)。
_CROSS_COMMUNITY_PENALTY = 0.8


def _community_factor(community: dict[str, str], a_id: str, b_id: str) -> float:
    """边两端社区一致性因子 ∈ (0,1]: 同社区或缺数据 = 1.0; 跨社区 = _CROSS_COMMUNITY_PENALTY。

    缺数据(无社区软层 / 任一端无社区)→ 1.0 → 路径评分逐位等于未接社区前的 baseline(零回归)。
    """
    if not community:
        return 1.0
    ca, cb = community.get(a_id), community.get(b_id)
    if ca is None or cb is None or ca == cb:
        return 1.0
    return _CROSS_COMMUNITY_PENALTY


def _edge_quality(attr: dict, kind: str) -> float:
    """单边质量 ∈ (0,1]: confidence × src 权重(解析确定性)× kind 权重(关系强度)。
    结构边×高置信×强关系 ≈1; regex/llm/低置信/弱关系(import 等)拉低。"""
    conf = attr.get("confidence")
    conf = 1.0 if conf is None else conf
    src_w = _SRC_WEIGHT.get(attr.get("src"), _SRC_DEFAULT_WEIGHT)
    kind_w = _KIND_WEIGHT.get(kind, _KIND_DEFAULT_WEIGHT)
    return conf * src_w * kind_w


def _best_paths(g: ImpactGraph, start_id: str, *, reverse: bool,
                max_depth: int = _MAX_DEPTH) -> dict[str, tuple[float, list]]:
    """Dijkstra 最大乘积: 每个可达节点保**最优单路径**(score=Π(边质量 × 深度衰减), 越大越强依赖)。

    每节点只留一条最优路径(非枚举全路径)→ 有界 O(节点数), 不指数爆炸。边质量 ≤1 且 depth_decay ≤1
    故 score 沿路单调降, 标准 Dijkstra(出堆即终态)。边质量=conf×src权重×kind权重(见 _edge_quality),
    每跳再乘 _DEPTH_DECAY → 近依赖优先。返回 {node_id: (score, [(node_id, kind, attr)...])}。
    """
    adj = g.rev if reverse else g.fwd
    best: dict[str, tuple[float, list]] = {start_id: (1.0, [])}
    heap: list[tuple[float, int, str]] = [(-1.0, 0, start_id)]   # (-score, depth, node)
    done: set[str] = set()
    while heap:
        neg, depth, nid = heapq.heappop(heap)
        if nid in done:
            continue
        done.add(nid)
        if depth >= max_depth:
            continue
        score = -neg
        for nbr, kind in adj.get(nid, ())[:_PATH_MAX_FANOUT]:
            if nbr in done or nbr not in g.nodes:
                continue
            key = (nbr, nid, kind) if reverse else (nid, nbr, kind)   # 边永远 source→target
            attr = g.edge_attr.get(key, {})
            # 社区因子对称(同/跨社区), 与方向无关 → 直接用 nid/nbr。无社区数据时退化为 1.0。
            nscore = (score * _edge_quality(attr, kind)
                      * _community_factor(g.community, nid, nbr) * _DEPTH_DECAY)
            if nbr not in best or nscore > best[nbr][0]:
                best[nbr] = (nscore, best[nid][1] + [(nbr, kind, attr)])
                heapq.heappush(heap, (-nscore, depth + 1, nbr))
    return best


def find_impact_paths(store, project_id: str, node_ref: str, *,
                      top_n: int = 10, certain_only: bool = False) -> dict:
    """改 node_ref → **top-N 最强依赖路径**(评分 + 每跳证据 + 确定/候选)(Phase 5)。

    反向 BFS(谁依赖它)每节点取最优路径, 按 score=Π(confidence×src权重)降序取 top-N。
    每跳给 node + via_edge + src + confidence(可解释); 全跳确定边则 path certain。

    社区因子(Phase 4): 仅当 config `analyzers.community.path_penalty_enabled`=true 才把社区
    映射载进图给跨社区边打 ≤1 惩罚(默认关, measure-first 待 A/B; 关时严格等于未接因子 baseline)。
    """
    from codev_platform.core.config import get, load_config
    with_community = bool(get(load_config(), "analyzers.community.path_penalty_enabled", False))
    g = build_impact_graph(store, project_id, certain_only=certain_only,
                           with_community=with_community)
    node, ambig = _resolve(g, node_ref, None)
    if node is None:
        return _not_found("ref", node_ref, ambig)
    best = _best_paths(g, node.id, reverse=True)
    scored = []
    for nid, (score, hops) in best.items():
        if nid == node.id or not hops:
            continue
        path_hops = [{
            "node": _node_brief(g.nodes[h_nid]), "via_edge": kind,
            "src": attr.get("src"), "confidence": attr.get("confidence"),
            "certain": _is_certain(attr.get("confidence")),
        } for (h_nid, kind, attr) in hops]
        scored.append({
            "endpoint": _node_brief(g.nodes[nid]),
            "score": round(score, 4),
            "depth": len(hops),
            "certain": all(h["certain"] for h in path_hops),
            "hops": path_hops,
        })
    # 排序: 分高优先 → 浅路优先 → 稳定(endpoint id)。可测可复现。
    scored.sort(key=lambda p: (-p["score"], p["depth"], p["endpoint"]["id"]))
    top = scored[: max(0, int(top_n))]
    return {"found": True, "target": _node_brief(node), "paths": top,
            "count": len(top), "totalReached": len(scored)}


# ---------------------------------------------------------------- 软节点消费查询 re-export
# A1 业务域 / Phase 4 结构社区 / A2 架构层的"查理解"类查询已分出到 impact_soft.py(控单文件
# ≤600 行 file-discipline §1)。末尾 re-export 保持 `impact.find_node_domain(...)` 等旧用法不破
# (mcp_server / agent tools / contract_drift / 测试)。**必须在文件末** —— impact_soft 反向 import
# 本模块的 build_impact_graph/_resolve/_node_brief/_not_found, 这些已在上方定义, circular 安全。
from codev_platform.graph.impact_soft import (  # noqa: E402,F401
    find_arch_role,
    find_arch_violations,
    find_node_community,
    find_node_domain,
    list_communities,
    list_domain_members,
    list_layer_members,
    search_nodes,
)
