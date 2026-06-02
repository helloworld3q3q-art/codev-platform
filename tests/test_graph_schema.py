"""Tests for codev_platform.graph.schema — 统一图谱模型 (Phase 1).

覆盖:基本构造、枚举完整性 (plan 列出的全部 node/edge kind)、to_dict/from_dict
往返序列化、AnalyzerResult 聚合 + merge。纯内存,无 DB / 无外部依赖。
"""
from __future__ import annotations

from codev_platform.graph import (
    AnalyzerResult,
    EdgeKind,
    Evidence,
    Finding,
    GraphEdge,
    GraphNode,
    NodeKind,
)


# ---- 枚举完整性 (对齐 plan Phase 1 清单) ----

def test_node_kind_covers_plan_list():
    expected = {
        "project", "file", "frontend_route", "frontend_component",
        "frontend_api_call", "backend_endpoint", "backend_function",
        "db_table", "db_column", "wiki_page", "jira_issue", "feishu_doc",
        "git_commit", "pull_request",
    }
    assert {k.value for k in NodeKind} == expected


def test_edge_kind_covers_plan_list():
    expected = {
        "contains", "imports", "calls", "renders", "defines_api", "calls_api",
        "implements", "reads_table", "writes_table", "updates_table",
        "mentions", "relates_to", "changed_by",
    }
    assert {k.value for k in EdgeKind} == expected


def test_kind_enum_is_str_subclass():
    # str 子类 -> 可直接当字符串用 (JSON / sqlite 友好)
    assert NodeKind.DB_TABLE == "db_table"
    assert EdgeKind.READS_TABLE == "reads_table"


# ---- 基本构造 ----

def test_graph_node_construct_minimal():
    n = GraphNode(id="p:db_table:customer", kind=NodeKind.DB_TABLE,
                  name="customer", project_id="demo-crm")
    assert n.name == "customer"
    assert n.file is None
    assert n.meta == {}


def test_graph_edge_default_confidence():
    e = GraphEdge(source="a", target="b", kind=EdgeKind.CALLS_API)
    assert e.confidence == 1.0
    assert e.meta == {}


def test_finding_defaults():
    f = Finding(kind="impact", severity="high", title="改字段影响接口")
    assert f.detail == ""
    assert f.node_ids == []
    assert f.evidence_ids == []


# ---- 序列化往返 ----

def test_graph_node_roundtrip_with_enum_kind():
    n = GraphNode(id="p:backend_endpoint:/api/x", kind=NodeKind.BACKEND_ENDPOINT,
                  name="GET /api/x", project_id="demo", file="src/x.py",
                  line=12, language="python", meta={"http_method": "GET"})
    d = n.to_dict()
    # 枚举被序列化成裸字符串
    assert d["kind"] == "backend_endpoint"
    assert isinstance(d["kind"], str)
    back = GraphNode.from_dict(d)
    assert back == n


def test_graph_node_roundtrip_with_bare_string_kind():
    # 开放枚举:插件可用 plan 未覆盖的裸字符串 kind
    n = GraphNode(id="p:custom:1", kind="frontend_store_action",
                  name="fetchList", project_id="demo")
    back = GraphNode.from_dict(n.to_dict())
    assert back.kind == "frontend_store_action"
    assert back == n


def test_graph_edge_roundtrip():
    e = GraphEdge(source="comp:1", target="api:1", kind=EdgeKind.CALLS_API,
                  confidence=0.7, meta={"rule": "axios.get"})
    back = GraphEdge.from_dict(e.to_dict())
    assert back == e
    assert back.confidence == 0.7


def test_evidence_roundtrip():
    ev = Evidence(source="builtin.cross_link", detail="SQL: SELECT * FROM customer",
                  file="Mapper.xml", line=42, confidence=0.9)
    assert Evidence.from_dict(ev.to_dict()) == ev


def test_finding_roundtrip():
    f = Finding(kind="risk", severity="medium", title="未链接的 API 调用",
                detail="前端调用未匹配到后端 endpoint", node_ids=["api:1"],
                evidence_ids=["ev0"], meta={"k": "v"})
    assert Finding.from_dict(f.to_dict()) == f


# ---- AnalyzerResult ----

def test_analyzer_result_default_empty():
    r = AnalyzerResult()
    assert r.nodes == [] and r.edges == [] and r.evidences == [] and r.findings == []
    assert r.plugin is None


def test_analyzer_result_roundtrip():
    r = AnalyzerResult(
        nodes=[GraphNode(id="p:file:a.py", kind=NodeKind.FILE, name="a.py",
                         project_id="demo")],
        edges=[GraphEdge(source="p:file:a.py", target="p:file:b.py",
                         kind=EdgeKind.IMPORTS)],
        evidences=[Evidence(source="builtin.codegraph", detail="import b")],
        findings=[Finding(kind="info", severity="low", title="ok")],
        plugin="builtin.codegraph",
        plugin_version="0.1.0",
    )
    back = AnalyzerResult.from_dict(r.to_dict())
    assert back == r
    assert back.plugin == "builtin.codegraph"


def test_analyzer_result_merge():
    a = AnalyzerResult(nodes=[GraphNode(id="n1", kind=NodeKind.FILE, name="1",
                                        project_id="d")])
    b = AnalyzerResult(
        nodes=[GraphNode(id="n2", kind=NodeKind.FILE, name="2", project_id="d")],
        edges=[GraphEdge(source="n1", target="n2", kind=EdgeKind.CONTAINS)],
        findings=[Finding(kind="impact", severity="high", title="t")],
    )
    a.merge(b)
    assert len(a.nodes) == 2
    assert len(a.edges) == 1
    assert len(a.findings) == 1
