"""统一图谱 MCP server — 暴露 impact + A1 业务域查询给开发端 agent(Claude Code/Codex)。

8 个 tools(薄包装 graph/impact 查询函数, 纯读 sqlite, **不调 LLM**):
  跨层影响 — find_impact / find_table_usage / find_page_dependencies /
             find_impacted_pages / find_api_callers
  A1 业务域 — find_node_domain(节点→域) / list_domain_members(域→成员)
  搜索 — search_nodes(模糊搜节点, 承接退役的 cross-link)

多租户单端点 + ?project_id= 路由(镜像 cross-link)。读 data/graph_store/<pid>.sqlite。
这是 A1 业务域 + 整个统一图谱对开发端 agent 的消费前门(第 5 套平台 MCP)。

启动: python -m codev_platform.graph.mcp_server --http [--port N] (SSE) / 无参 (stdio)。
"""
from __future__ import annotations

import asyncio
import contextvars
import datetime
import json
import os
import sqlite3
import sys
import traceback

from mcp.server import Server
from mcp.server.stdio import stdio_server
from mcp.types import TextContent, Tool

from codev_platform.core.errors import ErrorCode
from codev_platform.core.project_id import ProjectIdError, resolve_local
from codev_platform.graph import impact as _impact
from codev_platform.graph.store import graph_store_path, open_store


def _resolve_default_project() -> str | None:
    try:
        return resolve_local()
    except ProjectIdError:
        return None


PROJECT_ID = _resolve_default_project()


def _flog(msg: str) -> None:
    ts = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    print(f"[{ts}] [graph-mcp] {msg}", file=sys.stderr, flush=True)


# ---- per-project 连接(lazy, 多租户 contextvar 路由) ----
_conns: dict[str, sqlite3.Connection] = {}
_missing: dict[str, str] = {}
_current_project_id: contextvars.ContextVar[str | None] = contextvars.ContextVar(
    "_graph_project_id", default=None
)
# 调用方来源(agent=web 端 chat / dev=开发端 Claude Code/Codex 直调), 给 dashboard MCP 调用分析分桶。
# SSE/Streamable 的 ?client= 指定; 开发端 .mcp.json 不传 → 默认 dev。
_current_client: contextvars.ContextVar[str] = contextvars.ContextVar(
    "_graph_client", default="dev"
)


def _active_pid() -> str | None:
    return _current_project_id.get() or PROJECT_ID


def _log_usage(record: dict) -> None:
    """记一次 graph 工具调用到 graph_usage.jsonl(data_root/logs/, 运行态不进 git),
    给 dashboard 的 MCP 调用分析聚合。写失败静默(不影响查询)。"""
    try:
        from codev_platform.core.paths import logs_dir
        path = logs_dir() / "graph_usage.jsonl"
        with path.open("a", encoding="utf-8") as f:
            f.write(json.dumps(record, ensure_ascii=False) + "\n")
    except Exception:  # noqa: BLE001
        pass


def _conn_for(pid: str | None) -> sqlite3.Connection | None:
    if pid is None:
        return None
    c = _conns.get(pid)
    if c is not None:
        return c
    p = graph_store_path(pid)
    if not p.exists():
        _missing[pid] = f"graph store 不存在: {p}; 先跑 ingest(graph.ingest.ingest_project)"
        return None
    _missing.pop(pid, None)
    try:
        c = open_store(pid)  # 含前向迁移 + schema
        _conns[pid] = c
        return c
    except Exception as exc:  # noqa: BLE001
        _flog(f"[init] pid={pid} open_store 失败: {exc!s}")
        return None


# ---- MCP server ----
server: Server = Server("graph")

_REF_SCHEMA = {
    "type": "object",
    "properties": {"ref": {"type": "string", "description": "节点 id 或 name"}},
    "required": ["ref"],
}


def _str_schema(field: str, desc: str) -> dict:
    return {"type": "object", "properties": {field: {"type": "string", "description": desc}},
            "required": [field]}


def _ok(obj: object) -> list[TextContent]:
    return [TextContent(type="text", text=json.dumps(obj, ensure_ascii=False))]


def _err(msg: str) -> list[TextContent]:
    return _ok({"error": msg})


