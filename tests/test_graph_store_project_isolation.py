"""graph store 节点级 project_id 纵深防御 —— 共享库隔离回归。

用显式共享 store_path 模拟"两个 project 落进同一 sqlite"的场景, 断言:
- 写时 nodes.project_id 强制用入参 (不被 GraphNode.project_id 污染)
- load_graph 始终按 project_id 过滤 (不串项目)
- upsert_result 清旧数据时按 (plugin, project_id) 删 (不误删别项目同插件节点)
"""
from __future__ import annotations

from pathlib import Path

from codev_platform.graph.schema import AnalyzerResult, GraphNode
from codev_platform.graph.store import load_graph, open_store, upsert_result


def _result(node_id: str, node_project_id: str) -> AnalyzerResult:
    """同插件 "px", 单节点; node 自带的 project_id 故意可与入参不同。"""
    return AnalyzerResult(
        nodes=[
            GraphNode(
                id=node_id,
                kind="backend_function",
                name=node_id,
                project_id=node_project_id,
            )
        ],
        plugin="px",
    )


def test_shared_store_isolates_projects(tmp_path: Path) -> None:
    shared = tmp_path / "shared.sqlite"
    ra = _result("a:fn", "proj-a")
    rb = _result("b:fn", "proj-b")

    conn = open_store("proj-a", path=shared)
    try:
        upsert_result(conn, "proj-a", ra)
        upsert_result(conn, "proj-b", rb)

        # load_graph("proj-a") 只回 proj-a 的节点。
        ga = load_graph(conn, "proj-a")
        assert [n.id for n in ga.nodes] == ["a:fn"]
        assert all(n.project_id == "proj-a" for n in ga.nodes)

        # load_graph("proj-b") 只回 proj-b 的节点。
        gb = load_graph(conn, "proj-b")
        assert [n.id for n in gb.nodes] == ["b:fn"]
        assert all(n.project_id == "proj-b" for n in gb.nodes)

        # 再次 upsert proj-a (同 plugin "px") 不能误删 proj-b 的节点。
        upsert_result(conn, "proj-a", ra)
        gb_after = load_graph(conn, "proj-b")
        assert [n.id for n in gb_after.nodes] == ["b:fn"]
    finally:
        conn.close()


def test_node_project_id_forced_from_param(tmp_path: Path) -> None:
    """node 自带的 project_id 与入参不一致时, 落盘 / 读回都以入参为准。"""
    shared = tmp_path / "shared.sqlite"
    # GraphNode.project_id 故意写成 "wrong", upsert 入参是 "proj-a"。
    rogue = _result("a:fn", "wrong")

    conn = open_store("proj-a", path=shared)
    try:
        upsert_result(conn, "proj-a", rogue)
        ga = load_graph(conn, "proj-a")
        assert [n.project_id for n in ga.nodes] == ["proj-a"]
        # 用错误的 project_id 读, 读不到 (没被污染到 "wrong" 桶)。
        gw = load_graph(conn, "wrong")
        assert gw.nodes == []
    finally:
        conn.close()
