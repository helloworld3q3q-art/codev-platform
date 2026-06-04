"""平台 Memory MCP 前门(SSE)—— 开发端编程 agent(Claude Code / Codex)接平台分层记忆。

设计:dev-agent-memory-mcp-design-2026-06-05.md §3。对称 cross-link 的 SSE 骨架,但:
- **不选库**:同一 PG,靠 ``org_id`` 列硬隔离(cross-link 是 per-project sqlite);project_id 仅用于
  project-scope 记忆 + SSE 项目 ACL 闸。
- **身份非对称**:org_id / user_id 从 AuthMiddleware 注入的 ``request.state.identity`` 取并绑 contextvar
  ——**绝不由 client 传**(memory 有 personal + owner + redline 维度,cross-link 只读无此面)。
- **复用** ``deps.get_recall_service()`` + ``deps.get_memory_store()``,零新建 store。

P1 MVP 只读:``recall``(最高频,query-aware 去冲突 top-N)+ ``list_scope``(精确列单作用域,诊断)。
写侧(remember / forget / supersede)留 P2;set_task_state 不暴露(web agent 语义)。

启动:``python -m codev_platform.agent.memory_mcp --http [--port N]``(serve-mcp 编排拉起)。
"""
from __future__ import annotations

import asyncio
import contextvars
import json
import os
import sys
import traceback
from pathlib import Path
from typing import Any

from mcp.server import Server
from mcp.types import TextContent, Tool

from codev_platform.core.errors import ErrorCode, to_mcp_error

# ----------------------------------------------------------------------
# 多租户上下文:org/user 从认证身份绑定(SSE 建流时),project 从 ?project_id=。
# 同 cross-link 的 contextvar 路由思路;asyncio 单线程,dict/contextvar 操作原子,不上锁。
# ----------------------------------------------------------------------
_DEFAULT_ORG = "default"
_DEFAULT_USER = "local"

_ctx_org: contextvars.ContextVar[str] = contextvars.ContextVar("_mem_org", default=_DEFAULT_ORG)
_ctx_user: contextvars.ContextVar[str] = contextvars.ContextVar("_mem_user", default=_DEFAULT_USER)
_ctx_project: contextvars.ContextVar[str | None] = contextvars.ContextVar("_mem_project", default=None)
_ctx_identity: contextvars.ContextVar[Any] = contextvars.ContextVar("_mem_identity", default=None)

_USAGE_LOG = Path(__file__).resolve().parent / "memory_mcp_usage.jsonl"
_MEM_SSE_PORT = int(os.getenv("AGENT_MEMORY_SSE_PORT", "18087"))

server: Server = Server("agent-memory")


def _flog(msg: str) -> None:
    import datetime
    from codev_platform.core.paths import logs_dir
    ts = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    line = f"[{ts}] {msg}"
    try:
        with (logs_dir() / "agent_memory_mcp_server.log").open("a", encoding="utf-8") as f:
            f.write(line + "\n")
    except Exception:  # noqa: BLE001
        pass
    print(line, file=sys.stderr, flush=True)


def _log_usage(record: dict) -> None:
    try:
        with _USAGE_LOG.open("a", encoding="utf-8") as f:
            f.write(json.dumps(record, ensure_ascii=False) + "\n")
    except Exception:  # noqa: BLE001
        pass


def _err(msg: str, code: ErrorCode = ErrorCode.INTERNAL) -> list[TextContent]:
    return [TextContent(type="text", text=json.dumps(to_mcp_error(msg, code), ensure_ascii=False))]


def _ok(payload: Any) -> list[TextContent]:
    return [TextContent(type="text", text=json.dumps(payload, ensure_ascii=False, indent=2))]


def _entry_dict(e) -> dict:
    """MemoryEntry → MCP 返回 dict(只读视图;不含 PG 内部 ttl/extra 细节)。"""
    return {
        "id": e.id, "scope": e.scope, "scope_ref": e.scope_ref,
        "owner_user_id": e.owner_user_id, "content": e.content,
        "kind": e.kind, "topic_key": e.topic_key, "is_redline": e.is_redline,
        "status": e.status, "task_id": e.task_id, "task_state": e.task_state,
    }


# ----------------------------------------------------------------------
# MCP 工具(P1 只读)
# ----------------------------------------------------------------------
SCOPES = ("org", "team", "project", "personal")
_POLICIES = ("personal_first", "org_first")


