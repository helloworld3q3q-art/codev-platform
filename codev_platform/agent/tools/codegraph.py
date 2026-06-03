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


def _fts_query(q: str) -> str:
    """把自由文本转成安全的 FTS5 查询:逐 token 包成带引号的字符串字面量,
    避免 '.' '/' '(' 等被 FTS5 当语法符号报错。token 间 OR(宽松召回)。"""
    import re
    tokens = re.findall(r"[A-Za-z0-9_]+", q)
    if not tokens:
        return '""'
    return " OR ".join(f'"{t}"' for t in tokens)


def _find_db(project_id: str | None = None) -> Path | None:
    """定位 .codegraph/codegraph.db。
    P2 多租户:project_id 给定 → 按 meta.json repo_path 找该项目仓的 .codegraph;
    None → 从 cwd 向上找(agent 在某仓内运行,单项目兼容)。"""
    if project_id:
        # 优先平台集中路径 (data_root/codegraph_ext/<pid>/codegraph/codegraph.db) —— 环境无关。
        # 不走 meta.json repo_path: 它是异机绝对路径 (如 Windows 'D:/...'), WSL 跑的 agent 解析不到 →
        # 误报 "未建索引"。codegraph 数据 2026-05-30 起集中到平台, 这里是真值源。
        from codev_platform.core.paths import codegraph_db_path
        cand = codegraph_db_path(project_id)
        if cand.is_file():
            return cand
        # 回退: 业务仓内 .codegraph junction (未迁移到平台集中存放的旧部署)。
        from codev_platform.agent.tools._project import repo_path_of
        repo = repo_path_of(project_id)
        if repo is not None:
            cand = repo / ".codegraph" / "codegraph.db"
            return cand if cand.is_file() else None
        return None
    cur = Path.cwd().resolve()
    for d in (cur, *cur.parents):
        cand = d / ".codegraph" / "codegraph.db"
        if cand.is_file():
            return cand
    return None


def _connect(project_id: str | None = None) -> sqlite3.Connection:
    db = _find_db(project_id)
    if db is None:
        hint = f"project '{project_id}' " if project_id else ""
        raise FileNotFoundError(f"未找到 {hint}.codegraph/codegraph.db(codegraph 未建索引?)")
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

    def __init__(self, project_id: str | None = None) -> None:
        self.project_id = project_id

    def run(self, args: dict[str, Any]) -> ToolResult:
        q = (args or {}).get("query", "").strip()
        if not q:
            return ToolResult(call_id="", content="缺少 query 参数", is_error=True)
        try:
            con = _connect(self.project_id)
            try:
                rows = con.execute(
                    "SELECT n.* FROM nodes_fts f JOIN nodes n ON n.id = f.id "
                    "WHERE nodes_fts MATCH ? LIMIT ?",
                    (_fts_query(q), _MAX_ROWS),
                ).fetchall()
                if not rows:
                    # 回退:按最后一个标识符 token 做 LIKE(应对 FTS 分词不命中)
                    import re as _re
                    toks = _re.findall(r"[A-Za-z0-9_]+", q)
                    needle = toks[-1] if toks else q
                    rows = con.execute(
                        "SELECT * FROM nodes WHERE name LIKE ? LIMIT ?",
                        (f"%{needle}%", _MAX_ROWS),
                    ).fetchall()
            finally:
                con.close()
        except Exception as e:  # noqa: BLE001
            return ToolResult(call_id="", content=f"codegraph 查询失败: {e}", is_error=True)
        if not rows:
            return ToolResult(call_id="", content=f"未找到符号: {q}")
        # 排序:精确名命中 > 名字含 query token > 其余;同档 file/import 排后(优先 class/function/method)
        import re as _re
        toks = [t.lower() for t in _re.findall(r"[A-Za-z0-9_]+", q)]
        kind_rank = {"class": 0, "function": 0, "method": 0, "interface": 0}

        def _score(r: sqlite3.Row) -> tuple:
            nm = (r["name"] or "").lower()
            exact = 0 if nm in toks else 1
            contains = 0 if any(t in nm for t in toks) else 1
            return (exact, contains, kind_rank.get(r["kind"], 5))

        rows = sorted(rows, key=_score)
        out = [_node_brief(r) for r in rows]
        return ToolResult(call_id="", content=json.dumps(out, ensure_ascii=False, indent=2))


def _relations(name: str, incoming: bool, project_id: str | None = None) -> ToolResult:
    """incoming=True 找 callers(谁指向它);False 找 callees(它指向谁)."""
    if not name:
        return ToolResult(call_id="", content="缺少 name 参数", is_error=True)
    # 支持 Class.method 限定名:先按全名,再退到点号后的裸名(codegraph 多按裸名存方法)
    candidates = [name]
    if "." in name:
        candidates.append(name.rsplit(".", 1)[1])
    try:
        con = _connect(project_id)
        try:
            ids: list[Any] = []
            for cand in candidates:
                ids = [r["id"] for r in con.execute("SELECT id FROM nodes WHERE name = ? LIMIT 5", (cand,))]
                if ids:
                    break
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

    def __init__(self, project_id: str | None = None) -> None:
        self.project_id = project_id

    def run(self, args: dict[str, Any]) -> ToolResult:
        return _relations((args or {}).get("name", "").strip(), incoming=True, project_id=self.project_id)


class CodegraphCalleesTool(Tool):
    name = "codegraph_callees"
    description = "找一个符号引用了谁(它指向哪些符号)。入参 name=符号名。"
    input_schema = {
        "type": "object",
        "properties": {"name": {"type": "string", "description": "符号名"}},
        "required": ["name"],
    }

    def __init__(self, project_id: str | None = None) -> None:
        self.project_id = project_id

    def run(self, args: dict[str, Any]) -> ToolResult:
        return _relations((args or {}).get("name", "").strip(), incoming=False, project_id=self.project_id)


def register_into(registry, project_id: str | None = None) -> None:
    registry.register(CodegraphSearchTool(project_id))
    registry.register(CodegraphCallersTool(project_id))
    registry.register(CodegraphCalleesTool(project_id))
