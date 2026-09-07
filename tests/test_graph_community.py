"""Phase 4 结构社区检测测试 —— 算法确定性/退化 + analyzer + Phase 5 因子/单调性 + agent 查询。"""
from __future__ import annotations

from pathlib import Path

from codev_platform.graph.community import detect_communities, modularity
from codev_platform.graph.analyzers.community import CommunityAnalyzer
from codev_platform.graph.impact import (
    ImpactGraph,
    _best_paths,
    _community_factor,
    _CROSS_COMMUNITY_PENALTY,
    build_impact_graph,
    find_node_community,
    list_communities,
)
from codev_platform.graph.schema import (
    AnalyzerResult,
    EdgeKind,
    GraphEdge,
    GraphNode,
    NodeKind,
    is_soft_edge_kind,
    is_soft_node_kind,
)
from codev_platform.graph.store import open_store

PID = "p"


def _fn(key: str) -> GraphNode:
    return GraphNode(id=f"{PID}:backend_function:{key}", kind=NodeKind.BACKEND_FUNCTION.value,
                     name=key, project_id=PID)


def _edge(a: str, b: str) -> GraphEdge:
    return GraphEdge(source=f"{PID}:backend_function:{a}",
                     target=f"{PID}:backend_function:{b}", kind=EdgeKind.CALLS.value)


# 两个三角团 + 一条桥边 c-d: Louvain 应分成 {a,b,c} 与 {d,e,f}。
_NODES = [_fn(k) for k in ("a", "b", "c", "d", "e", "f")]
_EDGES = [_edge(*p) for p in
          (("a", "b"), ("b", "c"), ("a", "c"),
           ("d", "e"), ("e", "f"), ("d", "f"), ("c", "d"))]


# ---------------- 算法纯核 ----------------

def test_detect_two_cliques() -> None:
    m = detect_communities(_NODES, _EDGES)
    a, b, c = (f"{PID}:backend_function:{k}" for k in ("a", "b", "c"))
    d, e, f = (f"{PID}:backend_function:{k}" for k in ("d", "e", "f"))
    assert m[a] == m[b] == m[c]            # 同团同社区
    assert m[d] == m[e] == m[f]
    assert m[a] != m[d]                     # 两团不同社区
    assert m[a] == a and m[d] == d         # 社区 id = 成员最小 id


def test_detect_deterministic() -> None:
    assert detect_communities(_NODES, _EDGES) == detect_communities(_NODES, _EDGES)
    # 打乱输入顺序结果不变(确定性: sorted 遍历 + min-id tie-break, 无随机源)
    assert detect_communities(list(reversed(_NODES)), list(reversed(_EDGES))) \
        == detect_communities(_NODES, _EDGES)


def test_detect_isolated_and_empty() -> None:
    assert detect_communities([], []) == {}
    iso = [_fn("x"), _fn("y")]
    m = detect_communities(iso, [])        # 无边 → 各自单独社区(id=自身)
    assert m[f"{PID}:backend_function:x"] == f"{PID}:backend_function:x"
    assert m[f"{PID}:backend_function:y"] == f"{PID}:backend_function:y"


def test_detect_skips_soft_and_oversize() -> None:
    soft = GraphNode(id=f"{PID}:business_domain:订单", kind=NodeKind.BUSINESS_DOMAIN.value,
                     name="订单", project_id=PID)
    m = detect_communities(_NODES + [soft], _EDGES)
    assert soft.id not in m                 # 软节点不参与社区
    assert detect_communities(_NODES, _EDGES, max_nodes=2) == {}   # 超规模 fail-soft


# ---------------- analyzer 适配器 ----------------

def test_analyzer_produces_soft_layer() -> None:
    az = CommunityAnalyzer()
    assert az.applies(_NODES) is True
    assert az.applies([_fn("a")]) is False  # 太小不跑
    out = az.analyze(PID, _NODES, _EDGES)
    comms = [n for n in out.nodes if n.kind == NodeKind.COMMUNITY.value]
    assert len(comms) == 2                  # 两团 → 两社区软节点
    assert all(is_soft_node_kind(n.kind) for n in comms)
    assert all(is_soft_edge_kind(e.kind) for e in out.edges)
    assert all(e.kind == EdgeKind.IN_COMMUNITY.value for e in out.edges)
    # 每社区软节点带 size/dominant_kind, id 形如 <pid>:community:cN
    for n in comms:
        assert n.id.startswith(f"{PID}:community:c")
        assert n.meta["size"] >= 2
        assert n.meta["dominant_kind"] == NodeKind.BACKEND_FUNCTION.value


def test_analyzer_singletons_not_emitted() -> None:
    out = CommunityAnalyzer().analyze(PID, [_fn("x"), _fn("y"), _fn("z")], [])
    assert [n for n in out.nodes if n.kind == NodeKind.COMMUNITY.value] == []  # 全单点不产


