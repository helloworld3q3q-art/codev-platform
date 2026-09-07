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
from codev_platform.core.repos import RepoSpec, project_repo_specs

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
    # 单项目兼容 cwd 走查; 先过 token 模式守卫(server 部署禁 cwd fallback —— 否则会静默命中
    # 平台进程所在仓的 .codegraph = 越权读别项目, 与 _project.resolve_project_id 同一 Phase 0 底座)
    from codev_platform.agent.tools._project import forbid_cwd_fallback_in_token_mode
    forbid_cwd_fallback_in_token_mode()
    cur = Path.cwd().resolve()
    for d in (cur, *cur.parents):
        cand = d / ".codegraph" / "codegraph.db"
        if cand.is_file():
            return cand
    return None


def _find_dbs(project_id: str | None = None) -> list[tuple[RepoSpec, Path]]:
    """定位一个逻辑项目的全部 codegraph.db。主仓保持旧路径语义, extra repo 走 repo/.codegraph。"""
    if project_id:
        out: list[tuple[RepoSpec, Path]] = []
        try:
            specs = project_repo_specs(project_id)
        except Exception:
            specs = []
        for spec in specs:
            cand = spec.codegraph_db
            if cand.is_file():
                out.append((spec, cand))
                continue
            if spec.is_main:
                # 兼容集中路径但业务仓 .codegraph junction 尚未存在的旧部署。
                from codev_platform.core.paths import codegraph_db_path
                central = codegraph_db_path(project_id)
                if central.is_file():
                    out.append((spec, central))
        if out:
            return out

    db = _find_db(project_id)
    if db is None:
        return []
    root = db.parent.parent if db.parent.name == "codegraph" else db.parent.parent
    return [(RepoSpec(root=root, is_main=True, source_project_id=project_id), db)]


def _connect(project_id: str | None = None) -> sqlite3.Connection:
    db = _find_db(project_id)
    if db is None:
        hint = f"project '{project_id}' " if project_id else ""
        raise FileNotFoundError(f"未找到 {hint}.codegraph/codegraph.db(codegraph 未建索引?)")
    con = sqlite3.connect(f"file:{db}?mode=ro", uri=True)
    con.row_factory = sqlite3.Row
    return con


def _connect_db(db: Path) -> sqlite3.Connection:
    con = sqlite3.connect(f"file:{db}?mode=ro", uri=True)
    con.row_factory = sqlite3.Row
    return con


