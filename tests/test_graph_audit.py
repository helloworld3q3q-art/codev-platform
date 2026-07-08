"""图谱结构审计 (Phase 3 MVP) 单测 —— 临时 store 种已知问题, 不碰真 data。"""
from __future__ import annotations

from codev_platform.graph.audit import audit_all_stores, audit_graph, render_markdown
from codev_platform.graph.schema import (
    AnalyzerResult,
    EdgeKind,
    GraphEdge,
    GraphNode,
    NodeKind,
)
from codev_platform.graph.store import open_store

PID = "t-audit"


def _node(nid: str, name: str, file: str = "a.py",
          kind: str = NodeKind.BACKEND_FUNCTION.value) -> GraphNode:
    return GraphNode(id=nid, kind=kind, name=name, project_id=PID, file=file)


def _seed(conn, nodes, edges):
    conn.upsert_result(PID, AnalyzerResult(
        plugin="t", plugin_version="0", nodes=nodes, edges=edges,
        evidences=[], findings=[]))   # upsert_result 内部已 commit


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


def test_audit_reports_api_link_coverage(tmp_path):
    conn = open_store(PID, path=tmp_path / "g.sqlite")
    frontend_linked = GraphNode(
        id=f"{PID}:frontend_api_call:src/api.ts:createOrder",
        kind=NodeKind.FRONTEND_API_CALL.value,
        name="createOrder",
        project_id=PID,
        file="src/api.ts",
        meta={"url": "/orders", "http_method": "POST"},
    )
    frontend_unlinked = GraphNode(
        id=f"{PID}:frontend_api_call:src/api.ts:deleteOrder",
        kind=NodeKind.FRONTEND_API_CALL.value,
        name="deleteOrder",
        project_id=PID,
        file="src/api.ts",
        meta={"url": "/orders/delete", "http_method": "POST"},
    )
    backend = GraphNode(
        id=f"{PID}:backend_endpoint:POST:/orders",
        kind=NodeKind.BACKEND_ENDPOINT.value,
        name="createOrder",
        project_id=PID,
        file="OrderController.java",
        meta={"url": "/orders", "http_method": "POST"},
    )
    _seed(conn, [frontend_linked, frontend_unlinked, backend],
          [GraphEdge(source=frontend_linked.id, target=backend.id, kind=EdgeKind.CALLS_API.value)])
    rep = audit_graph(conn, PID)
    conn.close()

    api = rep["warnings"]["api_link_coverage"]
    assert rep["clean"] is True
    assert api["status"] == "partial_frontend_linkage"
    assert api["frontend_api_calls"] == 2
    assert api["backend_endpoints"] == 1
    assert api["calls_api_edges"] == 1
    assert api["linked_frontend_api_calls"] == 1
    assert api["unlinked_frontend_api_calls"] == 1
    assert api["frontend_link_ratio"] == 0.5
    assert api["unlinked_samples"][0]["url"] == "/orders/delete"


def test_audit_reports_frontend_backend_unlinked(tmp_path):
    conn = open_store(PID, path=tmp_path / "g.sqlite")
    frontend = GraphNode(
        id=f"{PID}:frontend_api_call:src/api.ts:getUser",
        kind=NodeKind.FRONTEND_API_CALL.value,
        name="getUser",
        project_id=PID,
        file="src/api.ts",
        meta={"url": "/users", "http_method": "GET"},
    )
    backend = GraphNode(
        id=f"{PID}:backend_endpoint:POST:/orders",
        kind=NodeKind.BACKEND_ENDPOINT.value,
        name="createOrder",
        project_id=PID,
        file="OrderController.java",
        meta={"url": "/orders", "http_method": "POST"},
    )
    _seed(conn, [frontend, backend], [])
    rep = audit_graph(conn, PID)
    conn.close()

    api = rep["warnings"]["api_link_coverage"]
    assert rep["clean"] is True
    assert api["status"] == "frontend_backend_unlinked"
    assert api["count"] == 1
    assert api["calls_api_edges"] == 0
    assert "没有 calls_api" in api["diagnosis"]


