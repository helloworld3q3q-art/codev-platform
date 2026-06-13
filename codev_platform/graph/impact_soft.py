"""软节点消费查询(A1 业务域 / Phase 4 结构社区 / A2 架构层)—— graph 软层对外读前门。

从 impact.py 分出(2026-06-13, 控单文件 ≤600 行 file-discipline §1): 这些查询读 analyzer
post-pass 产的**软节点 / 软边**(business_domain / community / arch_layer), 回答"查理解"类问题
(属哪个域 / 结构社区 / 架构角色), 与 impact.py 的"查依赖"硬骨架引擎正交。全部:
  - include_soft=True(放开软产物, 与查依赖默认过滤分开, 护城河不被 LLM 噪声污染)
  - 纯读 sqlite, **不调 LLM**(读已落库软标)

复用 impact.py 的图构建 + 解析 + brief 渲染(单一真值源)。impact.py 末尾 re-export 本模块全部
公共函数 → `impact.find_node_domain(...)` 旧用法不破。**impact 的助手在函数内惰性 import** ——
打破 impact↔impact_soft 模块级循环(任一先导入都安全), 单向依赖。import 仅一次(Python 缓存)。
"""
from __future__ import annotations

from codev_platform.graph.schema import EdgeKind, GraphNode, NodeKind


def _engine():
    """惰性取 impact 引擎(build_impact_graph/_resolve/_node_brief/_not_found)。

    避免模块级 `from .impact import ...` 与 impact 末尾的 re-export 形成循环导入
    (impact_soft 被先于 impact 单独 import 时会炸)。函数内 import → impact 此时已完成加载。
    """
    from codev_platform.graph import impact
    return impact


# ---------------------------------------------------------------- 节点搜索(模糊搜 name, 含软节点)

def _name_relevance(name: str, terms: list[str], full: str) -> tuple | None:
    """search_nodes 相关性排序键(可比较 tuple, **越小越相关**); 命中 0 词返回 None。

    分级(对齐 codegraph suite `name=? DESC, length ASC` 思路, 提升相关项 lane 内 rank):
    每词 精确名==词(3) > 前缀(2) > 子串(1); 命中词多优先; 整串(name 恰好/前缀是整个 query)
    额外强加权; 同档**短名优先**(匹配更聚焦, 不被长名稀释)。name 已小写。
    """
    matched = 0
    quality = 0
    for t in terms:
        if name == t:
            quality += 3
            matched += 1
        elif name.startswith(t):
            quality += 2
            matched += 1
        elif t in name:
            quality += 1
            matched += 1
    if not matched:
        return None
    whole = 2 if name == full else (1 if name.startswith(full) else 0)
    return (-matched, -whole, -quality, len(name))   # 升序排 → 越相关越靠前


def search_nodes(store, project_id: str, query: str, kind: str = "all",
                 limit: int = 50) -> dict:
    """模糊搜节点(name 命中 query 词, 可选 kind 过滤)。读已落库, 不调 LLM。

    含软节点(include_soft=True), 业务域名也能搜到。对齐 cross-link `search_nodes` 语义。
    **分词 + 相关性分级排序**(_name_relevance): 命中词多 > 精确/前缀 > 子串 > 短名。单词子串
    命中行为兼容原版; 多词不再要求整串连续子串(原"impact analysis"整串匹配不到任何 name → 0
    命中的弱点), 让跨 lane 融合召回的 graph lane 出**有序**结果(相关项排前 → 融合质量↑)。
    """
    eng = _engine()
    g = eng.build_impact_graph(store, project_id, include_soft=True)
    terms = [t for t in (query or "").strip().lower().split() if t]
    if not terms:
        return {"query": query, "kind": kind, "hits": [], "count": 0}
    full = " ".join(terms)
    scored: list[tuple[tuple, GraphNode]] = []
    for n in g.nodes.values():
        if kind not in ("all", "") and n.kind != kind:
            continue
        key = _name_relevance((n.name or "").lower(), terms, full)
        if key is not None:
            scored.append((key, n))
    scored.sort(key=lambda x: (x[0], x[1].kind, x[1].name or ""))   # 相关性键 + 稳定次序
    hits = [eng._node_brief(n) for _, n in scored[: max(0, int(limit))]]
    return {"query": query, "kind": kind, "hits": hits, "count": len(hits)}