def _node_brief(r: sqlite3.Row, spec: RepoSpec | None = None) -> dict[str, Any]:
    file_path = r["file_path"]
    loc_file = spec.local_file(file_path) if spec is not None else file_path
    return {
        "name": r["name"],
        "kind": r["kind"],
        "loc": f"{loc_file}:{r['start_line']}",
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
            rows: list[tuple[sqlite3.Row, RepoSpec]] = []
            for spec, db in _find_dbs(self.project_id):
                con = _connect_db(db)
                try:
                    got = con.execute(
                        "SELECT n.* FROM nodes_fts f JOIN nodes n ON n.id = f.id "
                        "WHERE nodes_fts MATCH ? LIMIT ?",
                        (_fts_query(q), _MAX_ROWS),
                    ).fetchall()
                    if not got:
                        # 回退:按最后一个标识符 token 做 LIKE(应对 FTS 分词不命中)
                        import re as _re
                        toks = _re.findall(r"[A-Za-z0-9_]+", q)
                        needle = toks[-1] if toks else q
                        got = con.execute(
                            "SELECT * FROM nodes WHERE name LIKE ? LIMIT ?",
                            (f"%{needle}%", _MAX_ROWS),
                        ).fetchall()
                    rows.extend((r, spec) for r in got)
                finally:
                    con.close()
            if not rows:
                dbs = _find_dbs(self.project_id)
                if not dbs:
                    hint = f"project '{self.project_id}' " if self.project_id else ""
                    raise FileNotFoundError(f"未找到 {hint}.codegraph/codegraph.db(codegraph 未建索引?)")
        except Exception as e:  # noqa: BLE001
            return ToolResult(call_id="", content=f"codegraph 查询失败: {e}", is_error=True)
        # 排序:精确名命中 > 名字含 query token > 其余;同档 file/import 排后(优先 class/function/method)
        import re as _re
        toks = [t.lower() for t in _re.findall(r"[A-Za-z0-9_]+", q)]
        kind_rank = {"class": 0, "function": 0, "method": 0, "interface": 0}

        def _score(item: tuple[sqlite3.Row, RepoSpec]) -> tuple:
            r, spec = item
            nm = (r["name"] or "").lower()
            exact = 0 if nm in toks else 1
            contains = 0 if any(t in nm for t in toks) else 1
            repo_rank = 0 if spec.is_main else 1
            return (exact, contains, kind_rank.get(r["kind"], 5), repo_rank)

        rows = sorted(rows, key=_score)[:_MAX_ROWS]
        out = [_node_brief(r, spec) for r, spec in rows]
        return ToolResult(call_id="", content=json.dumps(out, ensure_ascii=False, separators=(",", ":")))


def _relations(name: str, incoming: bool, project_id: str | None = None) -> ToolResult:
    """incoming=True 找 callers(谁指向它);False 找 callees(它指向谁)."""
    if not name:
        return ToolResult(call_id="", content="缺少 name 参数", is_error=True)
    # 支持 Class.method 限定名:先按全名,再退到点号后的裸名(codegraph 多按裸名存方法)
    candidates = [name]
    if "." in name:
        candidates.append(name.rsplit(".", 1)[1])
    try:
        dbs = _find_dbs(project_id)
        if not dbs:
            hint = f"project '{project_id}' " if project_id else ""
            raise FileNotFoundError(f"未找到 {hint}.codegraph/codegraph.db(codegraph 未建索引?)")
        rows: list[tuple[sqlite3.Row, RepoSpec]] = []
        found_symbol = False
        for spec, db in dbs:
            con = _connect_db(db)
            try:
                ids: list[Any] = []
                for cand in candidates:
                    ids = [r["id"] for r in con.execute("SELECT id FROM nodes WHERE name = ? LIMIT 5", (cand,))]
                    if ids:
                        break
                if not ids:
                    continue
                found_symbol = True
                ph = ",".join("?" * len(ids))
                if incoming:
                    sql = (f"SELECT n.name, n.kind, n.file_path, n.start_line, e.kind AS edge "
                           f"FROM edges e JOIN nodes n ON n.id = e.source WHERE e.target IN ({ph}) LIMIT ?")
                else:
                    sql = (f"SELECT n.name, n.kind, n.file_path, n.start_line, e.kind AS edge "
                           f"FROM edges e JOIN nodes n ON n.id = e.target WHERE e.source IN ({ph}) LIMIT ?")
                rows.extend((r, spec) for r in con.execute(sql, (*ids, _MAX_ROWS)).fetchall())
            finally:
                con.close()
    except Exception as e:  # noqa: BLE001
        return ToolResult(call_id="", content=f"codegraph 查询失败: {e}", is_error=True)
    if not found_symbol:
        return ToolResult(call_id="", content=f"未找到符号: {name}")
    if not rows:
        rel = "调用方" if incoming else "被调用项"
        return ToolResult(call_id="", content=f"{name} 无{rel}记录。")
    out = [{"name": r["name"], "kind": r["kind"],
            "loc": f"{spec.local_file(r['file_path'])}:{r['start_line']}", "edge": r["edge"]}
           for r, spec in rows[:_MAX_ROWS]]
    return ToolResult(call_id="", content=json.dumps(out, ensure_ascii=False, separators=(",", ":")))


def _trace(name: str, incoming: bool, depth: int, project_id: str | None = None) -> ToolResult:
    """多跳 BFS 调用链:沿 edges 表把 callers(incoming)/callees(!incoming)展开到 depth 层,
    一次返回逐层链。替代 agent 手动逐跳调 codegraph_callers(多跳题省步=省 cache-miss 主成本)。
    每层去重 + 限 _MAX_ROWS 防爆;访问过的节点不重复展开(防环)。"""
    if not name:
        return ToolResult(call_id="", content="缺少 name 参数", is_error=True)
    depth = max(1, min(int(depth or 3), 6))  # clamp 1..6, 默认 3
    candidates = [name]
    if "." in name:
        candidates.append(name.rsplit(".", 1)[1])
    try:
        dbs = _find_dbs(project_id)
        if not dbs:
            hint = f"project '{project_id}' " if project_id else ""
            raise FileNotFoundError(f"未找到 {hint}.codegraph/codegraph.db(codegraph 未建索引?)")
        levels: list[list[dict[str, Any]]] = []
        found_symbol = False
        for spec, db in dbs:
            con = _connect_db(db)
            try:
                frontier: list[Any] = []
                for cand in candidates:
                    frontier = [r["id"] for r in con.execute(
                        "SELECT id FROM nodes WHERE name = ? LIMIT 5", (cand,))]
                    if frontier:
                        break
                if not frontier:
                    continue
                found_symbol = True
                visited = set(frontier)
                for hop in range(depth):
                    if not frontier:
                        break
                    ph = ",".join("?" * len(frontier))
                    col_in, col_out = ("e.target", "e.source") if incoming else ("e.source", "e.target")
                    sql = (f"SELECT n.id, n.name, n.kind, n.file_path, n.start_line, e.kind AS edge "
                           f"FROM edges e JOIN nodes n ON n.id = {col_out} "
                           f"WHERE {col_in} IN ({ph}) LIMIT ?")
                    rows = con.execute(sql, (*frontier, _MAX_ROWS)).fetchall()
                    level: list[dict[str, Any]] = []
                    nxt: list[Any] = []
                    for r in rows:
                        if r["id"] in visited:
                            continue
                        visited.add(r["id"])
                        nxt.append(r["id"])
                        level.append({"name": r["name"], "kind": r["kind"],
                                      "loc": f"{spec.local_file(r['file_path'])}:{r['start_line']}",
                                      "edge": r["edge"]})
                    if not level:
                        break
                    while len(levels) <= hop:
                        levels.append([])
                    levels[hop].extend(level)
                    frontier = nxt
            finally:
                con.close()
        if not found_symbol:
            return ToolResult(call_id="", content=f"未找到符号: {name}")
        levels = [level[:_MAX_ROWS] for level in levels if level]
    except Exception as e:  # noqa: BLE001
        return ToolResult(call_id="", content=f"codegraph 查询失败: {e}", is_error=True)
    if not levels:
        rel = "调用方" if incoming else "被调用项"
        return ToolResult(call_id="", content=f"{name} 无{rel}记录。")
    out = {"symbol": name, "direction": "callers" if incoming else "callees",
           "depth": len(levels), "levels": {f"hop{i + 1}": lv for i, lv in enumerate(levels)}}
    return ToolResult(call_id="", content=json.dumps(out, ensure_ascii=False, separators=(",", ":")))


class CodegraphTraceTool(Tool):
    name = "codegraph_trace"
    description = (
        "多跳调用链:一次返回某符号的逐层 callers 或 callees(默认 3 层),省去手动逐跳调 "
        "codegraph_callers/callees。问'X 一路被谁用到/X 一路调到哪'、多跳影响面时用它。"
        "入参 name=符号名;direction=callers(谁用它,默认)|callees(它用谁);depth=层数(默认 3,上限 6)。"
    )
    input_schema = {
        "type": "object",
        "properties": {
            "name": {"type": "string", "description": "符号名"},
            "direction": {"type": "string", "enum": ["callers", "callees"],
                          "description": "callers=谁指向它(默认)/ callees=它指向谁"},
            "depth": {"type": "integer", "description": "遍历层数,默认 3,上限 6"},
        },
        "required": ["name"],
    }

    def __init__(self, project_id: str | None = None) -> None:
        self.project_id = project_id

    def run(self, args: dict[str, Any]) -> ToolResult:
        a = args or {}
        incoming = (a.get("direction", "callers") != "callees")
        return _trace(a.get("name", "").strip(), incoming=incoming,
                      depth=a.get("depth", 3), project_id=self.project_id)


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
    registry.register(CodegraphTraceTool(project_id))  # 多跳链(省多步手爬, loop 成本第一刀)