def test_audit_ignores_malformed_calls_api_for_coverage(tmp_path):
    conn = open_store(PID, path=tmp_path / "g.sqlite")
    frontend = GraphNode(
        id=f"{PID}:frontend_api_call:src/api.ts:getUser",
        kind=NodeKind.FRONTEND_API_CALL.value,
        name="getUser",
        project_id=PID,
        file="src/api.ts",
        meta={"url": "/users", "http_method": "GET"},
    )
    backend = GraphNode(
        id=f"{PID}:backend_endpoint:GET:/users",
        kind=NodeKind.BACKEND_ENDPOINT.value,
        name="getUser",
        project_id=PID,
        file="UserController.java",
        meta={"url": "/users", "http_method": "GET"},
    )
    wrong_target = _node("svc", "UserService")
    _seed(conn, [frontend, backend, wrong_target],
          [GraphEdge(source=frontend.id, target=wrong_target.id, kind=EdgeKind.CALLS_API.value)])
    rep = audit_graph(conn, PID)
    conn.close()

    api = rep["warnings"]["api_link_coverage"]
    assert rep["clean"] is True
    assert api["status"] == "frontend_backend_unlinked"
    assert api["calls_api_edges"] == 0
    assert api["raw_calls_api_edges"] == 1
    assert api["invalid_calls_api_edges"] == 1
    assert api["linked_frontend_api_calls"] == 0
    assert api["unlinked_frontend_api_calls"] == 1
    assert api["invalid_calls_api_samples"][0]["target"] == wrong_target.id


def test_audit_cross_project_leak(tmp_path):
    conn = open_store(PID, path=tmp_path / "g.sqlite")
    _seed(conn, [_node("f1", "foo")], [])
    # 原表直插一行别 project 的节点 (绕过 upsert 的 pid 强制), 模拟串台泄漏。GraphStore 不暴露
    # 底层 conn(防 recouple), 对抗 fixture 走独立 sqlite3 直连同文件注入。
    import sqlite3
    raw = sqlite3.connect(tmp_path / "g.sqlite")
    raw.execute(
        "INSERT INTO nodes (id, plugin, kind, name, project_id, file, line, language, meta_json) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
        ("x1", "t", "backend_function", "leak", "OTHER-PID", "a.py", None, None, None))
    raw.commit()
    raw.close()
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
    conn.upsert_result(PID, AnalyzerResult(plugin="builtin.analyzers", nodes=[role],
                                            edges=[], evidences=[], findings=[]))
    # 孤儿: 同软节点又被自名 plugin 写一份(plugin 漂移残留)
    conn.upsert_result(PID, AnalyzerResult(plugin="arch_layer", nodes=[role],
                                            edges=[], evidences=[], findings=[]))
    rep = audit_graph(conn, PID)
    conn.close()
    osp = rep["errors"]["orphan_soft_plugins"]
    assert osp["count"] == 1 and "arch_layer" in osp["plugins"]
    assert rep["clean"] is False   # 孤儿 plugin = error


def test_audit_no_orphan_when_only_canonical(tmp_path):
    conn = open_store(PID, path=tmp_path / "g.sqlite")
    role = GraphNode(id=f"{PID}:arch_layer:service", kind=NodeKind.ARCH_LAYER.value,
                     name="service", project_id=PID)
    conn.upsert_result(PID, AnalyzerResult(plugin="builtin.analyzers", nodes=[role],
                                            edges=[], evidences=[], findings=[]))
    rep = audit_graph(conn, PID)
    conn.close()
    assert rep["errors"]["orphan_soft_plugins"]["count"] == 0