@server.list_tools()
async def list_tools() -> list[Tool]:
    return [
        Tool(
            name="recall",
            description=(
                "按当前身份(org/user/项目)召回最相关的分层记忆:跨可见作用域取 active → 冲突消解"
                "(redline 优先)→ query 排序 → top-N。换机/重 clone 后用同一 token 即可拿回本人全部"
                "记忆 + 本项目团队约定。开工/换主题时主动调一次。"
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "query": {"type": "string", "description": "检索词(可空;空则按 redline>recency 返回)"},
                    "limit": {"type": "integer", "default": 8, "minimum": 1, "maximum": 50},
                    "task_id": {"type": "string", "description": "可选:当前任务 id,匹配的任务记忆前置"},
                    "policy": {"type": "string", "enum": list(_POLICIES),
                               "description": "冲突消解策略(默认 personal_first)"},
                },
            },
        ),
        Tool(
            name="list_scope",
            description=(
                "精确列出某单一作用域的 active 记忆(诊断用,不做冲突消解)。personal 强制本人;"
                "org/team/project 受 RBAC 角色门禁。常规召回用 recall,本工具用于排查某作用域有哪些条目。"
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "scope": {"type": "string", "enum": list(SCOPES)},
                    "scope_ref": {"type": "string",
                                  "description": "org='org' / team_id / project_id / (personal 自动用本人)"},
                    "limit": {"type": "integer", "default": 100, "minimum": 1, "maximum": 500},
                },
                "required": ["scope"],
            },
        ),
    ]


@server.call_tool()
async def call_tool(name: str, args: dict) -> list[TextContent]:
    import time as _t
    import datetime as _dt
    _t0 = _t.perf_counter()
    ok = True
    result: list[TextContent] = []
    try:
        result = await _dispatch(name, args or {})
        ok = not (len(result) == 1 and result[0].text.lstrip().startswith('{"error"'))
        return result
    finally:
        _log_usage({
            "ts": _dt.datetime.now().strftime("%Y-%m-%dT%H:%M:%S"),
            "org_id": _ctx_org.get(), "project_id": _ctx_project.get(),
            "tool": name, "ok": ok,
            "result_chars": sum(len(c.text) for c in result) if result else 0,
            "elapsed_ms": round((_t.perf_counter() - _t0) * 1000, 1),
        })


async def _dispatch(name: str, args: dict) -> list[TextContent]:
    from codev_platform.agent import deps
    org_id, user_id, project_id = _ctx_org.get(), _ctx_user.get(), _ctx_project.get()
    try:
        if name == "recall":
            svc = deps.get_recall_service()
            if svc is None:
                return _err("memory 未启用(未配 memory.pg_dsn)", ErrorCode.DEPENDENCY_MISSING)
            query = (args.get("query") or "").strip()
            limit = max(1, min(50, int(args.get("limit", 8))))
            policy = args.get("policy") if args.get("policy") in _POLICIES else None
            entries = svc.recall(
                org_id=org_id, user_id=user_id, project_id=project_id,
                query=query, limit=limit, policy=policy, task_id=args.get("task_id"),
            )
            return _ok({"query": query, "count": len(entries),
                        "entries": [_entry_dict(e) for e in entries]})

        if name == "list_scope":
            store = deps.get_memory_store()
            if store is None:
                return _err("memory 未启用(未配 memory.pg_dsn)", ErrorCode.DEPENDENCY_MISSING)
            scope = (args.get("scope") or "").strip()
            if scope not in SCOPES:
                return _err(f"scope must be one of {list(SCOPES)}", ErrorCode.INVALID_PARAMS)
            # personal 强制本人(隐私:不能列他人个人记忆);其它用 client 给的 ref。
            scope_ref = user_id if scope == "personal" else (args.get("scope_ref") or "").strip()
            if scope != "personal" and not scope_ref:
                return _err("scope_ref required for non-personal scope", ErrorCode.INVALID_PARAMS)
            # ACL:与路由 GET /memory 同一道闸(scope_decision + audit_access)。
            from codev_platform.agent.memory_authz import scope_decision
            from codev_platform.core.audit import audit_access
            from codev_platform.core.config import load_config
            ident = _ctx_identity.get()
            dec = scope_decision(load_config(), org_id, user_id, scope, scope_ref, ident)
            audit_access("agent-memory-mcp", ident, scope_ref, dec)
            if not dec.allowed:
                return _err("forbidden: memory scope access denied", ErrorCode.ACCESS_DENIED)
            limit = max(1, min(500, int(args.get("limit", 100))))
            entries = store.list_scope(scope, scope_ref, org_id=org_id, limit=limit)
            return _ok({"scope": scope, "scope_ref": scope_ref, "count": len(entries),
                        "entries": [_entry_dict(e) for e in entries]})

        return _err(f"未知 tool: {name}", ErrorCode.INVALID_PARAMS)
    except Exception as exc:  # noqa: BLE001
        _flog(f"[agent-memory] tool '{name}' error: {traceback.format_exc()}")
        return _err(f"tool '{name}' 执行失败: {exc!s}")


