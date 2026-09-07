"""codegraph 多租户代理 MCP server —— 把外部 `codegraph serve --mcp` (per-repo stdio,
无 HTTP) 包成平台统一的多租户 SSE 端点, 与 chroma / graph 完全对齐 (?project_id= 路由)。

架构 (方案 B, 自写多路复用, 不依赖 mcp-proxy):
  业务仓 .mcp.json  --SSE ?project_id=X-->  本 server  --stdio MCP client-->  codegraph serve --mcp (cwd=repo[X])

- 一个进程 / 一个端口 / 一个 systemd unit (codev-mcp-codegraph) 服务所有项目
- 每 project_id 一个 codegraph stdio 后端 (codegraph 按仓, 绕不开), 懒启动 + 复用 + 崩溃重启
- 工具集原样透传 codegraph 自身的 9 个工具 (callers/impact/context...), 不退化
- 鉴权 / 健康 / 日志 / contextvar 路由 全镜像 graph.mcp_server

关键: 每后端跑一个**专属 worker task** 全程持有 stdio_client + ClientSession (anyio 的 task
group / cancel scope 严格绑定创建它的 task, 不能跨 task 用)。请求经 asyncio.Queue 投递、
用 future 回结果 (future 跨 task 安全) —— MCP server 每请求一个 task, 故必须这样隔离。

启动 (平台 serve-mcp 拉起):
    python -m codev_platform.codegraph.server --http --port <codegraph_sse_port>
"""

from __future__ import annotations

import asyncio
import contextvars
import datetime
import json
import os
import sys
import traceback
from pathlib import Path

from mcp.client.session import ClientSession
from mcp.client.stdio import stdio_client
from mcp.server import Server
from mcp.types import CallToolResult, TextContent, Tool

from codev_platform.codegraph import maintenance_gate as _maintenance_gate
from codev_platform.codegraph.backend_inbox import (
    CodegraphBackendDrainingError,
    wait_for_writer_intent,
)
from codev_platform.codegraph.backend_runtime import (
    codegraph_backend_arguments as _codegraph_backend_arguments,
    codegraph_backend_environment as _codegraph_backend_environment,
)
from codev_platform.codegraph.backend_session import (
    BACKEND_DRAINING_MESSAGE as _BACKEND_DRAINING_MESSAGE,
    DEFAULT_BACKEND_CHILD_CANCEL_TIMEOUT_SEC,
    DEFAULT_BACKEND_DRAIN_GRACE_SEC,
    DEFAULT_WRITER_INTENT_POLL_SEC,
    BackendPayload,
    BackendSessionPorts,
    CodegraphBackend,
)
from codev_platform.codegraph.operation_lease import (
    codegraph_operation_lease,
    codegraph_writer_pending,
)
from codev_platform.codegraph.server_command import resolve_codegraph_command
from codev_platform.codegraph.server_public_errors import (
    BACKEND_CALL_FAILED,
    BACKEND_RETURNED_ERROR,
    PUBLIC_BACKEND_FAILURE,
    PUBLIC_TOOL_FAILURE,
    protected_error_detail,
)
from codev_platform.codegraph.server_response import (
    content_to_json as _content_to_json,
    repo_header as _repo_header,
    split_platform_args as _split_platform_args,
    structured_response as _structured_response,
)
from codev_platform.core.errors import ErrorCode, to_mcp_error
from codev_platform.core.config import get as _cfg_get, load_config
import codev_platform.core.runtime_artifacts as runtime_artifacts
from codev_platform.core.runtime_artifact_io import append_runtime_artifact_text
from codev_platform.core.obslog import logging_mode, redact_args
from codev_platform.core.project_id import ProjectIdError, resolve_local
from codev_platform.core.repos import project_repo_specs

_CODEGRAPH_CMD = resolve_codegraph_command()
_CG_SSE_PORT = int(os.getenv("CODEGRAPH_SSE_PORT", "18091"))
_WRITER_INTENT_POLL_SEC = DEFAULT_WRITER_INTENT_POLL_SEC
_BACKEND_DRAIN_GRACE_SEC = DEFAULT_BACKEND_DRAIN_GRACE_SEC
_BACKEND_CHILD_CANCEL_TIMEOUT_SEC = DEFAULT_BACKEND_CHILD_CANCEL_TIMEOUT_SEC

