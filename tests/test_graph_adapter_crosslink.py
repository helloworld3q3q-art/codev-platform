"""cross-link -> 统一 graph schema 适配器测试。

用临时 sqlite fixture (真实 cross_link schema) 验证:
- 节点 / 边 kind 映射正确
- 原始 kind / rel 保留进 meta (不丢信息)
- 缺库返回空 AnalyzerResult (不抛)
- 悬挂边 (端点缺失) 被跳过
"""
from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

from codev_platform.cross_link.schema import (
    SCHEMA_SQL,
    upsert_edge,
    upsert_node,
)
from codev_platform.graph import adapters
from codev_platform.graph.adapters import cross_link as adapter_mod
from codev_platform.graph.adapters.cross_link import build_cross_link_result
from codev_platform.graph.schema import EdgeKind, NodeKind

PID = "demo-project"


def _build_db(path: Path) -> dict[str, int]:
    """建一个含真实 cross_link schema + 样本节点/边的 sqlite, 返回名->rowid。"""
    conn = sqlite3.connect(path)
    conn.executescript(SCHEMA_SQL)
    ids = {
        "table": upsert_node(conn, "table", "stock_recommend_result"),
        "column": upsert_node(conn, "column", "signal", path="cols/signal"),
        "endpoint": upsert_node(
            conn, "java_endpoint", "StockController.page",
            path="X.java", line=10, meta_json='{"url": "/api/page"}',
        ),
        "jmethod": upsert_node(conn, "java_method", "Mapper.select", path="M.java"),
        "pymethod": upsert_node(conn, "python_method", "repo.read", path="r.py"),
        "fe": upsert_node(conn, "frontend_api", "pageApi", path="api.ts"),
        "flyway": upsert_node(conn, "flyway_migration", "V1__init", path="V1.sql"),
        "weird": upsert_node(conn, "some_unknown_kind", "mystery", path="m.x"),
    }
    upsert_edge(conn, ids["jmethod"], "queries_table", ids["table"], evidence="SELECT *")
    upsert_edge(conn, ids["pymethod"], "writes_table", ids["table"])
    upsert_edge(conn, ids["jmethod"], "updates_table", ids["table"])
    upsert_edge(conn, ids["fe"], "calls_api", ids["endpoint"], confidence=0.7)
    upsert_edge(conn, ids["flyway"], "defines_table", ids["table"])
    upsert_edge(conn, ids["table"], "defines_column", ids["column"])
    conn.commit()
    conn.close()
    return ids


@pytest.fixture()
def db_path(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    p = tmp_path / "cross_layer.sqlite"
    _build_db(p)
    monkeypatch.setattr(adapter_mod, "cross_link_db_path", lambda pid: p)
    monkeypatch.setattr(
        adapter_mod, "cross_link_legacy_db_path", lambda: tmp_path / "nope.sqlite"
    )
    return p


def test_node_kind_mapping(db_path: Path) -> None:
    result = build_cross_link_result(PID)
    by_name = {n.name: n for n in result.nodes}

    assert by_name["stock_recommend_result"].kind == NodeKind.DB_TABLE.value
    assert by_name["signal"].kind == NodeKind.DB_COLUMN.value
    assert by_name["StockController.page"].kind == NodeKind.BACKEND_ENDPOINT.value
    assert by_name["Mapper.select"].kind == NodeKind.BACKEND_FUNCTION.value
    assert by_name["repo.read"].kind == NodeKind.BACKEND_FUNCTION.value
    assert by_name["pageApi"].kind == NodeKind.FRONTEND_API_CALL.value
    assert by_name["V1__init"].kind == NodeKind.FILE.value


def test_unknown_kind_kept_raw(db_path: Path) -> None:
    result = build_cross_link_result(PID)
    weird = next(n for n in result.nodes if n.name == "mystery")
    # 无映射 -> 保留裸字符串 kind
    assert weird.kind == "some_unknown_kind"
    assert weird.meta["cross_link_kind"] == "some_unknown_kind"


def test_meta_preserves_original_kind(db_path: Path) -> None:
    result = build_cross_link_result(PID)
    for n in result.nodes:
        assert "cross_link_kind" in n.meta  # 每个节点都留底原始 kind
    # 原 meta_json 也被并入
    endpoint = next(n for n in result.nodes if n.name == "StockController.page")
    assert endpoint.meta.get("url") == "/api/page"
    assert endpoint.meta["cross_link_kind"] == "java_endpoint"


def test_edge_kind_mapping(db_path: Path) -> None:
    result = build_cross_link_result(PID)
    kinds = {e.kind for e in result.edges}
    assert EdgeKind.READS_TABLE.value in kinds      # queries_table
    assert EdgeKind.WRITES_TABLE.value in kinds     # writes_table
    assert EdgeKind.UPDATES_TABLE.value in kinds    # updates_table
    assert EdgeKind.CALLS_API.value in kinds        # calls_api
    assert "defines_table" in kinds                 # 无映射保留裸字符串
    assert "defines_column" in kinds

    for e in result.edges:
        assert "cross_link_rel" in e.meta           # 每条边留底原始 rel


def test_edge_confidence_and_evidence(db_path: Path) -> None:
    result = build_cross_link_result(PID)
    calls = next(e for e in result.edges if e.kind == EdgeKind.CALLS_API.value)
    assert calls.confidence == 0.7
    reads = next(e for e in result.edges if e.meta["cross_link_rel"] == "queries_table")
    assert reads.meta.get("evidence") == "SELECT *"


def test_project_id_on_every_node(db_path: Path) -> None:
    result = build_cross_link_result(PID)
    assert result.nodes
    assert all(n.project_id == PID for n in result.nodes)
    assert all(n.id.startswith(PID + ":") for n in result.nodes)


def test_plugin_name(db_path: Path) -> None:
    result = build_cross_link_result(PID)
    assert result.plugin == adapters.CROSS_LINK_PLUGIN_NAME == "builtin.cross_link"


def test_missing_db_returns_empty(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(
        adapter_mod, "cross_link_db_path", lambda pid: tmp_path / "absent.sqlite"
    )
    monkeypatch.setattr(
        adapter_mod, "cross_link_legacy_db_path", lambda: tmp_path / "absent2.sqlite"
    )
    result = build_cross_link_result(PID)
    assert result.nodes == []
    assert result.edges == []
    assert result.plugin == "builtin.cross_link"


def test_dangling_edge_skipped(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    p = tmp_path / "cross_layer.sqlite"
    conn = sqlite3.connect(p)
    conn.executescript(SCHEMA_SQL)
    t = upsert_node(conn, "table", "t1")
    # 手动插一条指向不存在 dst (id=9999) 的边
    conn.execute(
        "INSERT INTO edges (src_id, rel, dst_id, confidence) VALUES (?, ?, ?, ?)",
        (t, "queries_table", 9999, 1.0),
    )
    conn.commit()
    conn.close()
    monkeypatch.setattr(adapter_mod, "cross_link_db_path", lambda pid: p)
    monkeypatch.setattr(
        adapter_mod, "cross_link_legacy_db_path", lambda: tmp_path / "nope.sqlite"
    )
    result = build_cross_link_result(PID)
    assert len(result.nodes) == 1
    assert result.edges == []  # 悬挂边被跳过
