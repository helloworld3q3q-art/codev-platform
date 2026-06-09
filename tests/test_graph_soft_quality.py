"""软标签质量诊断单测(graph/soft_quality.py)—— 临时 store 种已知分布, 验退化信号。"""
from __future__ import annotations

from codev_platform.graph.schema import (
    AnalyzerResult,
    EdgeKind,
    GraphEdge,
    GraphNode,
    NodeKind,
)
from codev_platform.graph.soft_quality import assess_soft_labels, render_markdown
from codev_platform.graph.store import open_store, upsert_result

PID = "t-softq"


def _ep(i: int) -> GraphNode:
    return GraphNode(id=f"e{i}", kind=NodeKind.BACKEND_ENDPOINT.value,
                     name=f"GET /api/{i}", project_id=PID, file=f"api{i}.py")


def _table(i: int) -> GraphNode:
    return GraphNode(id=f"t{i}", kind=NodeKind.DB_TABLE.value, name=f"tbl{i}", project_id=PID)


def _domain(name: str) -> GraphNode:
    return GraphNode(id=f"{PID}:business_domain:{name}", kind=NodeKind.BUSINESS_DOMAIN.value,
                     name=name, project_id=PID, meta={"confidence": 0.8, "derived_by": "test"})


def _layer(name: str) -> GraphNode:
    return GraphNode(id=f"{PID}:arch_layer:{name}", kind=NodeKind.ARCH_LAYER.value,
                     name=name, project_id=PID, meta={"confidence": 0.8, "derived_by": "test"})


def _belongs(hard_id: str, domain: str) -> GraphEdge:
    return GraphEdge(source=hard_id, target=f"{PID}:business_domain:{domain}",
                     kind=EdgeKind.BELONGS_TO_DOMAIN.value, confidence=0.8)


def _plays(hard_id: str, role: str) -> GraphEdge:
    return GraphEdge(source=hard_id, target=f"{PID}:arch_layer:{role}",
                     kind=EdgeKind.PLAYS_ROLE.value, confidence=0.8)


def _seed(tmp_path, nodes, edges):
    conn = open_store(PID, path=tmp_path / "g.sqlite")
    upsert_result(conn, PID, AnalyzerResult(nodes=nodes, edges=edges, plugin="test"))
    return conn


def test_balanced_domains_healthy(tmp_path):
    # 4 endpoint 均分两域(各 2)→ 无巨型, 覆盖 100% → healthy。
    nodes = [_ep(1), _ep(2), _ep(3), _ep(4), _domain("订单"), _domain("行情")]
    edges = [_belongs("e1", "订单"), _belongs("e2", "订单"),
             _belongs("e3", "行情"), _belongs("e4", "行情")]
    conn = _seed(tmp_path, nodes, edges)
    rep = assess_soft_labels(conn, PID)
    conn.close()
    assert rep["healthy"] is True and rep["flags"] == []
    assert rep["domains"]["coverage"] == 1.0
    assert rep["domains"]["giant"] == []


def test_giant_cluster_flagged(tmp_path):
    # 一域吃 3/4 成员(75% > 50%)→ 巨型退化信号。
    nodes = [_ep(1), _ep(2), _ep(3), _table(1), _domain("巨型"), _domain("小")]
    edges = [_belongs("e1", "巨型"), _belongs("e2", "巨型"), _belongs("e3", "巨型"),
             _belongs("t1", "小")]
    conn = _seed(tmp_path, nodes, edges)
    rep = assess_soft_labels(conn, PID)
    conn.close()
    assert rep["healthy"] is False
    assert any("巨型 cluster" in f for f in rep["flags"])
    assert rep["domains"]["giant"][0]["name"] == "巨型"
    assert rep["domains"]["giant"][0]["share"] == 0.75


def test_low_coverage_flagged(tmp_path):
    # 4 个可标注 endpoint 只标了 1 个 → 覆盖 25% < 30%。
    nodes = [_ep(1), _ep(2), _ep(3), _ep(4), _domain("唯一")]
    edges = [_belongs("e1", "唯一")]
    conn = _seed(tmp_path, nodes, edges)
    rep = assess_soft_labels(conn, PID)
    conn.close()
    assert rep["domains"]["coverage"] == 0.25
    assert rep["domains"]["low_coverage"] is True
    assert any("覆盖率" in f for f in rep["flags"])