# ---------------- Phase 5 社区因子 ----------------

def test_community_factor() -> None:
    comm = {"a": "c1", "b": "c1", "c": "c2"}
    assert _community_factor(comm, "a", "b") == 1.0          # 同社区不罚
    assert _community_factor(comm, "a", "c") == _CROSS_COMMUNITY_PENALTY  # 跨社区罚
    assert _community_factor(comm, "a", "z") == 1.0          # 任一端无社区 → 不罚
    assert _community_factor({}, "a", "b") == 1.0            # 无社区数据 → 退化


def _line_graph(community: dict[str, str]) -> ImpactGraph:
    """start→m→t 两跳链(反向 BFS: t 依赖 m 依赖 start), 全 ast 满质量边, 注入 community。"""
    nodes = {x: GraphNode(id=x, kind=NodeKind.BACKEND_FUNCTION.value, name=x, project_id=PID)
             for x in ("start", "m", "t")}
    g = ImpactGraph(nodes=nodes, community=community)
    for s, d in (("start", "m"), ("m", "t")):
        g.fwd.setdefault(s, []).append((d, "calls"))
        g.rev.setdefault(d, []).append((s, "calls"))
        g.edge_attr[(s, d, "calls")] = {"confidence": 1.0, "src": "ast"}
    return g


def test_phase5_same_community_scores_higher() -> None:
    same = _best_paths(_line_graph({"start": "c1", "m": "c1", "t": "c1"}),
                       "start", reverse=False)
    cross = _best_paths(_line_graph({"start": "c1", "m": "c2", "t": "c3"}),
                        "start", reverse=False)
    assert same["t"][0] > cross["t"][0]     # 同社区路径分严格更高


def test_phase5_no_community_is_baseline() -> None:
    """无社区数据 → 评分逐位等于不接社区前的 baseline(零回归)。"""
    base = _best_paths(_line_graph({}), "start", reverse=False)
    # baseline: 两跳 ast 满质量边, 每跳 1.0 × depth_decay 0.9 → t = 0.9*0.9
    assert abs(base["t"][0] - 0.81) < 1e-9


def test_phase5_monotonic() -> None:
    g = _line_graph({"start": "c1", "m": "c2", "t": "c3"})
    best = _best_paths(g, "start", reverse=False)
    assert best["start"][0] >= best["m"][0] >= best["t"][0]   # 沿路单调降(Dijkstra 不变量)


# ---------------- agent 查询(store) ----------------

def test_find_node_community_and_list(tmp_path: Path) -> None:
    store = open_store(PID, path=tmp_path / "s.sqlite")
    try:
        store.upsert_result(PID, AnalyzerResult(nodes=_NODES, edges=_EDGES, plugin="builtin.x"))
        soft = CommunityAnalyzer().analyze(PID, _NODES, _EDGES)
        store.upsert_result(PID, AnalyzerResult(
            nodes=soft.nodes, edges=soft.edges, plugin="builtin.analyzers"))

        r = find_node_community(store, PID, f"{PID}:backend_function:a")
        assert r["found"] and len(r["communities"]) == 1
        member_names = {m["name"] for m in r["communities"][0]["members"]}
        assert member_names == {"b", "c"}    # 同簇(不含自身 a)

        lst = list_communities(store, PID)
        assert lst["count"] == 2 and lst["totalCount"] == 2
        assert all(c["dominant_kind"] == NodeKind.BACKEND_FUNCTION.value
                   for c in lst["communities"])
    finally:
        store.close()


def test_community_soft_filtered_from_impact(tmp_path: Path) -> None:
    """社区软边不污染查依赖: include_soft=False 的图里无 in_community 边, 但社区映射备查。"""
    store = open_store(PID, path=tmp_path / "s.sqlite")
    try:
        store.upsert_result(PID, AnalyzerResult(nodes=_NODES, edges=_EDGES, plugin="builtin.x"))
        soft = CommunityAnalyzer().analyze(PID, _NODES, _EDGES)
        store.upsert_result(PID, AnalyzerResult(
            nodes=soft.nodes, edges=soft.edges, plugin="builtin.analyzers"))

        g = build_impact_graph(store, PID)   # include_soft=False, with_community 默认关
        assert not any(n.kind == NodeKind.COMMUNITY.value for n in g.nodes.values())
        assert g.community == {}              # 默认关 → 社区映射不加载(measure-first, 零回归)
        # 显式开启才加载(Phase 5 评分用)
        g2 = build_impact_graph(store, PID, with_community=True)
        assert g2.community.get(f"{PID}:backend_function:a") is not None
    finally:
        store.close()


# ---------------- modularity 诊断指标 ----------------

