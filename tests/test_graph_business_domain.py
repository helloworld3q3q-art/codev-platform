"""业务域 analyzer A1-2a 确定性测试 —— FakeLabeler 把 LLM 完全挡在外面。

覆盖实施专家清单: 聚类(共享专属表合并 / hub 抑制 / 不相关分离)、解析回填、grounding
越界 ref 剔除、缓存命中/失效、labeler 抛错 fail-soft、域名归一 + 同名软节点复用。
不碰 brain、不需 key —— 全确定性。
"""
from __future__ import annotations

from codev_platform.graph.analyzers.business_domain import BusinessDomainAnalyzer
from codev_platform.graph.analyzers.domain_labeler import ClusterLabel
from codev_platform.graph.schema import (
    AnalyzerResult,
    EdgeKind,
    GraphEdge,
    GraphNode,
    NodeKind,
)


# ---- 测试图构造 ----

def _ep(name, pid="p"):
    return GraphNode(id=f"{pid}:backend_endpoint:{name}", kind=NodeKind.BACKEND_ENDPOINT,
                     name=name, project_id=pid)


def _tbl(name, pid="p"):
    return GraphNode(id=f"{pid}:db_table:{name}", kind=NodeKind.DB_TABLE,
                     name=name, project_id=pid)


def _reads(ep, tbl):
    return GraphEdge(source=ep.id, target=tbl.id, kind=EdgeKind.READS_TABLE)


class FakeLabeler:
    """确定性标注器: 按 cluster_id 查表或回默认域; 可配越界 refs / 抛错。"""

    signature = "fake-v1"

    def __init__(self, default_domain="测试域", *, by_cluster=None,
                 member_refs=None, raises=False):
        self._default = default_domain
        self._by_cluster = by_cluster or {}
        self._member_refs = member_refs       # 非 None 时强制返回(测越界剔除)
        self._raises = raises
        self.calls = 0

    def label(self, batch):
        self.calls += 1
        if self._raises:
            raise RuntimeError("fake labeler boom")
        out = []
        for req in batch:
            domain = self._by_cluster.get(req.cluster_id, self._default)
            # 默认只归类 endpoint(业务入口); table 作 grounding 上下文喂, 不默认连域边。
            refs = (self._member_refs if self._member_refs is not None
                    else tuple(m.ref for m in req.members if m.kind == "endpoint"))
            out.append(ClusterLabel(req.cluster_id, domain, refs))
        return out


# ---- 聚类(white-box, 不经 labeler) ----

def test_cluster_merges_endpoints_sharing_table():
    e1, e2, t = _ep("GET /orders"), _ep("POST /orders"), _tbl("orders")
    clusters, _ = BusinessDomainAnalyzer(FakeLabeler())._cluster(
        [e1, e2, t], [_reads(e1, t), _reads(e2, t)])
    assert len(clusters) == 1
    assert set(clusters[0][1]) == {e1.id, e2.id}


def test_cluster_groups_by_file_over_shared_table():
    # file 主信号: 同 file 合并; 不同 file 不合并 —— 即使都读同一共享表(避免跨域表共享
    # 退化成巨型 cluster, codev-platform A1-3 实测 58%->95% 的修复点)。
    def _epf(name, f):
        return GraphNode(id=f"p:backend_endpoint:{name}", kind=NodeKind.BACKEND_ENDPOINT,
                         name=name, project_id="p", file=f)
    e1, e2 = _epf("createOrg", "routes/orgs.py"), _epf("listOrgs", "routes/orgs.py")
    e3 = _epf("createUser", "routes/users.py")
    audit = _tbl("audit")
    nodes = [e1, e2, e3, audit]
    edges = [_reads(e1, audit), _reads(e2, audit), _reads(e3, audit)]  # 三者都读 audit
    clusters, _ = BusinessDomainAnalyzer(FakeLabeler())._cluster(nodes, edges)
    groups = [set(c[1]) for c in clusters]
    assert {e1.id, e2.id} in groups   # orgs.py 两 endpoint 合并
    assert {e3.id} in groups          # users.py 独立(file 优先于共享表)


