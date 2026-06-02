"""统一图谱 store 测试 —— 往返一致 / 幂等 / project 隔离 / 多插件归属。

全用临时 sqlite (path 显式覆盖), 不碰真实 data/ 布局。
"""
from __future__ import annotations

from pathlib import Path

import pytest

from codev_platform.graph.schema import (
    AnalyzerResult,
    Evidence,
    Finding,
    GraphEdge,
    GraphNode,
)
from codev_platform.graph.store import (
    graph_store_path,
    load_graph,
    open_store,
    stats,
    upsert_result,
)

PID = "demo-project"


def _sample_result(plugin: str = "builtin.fake") -> AnalyzerResult:
    return AnalyzerResult(
        nodes=[
            GraphNode(
                id=f"{PID}:db_table:t1", kind="db_table", name="t1",
                project_id=PID, file="a.sql", line=3, language="sql",
                meta={"rows": 100, "raw": "x"},
            ),
            GraphNode(
                id=f"{PID}:backend_function:m1", kind="backend_function",
                name="m1", project_id=PID, file="M.java", line=42,
            ),
        ],
        edges=[
            GraphEdge(
                source=f"{PID}:backend_function:m1", target=f"{PID}:db_table:t1",
                kind="reads_table", confidence=0.7, meta={"evidence": "SELECT *"},
            ),
        ],
        evidences=[
            Evidence(source=plugin, detail="hit", file="M.java", line=42,
                     confidence=0.9, meta={"snippet": "select"}),
        ],
        findings=[
            Finding(kind="impact", severity="high", title="t1 写入面广",
                    detail="多处写", node_ids=[f"{PID}:db_table:t1"],
                    evidence_ids=["0"], meta={"score": 5}),
        ],
        plugin=plugin,
        plugin_version="1.0.0",
    )


def test_roundtrip(tmp_path: Path) -> None:
    p = tmp_path / "g.sqlite"
    conn = open_store(PID, path=p)
    upsert_result(conn, PID, _sample_result())
    got = load_graph(conn, PID, plugin="builtin.fake")
    conn.close()

    src = _sample_result()
    # load_graph 按 id 排序, 与源 list 顺序无关 -> 按 id 建字典比对节点。
    assert {n.id: n.to_dict() for n in got.nodes} == {n.id: n.to_dict() for n in src.nodes}
    assert [e.to_dict() for e in got.edges] == [e.to_dict() for e in src.edges]
    assert [ev.to_dict() for ev in got.evidences] == [ev.to_dict() for ev in src.evidences]
    assert [f.to_dict() for f in got.findings] == [f.to_dict() for f in src.findings]
    assert got.plugin == "builtin.fake"
    assert got.plugin_version == "1.0.0"


def test_meta_and_types_preserved(tmp_path: Path) -> None:
    p = tmp_path / "g.sqlite"
    conn = open_store(PID, path=p)
    upsert_result(conn, PID, _sample_result())
    node = next(n for n in load_graph(conn, PID).nodes if n.name == "t1")
    conn.close()
    assert node.meta == {"rows": 100, "raw": "x"}
    assert node.line == 3
    assert node.language == "sql"


def test_idempotent_same_plugin(tmp_path: Path) -> None:
    p = tmp_path / "g.sqlite"
    conn = open_store(PID, path=p)
    upsert_result(conn, PID, _sample_result())
    upsert_result(conn, PID, _sample_result())  # 重灌
    got = load_graph(conn, PID, plugin="builtin.fake")
    s = stats(conn)
    conn.close()
    # 重灌不翻倍
    assert len(got.nodes) == 2
    assert len(got.edges) == 1
    assert s["totals"]["nodes"] == 2
    assert s["plugins"][0]["node_count"] == 2


def test_multi_plugin_coexist(tmp_path: Path) -> None:
    p = tmp_path / "g.sqlite"
    conn = open_store(PID, path=p)
    upsert_result(conn, PID, _sample_result(plugin="builtin.a"))
    upsert_result(conn, PID, _sample_result(plugin="builtin.b"))
    # 全集 = 两插件之和
    all_nodes = load_graph(conn, PID).nodes
    only_a = load_graph(conn, PID, plugin="builtin.a").nodes
    s = stats(conn)
    conn.close()
    assert len(all_nodes) == 4  # 2 + 2
    assert len(only_a) == 2
    assert {pl["plugin"] for pl in s["plugins"]} == {"builtin.a", "builtin.b"}


def test_reingest_one_plugin_does_not_touch_other(tmp_path: Path) -> None:
    p = tmp_path / "g.sqlite"
    conn = open_store(PID, path=p)
    upsert_result(conn, PID, _sample_result(plugin="builtin.a"))
    upsert_result(conn, PID, _sample_result(plugin="builtin.b"))
    # 只重灌 a (节点减为 1)
    thin = _sample_result(plugin="builtin.a")
    thin.nodes = thin.nodes[:1]
    upsert_result(conn, PID, thin)
    a = load_graph(conn, PID, plugin="builtin.a").nodes
    b = load_graph(conn, PID, plugin="builtin.b").nodes
    conn.close()
    assert len(a) == 1  # a 被替换
    assert len(b) == 2  # b 不受影响


def test_project_isolation(tmp_path: Path) -> None:
    pa = tmp_path / "a.sqlite"
    pb = tmp_path / "b.sqlite"
    ca = open_store("proj-a", path=pa)
    cb = open_store("proj-b", path=pb)
    upsert_result(ca, "proj-a", _sample_result())
    # proj-b store 是独立文件, 完全空
    assert load_graph(cb, "proj-b").nodes == []
    assert load_graph(ca, "proj-a").nodes
    ca.close()
    cb.close()


def test_empty_result(tmp_path: Path) -> None:
    p = tmp_path / "g.sqlite"
    conn = open_store(PID, path=p)
    upsert_result(conn, PID, AnalyzerResult(plugin="builtin.empty"))
    got = load_graph(conn, PID, plugin="builtin.empty")
    s = stats(conn)
    conn.close()
    assert got.nodes == [] and got.edges == []
    assert s["plugins"][0]["plugin"] == "builtin.empty"


def test_store_path_per_project() -> None:
    a = graph_store_path("proj-a")
    b = graph_store_path("proj-b")
    assert a != b
    assert a.name == "proj-a.sqlite"
    assert a.parent.name == "graph_store"


def test_invalid_project_id_rejected(tmp_path: Path) -> None:
    with pytest.raises(Exception):
        graph_store_path("../escape")
