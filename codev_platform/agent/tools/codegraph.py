"""codegraph 工具(直读 .codegraph/codegraph.db sqlite).

codegraph 无 codev_platform Python API、其 MCP 是 stdio(每调用 spawn 太重),
故直读它的 sqlite(只读,免 spawn)。schema:nodes / edges / nodes_fts。
"""
from __future__ import annotations

import json
import sqlite3
from pathlib import Path
from typing import Any

from codev_platform.agent.brain import ToolResult
from codev_platform.agent.tools.base import Tool

_MAX_ROWS = 20


def _find_db() -> Path | None:
    """从 cwd 向上找 .codegraph/codegraph.db(agent 在某仓内运行)."""
    cur = Path.cwd().resolve()
    for d in (cur, *cur.parents):
        cand = d / ".codegraph" / "codegraph.db"
        if cand.is_file():
            return cand
    return None


def _connect() -> sqlite3.Connection:
    db = _find_db()
    if db is None:
        raise FileNotFoundError("未找到 .codegraph/codegraph.db(codegraph 未建索引?)")
    con = sqlite3.connect(f"file:{db}?mode=ro", uri=True)
    con.row_factory = sqlite3.Row
    return con


def _node_brief(r: sqlite3.Row) -> dict[str, Any]:
    return {
        "name": r["name"],
        "kind": r["kind"],
        "loc": f"{r['file_path']}:{r['start_line']}",
        "signature": (r["signature"] or "").strip()[:200] or None,
    }


class CodegraphSearchTool(Tool):
    name = "codegraph_search"
    description = "按名字找代码符号(函数/类/方法),返回定义位置 file:line + 签名。入参 query=符号名或关键词。"
    input_schema = {
        "type": "object",
        "properties": {"query": {"type": "string", "description": "符号名 / 关键词"}},
        "required": ["query"],
    }

    def run(self, args: dict[str, Any]) -> ToolResult:
        q = (args or {}).get("query", "").strip()
        if not q:
            return ToolResult(call_id="", content="缺少 query 参数", is_error=True)
        try:
            con = _connect()
            try:
                rows = con.execute(
                    "SELECT n.* FROM nodes_fts f JOIN nodes n ON n.id = f.id "
                    "WHERE nodes_fts MATCH ? LIMIT ?",
                    (q, _MAX_ROWS),
                ).fetchall()
                if not rows:
                    rows = con.execute(
                        "SELECT * FROM nodes WHERE name LIKE ? LIMIT ?",
                        (f"%{q}%", _MAX_ROWS),
                    ).fetchall()
            finally:
                con.close()
        except Exception as e:  # noqa: BLE001
            return ToolResult(call_id="", content=f"codegraph 查询失败: {e}", is_error=True)
        if not rows:
            return ToolResult(call_id="", content=f"未找到符号: {q}")
        out = [_node_brief(r) for r in rows]
        return ToolResult(call_id="", content=json.dumps(out, ensure_ascii=False, indent=2))


def _relations(name: str, incoming: bool) -> ToolResult:
    """incoming=True 找 callers(谁指向它);False 找 callees(它指向谁)."""
    if not name:
        return ToolResult(call_id="", content="缺少 name 参数", is_error=True)
    try:
        con = _connect()
        try:
            ids = [r["id"] for r in con.execute("SELECT id FROM nodes WHERE name = ? LIMIT 5", (name,))]
            if not ids:
                return ToolResult(call_id="", content=f"未找到符号: {name}")
            ph = ",".join("?" * len(ids))
            if incoming:
                sql = (f"SELECT n.name, n.kind, n.file_path, n.start_line, e.kind AS edge "
                       f"FROM edges e JOIN nodes n ON n.id = e.source WHERE e.target IN ({ph}) LIMIT ?")
            else:
                sql = (f"SELECT n.name, n.kind, n.file_path, n.start_line, e.kind AS edge "
                       f"FROM edges e JOIN nodes n ON n.id = e.target WHERE e.source IN ({ph}) LIMIT ?")
            rows = con.execute(sql, (*ids, _MAX_ROWS)).fetchall()
        finally:
            con.close()
    except Exception as e:  # noqa: BLE001
        return ToolResult(call_id="", content=f"codegraph 查询失败: {e}", is_error=True)
    if not rows:
        rel = "调用方" if incoming else "被调用项"
        return ToolResult(call_id="", content=f"{name} 无{rel}记录。")
    out = [{"name": r["name"], "kind": r["kind"], "loc": f"{r['file_path']}:{r['start_line']}", "edge": r["edge"]}
           for r in rows]
    return ToolResult(call_id="", content=json.dumps(out, ensure_ascii=False, indent=2))


class CodegraphCallersTool(Tool):
    name = "codegraph_callers"
    description = "找一个符号的调用方/引用方(谁指向它)。入参 name=符号名。用于评估改动影响面。"
    input_schema = {
        "type": "object",
        "properties": {"name": {"type": "string", "description": "符号名"}},
        "required": ["name"],
    }

    def run(self, args: dict[str, Any]) -> ToolResult:
        return _relations((args or {}).get("name", "").strip(), incoming=True)


class CodegraphCalleesTool(Tool):
    name = "codegraph_callees"
    description = "找一个符号引用了谁(它指向哪些符号)。入参 name=符号名。"
    input_schema = {
        "type": "object",
        "properties": {"name": {"type": "string", "description": "符号名"}},
        "required": ["name"],
    }

    def run(self, args: dict[str, Any]) -> ToolResult:
        return _relations((args or {}).get("name", "").strip(), incoming=False)


def register_into(registry) -> None:
    registry.register(CodegraphSearchTool())
    registry.register(CodegraphCallersTool())
    registry.register(CodegraphCalleesTool())
