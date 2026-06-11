"""综合分析器框架(A1-1) —— 协议/registry + referential-integrity 校验 + impact 软边过滤。

覆盖软/硬隔离的确定性地基:① registry 注册/筛选 + fail-soft;② validate_soft_result 的
软标记钳制 + 悬空软边丢弃(grounding 代码侧承重墙);③ ingest _analyzers_pass 集成;
④ impact 默认过滤软边(护城河保护)。不碰 LLM(那是 A1-2)。
"""
from __future__ import annotations

import pytest

from codev_platform.graph.analyzers import base
from codev_platform.graph.analyzers.base import (
    applicable_analyzers,
    register_analyzer,
    registered_analyzers,
    validate_soft_result,
)
from codev_platform.graph.schema import (
    AnalyzerResult,
    EdgeKind,
    GraphEdge,
    GraphNode,
    NodeKind,
)


@pytest.fixture
def clean_registry():
    """隔离全局 _ANALYZERS(避免测试注册污染生产 registry / 彼此)。"""
    saved = list(base._ANALYZERS)
    base._ANALYZERS.clear()
    yield
    base._ANALYZERS.clear()
    base._ANALYZERS.extend(saved)


class _DomainAnalyzer:
    """测试 analyzer: 给每个 backend_endpoint 归一个固定业务域软节点 + 软边。"""

    name = "test_domain"

    def __init__(self, *, raises: bool = False):
        self._raises = raises

    def applies(self, nodes):
        if self._raises:
            raise RuntimeError("boom")
        return any(n.kind == NodeKind.BACKEND_ENDPOINT.value for n in nodes)

    def analyze(self, project_id, nodes, edges):
        dom = GraphNode(id=f"{project_id}:business_domain:orders",
                        kind=NodeKind.BUSINESS_DOMAIN, name="orders",
                        project_id=project_id, meta={"derived_by": "test"})
        soft_edges = [
            GraphEdge(source=n.id, target=dom.id, kind=EdgeKind.BELONGS_TO_DOMAIN,
                      confidence=0.8)
            for n in nodes if n.kind == NodeKind.BACKEND_ENDPOINT.value
        ]
        return AnalyzerResult(nodes=[dom], edges=soft_edges, plugin="x")


# ---- registry ----

def test_register_and_applicable(clean_registry):
    a = _DomainAnalyzer()
    register_analyzer(a)
    assert registered_analyzers() == [a]
    eps = [GraphNode(id="p:backend_endpoint:/x", kind=NodeKind.BACKEND_ENDPOINT,
                     name="GET /x", project_id="p")]
    assert applicable_analyzers(eps) == [a]
    # 无 endpoint → 不适用
    files = [GraphNode(id="n", kind=NodeKind.FILE, name="f", project_id="p")]
    assert applicable_analyzers(files) == []


def test_applies_error_is_fail_soft(clean_registry):
    register_analyzer(_DomainAnalyzer(raises=True))
    eps = [GraphNode(id="n", kind=NodeKind.BACKEND_ENDPOINT, name="x", project_id="p")]
    assert applicable_analyzers(eps) == []  # applies 抛错视为不适用, 不拖垮


# ---- referential-integrity 校验 ----

def _soft_node(pid="p"):
    return GraphNode(id=f"{pid}:business_domain:orders", kind=NodeKind.BUSINESS_DOMAIN,
                     name="orders", project_id=pid)


def test_validate_clamps_confidence_and_fills_derived_by():
    out = validate_soft_result(AnalyzerResult(nodes=[_soft_node()], plugin="x"), set())
    assert out.nodes[0].meta["confidence"] < 1.0
    assert "derived_by" in out.nodes[0].meta


def test_validate_clamps_overconfident_soft_node():
    n = GraphNode(id="p:business_domain:o", kind=NodeKind.BUSINESS_DOMAIN, name="o",
                  project_id="p", meta={"confidence": 1.0, "derived_by": "llm"})
    out = validate_soft_result(AnalyzerResult(nodes=[n], plugin="x"), set())
    assert out.nodes[0].meta["confidence"] < 1.0  # 1.0 被钳到软区