# dev (默认全量) / prod (脱敏) —— 见 core.obslog。模块加载时解析一次。
_LOG_MODE = logging_mode(load_config())


def _log_file() -> Path:
    # 落 data_root/logs (非 import 包目录: wheel/只读安装也可写, 见 core.paths.logs_dir)。
    # 文件名加 codegraph_ 前缀, 与 chroma / graph daemon 的同名日志区分, 防多 daemon 碰撞。
    return runtime_artifacts.codegraph_mcp_log_path()


def _usage_log_file() -> Path:
    """调用统计与服务日志共用可写数据根，禁止回写只读应用 wheel。"""
    return runtime_artifacts.codegraph_usage_path()


def _flog(msg: str) -> None:
    ts = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    line = f"[{ts}] {msg}"
    try:
        append_runtime_artifact_text(_log_file(), line + "\n")
    except Exception:
        pass
    print(line, file=sys.stderr, flush=True)


def _log_usage(record: dict) -> None:
    try:
        append_runtime_artifact_text(
            _usage_log_file(),
            json.dumps(record, ensure_ascii=False) + "\n",
        )
    except Exception:
        pass


def _resolve_default_project() -> str | None:
    try:
        return resolve_local()
    except ProjectIdError:
        return None


PROJECT_ID = _resolve_default_project()


def _repo_for(pid: str | None) -> Path | None:
    """project_id -> repo_path (config.projects.<pid>.repo_path)。codegraph 据此找 .codegraph。"""
    if not pid:
        return None
    projects = _cfg_get(load_config(), "projects") or {}
    pconf = projects.get(pid)
    if not isinstance(pconf, dict):
        return None
    repo = pconf.get("repo_path")
    if not repo:
        return None
    p = Path(repo).expanduser()
    return p if p.exists() else None


# ----------------------------------------------------------------------
# per-project codegraph stdio 后端 (专属 worker task + 队列, 懒启动 + 复用 + 崩溃重启)
# ----------------------------------------------------------------------
_BackendPayload = BackendPayload


def _backend_session_ports() -> BackendSessionPorts:
    """把历史 server 替换点动态转发到专职会话模块。"""
    return BackendSessionPorts(
        command=lambda: _CODEGRAPH_CMD,
        logger=lambda message: _flog(message),
        operation_lease=lambda repo: codegraph_operation_lease(repo),
        writer_pending=lambda repo: codegraph_writer_pending(repo),
        stdio_client=lambda params: stdio_client(params),
        client_session=lambda read, write: ClientSession(read, write),
        backend_arguments=lambda: _codegraph_backend_arguments(),
        backend_environment=lambda: _codegraph_backend_environment(),
        wait_for_writer_intent=lambda check, *, poll_sec: wait_for_writer_intent(
            check,
            poll_sec=poll_sec,
        ),
        writer_intent_poll_sec=lambda: _WRITER_INTENT_POLL_SEC,
        drain_grace_sec=lambda: _BACKEND_DRAIN_GRACE_SEC,
        child_cancel_timeout_sec=lambda: _BACKEND_CHILD_CANCEL_TIMEOUT_SEC,
    )


class _Backend(CodegraphBackend):
    """兼容服务器内部命名的单仓后端适配器。"""

    def __init__(self, pid: str, repo: Path) -> None:
        super().__init__(pid, repo, ports=_backend_session_ports())

    @staticmethod
    def _draining_error() -> CodegraphBackendDrainingError:
        return CodegraphBackendDrainingError(_BACKEND_DRAINING_MESSAGE)

    @staticmethod
    def _require_backend_spawn_permitted() -> None:
        if _maintenance_gate.codegraph_backend_start_permitted() is not True:
            raise _maintenance_gate.CodegraphMaintenanceGateError(
                "reindex 维护门禁已激活或状态不可证明，拒绝启动 CodeGraph 后端"
            )

    @staticmethod
    def _require_service_start_permitted() -> None:
        _maintenance_gate.require_codegraph_service_start_permitted()


_backends: dict[str, _Backend] = {}
_current_project_id: contextvars.ContextVar[str | None] = contextvars.ContextVar(
    "_codegraph_project_id", default=None
)
_TOOLS_CACHE: list[Tool] | None = None  # codegraph 工具集跨项目一致, 发现一次即缓存


def _active_pid() -> str | None:
    return _current_project_id.get() or PROJECT_ID


