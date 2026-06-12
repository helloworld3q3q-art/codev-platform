"""统一图谱 MCP server — 暴露 impact + A1 业务域 + A2 架构层查询给开发端 agent(Claude Code/Codex)。

11 个 tools(薄包装 graph/impact 查询函数, 纯读 sqlite, **不调 LLM**):
  跨层影响 — find_impact / find_table_usage / find_page_dependencies /
             find_impacted_pages / find_api_callers
  A1 业务域 — find_node_domain(节点→域) / list_domain_members(域→成员)
  A2 架构层 — find_arch_role(file→角色) / list_layer_members(角色→file) /
             find_arch_violations(确定性逆向依赖检测)
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
import sys
import traceback

from mcp.server import Server
from mcp.server.stdio import stdio_server
from mcp.types import TextContent, Tool

from codev_platform.core.errors import ErrorCode
from codev_platform.core.project_id import ProjectIdError, resolve_local
from codev_platform.graph import contract_drift as _contract_drift
from codev_platform.graph import impact as _impact
from codev_platform.graph.store import GraphStore, graph_store_path, open_store


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
_stores: dict[str, GraphStore] = {}
_missing: dict[str, str] = {}
_current_project_id: contextvars.ContextVar[str | None] = contextvars.ContextVar(
    "_graph_project_id", default=None
)
# 调用方来源(agent=web 端 chat / dev=开发端 Claude Code/Codex 直调), 给 dashboard MCP 调用分析分桶。
# SSE/Streamable 的 ?client= 指定; 开发端 .mcp.json 不传 → 默认 dev。
_current_client: contextvars.ContextVar[str] = contextvars.ContextVar(
    "_graph_client", default="dev"
)


def authorize_graph_request(pid_raw, identity):
    """graph MCP/SSE 边界统一鉴权(handle_sse 与 streamable bind_mcp_context **共用一处**, 防两处漂移)。

    校验 project_id + can_access(org/项目隔离闸: token 身份 org 不符目标项目 org → 拒)。返回
    (pid, None)=放行(pid 已落实, 缺省回 PROJECT_ID); (None, JSONResponse)=拒绝(400 非法 pid /
    403 越权)。org 隔离的真实判定在 core.acl.can_access(test_acl 覆盖); 本函数把"graph 边界确实
    调它 + 拒绝→403"的 wiring 收成可单测一处(test_graph_mcp_authz 锁死跨 org → 403)。
    """
    from starlette.responses import JSONResponse

    from codev_platform.core.acl import can_access
    from codev_platform.core.audit import audit_access
    from codev_platform.core.config import load_config
    from codev_platform.core.project_id import validate as _pid_validate

    if pid_raw:
        try:
            pid = _pid_validate(pid_raw)
        except Exception as exc:  # noqa: BLE001
            _flog(f"[mcp] reject invalid project_id {pid_raw!r}: {exc!s}")
            return None, JSONResponse(
                {"error": "invalid project_id", "code": ErrorCode.INVALID_PARAMS.value},
                status_code=400)
    else:
        pid = None
    dec = can_access(load_config(), identity, pid)
    audit_access("graph", identity, pid, dec)
    if not dec.allowed:
        _flog(f"[mcp] DENY project_id={pid} via={getattr(identity, 'via', None)}: {dec.reason}")
        return None, JSONResponse(
            {"error": "forbidden", "code": ErrorCode.ACCESS_DENIED.value}, status_code=403)
    return (pid if pid is not None else PROJECT_ID), None


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


def _store_for(pid: str | None) -> GraphStore | None:
    if pid is None:
        return None
    s = _stores.get(pid)
    if s is not None:
        return s
    p = graph_store_path(pid)
    if not p.exists():
        _missing[pid] = f"graph store 不存在: {p}; 先跑 ingest(graph.ingest.ingest_project)"
        return None
    _missing.pop(pid, None)
    try:
        s = open_store(pid)  # 含前向迁移 + schema
        _stores[pid] = s
        return s
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


def _impact_schema(field: str, desc: str) -> dict:
    """反向影响查询 schema: 必填 ref 字段 + 可选 certain_only(只看确定依赖, 滤候选边)。"""
    return {"type": "object", "properties": {
        field: {"type": "string", "description": desc},
        "certain_only": {"type": "boolean",
                         "description": "只返回确定依赖(高置信结构边), 滤掉低置信/名称启发式候选边; 高风险改动结论用。默认 false"}},
        "required": [field]}


def _ok(obj: object) -> list[TextContent]:
    return [TextContent(type="text", text=json.dumps(obj, ensure_ascii=False))]


def _err(msg: str) -> list[TextContent]:
    return _ok({"error": msg})


def _recall_code_tool(pid: str, args: dict) -> dict:
    """跨 lane 融合代码召回工具: recall_code 自开 graph+codegraph store(不用传入的 graph conn),
    序列化为 {hits, count, lanes}。query 缺失 → KeyError(call_tool 映射为'缺必填参数')。"""
    from dataclasses import asdict

    from codev_platform.recall import recall_code
    hits = recall_code(args["query"], pid, limit=int(args.get("limit", 20)))
    return {
        "hits": [asdict(h) for h in hits],
        "count": len(hits),
        "lanes": sorted({lane for h in hits for lane in h.lanes}),  # 实际有贡献的 lane(可观测)
    }


@server.list_tools()
async def list_tools() -> list[Tool]:
    return [
        Tool(name="find_impact",
             description="改某节点(endpoint/表/函数/组件)→ 跨层被波及集合(反向 BFS, 谁依赖它); certain_only=true 只看确定依赖",
             inputSchema=_impact_schema("ref", "节点 id 或 name")),
        Tool(name="find_impact_paths",
             description="改某节点 → **top-N 最强依赖路径**(每节点最优路径, 评分=Π(置信×来源权重)+ 每跳来源/置信证据 + 确定/候选)。要'谁怎样依赖它、按强度排'用它; certain_only 可选",
             inputSchema=_impact_schema("ref", "节点 id 或 name")),
        Tool(name="find_table_usage",
             description="给表名 → 哪些函数/端点/前端用它(反向 BFS); certain_only=true 只看确定依赖",
             inputSchema=_impact_schema("table", "数据库表名")),
        Tool(name="find_page_dependencies",
             description="给前端页/组件 → 它依赖的端点/函数/表(正向 BFS)",
             inputSchema=_str_schema("page", "前端页面/组件 id 或 name")),
        Tool(name="find_impacted_pages",
             description="改前端公共组件 → 哪些页面受影响(传递依赖)",
             inputSchema=_str_schema("component", "前端组件 id 或 name")),
        Tool(name="find_api_callers",
             description="给后端端点 → 哪些前端调它; certain_only=true 只看确定依赖",
             inputSchema=_impact_schema("endpoint", "后端端点 id 或 name")),
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
        Tool(name="find_arch_role",
             description="查某 file 演哪个架构层角色(A2 软节点: controller/service/repository/...)",
             inputSchema=_str_schema("file", "file 路径或节点 id")),
        Tool(name="list_layer_members",
             description="查某架构层角色下有哪些 file(A2 软节点)",
             inputSchema=_str_schema("role", "架构层角色, 如 controller/service/repository")),
        Tool(name="find_arch_violations",
             description="跨层违规检测(确定性: 逆向依赖如 repository→controller; 重构/PR 自检用)",
             inputSchema={"type": "object", "properties": {
                 "limit": {"type": "integer", "description": "返回上限(默认 200)"}}}),
        Tool(name="find_contract_drift",
             description="契约漂移: 列悬空前端调用(调了后端不暴露的接口=接口删/改签名/operationId漂移); 前后端分离/多服务自检",
             inputSchema={"type": "object", "properties": {
                 "limit": {"type": "integer", "description": "返回上限(默认 200)"}}}),
        Tool(name="recall_code",
             description="跨 lane 代码召回: 融合 graph(架构/跨层节点)+ codegraph(符号 FTS)→ 统一可解释排名; 按 query 类型自动调权。一次拿最相关代码实体, 不必分调两工具",
             inputSchema={"type": "object", "properties": {
                 "query": {"type": "string", "description": "检索词"},
                 "limit": {"type": "integer", "description": "返回上限(默认 20)"}},
                 "required": ["query"]}),
    ]


# name → 调用适配器(conn, pid, args) → impact 查询结果。改工具集只动这一处(list_tools 对齐)。
_DISPATCH = {
    "find_impact": lambda c, p, a: _impact.find_impact(
        c, p, a["ref"], certain_only=bool(a.get("certain_only", False))),
    "find_impact_paths": lambda c, p, a: _impact.find_impact_paths(
        c, p, a["ref"], certain_only=bool(a.get("certain_only", False))),
    "find_table_usage": lambda c, p, a: _impact.find_table_usage(
        c, p, a["table"], certain_only=bool(a.get("certain_only", False))),
    "find_page_dependencies": lambda c, p, a: _impact.find_page_dependencies(c, p, a["page"]),
    "find_impacted_pages": lambda c, p, a: _impact.find_impacted_pages(c, p, a["component"]),
    "find_api_callers": lambda c, p, a: _impact.find_api_callers(
        c, p, a["endpoint"], certain_only=bool(a.get("certain_only", False))),
    "find_node_domain": lambda c, p, a: _impact.find_node_domain(c, p, a["ref"]),
    "list_domain_members": lambda c, p, a: _impact.list_domain_members(c, p, a["domain"]),
    "search_nodes": lambda c, p, a: _impact.search_nodes(
        c, p, a["query"], a.get("kind", "all"), int(a.get("limit", 50))),
    "find_arch_role": lambda c, p, a: _impact.find_arch_role(c, p, a["file"]),
    "list_layer_members": lambda c, p, a: _impact.list_layer_members(c, p, a["role"]),
    "find_arch_violations": lambda c, p, a: _impact.find_arch_violations(
        c, p, int(a.get("limit", 200))),
    "find_contract_drift": lambda c, p, a: _contract_drift.find_contract_drift(
        c, p, limit=int(a.get("limit", 200))),
    # recall_code 自开 graph+codegraph store(融合), 忽略传入的 graph conn。
    "recall_code": lambda c, p, a: _recall_code_tool(p, a),
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
        store = _store_for(pid)
        if store is None:
            ok = False
            return _err(_missing.get(pid, f"无 graph store(project_id={pid}); 先 ingest"))
        try:
            return _ok(dispatch(name, args, store, pid))
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

    from codev_platform.core.config import load_config
    from codev_platform.gateway import (
        AuthMiddleware,
        build_authenticator,
        maybe_rate_limit_middleware,
        startup_policy_error,
    )
    from codev_platform.mcp_serve import mcp_bind_host
    from codev_platform.mcp_streamable import (
        ContextualStreamableHTTPASGIApp,
        streamable_lifespan,
    )

    sse_transport = SseServerTransport("/messages/")

    def bind_mcp_context(request):
        pid, denial = authorize_graph_request(
            request.query_params.get("project_id"), getattr(request.state, "identity", None))
        if denial is not None:
            return denial
        client = request.query_params.get("client") or "dev"
        token = _current_project_id.set(pid)
        ctok = _current_client.set(client)

        def reset() -> None:
            _current_project_id.reset(token)
            _current_client.reset(ctok)

        return reset

    async def handle_sse(request):
        pid, denial = authorize_graph_request(
            request.query_params.get("project_id"), getattr(request.state, "identity", None))
        if denial is not None:
            return denial
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
        seen = set(list(_stores) + list(_missing))
        return JSONResponse({
            "status": "ok", "service": "graph", "default_project_id": PROJECT_ID,
            "loaded_projects": {pid: (pid in _stores) for pid in seen},
            "missing_store": _missing,
        })

    _cfg = load_config()
    _host = mcp_bind_host(_cfg)
    # 启动统一认证策略闸(多 dev 串号 / prod 暴露 / 非 loopback bind 未认证)→ 任一命中硬拒。
    _policy_err = startup_policy_error(_cfg, _host)
    if _policy_err:
        _flog(f"[startup] REFUSE: {_policy_err}")
        raise SystemExit(f"graph MCP 拒绝启动:{_policy_err}")
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
    _flog(f"[http] graph SSE/MCP server starting on {_host}:{port}")
    config = uvicorn.Config(app, host=_host, port=port,
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
