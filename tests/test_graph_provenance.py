"""Phase 3 provenance 单测 —— 边来源可追溯 (schema 盖戳 + ingest 落戳 + impact 展示 + audit 计数)。

覆盖四块:
1. schema.stamp_provenance / edge_provenance: 写读 provenance 子袋 + 保留既有 meta + 序列化往返。
2. ingest 盖戳: frontend_bridge(src=bridge) 纯函数 + resolver prov_source 声明(codegraph=ast/fastapi=regex)。
3. impact 展示: 影响 brief 带 src + confidence + certain; 确定/候选拆分按置信阈值。
4. audit: no-provenance 硬边计数(软边不计)。
纯内存 / 临时 store, 不碰真 data, 不调 LLM。
"""
from __future__ import annotations

from codev_platform.graph.audit import audit_graph
from codev_platform.graph.impact import find_impact, generate_impact_report
from codev_platform.graph.ingest import (
    FRONTEND_BRIDGE_PLUGIN,
    build_frontend_bridge_edges,
)
from codev_platform.graph.schema import (
    PROV_KEY,
    AnalyzerResult,
    EdgeKind,
    GraphEdge,
    GraphNode,
    NodeKind,
    ProvSource,
    edge_provenance,
    stamp_provenance,
    stamp_unprovenanced,
)
from codev_platform.graph.store import open_store, upsert_result

PID = "t-prov"


# ---------------------------------------------------------------- 1. schema 盖戳/读取

def test_stamp_provenance_writes_subbag():
    e = GraphEdge(source="a", target="b", kind=EdgeKind.CALLS.value)
    ret = stamp_provenance(e, ProvSource.AST, parser="codegraph", pv="1")
    assert ret is e  # 原地写, 返回同一对象
    assert e.meta[PROV_KEY] == {"src": "ast", "parser": "codegraph", "pv": "1"}
    assert edge_provenance(e.meta) == {"src": "ast", "parser": "codegraph", "pv": "1"}


def test_stamp_provenance_preserves_existing_meta():
    e = GraphEdge(source="a", target="b", kind=EdgeKind.CALLS.value,
                  meta={"resolver": "fastapi", "via_handler": "h"})
    stamp_provenance(e, "regex", parser="fastapi")
    assert e.meta["resolver"] == "fastapi"  # 既有字段不丢
    assert e.meta["via_handler"] == "h"
    assert edge_provenance(e.meta)["src"] == "regex"
    assert "pv" not in e.meta[PROV_KEY]  # pv 省略时不写


def test_provenance_survives_serialization_roundtrip():
    e = GraphEdge(source="a", target="b", kind=EdgeKind.CONTAINS.value, confidence=1.0)
    stamp_provenance(e, ProvSource.BRIDGE, parser="builtin.frontend_bridge")
    back = GraphEdge.from_dict(e.to_dict())
    assert back.meta == e.meta
    assert edge_provenance(back.meta)["src"] == "bridge"


def test_edge_provenance_empty_when_unstamped():
    assert edge_provenance(None) == {}
    assert edge_provenance({}) == {}
    assert edge_provenance({"resolver": "x"}) == {}  # 无 prov 子袋


def test_stamp_unprovenanced_skips_already_stamped():
    a = GraphEdge(source="a", target="b", kind=EdgeKind.CALLS.value)  # 未盖
    b = stamp_provenance(
        GraphEdge(source="c", target="d", kind=EdgeKind.CALLS.value), ProvSource.AST)  # 已盖
    stamp_unprovenanced([a, b], ProvSource.REGEX, parser="p")
    assert edge_provenance(a.meta) == {"src": "regex", "parser": "p"}  # 补默认戳
    assert edge_provenance(b.meta)["src"] == "ast"  # 既有精确戳保留(局部覆盖优先)


# ---------------------------------------------------------------- 2. ingest 盖戳

def test_frontend_bridge_edges_stamped_bridge():
    nodes = [
        GraphNode(id="m1", kind=NodeKind.FRONTEND_MODULE.value, name="UserPage",
                  project_id=PID, file="src/UserPage.tsx"),
        GraphNode(id="c1", kind=NodeKind.FRONTEND_API_CALL.value, name="POST /users",
                  project_id=PID, file="src/UserPage.tsx"),
    ]
    edges = build_frontend_bridge_edges(nodes)
    assert len(edges) == 1
    prov = edge_provenance(edges[0].meta)
    assert prov["src"] == "bridge"
    assert prov["parser"] == FRONTEND_BRIDGE_PLUGIN