def _backend_for(pid: str | None) -> _Backend:
    """取/建某 project 的后端。pid 缺失或 repo 不存在 → ValueError (调用方转友好错误)。"""
    if not pid:
        raise ValueError(
            "未指定 project_id。SSE 连接请带 ?project_id=<id> (业务仓 .mcp.json url)。"
        )
    repo = _repo_for(pid)
    if repo is None:
        raise ValueError(f"project '{pid}' 未在平台 config.projects 配 repo_path, 或路径不存在。")
    be = _backends.get(pid)
    if be is None:
        be = _Backend(pid, repo)
        _backends[pid] = be
    return be


def _backend_for_repo(key: str, repo: Path) -> _Backend:
    be = _backends.get(key)
    if be is None or be.repo != repo:
        be = _Backend(key, repo)
        _backends[key] = be
    return be


def _backend_slots_for(pid: str | None) -> list[tuple[str, _Backend]]:
    """Logical project -> one or more codegraph stdio backends.

    Single-repo deployments keep the old exact behavior. Multi-repo projects fan out
    to every RepoSpec with a local codegraph.db, labeling extra repos by tag.
    """
    if not pid:
        return [("main", _backend_for(pid))]
    specs = project_repo_specs(pid)
    slots: list[tuple[str, _Backend]] = []
    for spec in specs:
        if not spec.codegraph_db.is_file():
            continue
        label = spec.tag or "main"
        key = pid if spec.is_main else f"{pid}:{label}"
        slots.append((label, _backend_for_repo(key, spec.root)))
    if slots:
        return slots
    return [("main", _backend_for(pid))]


# ----------------------------------------------------------------------
# MCP server: list_tools / call_tool 全部透传到 active project 的 codegraph 后端
# ----------------------------------------------------------------------
server: Server = Server("codegraph")


@server.list_tools()
async def list_tools() -> list[Tool]:
    global _TOOLS_CACHE
    pid = _active_pid()
    try:
        _maintenance_gate.require_codegraph_request_permitted()
        if _TOOLS_CACHE is not None:
            return _TOOLS_CACHE
        _label, be = _backend_slots_for(pid)[0]
        resp = await be.request("list")
        _TOOLS_CACHE = list(resp.tools)
        _flog(f"[list_tools] discovered {len(_TOOLS_CACHE)} tools from pid={pid}")
        return _TOOLS_CACHE
    except Exception as exc:  # noqa: BLE001
        _flog(f"[list_tools] failed pid={pid}: {protected_error_detail(exc)}")
        return []


def _err(msg: str, code: ErrorCode = ErrorCode.INTERNAL) -> list[TextContent]:
    """MCP 错误体。向后兼容: `error` 字符串保留, 并排新增机器可读 `code`。"""
    return [TextContent(type="text", text=json.dumps(to_mcp_error(msg, code), ensure_ascii=False))]