def test_cluster_separates_unrelated_endpoints():
    e1, e2 = _ep("GET /orders"), _ep("GET /users")
    t1, t2 = _tbl("orders"), _tbl("users")
    clusters, _ = BusinessDomainAnalyzer(FakeLabeler())._cluster(
        [e1, e2, t1, t2], [_reads(e1, t1), _reads(e2, t2)])
    assert len(clusters) == 2


def test_cluster_hub_table_does_not_merge():
    # 4 endpoint 各有专属表 + 都读 user(hub, 扇入 4 ≥ 阈值)→ 不因 user 合并 → 4 cluster。
    eps = [_ep(f"GET /r{i}") for i in range(4)]
    owns = [_tbl(f"own{i}") for i in range(4)]
    user = _tbl("user")
    nodes = eps + owns + [user]
    edges = []
    for e, o in zip(eps, owns):
        edges += [_reads(e, o), _reads(e, user)]
    clusters, _ = BusinessDomainAnalyzer(FakeLabeler())._cluster(nodes, edges)
    assert len(clusters) == 4  # user 是 hub, 不作合并依据


# ---- analyze 端到端(FakeLabeler) ----

def test_analyze_produces_soft_domain(tmp_path):
    e, t = _ep("GET /orders"), _tbl("orders")
    a = BusinessDomainAnalyzer(FakeLabeler(default_domain="订单"), cache_dir=tmp_path)
    r = a.analyze("p", [e, t], [_reads(e, t)])
    doms = [n for n in r.nodes if n.kind == NodeKind.BUSINESS_DOMAIN.value]
    assert len(doms) == 1 and doms[0].name == "订单"
    assert doms[0].meta["confidence"] < 1.0
    assert doms[0].meta["derived_by"] == "business_domain"
    edges = [e for e in r.edges if e.kind == EdgeKind.BELONGS_TO_DOMAIN.value]
    assert edges and edges[0].source == _ep("GET /orders").id


def test_out_of_range_ref_dropped(tmp_path):
    # labeler 回一个不存在的 ref "e99" + 合法 "e1" → e99 剔除, 只产 1 条软边。
    e, t = _ep("GET /orders"), _tbl("orders")
    labeler = FakeLabeler(default_domain="订单", member_refs=("e1", "e99"))
    r = BusinessDomainAnalyzer(labeler, cache_dir=tmp_path).analyze("p", [e, t], [_reads(e, t)])
    edges = [e for e in r.edges if e.kind == EdgeKind.BELONGS_TO_DOMAIN.value]
    assert len(edges) == 1  # e99 越界被剔除


def test_labeler_error_is_fail_soft(tmp_path):
    e, t = _ep("GET /orders"), _tbl("orders")
    r = BusinessDomainAnalyzer(FakeLabeler(raises=True), cache_dir=tmp_path).analyze(
        "p", [e, t], [_reads(e, t)])
    # labeler 抛错 → domain=None → 无软节点, 但不崩。
    assert not any(n.kind == NodeKind.BUSINESS_DOMAIN.value for n in r.nodes)


def test_cache_hit_skips_labeler(tmp_path):
    e, t = _ep("GET /orders"), _tbl("orders")
    labeler = FakeLabeler(default_domain="订单")
    a = BusinessDomainAnalyzer(labeler, cache_dir=tmp_path)
    a.analyze("p", [e, t], [_reads(e, t)])
    assert labeler.calls == 1
    a.analyze("p", [e, t], [_reads(e, t)])  # 同输入 → 命中缓存
    assert labeler.calls == 1               # 没再调 labeler


def test_cache_invalidated_on_cluster_change(tmp_path):
    e1, t = _ep("GET /orders"), _tbl("orders")
    labeler = FakeLabeler(default_domain="订单")
    a = BusinessDomainAnalyzer(labeler, cache_dir=tmp_path)
    a.analyze("p", [e1, t], [_reads(e1, t)])
    assert labeler.calls == 1
    # cluster 成员变(加 endpoint)→ fingerprint 变 → 重调。
    e2 = _ep("POST /orders")
    a.analyze("p", [e1, e2, t], [_reads(e1, t), _reads(e2, t)])
    assert labeler.calls == 2


