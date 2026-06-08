"""图谱结构审计 (Phase 3 MVP) 单测 —— 临时 store 种已知问题, 不碰真 data。"""
from __future__ import annotations

from codev_platform.graph.audit import audit_graph, render_markdown
from codev_platform.graph.schema import (
    AnalyzerResult,
    EdgeKind,
    GraphEdge,
    GraphNode,
    NodeKind,
)
from codev_platform.graph.store import open_store, upsert_result

PID = "t-audit"


def _node(nid: str, name: str, file: str = "a.py",
          kind: str = NodeKind.BACKEND_FUNCTION.value) -> GraphNode:
    return GraphNode(id=nid, kind=kind, name=name, project_id=PID, file=file)


def _seed(conn, nodes, edges):
    upsert_result(conn, PID, AnalyzerResult(
        plugin="t", plugin_version="0", nodes=nodes, edges=edges,
        evidences=[], findings=[]))
    conn.commit()


def test_audit_detects_dangling_dup_lowconf(tmp_path):
    conn = open_store(PID, path=tmp_path / "g.sqlite")
    nodes = [
        _node("f1", "foo"), _node("f2", "bar"),
        _node("d1", "dup", file="x.py"), _node("d2", "dup", file="x.py"),  # 同逻辑节点两个 id
    ]
    edges = [
        GraphEdge(source="f1", target="f2", kind=EdgeKind.CALLS.value, confidence=1.0),
        GraphEdge(source="f1", target="ghost", kind=EdgeKind.CALLS.value, confidence=1.0),  # 断链
        GraphEdge(source="f1", target="f2", kind=EdgeKind.READS_TABLE.value, confidence=0.4),  # 低置信硬边
    ]
    _seed(conn, nodes, edges)
    rep = audit_graph(conn, PID)
    conn.close()
    assert rep["errors"]["dangling_edges"]["count"] == 1
    assert rep["warnings"]["duplicate_nodes"]["count"] == 1
    assert rep["warnings"]["low_confidence_edges"]["count"] == 1
    assert rep["clean"] is False   # 断链 = error → 不 clean


def test_audit_clean_graph(tmp_path):
    conn = open_store(PID, path=tmp_path / "g.sqlite")
    _seed(conn, [_node("f1", "foo"), _node("f2", "bar")],
          [GraphEdge(source="f1", target="f2", kind=EdgeKind.CALLS.value, confidence=1.0)])
    rep = audit_graph(conn, PID)
    conn.close()
    assert rep["clean"] is True
    assert rep["errors"]["dangling_edges"]["count"] == 0
    assert rep["warnings"]["low_confidence_edges"]["count"] == 0


def test_audit_cross_project_leak(tmp_path):
    conn = open_store(PID, path=tmp_path / "g.sqlite")
    _seed(conn, [_node("f1", "foo")], [])
    # 原表直插一行别 project 的节点 (绕过 upsert 的 pid 强制), 模拟串台泄漏
    conn.execute(
        "INSERT INTO nodes (id, plugin, kind, name, project_id, file, line, language, meta_json) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
        ("x1", "t", "backend_function", "leak", "OTHER-PID", "a.py", None, None, None))
    conn.commit()
    rep = audit_graph(conn, PID)
    conn.close()
    assert rep["errors"]["cross_project_nodes"]["count"] == 1
    assert "OTHER-PID" in rep["errors"]["cross_project_nodes"]["foreign_project_ids"]
    assert rep["clean"] is False   # 串台 = error


def test_audit_orphan_soft_plugin(tmp_path):
    # 软节点应只来自规范 analyzer plugin(builtin.analyzers); 自名 plugin 残留 = 孤儿 error。
    # 复现 2026-06-08 arch_layer 重复根因。
    conn = open_store(PID, path=tmp_path / "g.sqlite")
    role = GraphNode(id=f"{PID}:arch_layer:service", kind=NodeKind.ARCH_LAYER.value,
                     name="service", project_id=PID)
    # 规范来源
    upsert_result(conn, PID, AnalyzerResult(plugin="builtin.analyzers", nodes=[role],
                                            edges=[], evidences=[], findings=[]))
    # 孤儿: 同软节点又被自名 plugin 写一份(plugin 漂移残留)
    upsert_result(conn, PID, AnalyzerResult(plugin="arch_layer", nodes=[role],
                                            edges=[], evidences=[], findings=[]))
    conn.commit()
    rep = audit_graph(conn, PID)
    conn.close()
    osp = rep["errors"]["orphan_soft_plugins"]
    assert osp["count"] == 1 and "arch_layer" in osp["plugins"]
    assert rep["clean"] is False   # 孤儿 plugin = error


def test_audit_no_orphan_when_only_canonical(tmp_path):
    conn = open_store(PID, path=tmp_path / "g.sqlite")
    role = GraphNode(id=f"{PID}:arch_layer:service", kind=NodeKind.ARCH_LAYER.value,
                     name="service", project_id=PID)
    upsert_result(conn, PID, AnalyzerResult(plugin="builtin.analyzers", nodes=[role],
                                            edges=[], evidences=[], findings=[]))
    conn.commit()
    rep = audit_graph(conn, PID)
    conn.close()
    assert rep["errors"]["orphan_soft_plugins"]["count"] == 0


def test_render_markdown_smoke(tmp_path):
    conn = open_store(PID, path=tmp_path / "g.sqlite")
    _seed(conn, [_node("f1", "foo")], [])
    rep = audit_graph(conn, PID)
    conn.close()
    md = render_markdown(rep)
    assert "graph audit" in md and "clean" in md