@server.call_tool()
async def call_tool(name: str, args: dict) -> list[TextContent]:
    t0 = datetime.datetime.now()
    pid = _active_pid()
    ok = True
    content: list = []
    try:
        _maintenance_gate.require_codegraph_request_permitted()
        backend_args, structured = _split_platform_args(args)
        slots = _backend_slots_for(pid)
        merged: list[TextContent] = []
        repos: list[dict] = []
        failures: list[dict] = []
        for label, be in slots:
            try:
                result = await _call_backend(be, pid, name, backend_args)
                part = list(result.content or [])
                is_error = bool(getattr(result, "isError", False))
                if len(slots) == 1 and not structured:
                    content = (
                        _err(PUBLIC_BACKEND_FAILURE, ErrorCode.UPSTREAM_UNAVAILABLE)
                        if is_error
                        else part
                    )
                    ok = not is_error
                    return content
                if structured:
                    repos.append(
                        {
                            "repo": label,
                            "ok": not is_error,
                            "is_error": is_error,
                            "content": [] if is_error else [_content_to_json(c) for c in part],
                        }
                    )
                    if is_error:
                        failures.append({"repo": label, "error": BACKEND_RETURNED_ERROR})
                    continue
                merged.append(_repo_header(label))
                if is_error:
                    merged.extend(_err(PUBLIC_BACKEND_FAILURE, ErrorCode.UPSTREAM_UNAVAILABLE))
                    failures.append({"repo": label, "error": BACKEND_RETURNED_ERROR})
                else:
                    merged.extend(part)
            except Exception as exc:  # noqa: BLE001
                failures.append({"repo": label, "error": BACKEND_CALL_FAILED})
                if len(slots) == 1:
                    raise
                _flog(
                    f"[call_tool] pid={pid} tool={name} repo={label} fan-out failed: "
                    f"{protected_error_detail(exc)}"
                )
        if structured:
            if not repos:
                raise RuntimeError(
                    "all codegraph fan-out backends failed: "
                    + ", ".join(f["repo"] for f in failures)
                )
            ok = not failures
            content = _structured_response(pid, name, repos, failures)
            return content
        if not merged:
            raise RuntimeError(
                "all codegraph fan-out backends failed: " + ", ".join(f["repo"] for f in failures)
            )
        ok = not failures
        content = merged
        return content
    except _maintenance_gate.CodegraphMaintenanceGateError:
        ok = False
        content = _err("维护门禁拒绝 CodeGraph 请求", ErrorCode.UPSTREAM_UNAVAILABLE)
        return content
    except Exception:  # noqa: BLE001
        ok = False
        print(
            f"[codegraph] tool failure: {protected_error_detail(traceback.format_exc())}",
            file=sys.stderr,
        )
        content = _err(PUBLIC_TOOL_FAILURE)
        return content
    finally:
        ms = (datetime.datetime.now() - t0).total_seconds() * 1000
        # args 值可能含 query / symbol / 路径等自由文本 -> prod 脱敏 (dev 原样)。
        # 结构字段 (project_id/tool/ok/elapsed) 不脱敏。
        _trunc_args = {k: str(v)[:80] for k, v in (args or {}).items()}
        _log_usage(
            {
                "ts": t0.strftime("%Y-%m-%dT%H:%M:%S"),
                "project_id": pid,
                "tool": name,
                "args": redact_args(_trunc_args, _LOG_MODE),
                "ok": ok,
                "elapsed_ms": round(ms, 1),
            }
        )


async def _call_backend(be: _Backend, pid: str | None, name: str, args: dict | None):
    for attempt in (1, 2):  # 后端死了 → worker 已退出, ensure() 会重启, 再试一次
        try:
            return await be.request("call", name, args or {})
        except CodegraphBackendDrainingError:
            return CallToolResult(
                content=_err(
                    _BACKEND_DRAINING_MESSAGE,
                    ErrorCode.UPSTREAM_UNAVAILABLE,
                ),
                isError=True,
            )
        except Exception as exc:  # noqa: BLE001
            if attempt == 1 and not be.alive:
                _flog(
                    f"[call_tool] pid={pid} tool={name} 后端已退出, 重启重试: "
                    f"{protected_error_detail(exc)}"
                )
                continue
            raise
    raise RuntimeError("unreachable")