def test_modularity_invariants() -> None:
    m = detect_communities(_NODES, _EDGES)
    q = modularity(_NODES, _EDGES, m)
    assert -0.5 <= q <= 1.0                  # 取值域
    assert q > 0.0                            # 两团合理划分 → 正模块度
    # 全合并成一个社区 → Q≈0(无社区结构增益)
    one = {n.id: "all" for n in _NODES}
    assert abs(modularity(_NODES, _EDGES, one)) < 0.2
    assert modularity(_NODES, [], m) == 0.0  # 无边 → 0


def test_phase5_factor_default_off(tmp_path: Path) -> None:
    """Phase 5 社区因子默认关: find_impact_paths 不受社区影响(零回归)。"""
    from codev_platform.graph.impact import find_impact_paths
    store = open_store(PID, path=tmp_path / "s.sqlite")
    try:
        store.upsert_result(PID, AnalyzerResult(nodes=_NODES, edges=_EDGES, plugin="builtin.x"))
        soft = CommunityAnalyzer().analyze(PID, _NODES, _EDGES)
        store.upsert_result(PID, AnalyzerResult(
            nodes=soft.nodes, edges=soft.edges, plugin="builtin.analyzers"))
        # 默认 config(path_penalty_enabled 缺省 = 关)→ 即便有社区软层也不套惩罚
        r = find_impact_paths(store, PID, f"{PID}:backend_function:a")
        assert r["found"]                     # 正常返回, 不因社区报错
    finally:
        store.close()


# ---------------- A1 + Community 共存(同 _analyzers_pass 一次 upsert 不串)----------------

class _FakeLabeler:
    signature = "fake-v1"

    def available(self) -> bool:
        return True

    def label(self, batch):
        from codev_platform.graph.analyzers.domain_labeler import ClusterLabel
        out = []
        for req in batch:
            ep_refs = tuple(m.ref for m in req.members if m.ref.startswith("e"))
            out.append(ClusterLabel(req.cluster_id, "订单", ep_refs))
        return out


def test_a1_and_community_coexist(tmp_path: Path) -> None:
    """A1 业务域 + 结构社区两 analyzer 同跑 _analyzers_pass(共享 builtin.analyzers 一次 upsert):
    两套软层各自正确、互不丢失、幂等重跑不翻倍。"""
    import codev_platform.graph.analyzers.base as abase
    from codev_platform.graph.analyzers.business_domain import BusinessDomainAnalyzer
    from codev_platform.graph.ingest import IngestReport, _analyzers_pass

    # 硬骨架: 2 endpoint(同 file 给 A1 聚一簇)+ 表 + function 团(给社区抱团)。
    ep = [GraphNode(id=f"{PID}:backend_endpoint:e{i}", kind=NodeKind.BACKEND_ENDPOINT.value,
                    name=f"e{i}", project_id=PID, file="ctrl.py") for i in (1, 2)]
    tbl = GraphNode(id=f"{PID}:db_table:t1", kind=NodeKind.DB_TABLE.value, name="t1",
                    project_id=PID)
    fns = [_fn(k) for k in ("a", "b", "c")]
    hard_nodes = ep + [tbl] + fns
    hard_edges = [
        GraphEdge(source=ep[0].id, target=tbl.id, kind="reads_table"),
        GraphEdge(source=ep[1].id, target=tbl.id, kind="reads_table"),
        _edge("a", "b"), _edge("b", "c"), _edge("a", "c"),
    ]
    store = open_store(PID, path=tmp_path / "s.sqlite")
    saved = list(abase._ANALYZERS)
    abase._ANALYZERS.clear()
    try:
        store.upsert_result(PID, AnalyzerResult(
            nodes=hard_nodes, edges=hard_edges, plugin="builtin.x"))
        abase.register_analyzer(BusinessDomainAnalyzer(_FakeLabeler(), cache_dir=tmp_path))
        abase.register_analyzer(CommunityAnalyzer())

        _analyzers_pass(store, PID, IngestReport(project_id=PID))
        g = store.load_graph(PID)
        doms = [n for n in g.nodes if n.kind == NodeKind.BUSINESS_DOMAIN.value]
        comms = [n for n in g.nodes if n.kind == NodeKind.COMMUNITY.value]
        assert doms and comms                 # 两套软层共存
        assert any(e.kind == EdgeKind.BELONGS_TO_DOMAIN.value for e in g.edges)
        assert any(e.kind == EdgeKind.IN_COMMUNITY.value for e in g.edges)

        # 幂等: 重跑 _analyzers_pass 软节点不翻倍
        _analyzers_pass(store, PID, IngestReport(project_id=PID))
        g2 = store.load_graph(PID)
        assert sum(1 for n in g2.nodes if n.kind == NodeKind.BUSINESS_DOMAIN.value) == len(doms)
        assert sum(1 for n in g2.nodes if n.kind == NodeKind.COMMUNITY.value) == len(comms)
    finally:
        abase._ANALYZERS[:] = saved
        store.close()
