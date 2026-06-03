"""cross-link MCP server —— 多租户 per-project 路由 + 4 tools 烟测 (store-only)。

重点验:4 个工具全部只读统一图谱 store (graph_store/<pid>.sqlite),一个 server 进程
按 contextvar(project_id)路由到不同 store,项目间数据不串(2026-05-28 串库事故护栏)。
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


def _build_store(path, pid: str, table: str, writer: str) -> None:
    """造一个最小 graph_store: 1 张 db_table + 1 个 python backend_function + writes_table 边。"""
    c = open_store(pid, path=path)
    tb = f"{pid}:db_table:{table}"
    wfn = f"{pid}:backend_function:w.py:{writer}"
    nodes = [
        GraphNode(id=tb, kind=NodeKind.DB_TABLE.value, name=table,
                  project_id=pid, file=f"db/migration/V1__{table}.sql"),
        GraphNode(id=wfn, kind=NodeKind.BACKEND_FUNCTION.value, name=writer,
                  project_id=pid, file="w.py", line=7, language="python"),
    ]
    edges = [GraphEdge(source=wfn, target=tb, kind=EdgeKind.WRITES_TABLE.value)]
    upsert_result(c, pid, AnalyzerResult(nodes=nodes, edges=edges, plugin="test"))
    c.close()


@pytest.fixture
def two_projects(tmp_path, monkeypatch):
    """proj-a 有表 t_a(+writer wa);proj-b 有表 t_b(+writer wb)。各自独立 store。"""
    store_a = tmp_path / "a" / "proj-a.sqlite"
    store_b = tmp_path / "b" / "proj-b.sqlite"
    store_a.parent.mkdir(parents=True)
    store_b.parent.mkdir(parents=True)
    _build_store(store_a, "proj-a", "t_a", "wa")
    _build_store(store_b, "proj-b", "t_b", "wb")

    mapping = {"proj-a": store_a, "proj-b": store_b}
    from codev_platform.graph import store as store_mod
    monkeypatch.setattr(
        store_mod, "graph_store_path",
        lambda pid: mapping.get(pid, tmp_path / "missing.sqlite"),
    )
    monkeypatch.setattr(srv, "PROJECT_ID", None)
    return mapping


def _call(pid, name, args):
    async def _go():
        tok = srv._current_project_id.set(pid)
        try:
            res = await srv._dispatch(name, args)
        finally:
            srv._current_project_id.reset(tok)
        return json.loads(res[0].text)

    return asyncio.run(_go())


def test_per_project_routing_isolates_data(two_projects):
    # proj-a 查 t_a → 命中 1 个 python writer
    a = _call("proj-a", "find_table_refs", {"table": "t_a"})
    assert a["table"] == "t_a"
    assert a["source"] == "graph_store"
    assert [w["name"] for w in a["python_writers"]] == ["wa"]

    # proj-b 查同名 t_a → 它库里没有 → INDEX_MISSING (证明没串到 proj-a)
    b = _call("proj-b", "find_table_refs", {"table": "t_a"})
    assert "error" in b

    # proj-b 查自己的 t_b → 命中 wb
    b2 = _call("proj-b", "find_table_refs", {"table": "t_b"})
    assert [w["name"] for w in b2["python_writers"]] == ["wb"]


def test_table_refs_definers_from_table_file(two_projects):
    a = _call("proj-a", "find_table_refs", {"table": "t_a"})
    assert [d["name"] for d in a["definers"]] == ["V1__t_a.sql"]
    assert a["definers"][0]["path"] == "db/migration/V1__t_a.sql"


def test_stats_reports_active_project(two_projects):
    s = _call("proj-a", "cross_link_stats", {})
    assert s["project_id"] == "proj-a"
    assert s["nodes_by_kind"].get("db_table") == 1
    assert s["edges_by_kind"].get("writes_table") == 1
    assert "proj-a.sqlite" in s["db_path"]
    assert "build_meta" in s


def test_search_nodes_scoped(two_projects):
    r = _call("proj-a", "search_nodes", {"query": "wa", "kind": "backend_function"})
    assert [h["name"] for h in r["hits"]] == ["wa"]
    # proj-b 搜 wa → 无 (store 存在但无匹配 → 空 hits, 非 error)
    r2 = _call("proj-b", "search_nodes", {"query": "wa"})
    assert r2["hits"] == []


def test_missing_project_returns_friendly_error(two_projects):
    r = _call("proj-zzz", "find_table_refs", {"table": "t_a"})
    assert "error" in r
    assert "统一图谱 store" in r["error"]


def test_concurrent_sessions_isolated(two_projects):
    # 两个并发 task 各 set 不同 project_id + 跨 await 边界交错, 断言互不串库
    # (锁定 2026-05-28 串库回归 + contextvar 跨 task 复制隔离)。
    async def query(pid):
        tok = srv._current_project_id.set(pid)
        try:
            await asyncio.sleep(0)  # 让出, 强制与另一 task 交错
            res = await srv._dispatch("find_table_refs", {"table": "t_a"})
            await asyncio.sleep(0)
            return json.loads(res[0].text)
        finally:
            srv._current_project_id.reset(tok)

    async def go():
        return await asyncio.gather(
            asyncio.create_task(query("proj-a")),
            asyncio.create_task(query("proj-b")),
        )

    a, b = asyncio.run(go())
    assert [w["name"] for w in a["python_writers"]] == ["wa"]  # proj-a 见 t_a
    assert "error" in b                                        # proj-b 无 t_a, 未串到 a


def test_active_pid_falls_back_to_explicit_key(monkeypatch):
    # 无 contextvar + 无 PROJECT_ID → _EXPLICIT_KEY
    monkeypatch.setattr(srv, "PROJECT_ID", None)
    assert srv._active_pid() == srv._EXPLICIT_KEY
    monkeypatch.setattr(srv, "PROJECT_ID", "default-proj")
    assert srv._active_pid() == "default-proj"
