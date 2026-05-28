"""Claude Code MCP server — 暴露 cross-layer KG 给 AI。

提供 4 个 tools：
- find_table_refs       一站汇总某 table 的所有跨层引用（Java + Python + Flyway）
- find_endpoint_link    前后端双向：传 frontend_api 名查 Java endpoint，或反之
- search_nodes          模糊搜索节点（按 name + kind 过滤）
- cross_link_stats      数据库概览（nodes/edges by kind/rel + build_meta）

启动方式（由 .git/hooks 之外的 launcher 调）：
    python tools/cross_link/mcp_server.py

stdio 协议；启动日志写 stderr 不污染。
依赖：mcp（标准库 + sqlite3）。无需 sqlglot/javalang（这些只构建索引时用）。
"""
from __future__ import annotations

import asyncio
import json
import os
import sqlite3
import sys
import traceback
from pathlib import Path
from typing import Any

from mcp.server import Server
from mcp.server.stdio import stdio_server
from mcp.types import TextContent, Tool

# ----------------------------------------------------------------------
# 数据库路径 + 日志
# ----------------------------------------------------------------------

# 多项目隔离 (与 schema.py 同源):
#   data/codegraph_ext/<project_id>/cross_layer.sqlite (新)
#   data/codegraph_ext/cross_layer.sqlite             (legacy fallback)
# CROSS_LINK_DB 环境变量可显式覆盖, 否则按 project_id 解析。
from codev_platform.core.project_id import ProjectIdError, resolve_local
from codev_platform.core.paths import (
    cross_link_db_path,
    cross_link_legacy_db_path,
)

_explicit_db = os.getenv("CROSS_LINK_DB")
if _explicit_db:
    DB_PATH = Path(_explicit_db)
    PROJECT_ID = None  # CROSS_LINK_DB 显式覆盖, 跳过 project_id 解析
    print(
        f"[cross_link.mcp_server] DB explicit override via CROSS_LINK_DB: {DB_PATH}",
        file=sys.stderr,
        flush=True,
    )
else:
    try:
        PROJECT_ID = resolve_local()
    except ProjectIdError as _pid_exc:
        print(f"[cross_link.mcp_server] FATAL: {_pid_exc!s}", file=sys.stderr, flush=True)
        sys.exit(1)
    _new_path = cross_link_db_path(PROJECT_ID)
    _legacy_path = cross_link_legacy_db_path()
    if _new_path.exists():
        DB_PATH = _new_path
    elif _legacy_path.exists():
        DB_PATH = _legacy_path
        print(
            f"[cross_link.mcp_server] WARN: 使用 legacy DB {_legacy_path}, "
            f"重跑 build_index.py 后迁移到 {_new_path}。",
            file=sys.stderr,
            flush=True,
        )
    else:
        DB_PATH = _new_path  # 不存在, _ensure_conn 会报"先跑 build_index"

_LOG_FILE = Path(__file__).resolve().parent / "mcp_server.log"
# 使用率埋点:每次 tool 调用一行 JSON,与 chroma/search_recall.jsonl 对齐,供 ai-health 统计
_USAGE_LOG = Path(__file__).resolve().parent / "cross_link_usage.jsonl"


def _flog(msg: str) -> None:
    """同时写文件 + stderr，便于 ai-health 观察"""
    import datetime
    ts = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    line = f"[{ts}] {msg}"
    try:
        with _LOG_FILE.open("a", encoding="utf-8") as f:
            f.write(line + "\n")
    except Exception:
        pass
    print(line, file=sys.stderr, flush=True)


def _log_usage(record: dict) -> None:
    """JSONL 使用率日志:每行一次 tool 调用。失败静默(不阻塞查询)。"""
    try:
        with _USAGE_LOG.open("a", encoding="utf-8") as f:
            f.write(json.dumps(record, ensure_ascii=False) + "\n")
    except Exception:
        pass


# ----------------------------------------------------------------------
# 连接（lazy 初始化）
# ----------------------------------------------------------------------

_conn: sqlite3.Connection | None = None
_init_error: str | None = None