def test_singleton_majority_flagged(tmp_path):
    # 4 域各 1 成员(全单成员)→ 噪声/弱标签偏多。覆盖 100% 不触发 low_coverage。
    nodes = [_ep(i) for i in range(1, 5)] + [_domain(f"d{i}") for i in range(1, 5)]
    edges = [_belongs(f"e{i}", f"d{i}") for i in range(1, 5)]
    conn = _seed(tmp_path, nodes, edges)
    rep = assess_soft_labels(conn, PID)
    conn.close()
    assert rep["domains"]["coverage"] == 1.0
    assert len(rep["domains"]["singletons"]) == 4
    assert any("单成员" in f for f in rep["flags"])


def test_no_soft_layer_is_healthy(tmp_path):
    # 没跑 analyzer(无软层)→ 不报假阳性。
    conn = _seed(tmp_path, [_ep(1), _ep(2)], [])
    rep = assess_soft_labels(conn, PID)
    conn.close()
    assert rep["healthy"] is True and rep["flags"] == []
    assert rep["domains"]["soft_nodes"] == 0
    # 2 eligible, 0 labeled → coverage 0.0, 但软层为空 → low_coverage 不报(非退化)。
    assert rep["domains"]["coverage"] == 0.0
    assert rep["domains"]["low_coverage"] is False


def test_arch_layer_axis_assessed(tmp_path):
    # 架构层轴独立评估(与业务域同构): 一层吃多数 → 巨型。
    nodes = ([_ep(1), _ep(2), _ep(3)]
             + [_layer("service"), _layer("controller")])
    edges = [_plays("e1", "service"), _plays("e2", "service"), _plays("e3", "controller")]
    conn = _seed(tmp_path, nodes, edges)
    rep = assess_soft_labels(conn, PID)
    conn.close()
    # service 2/3 = 67% > 50% → 巨型
    assert rep["layers"]["giant"][0]["name"] == "service"
    assert any("架构层" in f and "巨型" in f for f in rep["flags"])


def test_giant_not_triggered_by_method_density(tmp_path):
    # 回归(2026-06-09 四轮取证): 方法/列密集文件(Mapper 多方法 / 宽表多列)在**节点口径**下
    # 会把一个角色占比虚高误报 giant; **文件口径**不虚高。
    # repository: 20 方法挤在 4 个 Mapper 文件(5/文件); service/controller: 各 4 方法/4 文件。
    # 文件均衡(各 4/12=33%)→ 不报 giant; 但节点口径 repository 20/28=71% 会误报。
    nodes: list[GraphNode] = [_layer("repository"), _layer("service"), _layer("controller")]
    edges = []
    for role, files, per in (("repository", 4, 5), ("service", 4, 1), ("controller", 4, 1)):
        for fi in range(files):
            for mi in range(per):
                nid = f"{role}_{fi}_{mi}"
                nodes.append(GraphNode(id=nid, kind=NodeKind.BACKEND_FUNCTION.value,
                                       name=nid, project_id=PID, file=f"{role}{fi}.java"))
                edges.append(_plays(nid, role))
    conn = _seed(tmp_path, nodes, edges)
    rep = assess_soft_labels(conn, PID)
    conn.close()
    repo = next(d for d in rep["layers"]["distribution"] if d["name"] == "repository")
    assert repo["members"] == 20 and repo["files"] == 4   # 20 节点但仅 4 文件
    assert repo["share"] < 0.5                              # 文件口径 33%(节点口径会 71% 误报)
    assert "repository" not in [g["name"] for g in rep["layers"]["giant"]]
    assert rep["layers"]["giant"] == []                    # 文件均衡 → 全不报
    assert rep["healthy"] is True


def test_render_markdown_smoke(tmp_path):
    nodes = [_ep(1), _ep(2), _domain("订单")]
    edges = [_belongs("e1", "订单"), _belongs("e2", "订单")]
    conn = _seed(tmp_path, nodes, edges)
    rep = assess_soft_labels(conn, PID)
    conn.close()
    md = render_markdown(rep)
    assert "soft-quality" in md and "业务域" in md and "架构层" in md