def test_domain_alias_normalized(tmp_path):
    e, t = _ep("GET /orders"), _tbl("orders")
    # "下单" 经 _ALIAS 归一到 "订单"。
    r = BusinessDomainAnalyzer(FakeLabeler(default_domain="下单"), cache_dir=tmp_path).analyze(
        "p", [e, t], [_reads(e, t)])
    doms = [n for n in r.nodes if n.kind == NodeKind.BUSINESS_DOMAIN.value]
    assert doms and doms[0].name == "订单"


def test_same_domain_reuses_single_node(tmp_path):
    # 两个不相关 cluster 都被标 "订单" → 同 id 去重成 1 个软节点, 但 2 条软边。
    e1, e2 = _ep("GET /orders"), _ep("GET /checkout")
    t1, t2 = _tbl("orders"), _tbl("checkout")
    a = BusinessDomainAnalyzer(FakeLabeler(default_domain="订单"), cache_dir=tmp_path)
    r = a.analyze("p", [e1, e2, t1, t2], [_reads(e1, t1), _reads(e2, t2)])
    doms = [n for n in r.nodes if n.kind == NodeKind.BUSINESS_DOMAIN.value]
    assert len(doms) == 1  # 同名域复用单节点
    edges = [e for e in r.edges if e.kind == EdgeKind.BELONGS_TO_DOMAIN.value]
    assert len(edges) == 2  # 两 endpoint 各连该域


def test_invalid_domain_name_dropped(tmp_path):
    e, t = _ep("GET /orders"), _tbl("orders")
    # 超长域名(> _MAX_DOMAIN_LEN)→ 规范化判非法 → 不产软节点。
    r = BusinessDomainAnalyzer(FakeLabeler(default_domain="这是一个非常非常长的越界业务域名称"),
                               cache_dir=tmp_path).analyze("p", [e, t], [_reads(e, t)])
    assert not any(n.kind == NodeKind.BUSINESS_DOMAIN.value for n in r.nodes)


def test_no_endpoints_no_clusters(tmp_path):
    t = _tbl("orphan")
    a = BusinessDomainAnalyzer(FakeLabeler(), cache_dir=tmp_path)
    assert not a.applies([t])
    r = a.analyze("p", [t], [])
    assert r.nodes == [] and r.edges == []


def test_orphan_endpoint_forms_singleton_cluster():
    # 无下游表的 endpoint → 独立 singleton cluster(不与他人合并)。
    e = _ep("GET /health")
    clusters, _ = BusinessDomainAnalyzer(FakeLabeler())._cluster([e], [])
    assert len(clusters) == 1
    assert clusters[0][1] == [e.id] and clusters[0][2] == []  # 1 endpoint, 0 表


def test_member_rebind_on_node_id_change(tmp_path):
    # 缓存命中(name 不变)但 endpoint node.id 变 → 软边重绑到新 id, 不复用缓存旧 id。
    labeler = FakeLabeler(default_domain="订单")
    a = BusinessDomainAnalyzer(labeler, cache_dir=tmp_path)
    e1, t = _ep("GET /orders"), _tbl("orders")
    a.analyze("p", [e1, t], [_reads(e1, t)])
    assert labeler.calls == 1

    e1b = GraphNode(id="p:backend_endpoint:NEW", kind=NodeKind.BACKEND_ENDPOINT,
                    name="GET /orders", project_id="p")  # 同 name 不同 id
    r2 = a.analyze("p", [e1b, t],
                   [GraphEdge(source=e1b.id, target=t.id, kind=EdgeKind.READS_TABLE)])
    assert labeler.calls == 1  # name 不变 → 缓存命中, 没再调 labeler
    soft = [e for e in r2.edges if e.kind == EdgeKind.BELONGS_TO_DOMAIN.value]
    assert soft and soft[0].source == "p:backend_endpoint:NEW"  # 重绑到新 id