def test_audit_all_stores_gate(tmp_path):
    # 门禁聚合: 多 store 目录, 一个 clean 一个有断链 → total_errors>0。
    gs = tmp_path / "graph_store"
    gs.mkdir()
    # p1: clean
    c1 = open_store("p1", path=gs / "p1.sqlite")
    c1.upsert_result("p1", AnalyzerResult(
        plugin="t", plugin_version="0",
        nodes=[GraphNode(id="a", kind=NodeKind.BACKEND_FUNCTION.value, name="a",
                         project_id="p1", file="a.py")],
        edges=[], evidences=[], findings=[]))
    c1.close()
    # p2: 断链(边指向不存在节点)
    c2 = open_store("p2", path=gs / "p2.sqlite")
    c2.upsert_result("p2", AnalyzerResult(
        plugin="t", plugin_version="0",
        nodes=[GraphNode(id="x", kind=NodeKind.BACKEND_FUNCTION.value, name="x",
                         project_id="p2", file="x.py")],
        edges=[GraphEdge(source="x", target="ghost", kind=EdgeKind.CALLS.value)],
        evidences=[], findings=[]))
    c2.close()

    agg = audit_all_stores(gs)
    assert agg["projects"] == ["p1", "p2"]
    assert agg["reports"]["p1"]["clean"] is True
    assert agg["reports"]["p2"]["clean"] is False
    assert agg["total_errors"] >= 1   # p2 的断链


def test_audit_all_stores_empty_dir_skips(tmp_path):
    # 无 store 目录 → 空 + 0 error(门禁优雅跳过, 不阻断 push)。审计不该建目录(纯只读)。
    nope = tmp_path / "nope"
    agg = audit_all_stores(nope)
    assert agg["projects"] == [] and agg["total_errors"] == 0
    assert not nope.exists()   # #10: 只读门禁不创建目录


def test_audit_all_stores_readonly_no_mutation(tmp_path):
    # #10: audit_all_stores 用 read-only 连接, 不跑迁移/DDL → store 文件字节不变。
    import hashlib

    gs = tmp_path / "graph_store"
    gs.mkdir()
    c = open_store("p1", path=gs / "p1.sqlite")
    c.upsert_result("p1", AnalyzerResult(
        plugin="t", plugin_version="0",
        nodes=[GraphNode(id="a", kind=NodeKind.BACKEND_FUNCTION.value, name="a",
                         project_id="p1", file="a.py")],
        edges=[], evidences=[], findings=[]))
    c.close()
    db = gs / "p1.sqlite"
    before = hashlib.sha256(db.read_bytes()).hexdigest()
    agg = audit_all_stores(gs)
    after = hashlib.sha256(db.read_bytes()).hexdigest()
    assert agg["reports"]["p1"]["clean"] is True
    assert before == after   # 只读审计不改 db 内容(无迁移 / 无 DDL)


def test_audit_all_stores_old_schema_records_error(tmp_path):
    # #10: 旧 schema(edges 缺 project_id 列)read-only 读不动 → 记 audit_error, 不崩门禁。
    import sqlite3

    gs = tmp_path / "graph_store"
    gs.mkdir()
    conn = sqlite3.connect(gs / "old.sqlite")
    conn.executescript(
        "CREATE TABLE nodes (id TEXT, plugin TEXT, kind TEXT, name TEXT, "
        "project_id TEXT, file TEXT, line INTEGER, language TEXT, meta_json TEXT, "
        "PRIMARY KEY (id, plugin));"
        # 旧 edges: 无 project_id 列 → load_graph 的 WHERE project_id 会抛
        "CREATE TABLE edges (plugin TEXT, source TEXT, target TEXT, kind TEXT, "
        "confidence REAL, meta_json TEXT, PRIMARY KEY (plugin, source, target, kind));"
    )
    conn.execute(
        "INSERT INTO nodes VALUES ('a','t','backend_function','a','old','a.py',NULL,NULL,NULL)")
    conn.commit()
    conn.close()

    agg = audit_all_stores(gs)   # 不抛
    rep = agg["reports"]["old"]
    assert rep.get("audit_error")          # 记了原因
    assert rep["clean"] is False
    assert rep["error_count"] == 1
    assert agg["total_errors"] >= 1
    # render_markdown 能渲染 audit_error 报告(不 KeyError)
    md = render_markdown(rep)
    assert "无法审计" in md


def test_render_markdown_smoke(tmp_path):
    conn = open_store(PID, path=tmp_path / "g.sqlite")
    _seed(conn, [_node("f1", "foo")], [])
    rep = audit_graph(conn, PID)
    conn.close()
    md = render_markdown(rep)
    assert "graph audit" in md and "clean" in md
    assert "frontend API link coverage" in md