def test_validate_drops_dangling_soft_edge():
    dom = _soft_node()
    edge = GraphEdge(source="p:backend_endpoint:GHOST", target=dom.id,
                     kind=EdgeKind.BELONGS_TO_DOMAIN, confidence=0.8)
    out = validate_soft_result(AnalyzerResult(nodes=[dom], edges=[edge], plugin="x"), set())
    assert out.edges == []  # source 指向不存在的硬节点 → 悬空丢弃


def test_validate_keeps_grounded_soft_edge():
    dom, real = _soft_node(), "p:backend_endpoint:/orders"
    edge = GraphEdge(source=real, target=dom.id, kind=EdgeKind.BELONGS_TO_DOMAIN,
                     confidence=0.8)
    out = validate_soft_result(AnalyzerResult(nodes=[dom], edges=[edge], plugin="x"),
                               {real})
    assert len(out.edges) == 1 and out.edges[0].source == real


def test_validate_clamps_overconfident_soft_edge():
    dom, real = _soft_node(), "p:backend_endpoint:/o"
    edge = GraphEdge(source=real, target=dom.id, kind=EdgeKind.BELONGS_TO_DOMAIN,
                     confidence=1.0)
    out = validate_soft_result(AnalyzerResult(nodes=[dom], edges=[edge], plugin="x"),
                               {real})
    assert out.edges[0].confidence < 1.0


def test_validate_drops_soft_edge_with_dangling_target():
    # 软边 target 指向不存在的软节点(结果里无 GHOST 域节点)→ target 端悬空丢弃。
    real = "p:backend_endpoint:/o"
    edge = GraphEdge(source=real, target="p:business_domain:GHOST",
                     kind=EdgeKind.BELONGS_TO_DOMAIN, confidence=0.8)
    out = validate_soft_result(AnalyzerResult(edges=[edge], plugin="x"), {real})
    assert out.edges == []


def test_validate_drops_soft_edge_both_ends_dangling():
    edge = GraphEdge(source="p:backend_endpoint:NOPE", target="p:business_domain:NOPE",
                     kind=EdgeKind.BELONGS_TO_DOMAIN, confidence=0.8)
    out = validate_soft_result(AnalyzerResult(edges=[edge], plugin="x"), set())
    assert out.edges == []


# ---- ingest second post-pass 集成 ----

def test_analyzers_pass_persists_soft_products(clean_registry, tmp_path):
    from codev_platform.graph.ingest import ANALYZERS_PLUGIN, IngestReport, _analyzers_pass
    from codev_platform.graph.store import open_store

    register_analyzer(_DomainAnalyzer())
    conn = open_store("p", path=tmp_path / "g.sqlite")
    try:
        ep = GraphNode(id="p:backend_endpoint:/orders", kind=NodeKind.BACKEND_ENDPOINT,
                       name="GET /orders", project_id="p")
        conn.upsert_result("p",
                      AnalyzerResult(nodes=[ep], plugin="builtin.backend_fastapi"))
        _analyzers_pass(conn, "p", IngestReport(project_id="p"))
        g = conn.load_graph("p", plugin=ANALYZERS_PLUGIN)
        assert any(n.kind == NodeKind.BUSINESS_DOMAIN.value for n in g.nodes)
        soft = [e for e in g.edges if e.kind == EdgeKind.BELONGS_TO_DOMAIN.value]
        assert soft and soft[0].source == ep.id  # 软边 grounding 到真实硬 endpoint
    finally:
        conn.close()


def test_analyzers_pass_drops_hallucinated_edge(clean_registry, tmp_path):
    """analyzer 产指向虚构 endpoint 的软边 → ingest 校验丢弃(grounding 硬约束)。"""
    from codev_platform.graph.ingest import ANALYZERS_PLUGIN, IngestReport, _analyzers_pass
    from codev_platform.graph.store import open_store

    class _Halluc:
        name = "halluc"

        def applies(self, nodes):
            return True

        def analyze(self, project_id, nodes, edges):
            dom = GraphNode(id="p:business_domain:ghost", kind=NodeKind.BUSINESS_DOMAIN,
                            name="ghost", project_id="p")
            e = GraphEdge(source="p:backend_endpoint:NEVER_EXISTED", target=dom.id,
                          kind=EdgeKind.BELONGS_TO_DOMAIN, confidence=0.8)
            return AnalyzerResult(nodes=[dom], edges=[e], plugin="x")

    register_analyzer(_Halluc())
    conn = open_store("p", path=tmp_path / "g.sqlite")
    try:
        _analyzers_pass(conn, "p", IngestReport(project_id="p"))
        g = conn.load_graph("p", plugin=ANALYZERS_PLUGIN)
        assert any(n.kind == NodeKind.BUSINESS_DOMAIN.value for n in g.nodes)  # 软节点留
        assert [e for e in g.edges
                if e.kind == EdgeKind.BELONGS_TO_DOMAIN.value] == []  # 悬空软边被丢
    finally:
        conn.close()