# ---------------------------------------------------------------- 业务域查询(A1 软节点)

def find_node_domain(store, project_id: str, node_ref: str) -> dict:
    """查 endpoint/表属于哪个业务域(A1 软节点)。读已标好的软边, **不调 LLM**。

    放开软边(include_soft=True)—— 这是"查理解"类查询, 与"查依赖"(默认过滤软边)分开,
    互不污染: 查依赖走确定性硬骨架, 查理解才放开 LLM 标的软节点。
    """
    eng = _engine()
    g = eng.build_impact_graph(store, project_id, include_soft=True)
    node, ambig = eng._resolve(g, node_ref, None)
    if node is None:
        return eng._not_found("ref", node_ref, ambig)
    domains = []
    for tgt, kind in g.fwd.get(node.id, []):
        if kind == EdgeKind.BELONGS_TO_DOMAIN.value:
            dom = g.nodes.get(tgt)
            if dom is not None:
                domains.append(dom.name)
    return {"found": True, "node": eng._node_brief(node), "domains": sorted(set(domains))}


def list_domain_members(store, project_id: str, domain_name: str) -> dict:
    """查某业务域下有哪些 endpoint/表(反向软边)。读已标好的软节点, **不调 LLM**。"""
    eng = _engine()
    g = eng.build_impact_graph(store, project_id, include_soft=True)
    doms = g.find_nodes_by_name(domain_name, NodeKind.BUSINESS_DOMAIN.value)
    if not doms:
        return {"found": False, "domain": domain_name}
    dom = doms[0]
    members = []
    for src, kind in g.rev.get(dom.id, []):
        if kind == EdgeKind.BELONGS_TO_DOMAIN.value:
            m = g.nodes.get(src)
            if m is not None:
                members.append(eng._node_brief(m))
    members.sort(key=lambda x: x["name"])
    return {"found": True, "domain": dom.name, "members": members, "count": len(members)}


# ---------------------------------------------------------------- 结构社区查询(Phase 4 软节点)

_COMMUNITY_MEMBER_CAP = 50   # 单社区返回成员上限(大社区防 payload 爆; 仍给 size 全量计数)


def find_node_community(store, project_id: str, node_ref: str) -> dict:
    """查某节点属于哪个结构社区 + 同簇成员(Phase 4 软节点)。读 IN_COMMUNITY 软边, **不调 LLM**。

    给 agent 答"这块代码结构上和谁抱团"(全节点覆盖, 区别于 A1 业务域只 endpoint/表)。
    放开软边(include_soft=True)——"查理解"类查询, 与"查依赖"(默认过滤软边)分开互不污染。
    """
    eng = _engine()
    g = eng.build_impact_graph(store, project_id, include_soft=True)
    node, ambig = eng._resolve(g, node_ref, None)
    if node is None:
        return eng._not_found("ref", node_ref, ambig)
    communities = []
    for tgt, kind in g.fwd.get(node.id, []):
        if kind != EdgeKind.IN_COMMUNITY.value:
            continue
        comm = g.nodes.get(tgt)
        if comm is None:
            continue
        members = [
            eng._node_brief(g.nodes[src]) for src, k in g.rev.get(comm.id, [])
            if k == EdgeKind.IN_COMMUNITY.value and src in g.nodes and src != node.id
        ]
        members.sort(key=lambda x: (x["layer"], x["kind"], x["name"] or ""))
        meta = comm.meta or {}
        communities.append({
            "id": comm.id, "name": comm.name,
            "size": meta.get("size", len(members) + 1),
            "dominant_kind": meta.get("dominant_kind"),
            "members": members[:_COMMUNITY_MEMBER_CAP],
            "membersTruncated": len(members) > _COMMUNITY_MEMBER_CAP,
        })
    return {"found": True, "node": eng._node_brief(node), "communities": communities}