# ---- 后端分流 + 孤儿 reconcile(① 修门禁在 PG 后端扫空 / ② store 外孤儿检测)----

class _FakeSharedStore:
    """模拟共享后端(pg): list_project_ids 枚举全部 pid, load_graph 返空(审计为 clean)。"""
    def __init__(self, pids):
        self._pids = pids
    def list_project_ids(self):
        return self._pids
    def load_graph(self, project_id, *, plugin=None):
        return AnalyzerResult(plugin="t", nodes=[], edges=[], evidences=[], findings=[])
    def audit_scan(self, project_id):
        return {"foreign_project_ids": {"nodes": [], "edges": []},
                "soft_plugins": {"nodes": [], "edges": []}}
    def close(self):
        pass


def test_audit_all_stores_pg_dispatch_enumerates_via_list_project_ids(monkeypatch):
    # pg 后端 + 无显式 dir → 不再 glob sqlite(会扫空), 而是单 store list_project_ids() 枚举共享库。
    import codev_platform.graph.audit as A
    monkeypatch.setattr(A, "_graph_backend", lambda *a, **k: "pg")
    monkeypatch.setattr(A, "open_store", lambda *a, **k: _FakeSharedStore(["p1", "p2"]))
    agg = A.audit_all_stores()
    assert agg["projects"] == ["p1", "p2"]          # 枚举到两个 project(旧版会是 [])
    assert set(agg["reports"]) == {"p1", "p2"}
    assert agg["total_errors"] == 0


def test_audit_all_stores_pg_unreachable_failsoft(monkeypatch):
    # pg 连不上 / 缺 dsn → open_store 抛 → fail-soft 空报告, 不崩门禁(同 sqlite 无 store 跳过)。
    import codev_platform.graph.audit as A
    def _boom(*a, **k):
        raise ValueError("graph.store_backend=pg 但未配 memory.pg_dsn")
    monkeypatch.setattr(A, "_graph_backend", lambda *a, **k: "pg")
    monkeypatch.setattr(A, "open_store", _boom)
    assert A.audit_all_stores() == {"projects": [], "reports": {}, "total_errors": 0}


def test_audit_all_stores_explicit_dir_still_globs_sqlite(tmp_path):
    # 显式 dir(测试/显式路径)→ 仍按文件 glob, 与后端配置无关(回归: 现有调用方/测试零变化)。
    conn = open_store(PID, path=tmp_path / f"{PID}.sqlite")
    _seed(conn, [_node("f1", "foo")], [])
    conn.close()
    agg = audit_all_stores(tmp_path)
    assert PID in agg["projects"] and agg["total_errors"] == 0


def test_reconcile_orphan_pids():
    from codev_platform.graph.audit import reconcile_orphan_pids
    assert reconcile_orphan_pids(["a", "b", "x"], {"a", "b"}) == {"orphan_pids": ["x"], "count": 1}
    assert reconcile_orphan_pids(["a"], {"a", "b"}) == {"orphan_pids": [], "count": 0}  # known 超集
    assert reconcile_orphan_pids([], {"a"}) == {"orphan_pids": [], "count": 0}


def test_audit_detects_duplicate_edges(tmp_path):
    # 同 (source,target,kind) 被两 plugin 各产一份(退役 codegraph_bridge 残留 vs call_resolvers)
    # → duplicate_edges 冲突 warning。
    conn = open_store(PID, path=tmp_path / "g.sqlite")
    e = GraphEdge(source="f1", target="f2", kind=EdgeKind.CALLS.value, confidence=0.7)
    conn.upsert_result(PID, AnalyzerResult(nodes=[_node("f1", "a"), _node("f2", "b")],
                                            edges=[e], plugin="builtin.call_resolvers"))
    conn.upsert_result(PID, AnalyzerResult(edges=[e], plugin="builtin.codegraph_bridge"))
    rep = audit_graph(conn, PID)
    conn.close()
    dup = rep["warnings"]["duplicate_edges"]
    assert dup["count"] == 1 and dup["by_kind"] == {"calls": 1}
    assert dup["samples"][0]["copies"] == 2
