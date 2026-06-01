"""codegraph 多租户代理 MCP server —— 把外部 `codegraph serve --mcp` (per-repo stdio,
无 HTTP) 包成平台统一的多租户 SSE 端点, 与 chroma / cross-link 完全对齐 (?project_id= 路由)。

架构 (方案 B, 自写多路复用, 不依赖 mcp-proxy):
  业务仓 .mcp.json  --SSE ?project_id=X-->  本 server  --stdio MCP client-->  codegraph serve --mcp (cwd=repo[X])

- 一个进程 / 一个端口 / 一个 systemd unit (codev-mcp-codegraph) 服务所有项目
- 每 project_id 一个 codegraph stdio 后端 (codegraph 按仓, 绕不开), 懒启动 + 复用 + 崩溃重启
- 工具集原样透传 codegraph 自身的 9 个工具 (callers/impact/context...), 不退化
- 鉴权 / 健康 / 日志 / contextvar 路由 全镜像 cross_link.server

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

from mcp.server import Server
from mcp.client.session import ClientSession
from mcp.client.stdio import StdioServerParameters, stdio_client
from mcp.types import TextContent, Tool

from codev_platform.core.config import get as _cfg_get, load_config
from codev_platform.core.obslog import logging_mode, redact_args
from codev_platform.core.project_id import ProjectIdError, resolve_local


def _resolve_codegraph_cmd() -> str:
    """校验 CODEGRAPH_CMD (服务化后收紧 spawn 入口)。

    只允许两种形态:
      1. 裸命令名 "codegraph" (走 PATH 查找, 默认);
      2. 一个**绝对路径且文件存在**的可执行 (显式 pin)。
    含 shell 元字符 / 相对路径 (非裸 "codegraph") / 不存在的绝对路径 -> 拒绝, 回退默认
    "codegraph" + stderr 警告 (不 raise, 避免 import 期炸掉整个 server)。
    """
    default = "codegraph"
    raw = os.getenv("CODEGRAPH_CMD")
    if not raw:
        return default
    cmd = raw.strip()
    if cmd == default:
        return default
    # shell 元字符黑名单 (spawn 用 list args 本不过 shell, 但收紧防误配 / 注入意图)
    if any(ch in cmd for ch in (";", "&", "|", "$", "`", ">", "<", "\n", "\r", "*", "?", "(", ")", '"', "'", " ")):
        print(
            f"[codegraph.server] WARN: CODEGRAPH_CMD {raw!r} 含非法字符, 回退默认 'codegraph'",
            file=sys.stderr, flush=True,
        )
        return default
    p = Path(cmd)
    if p.is_absolute() and p.is_file():
        return str(p)
    print(
        f"[codegraph.server] WARN: CODEGRAPH_CMD {raw!r} 既非裸名 'codegraph' 也非存在的绝对路径文件, "
        f"回退默认 'codegraph'",
        file=sys.stderr, flush=True,
    )
    return default


_CODEGRAPH_CMD = _resolve_codegraph_cmd()
_CG_SSE_PORT = int(os.getenv("CODEGRAPH_SSE_PORT", "18091"))

# dev (默认全量) / prod (脱敏) —— 见 core.obslog。模块加载时解析一次。
_LOG_MODE = logging_mode(load_config())

_LOG_FILE = Path(__file__).resolve().parent / "mcp_server.log"
_USAGE_LOG = Path(__file__).resolve().parent / "codegraph_usage.jsonl"


def _flog(msg: str) -> None:
    ts = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    line = f"[{ts}] {msg}"
    try:
        with _LOG_FILE.open("a", encoding="utf-8") as f:
            f.write(line + "\n")
    except Exception:
        pass
    print(line, file=sys.stderr, flush=True)


def _log_usage(record: dict) -> None:
    try:
        with _USAGE_LOG.open("a", encoding="utf-8") as f:
            f.write(json.dumps(record, ensure_ascii=False) + "\n")
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
class _Backend:
    """一个项目的 codegraph serve --mcp stdio 后端。

    worker task (_run) 在自身 task 内全程持有 stdio_client + ClientSession; 外部请求
    (来自不同的 MCP request task) 经 _inbox 队列投递, 结果走 future 回传 (跨 task 安全)。
    """

    def __init__(self, pid: str, repo: Path) -> None:
        self.pid = pid
        self.repo = repo
        self._task: asyncio.Task | None = None
        self._inbox: asyncio.Queue = asyncio.Queue()
        self._ready: asyncio.Event = asyncio.Event()
        self._error: Exception | None = None
        self._lock = asyncio.Lock()

    async def _run(self) -> None:
        params = StdioServerParameters(
            command=_CODEGRAPH_CMD, args=["serve", "--mcp"],
            cwd=str(self.repo), env=os.environ.copy(),
        )
        try:
            _flog(f"[backend] spawn codegraph pid={self.pid} cwd={self.repo}")
            async with stdio_client(params) as (read, write):
                async with ClientSession(read, write) as session:
                    await session.initialize()
                    self._ready.set()
                    _flog(f"[backend] ready pid={self.pid}")
                    while True:
                        kind, name, args, fut = await self._inbox.get()
                        try:
                            if kind == "list":
                                res = await session.list_tools()
                            else:
                                res = await session.call_tool(name, args or {})
                            if not fut.done():
                                fut.set_result(res)
                        except Exception as exc:  # noqa: BLE001 — 单次请求失败, 不拖垮 worker
                            if not fut.done():
                                fut.set_exception(exc)
        except Exception as exc:  # noqa: BLE001 — 后端起不来 / 会话整体崩
            self._error = exc
            self._ready.set()
            _flog(f"[backend] pid={self.pid} worker exit: {exc!s}")

    async def ensure(self) -> None:
        async with self._lock:
            if self._task is None or self._task.done():
                self._inbox = asyncio.Queue()
                self._ready = asyncio.Event()
                self._error = None
                self._task = asyncio.create_task(self._run())
            await self._ready.wait()
            if self._error is not None:
                raise self._error

    async def request(self, kind: str, name: str | None = None, args: dict | None = None):
        await self.ensure()
        fut: asyncio.Future = asyncio.get_running_loop().create_future()
        await self._inbox.put((kind, name, args, fut))
        return await fut

    @property
    def alive(self) -> bool:
        return self._task is not None and not self._task.done()


_backends: dict[str, _Backend] = {}
_current_project_id: contextvars.ContextVar["str | None"] = contextvars.ContextVar(
    "_codegraph_project_id", default=None
)
_TOOLS_CACHE: list[Tool] | None = None  # codegraph 工具集跨项目一致, 发现一次即缓存


def _active_pid() -> str | None:
    return _current_project_id.get() or PROJECT_ID


def _backend_for(pid: str | None) -> _Backend:
    """取/建某 project 的后端。pid 缺失或 repo 不存在 → ValueError (调用方转友好错误)。"""
    if not pid:
        raise ValueError("未指定 project_id。SSE 连接请带 ?project_id=<id> (业务仓 .mcp.json url)。")
    repo = _repo_for(pid)
    if repo is None:
        raise ValueError(f"project '{pid}' 未在平台 config.projects 配 repo_path, 或路径不存在。")
    be = _backends.get(pid)
    if be is None:
        be = _Backend(pid, repo)
        _backends[pid] = be
    return be


# ----------------------------------------------------------------------
# MCP server: list_tools / call_tool 全部透传到 active project 的 codegraph 后端
# ----------------------------------------------------------------------
server: Server = Server("codegraph")


@server.list_tools()
async def list_tools() -> list[Tool]:
    global _TOOLS_CACHE
    if _TOOLS_CACHE is not None:
        return _TOOLS_CACHE
    pid = _active_pid()
    try:
        be = _backend_for(pid)
        resp = await be.request("list")
        _TOOLS_CACHE = list(resp.tools)
        _flog(f"[list_tools] discovered {len(_TOOLS_CACHE)} tools from pid={pid}")
        return _TOOLS_CACHE
    except Exception as exc:  # noqa: BLE001
        _flog(f"[list_tools] failed pid={pid}: {exc!s}")
        return []


def _err(msg: str) -> list[TextContent]:
    return [TextContent(type="text", text=json.dumps({"error": msg}, ensure_ascii=False))]


@server.call_tool()
async def call_tool(name: str, args: dict) -> list[TextContent]:
    t0 = datetime.datetime.now()
    pid = _active_pid()
    ok = True
    content: list = []
    try:
        be = _backend_for(pid)
        for attempt in (1, 2):  # 后端死了 → worker 已退出, ensure() 会重启, 再试一次
            try:
                result = await be.request("call", name, args or {})
                content = list(result.content or [])
                ok = not bool(getattr(result, "isError", False))
                return content
            except Exception as exc:  # noqa: BLE001
                if attempt == 1 and not be.alive:
                    _flog(f"[call_tool] pid={pid} tool={name} 后端已退出, 重启重试: {exc!s}")
                    continue
                raise
    except Exception as exc:  # noqa: BLE001
        ok = False
        print(f"[codegraph] tool '{name}' pid={pid} error: {traceback.format_exc()}", file=sys.stderr)
        content = _err(f"codegraph tool '{name}' 执行失败 (project={pid}): {exc!s}")
        return content
    finally:
        ms = (datetime.datetime.now() - t0).total_seconds() * 1000
        # args 值可能含 query / symbol / 路径等自由文本 -> prod 脱敏 (dev 原样)。
        # 结构字段 (project_id/tool/ok/elapsed) 不脱敏。
        _trunc_args = {k: str(v)[:80] for k, v in (args or {}).items()}
        _log_usage({
            "ts": t0.strftime("%Y-%m-%dT%H:%M:%S"),
            "project_id": pid,
            "tool": name,
            "args": redact_args(_trunc_args, _LOG_MODE),
            "ok": ok,
            "elapsed_ms": round(ms, 1),
        })


# ----------------------------------------------------------------------
# HTTP (SSE) transport —— 多租户单端点, 镜像 cross_link.run_http
# ----------------------------------------------------------------------
async def run_http(port: int = _CG_SSE_PORT) -> None:
    from mcp.server.sse import SseServerTransport
    from starlette.applications import Starlette
    from starlette.middleware import Middleware
    from starlette.responses import JSONResponse
    from starlette.routing import Mount, Route
    import uvicorn

    from codev_platform.core.project_id import validate as _pid_validate
    from codev_platform.gateway import AuthMiddleware, build_authenticator, maybe_rate_limit_middleware

    sse_transport = SseServerTransport("/messages/")

    async def handle_sse(request):
        pid_raw = request.query_params.get("project_id")
        if pid_raw:
            try:
                pid = _pid_validate(pid_raw)
            except Exception as exc:  # noqa: BLE001
                _flog(f"[sse] reject invalid project_id {pid_raw!r}: {exc!s}")
                return JSONResponse({"error": "invalid project_id"}, status_code=400)
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
            _flog(f"[sse] DENY project_id={pid} via={getattr(_ident,'via',None)}: {_dec.reason}")
            return JSONResponse({"error": "forbidden"}, status_code=403)
        # ACL 放行后才回退默认(仅 passthrough; token 无显式 project 已被拒)
        if pid is None:
            pid = PROJECT_ID
            _flog(f"[sse] no ?project_id=, fallback default {pid}")
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

    async def healthz(_request):
        # PUBLIC 存活探针: 仅最小信息, 不泄敏 (审计 #4 — 旧 /health 泄露
        # default_project_id / live_backends)。详情走鉴权的 /platform/status。
        return JSONResponse({"status": "ok", "service": "codegraph"})

    async def platform_status(_request):
        # 鉴权后详情面 (不在 public_paths): 报默认 project + 活跃后端。
        return JSONResponse({
            "status": "ok",
            "service": "codegraph",
            "default_project_id": PROJECT_ID,
            "live_backends": {pid: be.alive for pid, be in _backends.items()},
        })

    _cfg = load_config()
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

    app = Starlette(
        debug=False,
        routes=[
            Route("/healthz", healthz, methods=["GET"]),
            Route("/health", healthz, methods=["GET"]),  # backward-compat public alias (最小)
            Route("/platform/status", platform_status, methods=["GET"]),  # 鉴权: 详情
            Route("/sse", handle_sse, methods=["GET"]),
            Mount("/messages/", app=sse_transport.handle_post_message),
        ],
        middleware=_mw,
    )
    _flog(f"[http] codegraph multi-tenant SSE server starting on 127.0.0.1:{port}")
    config = uvicorn.Config(app, host="127.0.0.1", port=port, log_level="warning", access_log=False)
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