# ----------------------------------------------------------------------
# HTTP (SSE) transport —— 多租户单端点, 镜像 graph.run_http
# ----------------------------------------------------------------------
async def run_http(port: int = _CG_SSE_PORT) -> None:
    _maintenance_gate.require_codegraph_service_start_permitted()
    from mcp.server.sse import SseServerTransport
    from mcp.server.streamable_http_manager import StreamableHTTPSessionManager
    from starlette.applications import Starlette
    from starlette.middleware import Middleware
    from starlette.responses import JSONResponse, Response
    from starlette.routing import Mount, Route
    import uvicorn

    from codev_platform.core.project_id import validate as _pid_validate
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
    from codev_platform.core.runtime_identity import runtime_identity

    runtime = runtime_identity()
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
        from codev_platform.core.acl import can_access
        from codev_platform.core.audit import audit_access

        _ident = getattr(request.state, "identity", None)
        _dec = can_access(load_config(), _ident, pid)
        audit_access("codegraph", _ident, pid, _dec)
        if not _dec.allowed:
            _flog(f"[mcp] DENY project_id={pid} via={getattr(_ident, 'via', None)}: {_dec.reason}")
            return JSONResponse(
                {"error": "forbidden", "code": ErrorCode.ACCESS_DENIED.value},
                status_code=403,
            )
        if pid is None:
            pid = PROJECT_ID
            _flog(f"[mcp] no ?project_id=, fallback default {pid}")
        token = _current_project_id.set(pid)

        def reset() -> None:
            _current_project_id.reset(token)

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
                    status_code=400,
                )
        else:
            # 缺显式 project_id: 先置 None 过 ACL(token 模式 deny), 放行后再回退默认。
            pid = None
        # 项目级 ACL 闸(在回退默认 *之前*): passthrough(dev) 放行 / token 越权或无显式 project → 403。
        from codev_platform.core.acl import can_access
        from codev_platform.core.audit import audit_access

        _ident = getattr(request.state, "identity", None)
        _dec = can_access(load_config(), _ident, pid)
        audit_access("codegraph", _ident, pid, _dec)
        if not _dec.allowed:
            _flog(f"[sse] DENY project_id={pid} via={getattr(_ident, 'via', None)}: {_dec.reason}")
            return JSONResponse(
                {"error": "forbidden", "code": ErrorCode.ACCESS_DENIED.value}, status_code=403
            )
        # ACL 放行后才回退默认(仅 passthrough; token 无显式 project 已被拒)
        if pid is None:
            pid = PROJECT_ID
            _flog(f"[sse] no ?project_id=, fallback default {pid}")
        token = _current_project_id.set(pid)
        _flog(f"[sse] session start project_id={pid}")
        try:
            async with sse_transport.connect_sse(request.scope, request.receive, request._send) as (
                read_stream,
                write_stream,
            ):
                await server.run(read_stream, write_stream, server.create_initialization_options())
        finally:
            _current_project_id.reset(token)
            _flog(f"[sse] session end project_id={pid}")
        # SDK 强制(mcp/server/sse.py): SSE 结束/客户端断开后必返 Response, 否则 starlette
        # request_response 走 `await None(...)` → TypeError 噪声(对齐 graph/chroma/memory 三个同类 handler)。
        return Response()

    async def healthz(_request):
        # PUBLIC 存活探针: 仅最小信息, 不泄敏 (审计 #4 — 旧 /health 泄露
        # default_project_id / live_backends)。详情走鉴权的 /platform/status。
        return JSONResponse({"status": "ok", "service": "codegraph"})

    async def platform_status(_request):
        # 鉴权后详情面 (不在 public_paths): 报默认 project + 活跃后端。
        return JSONResponse(
            {
                "status": "ok",
                "service": "codegraph",
                "default_project_id": PROJECT_ID,
                "live_backends": {pid: be.alive for pid, be in _backends.items()},
                "runtime": runtime.as_dict(),
            }
        )

    _cfg = load_config()
    _host = mcp_bind_host(_cfg)
    # 启动统一认证策略闸(多 dev 串号 / prod 暴露 / 非 loopback bind 未认证)→ 任一命中硬拒。
    _policy_err = startup_policy_error(_cfg, _host)
    if _policy_err:
        _flog(f"[startup] REFUSE: {_policy_err}")
        raise SystemExit(f"codegraph MCP 拒绝启动:{_policy_err}")
    _mw = [
        Middleware(
            AuthMiddleware,
            authenticator=build_authenticator(_cfg),
            public_paths={"/healthz", "/health"},
        ),
    ]
    _rl = maybe_rate_limit_middleware(_cfg)  # Auth 之后 (内层读 identity); dev 默认关
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
            Route("/health", healthz, methods=["GET"]),  # backward-compat public alias (最小)
            Route("/platform/status", platform_status, methods=["GET"]),  # 鉴权: 详情
            Route("/mcp", mcp_http_app, methods=["GET", "POST", "DELETE"]),
            Route("/sse", handle_sse, methods=["GET"]),
            Mount("/messages/", app=sse_transport.handle_post_message),
        ],
        middleware=_mw,
        lifespan=streamable_lifespan(mcp_session_manager),
    )
    _flog(f"[http] codegraph multi-tenant SSE/MCP server starting on {_host}:{port}")
    config = uvicorn.Config(app, host=_host, port=port, log_level="warning", access_log=False)
    await uvicorn.Server(config).serve()


if __name__ == "__main__":
    if "--http" in sys.argv:
        _port = _CG_SSE_PORT
        if "--port" in sys.argv:
            _port = int(sys.argv[sys.argv.index("--port") + 1])
        asyncio.run(run_http(_port))
    else:
        _flog("codegraph 代理仅支持 --http 多租户模式 (业务仓走 SSE ?project_id=)")
        sys.exit(1)