def _ensure_conn() -> sqlite3.Connection | None:
    global _conn, _init_error
    if _conn is not None:
        return _conn
    if _init_error is not None:
        return None
    try:
        _flog(f"[init] db_path={DB_PATH}")
        if not DB_PATH.exists():
            _init_error = f"cross_layer DB 不存在: {DB_PATH}；先跑 python -m cross_link.build_index"
            _flog(f"[init] ERROR: {_init_error}")
            return None
        _conn = sqlite3.connect(DB_PATH)
        _conn.row_factory = sqlite3.Row
        # 摸下基础统计
        nodes = _conn.execute("SELECT COUNT(*) FROM nodes").fetchone()[0]
        edges = _conn.execute("SELECT COUNT(*) FROM edges").fetchone()[0]
        meta_row = _conn.execute(
            "SELECT value FROM build_meta WHERE key='last_build_at'"
        ).fetchone()
        last_build = meta_row[0] if meta_row else "?"
        _flog(f"[init] loaded nodes={nodes} edges={edges} last_build={last_build}")
        return _conn
    except Exception as exc:
        _init_error = f"cross_layer DB 加载失败: {exc!s}"
        _flog(f"[init] ERROR: {_init_error}")
        return None


# ----------------------------------------------------------------------
# MCP server 定义
# ----------------------------------------------------------------------

server: Server = Server("cross-link")

NODE_KINDS = [
    "table", "column", "flyway_migration",
    "java_method", "java_endpoint",
    "python_method",
    "frontend_api",
    "all",
]


@server.list_tools()
async def list_tools() -> list[Tool]:
    return [
        Tool(
            name="find_table_refs",
            description=(
                "一站汇总某 table 的所有跨层引用（Flyway 定义者 + Java reader/writer/updater + "
                "Python reader/writer/updater）。改字段前必查。"
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "table": {
                        "type": "string",
                        "description": "表名，如 'stock_recommend_result'",
                    },
                },
                "required": ["table"],
            },
        ),
        Tool(
            name="find_endpoint_link",
            description=(
                "前后端双向 endpoint 关联：传 frontend_api 函数名（如 'postStocksPage'）"
                "查它调到的 Java endpoint；传 java_endpoint 名（如 'StockController.page'）"
                "查它被哪些前端调。自动按名称推断方向。"
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "name": {
                        "type": "string",
                        "description": "frontend_api 函数名 或 java_endpoint 全名（ClassName.methodName）",
                    },
                },
                "required": ["name"],
            },
        ),
        Tool(
            name="search_nodes",
            description=(
                "模糊搜索节点（按 name 含子串）。可按 kind 过滤。"
                "返回 [{name, kind, path, line, language, meta}, ...] 最多 50 条。"
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "query": {
                        "type": "string",
                        "description": "节点名子串（不区分大小写）",
                    },
                    "kind": {
                        "type": "string",
                        "enum": NODE_KINDS,
                        "default": "all",
                        "description": "节点类型过滤",
                    },
                    "limit": {
                        "type": "integer",
                        "default": 20,
                        "minimum": 1,
                        "maximum": 50,
                        "description": "返回上限",
                    },
                },
                "required": ["query"],
            },
        ),
        Tool(
            name="cross_link_stats",
            description="数据库概览：nodes/edges by kind/rel + build_meta（含 last_build_at）。",
            inputSchema={"type": "object", "properties": {}},
        ),
    ]


def _err(msg: str) -> list[TextContent]:
    return [TextContent(type="text", text=json.dumps({"error": msg}, ensure_ascii=False))]


def _ok(payload: Any) -> list[TextContent]:
    return [TextContent(type="text", text=json.dumps(payload, ensure_ascii=False, indent=2))]


def _list_edge_sources(
    conn: sqlite3.Connection, table: str, rel: str, src_kind: str,
) -> list[dict]:
    cur = conn.execute(
        """SELECT n.name, n.path, n.line, n.kind, e.confidence, e.evidence, n.meta_json
           FROM nodes t
           JOIN edges e ON e.dst_id = t.id AND e.rel = ?
           JOIN nodes n ON n.id = e.src_id AND n.kind = ?
           WHERE t.kind = 'table' AND t.name = ? AND t.path IS NULL
           ORDER BY n.name, n.line""",
        (rel, src_kind, table),
    )
    out: list[dict] = []
    for r in cur.fetchall():
        meta = json.loads(r["meta_json"]) if r["meta_json"] else {}
        out.append({
            "name": r["name"], "kind": r["kind"],
            "path": r["path"], "line": r["line"],
            "confidence": r["confidence"], "evidence": r["evidence"],
            "meta": meta,
        })
    return out