def test_corrupt_cache_tolerated(tmp_path):
    # 缓存文件坏 json → 当空缓存, analyze 不崩、照常标注。
    (tmp_path / "p.json").write_text("{ broken json", encoding="utf-8")
    e, t = _ep("GET /orders"), _tbl("orders")
    r = BusinessDomainAnalyzer(FakeLabeler(default_domain="订单"),
                               cache_dir=tmp_path).analyze("p", [e, t], [_reads(e, t)])
    assert any(n.kind == NodeKind.BUSINESS_DOMAIN.value for n in r.nodes)


def test_business_domain_via_ingest_analyzers_pass(tmp_path):
    """真 BusinessDomainAnalyzer 经 ingest _analyzers_pass + validate_soft_result 落库
    (A1-3 放开生产注册前的集成确认: 软产物钳 confidence + grounding 到真 endpoint)。"""
    from codev_platform.graph.analyzers import base as abase
    from codev_platform.graph.analyzers import register_analyzer
    from codev_platform.graph.ingest import (
        ANALYZERS_PLUGIN,
        IngestReport,
        _analyzers_pass,
    )
    from codev_platform.graph.store import open_store

    saved = list(abase._ANALYZERS)
    abase._ANALYZERS.clear()
    try:
        register_analyzer(BusinessDomainAnalyzer(
            FakeLabeler(default_domain="订单"), cache_dir=tmp_path))
        conn = open_store("p", path=tmp_path / "g.sqlite")
        try:
            e, t = _ep("GET /orders"), _tbl("orders")
            conn.upsert_result("p", AnalyzerResult(
                nodes=[e, t], edges=[_reads(e, t)], plugin="builtin.backend_fastapi"))
            _analyzers_pass(conn, "p", IngestReport(project_id="p"))
            g = conn.load_graph("p", plugin=ANALYZERS_PLUGIN)
            doms = [n for n in g.nodes if n.kind == NodeKind.BUSINESS_DOMAIN.value]
            assert doms and doms[0].name == "订单"
            assert doms[0].meta["confidence"] < 1.0          # validate 后软标记
            soft = [e2 for e2 in g.edges
                    if e2.kind == EdgeKind.BELONGS_TO_DOMAIN.value]
            assert soft and soft[0].source == e.id           # grounding 到真 endpoint
        finally:
            conn.close()
    finally:
        abase._ANALYZERS.clear()
        abase._ANALYZERS.extend(saved)


def test_business_domain_registered_only_when_config_enabled():
    # config gate: 默认/无配置不注册(no-op); 显式 enabled=true 才注册(生产开启路径)。
    from codev_platform.graph.analyzers import _register_configured, base

    saved = list(base._ANALYZERS)
    base._ANALYZERS.clear()
    try:
        assert _register_configured({}) == 0   # 返回注册数(2026-06-08: bool→int, 支持多 analyzer)
        assert base._ANALYZERS == []
        assert _register_configured(
            {"analyzers": {"business_domain": {"enabled": True}}}) == 1
        assert "business_domain" in [a.name for a in base._ANALYZERS]
    finally:
        base._ANALYZERS.clear()
        base._ANALYZERS.extend(saved)


def test_to_request_caps_members_endpoint_priority():
    # 超大簇采样: endpoint 优先全留, table 按剩余配额填(防噪声/爆 token)。
    by_id, eps, tables = {}, [], []
    for i in range(3):
        e = _ep(f"GET /r{i}"); by_id[e.id] = e; eps.append(e.id)
    for i in range(10):
        t = _tbl(f"t{i}"); by_id[t.id] = t; tables.append(t.id)
    req, _ = BusinessDomainAnalyzer._to_request("c", eps, tables, by_id, 5)
    assert len(req.members) == 5
    assert sum(1 for m in req.members if m.kind == "endpoint") == 3  # 全留
    assert sum(1 for m in req.members if m.kind == "table") == 2     # 填剩余 2


def test_to_request_caps_endpoints_when_over_budget():
    by_id, eps = {}, []
    for i in range(8):
        e = _ep(f"GET /r{i}"); by_id[e.id] = e; eps.append(e.id)
    req, _ = BusinessDomainAnalyzer._to_request("c", eps, [], by_id, 5)
    assert len(req.members) == 5 and all(m.kind == "endpoint" for m in req.members)