def test_call_resolvers_declare_prov_source():
    # codegraph = ast 精确解析; fastapi = regex 名称 BFS。
    from codev_platform.graph.call_resolvers.codegraph import CodegraphCallResolver
    from codev_platform.graph.call_resolvers.fastapi import FastApiCallResolver
    assert CodegraphCallResolver.prov_source == ProvSource.AST.value
    assert FastApiCallResolver.prov_source == ProvSource.REGEX.value


def test_edge_producing_plugins_declare_prov_source():
    # 产边插件须声明 prov_source(否则边无来源)。三者均正则解析 → regex。
    from codev_platform.plugins.builtin.frontend_react import FrontendReactPlugin
    from codev_platform.plugins.builtin.sql import SqlPlugin
    from codev_platform.plugins.builtin.vue import VuePlugin
    assert SqlPlugin.prov_source == ProvSource.REGEX.value
    assert FrontendReactPlugin.prov_source == ProvSource.REGEX.value
    assert VuePlugin.prov_source == ProvSource.REGEX.value


# ---- executor 边界统一盖戳(plugin 实例 ↔ result 唯一交汇点)----

class _FakePlugin:
    """最小插件: 声明 prov_source, analyze 返回带边的 result(测 executor 归因)。"""
    name = "fake.plugin"
    version = "1.0.0"
    prov_source = ProvSource.AST.value

    def __init__(self, edges):
        self._edges = edges

    def detect(self, repo):
        return True

    def analyze(self, repo, project_id):
        return AnalyzerResult(edges=self._edges)


def test_executor_stamps_plugin_edges_from_prov_source(tmp_path):
    from codev_platform.plugins.executor import run_plugin
    e = GraphEdge(source="a", target="b", kind=EdgeKind.READS_TABLE.value)
    res = run_plugin(_FakePlugin([e]), tmp_path, PID)
    assert res.ok
    prov = edge_provenance(res.result.edges[0].meta)
    assert prov == {"src": "ast", "parser": "fake.plugin"}  # parser = 插件名


def test_executor_preserves_pre_stamped_plugin_edge(tmp_path):
    # 插件自盖更精确的戳 → executor 不覆盖(局部覆盖优先)。
    from codev_platform.plugins.executor import run_plugin
    e = stamp_provenance(
        GraphEdge(source="a", target="b", kind=EdgeKind.CALLS.value),
        ProvSource.FRAMEWORK, parser="self")
    res = run_plugin(_FakePlugin([e]), tmp_path, PID)
    assert edge_provenance(res.result.edges[0].meta)["src"] == "framework"


def test_executor_skips_stamping_when_no_prov_source(tmp_path):
    # 未声明 prov_source 的插件 → 边不盖戳(不臆测来源, 留 audit no-provenance 标出)。
    from codev_platform.plugins.executor import run_plugin
    plugin = _FakePlugin([GraphEdge(source="a", target="b", kind=EdgeKind.CALLS.value)])
    plugin.prov_source = None
    res = run_plugin(plugin, tmp_path, PID)
    assert edge_provenance(res.result.edges[0].meta) == {}


# ---------------------------------------------------------------- 3. impact 展示 src/conf/certain

_FE = "p:frontend_api_call:src/U.tsx:POST:/users"
_EP = "p:backend_endpoint:POST:/users"
_FN = "p:backend_function:repo.py:save_user"
_TB = "p:db_table:users"


