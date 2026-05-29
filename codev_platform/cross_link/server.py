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
import contextvars
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
from codev_platform.core.paths import cross_link_db_path

# CROSS_LINK_DB 显式覆盖所有 project (stdio 调试用); 否则按 project_id 解析 per-project DB。
# Per-project DB only. NO legacy unprefixed fallback: the unprefixed cross_layer.sqlite
# is openclaw's historical data, so falling back would make every OTHER project serve
# openclaw's chains (cross-project data bleed, found 2026-05-28).
_explicit_db = os.getenv("CROSS_LINK_DB")
_EXPLICIT_KEY = "__explicit__"  # 无 project 时的连接缓存键 (CROSS_LINK_DB 覆盖)

if _explicit_db:
    print(
        f"[cross_link.mcp_server] DB explicit override via CROSS_LINK_DB: {_explicit_db}",
        file=sys.stderr,
        flush=True,
    )


def _resolve_default_project() -> str | None:
    """import 期 best-effort 解析单 project (stdio 模式默认)。

    HTTP daemon 多租户, 每请求带 ?project_id=, 不依赖此值; 故解析失败**不退出**
    (从平台仓跑 daemon 时 resolve_local 可能解析到平台自身或失败, 都无所谓)。
    stdio 模式真缺 project 时, main() 会硬失败。
    """
    if _explicit_db:
        return None
    try:
        return resolve_local()
    except ProjectIdError:
        return None


PROJECT_ID = _resolve_default_project()


def _db_path_for(pid: str | None) -> Path:
    """project_id -> cross_layer.sqlite 路径。CROSS_LINK_DB 覆盖时忽略 pid。"""
    if _explicit_db:
        return Path(_explicit_db)
    return cross_link_db_path(pid)

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
# 连接（per-project, lazy 初始化）—— 多租户: 一 daemon 服务多 project_id,
# 每 SSE session 用 contextvar 绑定 project_id, 路由到对应 cross_layer.sqlite。
# (镜像 chroma daemon 的 _current_project_id contextvar 模式)
# ----------------------------------------------------------------------

_conns: dict[str, sqlite3.Connection] = {}   # project_id -> sqlite conn
_init_errors: dict[str, str] = {}            # project_id -> 初始化失败原因

# asyncio 单线程, dict 操作原子, 不上锁。stdio 模式 contextvar 不设 → fallback PROJECT_ID。
_current_project_id: contextvars.ContextVar["str | None"] = contextvars.ContextVar(
    "_cross_link_project_id", default=None
)


def _active_pid() -> str:
    """当前请求的 project_id: contextvar(HTTP per-session) > 默认 PROJECT_ID > explicit 键。"""
    pid = _current_project_id.get() or PROJECT_ID
    return pid if pid is not None else _EXPLICIT_KEY


def _ensure_conn_for(pid: str) -> sqlite3.Connection | None:
    """按 project_id 取/建 sqlite 连接(缓存)。缺 DB / 加载失败 → None + 记 _init_errors。"""
    conn = _conns.get(pid)
    if conn is not None:
        return conn
    if pid in _init_errors:
        return None
    dbp = _db_path_for(None if pid == _EXPLICIT_KEY else pid)
    try:
        _flog(f"[init] pid={pid} db_path={dbp}")
        if not dbp.exists():
            _init_errors[pid] = (
                f"cross_layer DB 不存在: {dbp}; 先跑 python -m codev_platform.cross_link.build_index"
            )
            _flog(f"[init] ERROR: {_init_errors[pid]}")
            return None
        conn = sqlite3.connect(dbp)
        conn.row_factory = sqlite3.Row
        nodes = conn.execute("SELECT COUNT(*) FROM nodes").fetchone()[0]
        edges = conn.execute("SELECT COUNT(*) FROM edges").fetchone()[0]
        meta_row = conn.execute(
            "SELECT value FROM build_meta WHERE key='last_build_at'"
        ).fetchone()
        last_build = meta_row[0] if meta_row else "?"
        _flog(f"[init] pid={pid} loaded nodes={nodes} edges={edges} last_build={last_build}")
        _conns[pid] = conn
        return conn
    except Exception as exc:
        _init_errors[pid] = f"cross_layer DB 加载失败: {exc!s}"
        _flog(f"[init] ERROR: {_init_errors[pid]}")
        return None