@server.call_tool()
async def call_tool(name: str, args: dict) -> list[TextContent]:
    """计时 + usage 埋点的薄包装,再分发到 _dispatch(查询逻辑不变)。"""
    import time as _t
    import datetime as _dt
    _t0 = _t.perf_counter()
    ok = True
    result: list[TextContent] = []
    try:
        result = await _dispatch(name, args)
        # _err 返回 [{"error": ...}],据此判失败
        ok = not (len(result) == 1 and result[0].text.lstrip().startswith('{"error"'))
        return result
    finally:
        _ms = (_t.perf_counter() - _t0) * 1000
        _log_usage({
            "ts": _dt.datetime.now().strftime("%Y-%m-%dT%H:%M:%S"),
            "project_id": PROJECT_ID,
            "tool": name,
            "args": {k: str(v)[:80] for k, v in (args or {}).items()},
            "ok": ok,
            "result_chars": sum(len(c.text) for c in result) if result else 0,
            "elapsed_ms": round(_ms, 1),
        })


async def _dispatch(name: str, args: dict) -> list[TextContent]:
    conn = _ensure_conn()
    if conn is None:
        return _err(_init_error or "cross_layer DB 未初始化")

    import time as _t
    _t0 = _t.perf_counter()
    try:
        if name == "find_table_refs":
            table = (args.get("table") or "").strip()
            if not table:
                return _err("table 不能为空")
            _flog(f"[find_table_refs] table={table!r}")
            # Flyway definers
            cur = conn.execute(
                """SELECT n.name, n.path FROM nodes t
                   JOIN edges e ON e.dst_id = t.id AND e.rel = 'defines_table'
                   JOIN nodes n ON n.id = e.src_id AND n.kind = 'flyway_migration'
                   WHERE t.kind = 'table' AND t.name = ? AND t.path IS NULL
                   ORDER BY n.name""",
                (table,),
            )
            definers = [{"name": r["name"], "path": r["path"]} for r in cur.fetchall()]
            payload = {
                "table": table,
                "definers":         definers,
                "java_readers":     _list_edge_sources(conn, table, "queries_table", "java_method"),
                "java_writers":     _list_edge_sources(conn, table, "writes_table",  "java_method"),
                "java_updaters":    _list_edge_sources(conn, table, "updates_table", "java_method"),
                "python_readers":   _list_edge_sources(conn, table, "reads_table",   "python_method"),
                "python_writers":   _list_edge_sources(conn, table, "writes_table",  "python_method"),
                "python_updaters":  _list_edge_sources(conn, table, "updates_table", "python_method"),
            }
            counts = {k: len(v) for k, v in payload.items() if isinstance(v, list)}
            _ms = (_t.perf_counter() - _t0) * 1000
            _flog(f"[find_table_refs] counts={counts} took={_ms:.1f}ms")
            return _ok(payload)

        if name == "find_endpoint_link":
            qname = (args.get("name") or "").strip()
            if not qname:
                return _err("name 不能为空")
            _flog(f"[find_endpoint_link] name={qname!r}")
            # 判断是 frontend_api 还是 java_endpoint
            cur = conn.execute(
                "SELECT id, kind, path, line, meta_json FROM nodes WHERE name = ? "
                "AND kind IN ('frontend_api', 'java_endpoint')",
                (qname,),
            )
            src_rows = cur.fetchall()
            if not src_rows:
                return _err(f"未找到节点 '{qname}'（kind 必须是 frontend_api / java_endpoint）")
            results: list[dict] = []
            for src in src_rows:
                meta = json.loads(src["meta_json"]) if src["meta_json"] else {}
                if src["kind"] == "frontend_api":
                    # frontend → java
                    cur2 = conn.execute(
                        """SELECT j.name, j.path, j.line, j.meta_json, e.confidence, e.evidence
                           FROM nodes j
                           JOIN edges e ON e.dst_id = j.id AND e.rel = 'calls_api'
                           WHERE e.src_id = ? AND j.kind = 'java_endpoint'""",
                        (src["id"],),
                    )
                    targets = []
                    for r in cur2.fetchall():
                        tmeta = json.loads(r["meta_json"]) if r["meta_json"] else {}
                        targets.append({
                            "name": r["name"], "path": r["path"], "line": r["line"],
                            "url": tmeta.get("url"),
                            "confidence": r["confidence"], "evidence": r["evidence"],
                        })
                    results.append({
                        "node": qname, "kind": "frontend_api",
                        "url": meta.get("url"),
                        "path": src["path"], "line": src["line"],
                        "direction": "frontend -> java",
                        "targets": targets,
                    })
                else:  # java_endpoint
                    cur2 = conn.execute(
                        """SELECT f.name, f.path, f.line, f.meta_json, e.confidence, e.evidence
                           FROM nodes f
                           JOIN edges e ON e.src_id = f.id AND e.rel = 'calls_api'
                           WHERE e.dst_id = ? AND f.kind = 'frontend_api'""",
                        (src["id"],),
                    )
                    callers = []
                    for r in cur2.fetchall():
                        fmeta = json.loads(r["meta_json"]) if r["meta_json"] else {}
                        callers.append({
                            "name": r["name"], "path": r["path"], "line": r["line"],
                            "url": fmeta.get("url"),
                            "confidence": r["confidence"], "evidence": r["evidence"],
                        })
                    results.append({
                        "node": qname, "kind": "java_endpoint",
                        "url": meta.get("url"),
                        "path": src["path"], "line": src["line"],
                        "direction": "java <- frontend",
                        "callers": callers,
                    })
            _ms = (_t.perf_counter() - _t0) * 1000
            _flog(f"[find_endpoint_link] results={len(results)} took={_ms:.1f}ms")
            return _ok(results if len(results) > 1 else results[0])

        if name == "search_nodes":
            query = (args.get("query") or "").strip()
            if not query:
                return _err("query 不能为空")
            kind = args.get("kind", "all")
            limit = max(1, min(50, int(args.get("limit", 20))))
            _flog(f"[search_nodes] q={query!r} kind={kind} limit={limit}")
            params: list[Any] = [f"%{query}%"]
            sql = ("SELECT name, kind, path, line, language, meta_json FROM nodes "
                   "WHERE name LIKE ? COLLATE NOCASE")
            if kind != "all":
                sql += " AND kind = ?"
                params.append(kind)
            sql += " ORDER BY kind, name LIMIT ?"
            params.append(limit)
            cur = conn.execute(sql, params)
            out: list[dict] = []
            for r in cur.fetchall():
                meta = json.loads(r["meta_json"]) if r["meta_json"] else {}
                out.append({
                    "name": r["name"], "kind": r["kind"],
                    "path": r["path"], "line": r["line"],
                    "language": r["language"],
                    "meta": meta,
                })
            _ms = (_t.perf_counter() - _t0) * 1000
            _flog(f"[search_nodes] hits={len(out)} took={_ms:.1f}ms")
            return _ok({"query": query, "kind": kind, "hits": out})

        if name == "cross_link_stats":
            nodes_by_kind = {
                r["kind"]: r["c"]
                for r in conn.execute("SELECT kind, COUNT(*) c FROM nodes GROUP BY kind")
            }
            edges_by_rel = {
                r["rel"]: r["c"]
                for r in conn.execute("SELECT rel, COUNT(*) c FROM edges GROUP BY rel")
            }
            meta = {
                r["key"]: r["value"]
                for r in conn.execute("SELECT key, value FROM build_meta")
            }
            payload = {
                "db_path": str(DB_PATH),
                "nodes_by_kind": dict(sorted(nodes_by_kind.items(), key=lambda x: -x[1])),
                "edges_by_rel":  dict(sorted(edges_by_rel.items(), key=lambda x: -x[1])),
                "build_meta": meta,
            }
            return _ok(payload)

        return _err(f"未知 tool: {name}")
    except Exception as exc:
        tb = traceback.format_exc()
        print(f"[cross-link] tool '{name}' error: {tb}", file=sys.stderr)
        return _err(f"tool '{name}' 执行失败: {exc!s}")


async def main() -> None:
    _ensure_conn()  # 预热（失败不挂 server，让 tool 调用时返回友好错误）
    async with stdio_server() as (read_stream, write_stream):
        await server.run(read_stream, write_stream, server.create_initialization_options())


if __name__ == "__main__":
    asyncio.run(main())