def test_cache_gc_drops_orphan_entries(tmp_path):
    import json

    a = BusinessDomainAnalyzer(FakeLabeler(default_domain="订单"), cache_dir=tmp_path)
    e1, t1 = _ep("GET /orders"), _tbl("orders")
    a.analyze("p", [e1, t1], [_reads(e1, t1)])
    c1 = json.loads((tmp_path / "p.json").read_text(encoding="utf-8"))["entries"]
    assert len(c1) == 1
    # 换完全不同的 endpoint → 新 cluster, 旧 entry 成孤儿应被 GC。
    e2, t2 = _ep("GET /quotes"), _tbl("quotes")
    a.analyze("p", [e2, t2], [_reads(e2, t2)])
    c2 = json.loads((tmp_path / "p.json").read_text(encoding="utf-8"))["entries"]
    assert len(c2) == 1  # 孤儿清掉, 只剩本轮


def test_ownership_override_beats_llm(tmp_path):
    # 人工纠正过的 domain 盖过 LLM(FakeLabeler 标"错误域")。
    a = BusinessDomainAnalyzer(FakeLabeler(default_domain="错误域"), cache_dir=tmp_path)
    e, t = _ep("GET /orders"), _tbl("orders")
    a.set_override("p", [e.id], "订单")
    r = a.analyze("p", [e, t], [_reads(e, t)])
    doms = [n for n in r.nodes if n.kind == NodeKind.BUSINESS_DOMAIN.value]
    assert doms and doms[0].name == "订单"   # 纠正值, 非 LLM 的"错误域"


def test_ownership_override_skips_labeler(tmp_path):
    labeler = FakeLabeler(default_domain="错误域")
    a = BusinessDomainAnalyzer(labeler, cache_dir=tmp_path)
    e, t = _ep("GET /orders"), _tbl("orders")
    a.set_override("p", [e.id], "订单")
    a.analyze("p", [e, t], [_reads(e, t)])
    assert labeler.calls == 0                # 纠正过 → 跳 LLM, 省钱


def test_ownership_override_still_produces_edges(tmp_path):
    a = BusinessDomainAnalyzer(FakeLabeler(default_domain="x"), cache_dir=tmp_path)
    e, t = _ep("GET /orders"), _tbl("orders")
    a.set_override("p", [e.id], "订单")
    r = a.analyze("p", [e, t], [_reads(e, t)])
    edges = [x for x in r.edges if x.kind == EdgeKind.BELONGS_TO_DOMAIN.value]
    assert edges and edges[0].source == e.id  # endpoint→纠正域 软边照常产


def test_find_node_domain_and_list_members(tmp_path):
    # 消费前门: 正向(节点→业务域)+ 反向(业务域→成员), 读已标好的软节点不调 LLM。
    from codev_platform.graph.impact import find_node_domain, list_domain_members
    from codev_platform.graph.store import open_store

    conn = open_store("p", path=tmp_path / "g.sqlite")
    try:
        e1, e2 = _ep("GET /orders"), _ep("POST /orders")
        dom = GraphNode(id="p:business_domain:订单", kind=NodeKind.BUSINESS_DOMAIN,
                        name="订单", project_id="p", meta={"confidence": 0.7})
        se1 = GraphEdge(source=e1.id, target=dom.id, kind=EdgeKind.BELONGS_TO_DOMAIN,
                        confidence=0.7)
        se2 = GraphEdge(source=e2.id, target=dom.id, kind=EdgeKind.BELONGS_TO_DOMAIN,
                        confidence=0.7)
        conn.upsert_result("p", AnalyzerResult(nodes=[e1, e2, dom],
                                                edges=[se1, se2], plugin="x"))
        r = find_node_domain(conn, "p", "GET /orders")
        assert r["found"] and r["domains"] == ["订单"]
        m = list_domain_members(conn, "p", "订单")
        assert m["found"] and m["count"] == 2
        assert sorted(x["name"] for x in m["members"]) == ["GET /orders", "POST /orders"]
    finally:
        conn.close()