def test_analyze_error_is_fail_soft(clean_registry, tmp_path):
    """单 analyzer 的 analyze 抛错 → fail-soft, 不拖垮其余 analyzer 的产出。"""
    from codev_platform.graph.ingest import ANALYZERS_PLUGIN, IngestReport, _analyzers_pass
    from codev_platform.graph.store import open_store

    class _Boom:
        name = "boom"

        def applies(self, nodes):
            return True

        def analyze(self, project_id, nodes, edges):
            raise RuntimeError("analyze boom")

    register_analyzer(_Boom())            # 先注册会崩的
    register_analyzer(_DomainAnalyzer())  # 再注册正常的
    conn = open_store("p", path=tmp_path / "g.sqlite")
    try:
        ep = GraphNode(id="p:backend_endpoint:/o", kind=NodeKind.BACKEND_ENDPOINT,
                       name="o", project_id="p")
        conn.upsert_result("p",
                      AnalyzerResult(nodes=[ep], plugin="builtin.backend_fastapi"))
        _analyzers_pass(conn, "p", IngestReport(project_id="p"))  # 不抛
        g = conn.load_graph("p", plugin=ANALYZERS_PLUGIN)
        # 崩的被跳过, 正常 analyzer 的软产物仍落库。
        assert any(n.kind == NodeKind.BUSINESS_DOMAIN.value for n in g.nodes)
    finally:
        conn.close()


def test_analyzers_pass_noop_without_registered(clean_registry, tmp_path):
    """无注册 analyzer → no-op(不抛, 写空 ANALYZERS_PLUGIN), 生产默认行为。"""
    from codev_platform.graph.ingest import ANALYZERS_PLUGIN, IngestReport, _analyzers_pass
    from codev_platform.graph.store import open_store

    conn = open_store("p", path=tmp_path / "g.sqlite")
    try:
        report = IngestReport(project_id="p")
        _analyzers_pass(conn, "p", report)
        assert ANALYZERS_PLUGIN in report.ingested
        assert conn.load_graph("p", plugin=ANALYZERS_PLUGIN).nodes == []
    finally:
        conn.close()


# ---- impact 软边过滤(护城河保护) ----

def test_impact_excludes_soft_by_default(tmp_path):
    from codev_platform.graph.impact import build_impact_graph
    from codev_platform.graph.store import open_store

    conn = open_store("p", path=tmp_path / "g.sqlite")
    try:
        ep = GraphNode(id="p:backend_endpoint:/o", kind=NodeKind.BACKEND_ENDPOINT,
                       name="o", project_id="p")
        fn = GraphNode(id="p:backend_function:h", kind=NodeKind.BACKEND_FUNCTION,
                       name="h", project_id="p")
        dom = GraphNode(id="p:business_domain:orders", kind=NodeKind.BUSINESS_DOMAIN,
                        name="orders", project_id="p")
        hard = GraphEdge(source=ep.id, target=fn.id, kind=EdgeKind.CALLS)
        soft = GraphEdge(source=ep.id, target=dom.id, kind=EdgeKind.BELONGS_TO_DOMAIN,
                         confidence=0.8)
        conn.upsert_result("p", AnalyzerResult(nodes=[ep, fn, dom],
                                                edges=[hard, soft], plugin="x"))

        g = build_impact_graph(conn, "p")  # 默认不含软
        assert dom.id not in g.nodes
        targets = [t for t, _ in g.fwd.get(ep.id, [])]
        assert fn.id in targets and dom.id not in targets

        g2 = build_impact_graph(conn, "p", include_soft=True)  # 显式放开
        assert dom.id in g2.nodes
        assert dom.id in [t for t, _ in g2.fwd.get(ep.id, [])]
    finally:
        conn.close()