@server.list_tools()
async def list_tools() -> list[Tool]:
    return [
        Tool(name="find_impact",
             description="改某节点(endpoint/表/函数/组件)→ 跨层被波及集合(反向 BFS, 谁依赖它)",
             inputSchema=_REF_SCHEMA),
        Tool(name="find_table_usage",
             description="给表名 → 哪些函数/端点/前端用它(反向 BFS)",
             inputSchema=_str_schema("table", "数据库表名")),
        Tool(name="find_page_dependencies",
             description="给前端页/组件 → 它依赖的端点/函数/表(正向 BFS)",
             inputSchema=_str_schema("page", "前端页面/组件 id 或 name")),
        Tool(name="find_impacted_pages",
             description="改前端公共组件 → 哪些页面受影响(传递依赖)",
             inputSchema=_str_schema("component", "前端组件 id 或 name")),
        Tool(name="find_api_callers",
             description="给后端端点 → 哪些前端调它",
             inputSchema=_str_schema("endpoint", "后端端点 id 或 name")),
        Tool(name="find_node_domain",
             description="查 endpoint/表属于哪个业务域(A1 LLM 语义标注, 不调 LLM 读已标)",
             inputSchema=_REF_SCHEMA),
        Tool(name="list_domain_members",
             description="查某业务域下有哪些 endpoint/表(A1 软节点)",
             inputSchema=_str_schema("domain", "业务域名, 如 订单/行情")),
        Tool(name="search_nodes",
             description="模糊搜节点(name 含 query, 可选 kind 过滤)—— 找端点/表/函数/业务域(承接 cross-link)",
             inputSchema={"type": "object", "properties": {
                 "query": {"type": "string", "description": "搜索词(匹配 name)"},
                 "kind": {"type": "string",
                          "description": "可选 kind 过滤(backend_endpoint/db_table/... 默认 all)"},
                 "limit": {"type": "integer", "description": "返回上限(默认 50)"}},
                 "required": ["query"]}),
    ]


# name → 调用适配器(conn, pid, args) → impact 查询结果。改工具集只动这一处(list_tools 对齐)。
_DISPATCH = {
    "find_impact": lambda c, p, a: _impact.find_impact(c, p, a["ref"]),
    "find_table_usage": lambda c, p, a: _impact.find_table_usage(c, p, a["table"]),
    "find_page_dependencies": lambda c, p, a: _impact.find_page_dependencies(c, p, a["page"]),
    "find_impacted_pages": lambda c, p, a: _impact.find_impacted_pages(c, p, a["component"]),
    "find_api_callers": lambda c, p, a: _impact.find_api_callers(c, p, a["endpoint"]),
    "find_node_domain": lambda c, p, a: _impact.find_node_domain(c, p, a["ref"]),
    "list_domain_members": lambda c, p, a: _impact.list_domain_members(c, p, a["domain"]),
    "search_nodes": lambda c, p, a: _impact.search_nodes(
        c, p, a["query"], a.get("kind", "all"), int(a.get("limit", 50))),
}


def dispatch(name: str, args: dict, conn, pid: str) -> dict:
    """name → impact 查询(纯函数, 可测, 绕 MCP 装饰器)。未知 tool raise ValueError / 缺参 KeyError。"""
    if name not in _DISPATCH:
        raise ValueError(f"未知 tool: {name}")
    return _DISPATCH[name](conn, pid, args)


@server.call_tool()
async def call_tool(name: str, args: dict) -> list[TextContent]:
    t0 = datetime.datetime.now()
    pid = _active_pid()
    ok = True
    try:
        conn = _conn_for(pid)
        if conn is None:
            ok = False
            return _err(_missing.get(pid, f"无 graph store(project_id={pid}); 先 ingest"))
        try:
            return _ok(dispatch(name, args, conn, pid))
        except KeyError as exc:
            ok = False
            return _err(f"tool '{name}' 缺必填参数: {exc!s}")
        except ValueError as exc:
            ok = False
            return _err(str(exc))
        except Exception as exc:  # noqa: BLE001
            ok = False
            traceback.print_exc()
            return _err(f"tool '{name}' 执行失败: {exc!s}")
    finally:
        _log_usage({
            "ts": t0.strftime("%Y-%m-%dT%H:%M:%S"),
            "project_id": pid,
            "tool": name,
            "client": _current_client.get(),
            "ok": ok,
            "elapsed_ms": round((datetime.datetime.now() - t0).total_seconds() * 1000, 1),
        })


async def main() -> None:
    if PROJECT_ID is None:
        _flog("FATAL(stdio): 无法解析 project_id (env CODEV_PROJECT_ID / .claude/project.json)")
        sys.exit(1)
    async with stdio_server() as (read_stream, write_stream):
        await server.run(read_stream, write_stream, server.create_initialization_options())


# ---- HTTP (SSE) 多租户单端点(镜像 cross-link) ----
_GRAPH_SSE_PORT = int(os.getenv("GRAPH_SSE_PORT", "18092"))


