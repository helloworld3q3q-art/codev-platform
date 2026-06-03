"""cross-link MCP 4 工具全部只读统一图谱 store (store-only, 无 cross_layer fallback)。

覆盖:
  1. store 命中 —— find_table_refs / find_endpoint_link / search_nodes / cross_link_stats
     从 graph_store 读 (db_table + backend_function reads/writes_table 边,
     frontend_api_call --calls_api--> backend_endpoint)。
  2. store 缺失 / 空 / 无此节点 —— 返回 INDEX_MISSING 友好错误 (不再 fallback cross_layer)。
"""
from __future__ import annotations

import asyncio
import json

import pytest

from codev_platform.cross_link import server as srv
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


def _build_store(store_path) -> None:
    """造 graph_store: 表 users; java reader + python writer; 前端→端点 calls_api。"""
    c = open_store(_PID, path=store_path)
    tb = f"{_PID}:db_table:users"
    java_fn = f"{_PID}:backend_function:UserMapper.selectUsers"
    py_fn = f"{_PID}:backend_function:repo.py:save_user"
    ep = f"{_PID}:backend_endpoint:POST:/users"
    fe = f"{_PID}:frontend_api_call:src/UserPage.tsx:postUsers"
    nodes = [
        GraphNode(id=tb, kind=NodeKind.DB_TABLE.value, name="users", project_id=_PID,
                  file="db/migration/V3__users.sql"),
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
def _isolate(monkeypatch):
    """隔离全局连接缓存 + 默认 project, 防污染其它测试。"""
    monkeypatch.setattr(srv, "_conns", {})
    monkeypatch.setattr(srv, "_init_errors", {})
    monkeypatch.setattr(srv, "_missing_db", {})
    monkeypatch.setattr(srv, "PROJECT_ID", None)


# ---------------------------------------------------------------- 路 1: store 命中


@pytest.fixture
def store_hit(tmp_path, monkeypatch):
    """graph_store 存在且有数据 → 4 工具从 store 读。"""
    store_path = tmp_path / "store" / f"{_PID}.sqlite"
    store_path.parent.mkdir(parents=True)
    _build_store(store_path)
    from codev_platform.graph import store as store_mod
    monkeypatch.setattr(store_mod, "graph_store_path", lambda pid: store_path)
    return store_path


def test_find_table_refs_served_from_store(store_hit):
    r = _call(_PID, "find_table_refs", {"table": "users"})
    assert r["source"] == "graph_store"
    # java reader (language=java) 桶到 java_readers
    assert [x["name"] for x in r["java_readers"]] == ["selectUsers"]
    # python writer 桶到 python_writers
    assert [x["name"] for x in r["python_writers"]] == ["save_user"]


def test_find_table_refs_definers_from_table_file(store_hit):
    r = _call(_PID, "find_table_refs", {"table": "users"})
    # definers 取 db_table 节点 file 的文件名
    assert [d["name"] for d in r["definers"]] == ["V3__users.sql"]
    assert r["definers"][0]["path"] == "db/migration/V3__users.sql"


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


def test_find_endpoint_link_classname_dot_method_falls_back_to_handler(store_hit):
    # store 端点按 handler 名 (createUser) 命名; 传 cross_layer 旧约定全名 ClassName.method
    # → 全名没命中时退到取最后一段 (createUser) 匹配 (gap 修复)。
    r = _call(_PID, "find_endpoint_link", {"name": "UserController.createUser"})
    assert r["source"] == "graph_store"
    assert r["kind"] == "java_endpoint"
    assert r["node"] == "createUser"          # 实际命中的 store 节点名
    assert r["query"] == "UserController.createUser"  # 原始入参留痕
    assert [c["name"] for c in r["callers"]] == ["postUsers"]


def test_search_nodes_from_store(store_hit):
    # name 子串 (NOCASE), 无 kind 过滤
    r = _call(_PID, "search_nodes", {"query": "user"})
    names = {h["name"] for h in r["hits"]}
    # selectUsers / createUser / postUsers / save_user(无 user 子串, 不应命中)
    assert "selectUsers" in names
    assert "createUser" in names
    assert "postUsers" in names
    # kind 过滤 (store NodeKind 值)
    r2 = _call(_PID, "search_nodes", {"query": "user", "kind": "backend_endpoint"})
    assert [h["name"] for h in r2["hits"]] == ["createUser"]
    assert r2["hits"][0]["kind"] == "backend_endpoint"


def test_search_nodes_limit(store_hit):
    r = _call(_PID, "search_nodes", {"query": "user", "limit": 1})
    assert len(r["hits"]) == 1


def test_cross_link_stats_from_store(store_hit):
    s = _call(_PID, "cross_link_stats", {})
    assert s["project_id"] == _PID
    assert "store-proj.sqlite" in s["db_path"]
    assert s["nodes_by_kind"].get("backend_function") == 2
    assert s["nodes_by_kind"].get("db_table") == 1
    assert s["edges_by_kind"].get("reads_table") == 1
    assert s["edges_by_kind"].get("writes_table") == 1
    assert s["edges_by_kind"].get("calls_api") == 1
    # build_meta 来自 ingest_meta (graph/store.stats)
    assert "plugins" in s["build_meta"]
    assert s["build_meta"]["totals"]["nodes"] == 5


# ---------------------------------------------------------------- 路 2: store 缺失/空/无节点 → INDEX_MISSING


def _expect_missing(r):
    assert "error" in r
    assert "统一图谱 store" in r["error"]


@pytest.fixture
def store_missing(tmp_path, monkeypatch):
    """graph_store 文件不存在 → 全部 INDEX_MISSING。"""
    missing = tmp_path / "nostore" / f"{_PID}.sqlite"  # 不创建
    from codev_platform.graph import store as store_mod
    monkeypatch.setattr(store_mod, "graph_store_path", lambda pid: missing)
    return missing


def test_all_tools_missing_when_store_absent(store_missing):
    _expect_missing(_call(_PID, "find_table_refs", {"table": "users"}))
    _expect_missing(_call(_PID, "find_endpoint_link", {"name": "postUsers"}))
    _expect_missing(_call(_PID, "search_nodes", {"query": "user"}))
    _expect_missing(_call(_PID, "cross_link_stats", {}))


def test_empty_store_is_missing(tmp_path, monkeypatch):
    """store 文件存在但空 (无节点) → 视同缺失 → INDEX_MISSING。"""
    empty = tmp_path / "empty" / f"{_PID}.sqlite"
    empty.parent.mkdir(parents=True)
    open_store(_PID, path=empty).close()  # 建表但不写数据
    from codev_platform.graph import store as store_mod
    monkeypatch.setattr(store_mod, "graph_store_path", lambda pid: empty)

    _expect_missing(_call(_PID, "find_table_refs", {"table": "users"}))
    _expect_missing(_call(_PID, "cross_link_stats", {}))


def test_table_absent_in_store_is_missing(store_hit):
    """store 存在但无此表 → INDEX_MISSING (不再 fallback cross_layer)。"""
    r = _call(_PID, "find_table_refs", {"table": "t_does_not_exist"})
    _expect_missing(r)


def test_endpoint_absent_in_store_is_missing(store_hit):
    """store 存在但无此 endpoint 节点 → INDEX_MISSING。"""
    r = _call(_PID, "find_endpoint_link", {"name": "noSuchNode"})
    _expect_missing(r)