# ----------------------------------------------------------------------
# HTTP (SSE) transport —— 多租户单端点。org/user 从认证身份绑定,project 从 ?project_id=。
# ----------------------------------------------------------------------
async def run_http(port: int = _MEM_SSE_PORT) -> None:
    from mcp.server.sse import SseServerTransport
    from starlette.applications import Starlette
    from starlette.middleware import Middleware
    from starlette.responses import JSONResponse
    from starlette.routing import Mount, Route
    import uvicorn

    from codev_platform.core.acl import can_access
    from codev_platform.core.audit import audit_access
    from codev_platform.core.config import load_config
    from codev_platform.core.project_id import validate as _pid_validate
    from codev_platform.gateway import AuthMiddleware, build_authenticator, maybe_rate_limit_middleware

    sse_transport = SseServerTransport("/messages/")

    async def handle_sse(request):
        ident = getattr(request.state, "identity", None)
        # 身份绑定(红线:org/user 取认证身份,不读 client header/query)。无中间件(dev)→ 默认值。
        org_id = getattr(ident, "org_id", None) or _DEFAULT_ORG
        user_id = getattr(ident, "user_id", None) or _DEFAULT_USER
        # project_id 可选:给了则过项目 ACL 闸(token 越权 → 403);不给则仅 personal/org 召回。
        pid_raw = request.query_params.get("project_id")
        pid: str | None = None
        if pid_raw:
            try:
                pid = _pid_validate(pid_raw)
            except Exception as exc:  # noqa: BLE001
                _flog(f"[sse] reject invalid project_id {pid_raw!r}: {exc!s}")
                return JSONResponse({"error": "invalid project_id",
                                     "code": ErrorCode.INVALID_PARAMS.value}, status_code=400)
            dec = can_access(load_config(), ident, pid)
            audit_access("agent-memory", ident, pid, dec)
            if not dec.allowed:
                _flog(f"[sse] DENY project_id={pid} via={getattr(ident,'via',None)}: {dec.reason}")
                return JSONResponse({"error": "forbidden",
                                     "code": ErrorCode.ACCESS_DENIED.value}, status_code=403)
        t_org = _ctx_org.set(org_id)
        t_user = _ctx_user.set(user_id)
        t_pid = _ctx_project.set(pid)
        t_id = _ctx_identity.set(ident)
        _flog(f"[sse] session start org={org_id} user={user_id} project={pid}")
        try:
            async with sse_transport.connect_sse(
                request.scope, request.receive, request._send
            ) as (read_stream, write_stream):
                await server.run(read_stream, write_stream, server.create_initialization_options())
        finally:
            _ctx_org.reset(t_org)
            _ctx_user.reset(t_user)
            _ctx_project.reset(t_pid)
            _ctx_identity.reset(t_id)
            _flog(f"[sse] session end org={org_id} user={user_id} project={pid}")

    async def healthz(_request):
        return JSONResponse({"status": "ok", "service": "agent-memory"})

    async def platform_status(_request):
        from codev_platform.agent import deps
        return JSONResponse({
            "status": "ok", "service": "agent-memory",
            "memory_enabled": deps.get_memory_store() is not None,
            "rbac_enabled": deps.get_rbac_store() is not None,
        })

    _cfg = load_config()
    _mw = [Middleware(AuthMiddleware, authenticator=build_authenticator(_cfg),
                      public_paths={"/healthz", "/health"})]
    _rl = maybe_rate_limit_middleware(_cfg)
    if _rl is not None:
        _mw.append(_rl)

    app = Starlette(
        debug=False,
        routes=[
            Route("/healthz", healthz, methods=["GET"]),
            Route("/health", healthz, methods=["GET"]),
            Route("/platform/status", platform_status, methods=["GET"]),
            Route("/sse", handle_sse, methods=["GET"]),
            Mount("/messages/", app=sse_transport.handle_post_message),
        ],
        middleware=_mw,
    )
    _flog(f"[http] agent-memory SSE server starting on 127.0.0.1:{port}")
    config = uvicorn.Config(app, host="127.0.0.1", port=port, log_level="warning", access_log=False)
    await uvicorn.Server(config).serve()


if __name__ == "__main__":
    _port = _MEM_SSE_PORT
    if "--port" in sys.argv:
        _port = int(sys.argv[sys.argv.index("--port") + 1])
    asyncio.run(run_http(_port))
