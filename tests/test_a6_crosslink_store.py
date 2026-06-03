"""A6: cross-link MCP find_table_refs / find_endpoint_link 改读统一图谱 store。

两路覆盖:
  1. store 命中 —— 造 graph_store (db_table + backend_function reads/writes_table 边,
     frontend_api_call --calls_api--> backend_endpoint), 验 store 优先返回 (source=graph_store)。
  2. store 缺失 fallback —— store 文件不存在时退回 cross_layer 旧查询 (source 不为 graph_store)。
"""
from __future__ import annotations

import asyncio
import json
import sqlite3

import pytest

from codev_platform.cross_link import server as srv
from codev_platform.cross_link.schema import SCHEMA_SQL
from codev_platform.graph.schema import (
    AnalyzerResult,
    EdgeKind,
    GraphEdge,
    GraphNode,
    NodeKind,
)
from codev_platform.graph.store import open_store, upsert_result

_PID = "store-proj"


# ---------------------------------------------------------------- 工具


def _build_cross_layer(path, table: str, writer: str) -> None:
    """造一个最小 cross_layer.sqlite (旧库): 1 表 + 1 python writer + writes_table 边。"""
    conn = sqlite3.connect(path)
    conn.executescript(SCHEMA_SQL)
    cur = conn.cursor()
    cur.execute("INSERT INTO nodes(kind, name, path) VALUES('table', ?, NULL)", (table,))
    tid = cur.lastrowid
    cur.execute(
        "INSERT INTO nodes(kind, name, path, line, language) "
        "VALUES('python_method', ?, 'w.py', 7, 'python')",
        (writer,),
    )
    wid = cur.lastrowid
    cur.execute(
        "INSERT INTO edges(src_id, rel, dst_id, confidence, evidence) "
        "VALUES(?, 'writes_table', ?, 1.0, 'test')",
        (wid, tid),
    )
    cur.execute(
        "INSERT INTO build_meta(key, value) VALUES('last_build_at', '2026-06-03T00:00:00')"
    )
    conn.commit()
    conn.close()


def _build_store(store_path) -> None:
    """造 graph_store: 表 users; java reader + python writer; 前端→端点 calls_api。"""
    c = open_store(_PID, path=store_path)
    tb = f"{_PID}:db_table:users"
    java_fn = f"{_PID}:backend_function:UserMapper.selectUsers"
    py_fn = f"{_PID}:backend_function:repo.py:save_user"
    ep = f"{_PID}:backend_endpoint:POST:/users"
    fe = f"{_PID}:frontend_api_call:src/UserPage.tsx:postUsers"
    nodes = [
        GraphNode(id=tb, kind=NodeKind.DB_TABLE.value, name="users", project_id=_PID),
        GraphNode(id=java_fn, kind=NodeKind.BACKEND_FUNCTION.value, name="selectUsers",
                  project_id=_PID, file="UserMapper.java", line=10, language="java"),
        GraphNode(id=py_fn, kind=NodeKind.BACKEND_FUNCTION.value, name="save_user",
                  project_id=_PID, file="repo.py", line=20, language="python"),
        GraphNode(id=ep, kind=NodeKind.BACKEND_ENDPOINT.value, name="createUser",
                  project_id=_PID, file="UserController.java", meta={"url": "/users"}),
        GraphNode(id=fe, kind=NodeKind.FRONTEND_API_CALL.value, name="postUsers",
                  project_id=_PID, file="src/UserPage.tsx", meta={"url": "/users"}),
    ]
    edges = [
        GraphEdge(source=java_fn, target=tb, kind=EdgeKind.READS_TABLE.value),
        GraphEdge(source=py_fn, target=tb, kind=EdgeKind.WRITES_TABLE.value),
        GraphEdge(source=fe, target=ep, kind=EdgeKind.CALLS_API.value),
    ]
    upsert_result(c, _PID, AnalyzerResult(nodes=nodes, edges=edges, plugin="test"))
    c.close()


def _call(pid, name, args):
    async def _go():
        tok = srv._current_project_id.set(pid)
        try:
            res = await srv._dispatch(name, args)
        finally:
            srv._current_project_id.reset(tok)
        return json.loads(res[0].text)

    return asyncio.run(_go())


@pytest.fixture(autouse=True)
def _isolate_conns(monkeypatch):
    """隔离全局连接缓存, 防止污染其它测试。"""
    monkeypatch.setattr(srv, "_conns", {})
    monkeypatch.setattr(srv, "_init_errors", {})
    monkeypatch.setattr(srv, "_missing_db", {})
    monkeypatch.setattr(srv, "PROJECT_ID", None)


# ---------------------------------------------------------------- 路 1: store 命中