def list_communities(store, project_id: str, limit: int = 50) -> dict:
    """列整仓结构社区地图(每簇大小/主导 kind/代表成员)——给 agent 做 onboarding / 结构概览。

    读 COMMUNITY 软节点 meta(size/dominant_kind/sample), 按 size 降序。**不调 LLM**。
    """
    eng = _engine()
    g = eng.build_impact_graph(store, project_id, include_soft=True)
    items = []
    for n in g.nodes.values():
        if n.kind != NodeKind.COMMUNITY.value:
            continue
        m = n.meta or {}
        items.append({
            "id": n.id, "name": n.name, "size": m.get("size", 0),
            "dominant_kind": m.get("dominant_kind"), "sample": m.get("sample", []),
        })
    items.sort(key=lambda x: (-(x["size"] or 0), x["id"]))   # 大簇优先, 稳定
    capped = items[: max(0, int(limit))]
    return {"project_id": project_id, "communities": capped, "count": len(capped),
            "totalCount": len(items), "truncated": len(items) > len(capped)}


# ---------------------------------------------------------------- 架构分层查询(A2 软节点)

# 分层偏序(rank 越小越"上层"; 允许上层依赖下层, 下层依赖上层 = 逆向违规)。
# util/config/domain_model 之外的横切角色不入 rank → 不参与违规判定(谁都能用)。
_LAYER_RANK: dict[str, int] = {
    "controller": 0, "gateway": 0, "adapter": 1, "service": 2, "repository": 3,
}
# 违规检测遍历的硬边(确定性血缘; calls 是 function→function, imports 是 file→file)。
_DEP_EDGES = frozenset({EdgeKind.CALLS.value, EdgeKind.IMPORTS.value})


def find_arch_role(store, project_id: str, node_ref: str) -> dict:
    """查某节点(function/endpoint/module)演哪个架构层角色(A2 软节点)。读 PLAYS_ROLE 软边, **不调 LLM**。

    graph 无 FILE kind 节点, 故角色落到**节点级**(同一 file 的节点共享其 file 的角色); 传 endpoint/
    function/module 的 name 或 id 均可。
    """
    eng = _engine()
    g = eng.build_impact_graph(store, project_id, include_soft=True)
    node, ambig = eng._resolve(g, node_ref, None)
    if node is None:
        return eng._not_found("node", node_ref, ambig)
    roles = [
        g.nodes[tgt].name for tgt, kind in g.fwd.get(node.id, [])
        if kind == EdgeKind.PLAYS_ROLE.value and tgt in g.nodes
    ]
    return {"found": True, "node": eng._node_brief(node), "roles": sorted(set(roles))}


def list_layer_members(store, project_id: str, role: str) -> dict:
    """查某架构层角色下有哪些 file(反向 PLAYS_ROLE 软边)。读已标好的软节点, **不调 LLM**。"""
    eng = _engine()
    g = eng.build_impact_graph(store, project_id, include_soft=True)
    layers = g.find_nodes_by_name(role, NodeKind.ARCH_LAYER.value)
    if not layers:
        return {"found": False, "role": role}
    layer = layers[0]
    members = [
        eng._node_brief(g.nodes[src]) for src, kind in g.rev.get(layer.id, [])
        if kind == EdgeKind.PLAYS_ROLE.value and src in g.nodes
    ]
    members.sort(key=lambda x: x["name"])
    return {"found": True, "role": layer.name, "members": members, "count": len(members)}


def find_arch_violations(store, project_id: str, limit: int = 200) -> dict:
    """跨层违规检测(**确定性**: layer 软标签 × calls/imports 硬边 × 偏序规则, 不调 LLM)。

    逆向依赖 = 下层角色(rank 大)经 calls/imports 依赖上层角色(rank 小), 如 repository→controller。
    LLM 只提供 layer 标签这一个软输入; 违规判定全确定性(硬边 + rank), 给 agent 重构/PR 自检用。
    """
    eng = _engine()
    g = eng.build_impact_graph(store, project_id, include_soft=True)
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
                "from": eng._node_brief(g.nodes[src]), "fromRole": sr,
                "to": eng._node_brief(g.nodes[tgt]), "toRole": tr, "via": kind,
                "detail": f"{sr} 逆向依赖 {tr}: {g.nodes[src].name} --{kind}--> {g.nodes[tgt].name}",
            })
    violations.sort(key=lambda v: (v["fromRole"], v["toRole"], v["from"]["name"]))
    capped = violations[: max(0, int(limit))]
    return {"project_id": project_id, "violations": capped,
            "count": len(capped),                    # 兼容旧字段(=返回数)
            "returnedCount": len(capped), "totalCount": len(violations),
            "truncated": len(violations) > len(capped)}
