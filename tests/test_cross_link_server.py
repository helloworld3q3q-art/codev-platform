"""cross-link MCP server —— 多租户 per-project 路由 + 4 tools 烟测。

重点验:HTTP 化后一个 server 进程按 contextvar(project_id)路由到不同
cross_layer.sqlite,项目间数据不串(2026-05-28 串库事故的回归护栏)。
"""
from __future__ import annotations

import asyncio
import json
import sqlite3

import pytest

from codev_platform.cross_link import server as srv
from codev_platform.cross_link.schema import SCHEMA_SQL


def _build_db(path, table: str, writer: str) -> None:
    """造一个最小 cross_layer.sqlite:1 张 table + 1 个 python writer + writes_table 边。"""
    conn = sqlite3.connect(path)
    conn.executescript(SCHEMA_SQL)
    cur = conn.cursor()
    cur.execute("INSERT INTO nodes(kind, name, path) VALUES('table', ?, NULL)", (table,))
    tid = cur.lastrowid
    cur.execute(
        "INSERT INTO nodes(kind, name, path, line, language) VALUES('python_method', ?, 'w.py', 7, 'python')",
        (writer,),
    )
    wid = cur.lastrowid
    cur.execute(
        "INSERT INTO edges(src_id, rel, dst_id, confidence, evidence) VALUES(?, 'writes_table', ?, 1.0, 'test')",
        (wid, tid),
    )
    cur.execute("INSERT INTO build_meta(key, value) VALUES('last_build_at', '2026-05-30T00:00:00')")
    conn.commit()
    conn.close()


@pytest.fixture
def two_projects(tmp_path, monkeypatch):
    """proj-a 有表 t_a(+writer wa);proj-b 有表 t_b(+writer wb)。各自独立 DB。"""
    db_a = tmp_path / "a" / "cross_layer.sqlite"
    db_b = tmp_path / "b" / "cross_layer.sqlite"
    db_a.parent.mkdir(parents=True)
    db_b.parent.mkdir(parents=True)
    _build_db(db_a, "t_a", "wa")
    _build_db(db_b, "t_b", "wb")

    mapping = {"proj-a": db_a, "proj-b": db_b}
    monkeypatch.setattr(srv, "_db_path_for", lambda pid: mapping.get(pid, tmp_path / "missing.sqlite"))
    # 隔离全局连接缓存,避免污染其它测试
    monkeypatch.setattr(srv, "_conns", {})
    monkeypatch.setattr(srv, "_init_errors", {})
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
    assert [w["name"] for w in a["python_writers"]] == ["wa"]

    # proj-b 查同名 t_a → 它库里没有, 全空 (证明没串到 proj-a)
    b = _call("proj-b", "find_table_refs", {"table": "t_a"})
    assert b["python_writers"] == []

    # proj-b 查自己的 t_b → 命中 wb
    b2 = _call("proj-b", "find_table_refs", {"table": "t_b"})
    assert [w["name"] for w in b2["python_writers"]] == ["wb"]


def test_stats_reports_active_project(two_projects):
    s = _call("proj-a", "cross_link_stats", {})
    assert s["project_id"] == "proj-a"
    assert s["nodes_by_kind"].get("table") == 1
    assert s["edges_by_rel"].get("writes_table") == 1


def test_search_nodes_scoped(two_projects):
    r = _call("proj-a", "search_nodes", {"query": "wa", "kind": "python_method"})
    assert [h["name"] for h in r["hits"]] == ["wa"]
    # proj-b 搜 wa → 无
    r2 = _call("proj-b", "search_nodes", {"query": "wa"})
    assert r2["hits"] == []


def test_missing_project_returns_friendly_error(two_projects):
    r = _call("proj-zzz", "find_table_refs", {"table": "t_a"})
    assert "error" in r
    assert "cross_layer DB 不存在" in r["error"]


def test_active_pid_falls_back_to_explicit_key(monkeypatch):
    # 无 contextvar + 无 PROJECT_ID → _EXPLICIT_KEY
    monkeypatch.setattr(srv, "PROJECT_ID", None)
    assert srv._active_pid() == srv._EXPLICIT_KEY
    monkeypatch.setattr(srv, "PROJECT_ID", "default-proj")
    assert srv._active_pid() == "default-proj"