@pytest.fixture
def store_hit(tmp_path, monkeypatch):
    """graph_store 存在且有数据 → store 优先路径。cross_layer 也在 (但应被 store 抢先)。"""
    store_path = tmp_path / "store" / f"{_PID}.sqlite"
    store_path.parent.mkdir(parents=True)
    _build_store(store_path)
    # 让 _open_graph_store_for 走到这个 store: patch graph_store_path
    from codev_platform.graph import store as store_mod
    monkeypatch.setattr(store_mod, "graph_store_path", lambda pid: store_path)

    # cross_layer 也建一个 (内容不同), 命中 store 时它不该被用到
    cl = tmp_path / "cl" / "cross_layer.sqlite"
    cl.parent.mkdir(parents=True)
    _build_cross_layer(cl, "users", "legacy_writer")
    monkeypatch.setattr(srv, "_db_path_for", lambda pid: cl)
    return store_path


def test_find_table_refs_served_from_store(store_hit):
    r = _call(_PID, "find_table_refs", {"table": "users"})
    assert r["source"] == "graph_store"
    # java reader (language=java) 桶到 java_readers
    assert [x["name"] for x in r["java_readers"]] == ["selectUsers"]
    # python writer 桶到 python_writers
    assert [x["name"] for x in r["python_writers"]] == ["save_user"]
    # 不是旧库的 legacy_writer (证明走了 store 而非 cross_layer)
    all_names = [
        x["name"]
        for k, v in r.items()
        if isinstance(v, list)
        for x in v
    ]
    assert "legacy_writer" not in all_names


def test_find_table_refs_case_insensitive_in_store(store_hit):
    r = _call(_PID, "find_table_refs", {"table": "USERS"})
    assert r["source"] == "graph_store"
    assert [x["name"] for x in r["java_readers"]] == ["selectUsers"]


def test_find_endpoint_link_frontend_to_endpoint_from_store(store_hit):
    r = _call(_PID, "find_endpoint_link", {"name": "postUsers"})
    assert r["source"] == "graph_store"
    assert r["kind"] == "frontend_api"
    assert [t["name"] for t in r["targets"]] == ["createUser"]


def test_find_endpoint_link_endpoint_to_callers_from_store(store_hit):
    r = _call(_PID, "find_endpoint_link", {"name": "createUser"})
    assert r["source"] == "graph_store"
    assert r["kind"] == "java_endpoint"
    assert [c["name"] for c in r["callers"]] == ["postUsers"]


def test_table_absent_in_store_falls_back(store_hit, tmp_path, monkeypatch):
    """store 存在但无此表 → fallback 到 cross_layer (那里有 legacy_writer)。"""
    # cross_layer 用另一个表名, store 里没有 → find_table_refs("t_legacy") 应 fallback
    cl = tmp_path / "cl2" / "cross_layer.sqlite"
    cl.parent.mkdir(parents=True)
    _build_cross_layer(cl, "t_legacy", "legacy_writer")
    monkeypatch.setattr(srv, "_db_path_for", lambda pid: cl)
    r = _call(_PID, "find_table_refs", {"table": "t_legacy"})
    assert r.get("source") != "graph_store"
    assert [x["name"] for x in r["python_writers"]] == ["legacy_writer"]


# ---------------------------------------------------------------- 路 2: store 缺失 fallback


@pytest.fixture
def store_missing(tmp_path, monkeypatch):
    """graph_store 文件不存在 → 退回 cross_layer 旧查询。"""
    missing = tmp_path / "nostore" / f"{_PID}.sqlite"  # 不创建
    from codev_platform.graph import store as store_mod
    monkeypatch.setattr(store_mod, "graph_store_path", lambda pid: missing)

    cl = tmp_path / "cl" / "cross_layer.sqlite"
    cl.parent.mkdir(parents=True)
    _build_cross_layer(cl, "users", "legacy_writer")
    monkeypatch.setattr(srv, "_db_path_for", lambda pid: cl)
    return cl


def test_find_table_refs_fallback_when_store_missing(store_missing):
    r = _call(_PID, "find_table_refs", {"table": "users"})
    # 旧 payload 无 source=graph_store 标记
    assert r.get("source") != "graph_store"
    assert [x["name"] for x in r["python_writers"]] == ["legacy_writer"]


def test_empty_store_falls_back(tmp_path, monkeypatch):
    """store 文件存在但空 (无节点) → 视同缺失, fallback 到 cross_layer。"""
    empty = tmp_path / "empty" / f"{_PID}.sqlite"
    empty.parent.mkdir(parents=True)
    open_store(_PID, path=empty).close()  # 建表但不写数据
    from codev_platform.graph import store as store_mod
    monkeypatch.setattr(store_mod, "graph_store_path", lambda pid: empty)

    cl = tmp_path / "cl" / "cross_layer.sqlite"
    cl.parent.mkdir(parents=True)
    _build_cross_layer(cl, "users", "legacy_writer")
    monkeypatch.setattr(srv, "_db_path_for", lambda pid: cl)

    r = _call(_PID, "find_table_refs", {"table": "users"})
    assert r.get("source") != "graph_store"
    assert [x["name"] for x in r["python_writers"]] == ["legacy_writer"]
