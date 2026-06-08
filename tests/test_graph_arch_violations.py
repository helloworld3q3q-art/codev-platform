"""A2-3 架构层查询 + 确定性违规检测(find_arch_role / list_layer_members / find_arch_violations)。

违规检测纯确定性: layer 软标签 × calls/imports 硬边 × 偏序规则(不调 LLM)。验证逆向依赖检出、
正向不误报、横切角色(util)不参与、MCP dispatch 与直读引擎一致。
"""
from __future__ import annotations

from codev_platform.graph import impact as I
from codev_platform.graph import mcp_server as gm
from codev_platform.graph.analyzers.architecture_layer import ArchLayerAnalyzer
from codev_platform.graph.analyzers.layer_labeler import FakeLayerLabeler
from codev_platform.graph.schema import (
    AnalyzerResult,
    EdgeKind,
    GraphEdge,
    GraphNode,
    NodeKind,
)
from codev_platform.graph.store import load_graph, open_store, upsert_result


def _file(path):
    return GraphNode(id=f"p:file:{path}", kind=NodeKind.FILE, name=path, project_id="p", file=path)


def _func(path, name):
    return GraphNode(id=f"p:backend_function:{name}", kind=NodeKind.BACKEND_FUNCTION,
                     name=name, project_id="p", file=path)


def _seed(conn, *, reverse: bool):
    """ctrl/repo file 各一函数 + calls 硬边; 软节点/边经 ArchLayerAnalyzer 真产(节点级 PLAYS_ROLE)。
    reverse=True: repo.save → ctrl.handle(逆向违规); False: ctrl.handle → repo.save(正向合法)。
    FakeLayerLabeler 按路径关键词标: web/controller/→controller, web/repository/→repository。"""
    fctrl, frepo = _file("web/controller/order_ctrl.py"), _file("web/repository/order_repo.py")
    fc, fr = _func("web/controller/order_ctrl.py", "handle"), _func("web/repository/order_repo.py", "save")
    src, tgt = (fr.id, fc.id) if reverse else (fc.id, fr.id)
    upsert_result(conn, "p", AnalyzerResult(
        nodes=[fctrl, frepo, fc, fr],
        edges=[GraphEdge(source=src, target=tgt, kind=EdgeKind.CALLS)]))
    m = load_graph(conn, "p")   # 用真 analyzer 产软(对齐生产: 节点级 PLAYS_ROLE 连该 file 每个节点)
    upsert_result(conn, "p", ArchLayerAnalyzer(FakeLayerLabeler()).analyze("p", m.nodes, m.edges))
    return fctrl, frepo


def test_find_arch_role(tmp_path):
    conn = open_store("p", path=tmp_path / "g.sqlite")
    try:
        _seed(conn, reverse=True)
        r = I.find_arch_role(conn, "p", "web/controller/order_ctrl.py")
        assert r["found"] and r["roles"] == ["controller"]
        assert I.find_arch_role(conn, "p", "ghost.py")["found"] is False
    finally:
        conn.close()


def test_list_layer_members(tmp_path):
    conn = open_store("p", path=tmp_path / "g.sqlite")
    try:
        _seed(conn, reverse=True)
        r = I.list_layer_members(conn, "p", "repository")
        assert r["found"]
        names = {m["name"] for m in r["members"]}
        assert "web/repository/order_repo.py" in names   # repository 层含该 file 节点(节点级: file+function)
        assert I.list_layer_members(conn, "p", "nonexist")["found"] is False
    finally:
        conn.close()


def test_violations_detect_reverse_dependency(tmp_path):
    conn = open_store("p", path=tmp_path / "g.sqlite")
    try:
        _seed(conn, reverse=True)   # repository → controller(逆向)
        r = I.find_arch_violations(conn, "p")
        assert r["count"] == 1
        v = r["violations"][0]
        assert v["fromRole"] == "repository" and v["toRole"] == "controller" and v["via"] == "calls"
    finally:
        conn.close()


def test_violations_no_false_positive_on_forward_dep(tmp_path):
    conn = open_store("p", path=tmp_path / "g.sqlite")
    try:
        _seed(conn, reverse=False)   # controller → repository(正向合法)
        assert I.find_arch_violations(conn, "p")["count"] == 0
    finally:
        conn.close()


def test_violations_via_mcp_dispatch_matches_engine(tmp_path):
    conn = open_store("p", path=tmp_path / "g.sqlite")
    try:
        _seed(conn, reverse=True)
        assert {"find_arch_role", "list_layer_members", "find_arch_violations"} <= set(gm._DISPATCH)
        # dispatch 与直读引擎一致(对齐双路径一致性精神)
        assert (gm.dispatch("find_arch_role", {"file": "web/controller/order_ctrl.py"}, conn, "p")
                == I.find_arch_role(conn, "p", "web/controller/order_ctrl.py"))
        assert gm.dispatch("find_arch_violations", {}, conn, "p")["count"] == 1
    finally:
        conn.close()


def test_violations_isolation_uses_soft_edges_only_for_roles(tmp_path):
    # 违规检测放开软边读 role, 但 build_impact_graph 默认(查依赖)仍不含软节点(护城河断言)。
    conn = open_store("p", path=tmp_path / "g.sqlite")
    try:
        _seed(conn, reverse=True)
        hard = I.build_impact_graph(conn, "p")   # include_soft=False(默认)
        assert not any(n.kind == NodeKind.ARCH_LAYER.value for n in hard.nodes.values())
    finally:
        conn.close()