async def run_http(port: int = _GRAPH_SSE_PORT) -> None:
    from mcp.server.sse import SseServerTransport
    from mcp.server.streamable_http_manager import StreamableHTTPSessionManager
    from starlette.applications import Starlette
    from starlette.middleware import Middleware
    from starlette.responses import JSONResponse, Response
    from starlette.routing import Mount, Route
    import uvicorn

    from codev_platform.core.acl import can_access
    from codev_platform.core.audit import audit_access
    from codev_platform.core.config import load_config
    from codev_platform.core.project_id import validate as _pid_validate
    from codev_platform.gateway import (
        AuthMiddleware,
        build_authenticator,
        maybe_rate_limit_middleware,
    )
    from codev_platform.mcp_streamable import (
        ContextualStreamableHTTPASGIApp,
        streamable_lifespan,
    )

    sse_transport = SseServerTransport("/messages/")

    def bind_mcp_context(request):
        pid_raw = request.query_params.get("project_id")
        if pid_raw:
            try:
                pid = _pid_validate(pid_raw)
            except Exception as exc:  # noqa: BLE001
                _flog(f"[mcp] reject invalid project_id {pid_raw!r}: {exc!s}")
                return JSONResponse(
                    {"error": "invalid project_id", "code": ErrorCode.INVALID_PARAMS.value},
                    status_code=400,
                )
        else:
            pid = None
        _ident = getattr(request.state, "identity", None)
        _dec = can_access(load_config(), _ident, pid)
        audit_access("graph", _ident, pid, _dec)
        if not _dec.allowed:
            _flog(f"[mcp] DENY project_id={pid} via={getattr(_ident,'via',None)}: {_dec.reason}")
            return JSONResponse(
                {"error": "forbidden", "code": ErrorCode.ACCESS_DENIED.value},
                status_code=403,
            )
        if pid is None:
            pid = PROJECT_ID
        client = request.query_params.get("client") or "dev"
        token = _current_project_id.set(pid)
        ctok = _current_client.set(client)

        def reset() -> None:
            _current_project_id.reset(token)
            _current_client.reset(ctok)

        return reset

    async def handle_sse(request):
        pid_raw = request.query_params.get("project_id")
        if pid_raw:
            try:
                pid = _pid_validate(pid_raw)
            except Exception as exc:  # noqa: BLE001
                _flog(f"[sse] reject invalid project_id {pid_raw!r}: {exc!s}")
                return JSONResponse(
                    {"error": "invalid project_id", "code": ErrorCode.INVALID_PARAMS.value},
                    status_code=400)
        else:
            pid = None
        _ident = getattr(request.state, "identity", None)
        _dec = can_access(load_config(), _ident, pid)
        audit_access("graph", _ident, pid, _dec)
        if not _dec.allowed:
            return JSONResponse({"error": "forbidden", "code": ErrorCode.ACCESS_DENIED.value},
                                status_code=403)
        if pid is None:
            pid = PROJECT_ID
        client = request.query_params.get("client") or "dev"
        token = _current_project_id.set(pid)
        ctok = _current_client.set(client)
        _flog(f"[sse] session start project_id={pid}")
        try:
            async with sse_transport.connect_sse(
                request.scope, request.receive, request._send
            ) as (read_stream, write_stream):
                await server.run(read_stream, write_stream,
                                 server.create_initialization_options())
        finally:
            _current_project_id.reset(token)
            _current_client.reset(ctok)
        # SDK 强制(mcp/server/sse.py docstring): SSE 结束/客户端断开后必返 Response,
        # 否则 starlette 1.2.0 request_response 走 `await None(...)` → TypeError 噪声日志。
        return Response()

    async def healthz(_request):
        return JSONResponse({"status": "ok", "service": "graph"})

    async def platform_status(_request):
        seen = set(list(_conns) + list(_missing))
        return JSONResponse({
            "status": "ok", "service": "graph", "default_project_id": PROJECT_ID,
            "loaded_projects": {pid: (pid in _conns) for pid in seen},
            "missing_store": _missing,
        })

    _cfg = load_config()
    _mw = [Middleware(AuthMiddleware, authenticator=build_authenticator(_cfg),
                      public_paths={"/healthz", "/health"})]
    _rl = maybe_rate_limit_middleware(_cfg)
    if _rl is not None:
        _mw.append(_rl)

    # stateless=True: 每请求新鲜绑定 project_id contextvar (与 /sse 等价)。stateful 只在 session
    # initialize 时绑一次, 会让多租户路由退化成"一 session 锁一个项目"。
    mcp_session_manager = StreamableHTTPSessionManager(server, stateless=True)
    mcp_http_app = ContextualStreamableHTTPASGIApp(mcp_session_manager, bind_mcp_context)

    app = Starlette(
        debug=False,
        routes=[
            Route("/healthz", healthz, methods=["GET"]),
            Route("/health", healthz, methods=["GET"]),
            Route("/platform/status", platform_status, methods=["GET"]),
            Route("/mcp", mcp_http_app, methods=["GET", "POST", "DELETE"]),
            Route("/sse", handle_sse, methods=["GET"]),
            Mount("/messages/", app=sse_transport.handle_post_message),
        ],
        middleware=_mw,
        lifespan=streamable_lifespan(mcp_session_manager),
    )
    _flog(f"[http] graph SSE/MCP server starting on 127.0.0.1:{port}")
    config = uvicorn.Config(app, host="127.0.0.1", port=port,
                            log_level="warning", access_log=False)
    await uvicorn.Server(config).serve()


if __name__ == "__main__":
    if "--http" in sys.argv:
        _port = _GRAPH_SSE_PORT
        if "--port" in sys.argv:
            _port = int(sys.argv[sys.argv.index("--port") + 1])
        asyncio.run(run_http(_port))
    else:
        asyncio.run(main())