def _current_conn() -> sqlite3.Connection | None:
    """当前 contextvar 绑定的 project 的连接。"""
    return _ensure_conn_for(_active_pid())


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
            "project_id": _active_pid(),
            "tool": name,
            "args": {k: str(v)[:80] for k, v in (args or {}).items()},
            "ok": ok,
            "result_chars": sum(len(c.text) for c in result) if result else 0,
            "elapsed_ms": round(_ms, 1),
        })


async def _dispatch(name: str, args: dict) -> list[TextContent]:
    conn = _current_conn()
    if conn is None:
        return _err(_init_errors.get(_active_pid()) or "cross_layer DB 未初始化")

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
                "project_id": _active_pid(),
                "db_path": str(_db_path_for(None if _active_pid() == _EXPLICIT_KEY else _active_pid())),
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
    # stdio 模式: 真缺 project 时硬失败 (HTTP 模式才允许无单 project)。
    if not _explicit_db and PROJECT_ID is None:
        _flog("FATAL(stdio): 无法解析 project_id (env CODEV_PROJECT_ID / .claude/project.json)")
        sys.exit(1)
    _ensure_conn_for(_active_pid())  # 预热（失败不挂 server，让 tool 调用时返回友好错误）
    async with stdio_server() as (read_stream, write_stream):
        await server.run(read_stream, write_stream, server.create_initialization_options())


# ----------------------------------------------------------------------
# HTTP (SSE) transport —— 多租户单端点, 镜像 chroma daemon。
# 业务仓 .mcp.json 走 {"type":"sse","url":"http://<平台>:<port>/sse?project_id=<id>"}
# 不再用文件路径 launcher。
# ----------------------------------------------------------------------

_CL_SSE_PORT = int(os.getenv("CROSS_LINK_SSE_PORT", "18086"))


async def run_http(port: int = _CL_SSE_PORT) -> None:
    from mcp.server.sse import SseServerTransport
    from starlette.applications import Starlette
    from starlette.middleware import Middleware
    from starlette.responses import JSONResponse
    from starlette.routing import Mount, Route
    import uvicorn

    from codev_platform.core.project_id import validate as _pid_validate
    from codev_platform.gateway import AuthMiddleware, build_authenticator
    from codev_platform.core.config import load_config

    sse_transport = SseServerTransport("/messages/")

    async def handle_sse(request):
        # GET /sse?project_id=<pid>: 建流 + 绑定 project_id 到 contextvar (多租户路由)。
        pid_raw = request.query_params.get("project_id")
        if not pid_raw:
            pid = PROJECT_ID  # 向后兼容: 不传则 fallback 默认 (可能 None → tool 报错)
            _flog(f"[sse] no ?project_id=, fallback default {pid}")
        else:
            try:
                pid = _pid_validate(pid_raw)
            except Exception as exc:  # noqa: BLE001
                _flog(f"[sse] reject invalid project_id {pid_raw!r}: {exc!s}")
                return
        token = _current_project_id.set(pid)
        _flog(f"[sse] session start project_id={pid}")
        try:
            async with sse_transport.connect_sse(
                request.scope, request.receive, request._send
            ) as (read_stream, write_stream):
                await server.run(read_stream, write_stream, server.create_initialization_options())
        finally:
            _current_project_id.reset(token)
            _flog(f"[sse] session end project_id={pid}")

    async def health(_request):
        # 存活探针: 报已加载 project 的连接状态 (不强制任何 project 可用)。
        loaded = {pid: (pid in _conns) for pid in set(list(_conns) + list(_init_errors))}
        return JSONResponse({
            "status": "ok",
            "service": "cross-link",
            "default_project_id": PROJECT_ID,
            "loaded_projects": loaded,
            "init_errors": _init_errors,
        })

    app = Starlette(
        debug=False,
        routes=[
            Route("/health", health, methods=["GET"]),
            Route("/sse", handle_sse, methods=["GET"]),
            Mount("/messages/", app=sse_transport.handle_post_message),
        ],
        middleware=[
            Middleware(
                AuthMiddleware,
                authenticator=build_authenticator(load_config()),
                public_paths={"/health"},
            ),
        ],
    )
    _flog(f"[http] cross-link SSE server starting on 127.0.0.1:{port}")
    config = uvicorn.Config(app, host="127.0.0.1", port=port, log_level="warning", access_log=False)
    await uvicorn.Server(config).serve()


if __name__ == "__main__":
    if "--http" in sys.argv:
        # 端口: --port <n> > env CROSS_LINK_SSE_PORT > 默认
        _port = _CL_SSE_PORT
        if "--port" in sys.argv:
            _port = int(sys.argv[sys.argv.index("--port") + 1])
        asyncio.run(run_http(_port))
    else:
        asyncio.run(main())