def _seed_chain(conn):
    """FE --calls_api(framework,1.0)--> EP --calls(regex,0.65)--> FN --writes_table(无戳,1.0)--> TB。"""
    nodes = [
        GraphNode(id=_FE, kind=NodeKind.FRONTEND_API_CALL.value, name="POST /users",
                  project_id=PID, file="src/U.tsx"),
        GraphNode(id=_EP, kind=NodeKind.BACKEND_ENDPOINT.value, name="create_user",
                  project_id=PID, file="api.py"),
        GraphNode(id=_FN, kind=NodeKind.BACKEND_FUNCTION.value, name="save_user",
                  project_id=PID, file="repo.py"),
        GraphNode(id=_TB, kind=NodeKind.DB_TABLE.value, name="users", project_id=PID),
    ]
    e_api = stamp_provenance(
        GraphEdge(source=_FE, target=_EP, kind=EdgeKind.CALLS_API.value, confidence=1.0),
        ProvSource.FRAMEWORK, parser="builtin.linker")
    e_calls = stamp_provenance(
        GraphEdge(source=_EP, target=_FN, kind=EdgeKind.CALLS.value, confidence=0.65),
        ProvSource.REGEX, parser="fastapi")
    e_table = GraphEdge(source=_FN, target=_TB, kind=EdgeKind.WRITES_TABLE.value,
                        confidence=1.0)  # 未盖戳(模拟插件直产边)
    upsert_result(conn, PID, AnalyzerResult(nodes=nodes, edges=[e_api, e_calls, e_table],
                                            plugin="test"))


def test_impact_brief_carries_src_and_confidence(tmp_path):
    conn = open_store(PID, path=tmp_path / "g.sqlite")
    _seed_chain(conn)
    r = find_impact(conn, PID, _TB)  # 反向: 谁依赖 users 表
    conn.close()
    assert r["found"]
    briefs = {b["id"]: b for layer in r["impact"]["byLayer"].values() for b in layer}
    # EP 经 calls(regex,0.65) 到达 → 候选
    assert briefs[_EP]["src"] == "regex"
    assert briefs[_EP]["confidence"] == 0.65
    assert briefs[_EP]["certain"] is False
    # FE 经 calls_api(framework,1.0) → 确定
    assert briefs[_FE]["src"] == "framework"
    assert briefs[_FE]["certain"] is True
    # FN 经 writes_table(无戳,1.0) → 确定, 无 src 键
    assert briefs[_FN]["certain"] is True
    assert "src" not in briefs[_FN]


def test_impact_certain_candidate_split(tmp_path):
    conn = open_store(PID, path=tmp_path / "g.sqlite")
    _seed_chain(conn)
    r = find_impact(conn, PID, _TB)
    conn.close()
    imp = r["impact"]
    assert imp["total"] == 3
    assert imp["certainCount"] == 2   # FN + FE
    assert imp["candidateCount"] == 1  # EP(regex 0.65)


def test_impact_report_summary_mentions_split(tmp_path):
    conn = open_store(PID, path=tmp_path / "g.sqlite")
    _seed_chain(conn)
    rep = generate_impact_report(conn, PID, _TB)
    conn.close()
    assert rep["certainCount"] == 2 and rep["candidateCount"] == 1
    assert "确定依赖 2" in rep["summary"]
    assert "候选 1" in rep["summary"]


# ---------------------------------------------------------------- 4. audit no-provenance 计数

def test_audit_counts_no_provenance_hard_edges(tmp_path):
    conn = open_store(PID, path=tmp_path / "g.sqlite")
    nodes = [
        GraphNode(id="f1", kind=NodeKind.BACKEND_FUNCTION.value, name="f1",
                  project_id=PID, file="a.py"),
        GraphNode(id="f2", kind=NodeKind.BACKEND_FUNCTION.value, name="f2",
                  project_id=PID, file="a.py"),
        GraphNode(id="dom", kind=NodeKind.BUSINESS_DOMAIN.value, name="用户域",
                  project_id=PID),
    ]
    stamped = stamp_provenance(
        GraphEdge(source="f1", target="f2", kind=EdgeKind.CALLS.value, confidence=1.0),
        ProvSource.AST, parser="codegraph")
    unstamped = GraphEdge(source="f1", target="f2", kind=EdgeKind.READS_TABLE.value,
                          confidence=1.0)  # 硬边未盖戳 → 计入
    soft = GraphEdge(source="f1", target="dom", kind=EdgeKind.BELONGS_TO_DOMAIN.value,
                     confidence=0.8)  # 软边未盖戳 → 不计
    upsert_result(conn, PID, AnalyzerResult(nodes=nodes, edges=[stamped, unstamped, soft],
                                            plugin="test"))
    rep = audit_graph(conn, PID)
    conn.close()
    npv = rep["warnings"]["no_provenance_edges"]
    assert npv["count"] == 1
    assert npv["by_kind"] == {"reads_table": 1}  # 软边 belongs_to_domain 不计
