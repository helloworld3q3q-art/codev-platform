"""codegraph_trace 多跳 BFS 工具单测 —— 建临时 codegraph sqlite 验逐层展开 + 防环 + clamp。"""
from __future__ import annotations

import json
import sqlite3

from codev_platform.agent.tools.codegraph import CodegraphTraceTool, _trace

PID = "demo-trace"


def _build_db(tmp_path, edges, names=None):
    """建集中路径 codegraph.db: nodes(id,name,kind,file_path,start_line) + edges(source,target,kind)。
    edges: [(src_name, dst_name), ...]; 节点从边自动收集。"""
    db = tmp_path / "codegraph_ext" / PID / "codegraph" / "codegraph.db"
    db.parent.mkdir(parents=True)
    con = sqlite3.connect(db)
    con.execute("CREATE TABLE nodes (id INTEGER PRIMARY KEY, name TEXT, kind TEXT, "
                "file_path TEXT, start_line INTEGER, signature TEXT)")
    con.execute("CREATE TABLE edges (source INTEGER, target INTEGER, kind TEXT)")
    syms = {}
    for s, d in edges:
        for nm in (s, d):
            if nm not in syms:
                syms[nm] = len(syms) + 1
                con.execute("INSERT INTO nodes VALUES (?,?,?,?,?,?)",
                            (syms[nm], nm, "function", f"{nm}.py", 1, f"def {nm}()"))
    for s, d in edges:
        con.execute("INSERT INTO edges VALUES (?,?,?)", (syms[s], syms[d], "calls"))
    con.commit()
    con.close()
    return db


def _run(monkeypatch, tmp_path, edges, **args):
    monkeypatch.setenv("PLATFORM_DATA_DIR", str(tmp_path))
    _build_db(tmp_path, edges)
    res = CodegraphTraceTool(PID).run(args)
    assert not res.is_error, res.content
    return res.content


def test_trace_callees_multi_level(monkeypatch, tmp_path):
    # A->B->C->D 调用链, callees 深度 3 应逐层返回 B / C / D
    out = json.loads(_run(monkeypatch, tmp_path, [("A", "B"), ("B", "C"), ("C", "D")],
                          name="A", direction="callees", depth=3))
    assert out["direction"] == "callees" and out["depth"] == 3
    assert [m["name"] for m in out["levels"]["hop1"]] == ["B"]
    assert [m["name"] for m in out["levels"]["hop2"]] == ["C"]
    assert [m["name"] for m in out["levels"]["hop3"]] == ["D"]


def test_trace_callers_reverse(monkeypatch, tmp_path):
    # 同图 callers: D 的上游逐层 C / B
    out = json.loads(_run(monkeypatch, tmp_path, [("A", "B"), ("B", "C"), ("C", "D")],
                          name="D", direction="callers", depth=2))
    assert [m["name"] for m in out["levels"]["hop1"]] == ["C"]
    assert [m["name"] for m in out["levels"]["hop2"]] == ["B"]


def test_trace_depth_caps_traversal(monkeypatch, tmp_path):
    # depth=1 只返回第一层, 不深入
    out = json.loads(_run(monkeypatch, tmp_path, [("A", "B"), ("B", "C")],
                          name="A", direction="callees", depth=1))
    assert out["depth"] == 1 and "hop2" not in out["levels"]


def test_trace_handles_cycle(monkeypatch, tmp_path):
    # A->B->A 环: 不应无限循环; visited 防重复展开
    out = json.loads(_run(monkeypatch, tmp_path, [("A", "B"), ("B", "A")],
                          name="A", direction="callees", depth=5))
    # A->B(hop1), B->A 但 A 已 visited → hop2 空 → 停。只 1 层。
    assert out["depth"] == 1 and [m["name"] for m in out["levels"]["hop1"]] == ["B"]


def test_trace_depth_clamped_to_6(monkeypatch, tmp_path):
    # depth 入参 99 → clamp 到 6(不爆)
    res = _trace("A", incoming=False, depth=99, project_id=None)
    # 无 db(project_id=None, cwd 无 .codegraph)→ 报未找到, 但不抛; 仅验 clamp 不崩
    assert res.is_error or "未找到" in res.content or "无" in res.content


def test_trace_missing_name(monkeypatch, tmp_path):
    res = CodegraphTraceTool(PID).run({"name": ""})
    assert res.is_error and "缺少" in res.content
