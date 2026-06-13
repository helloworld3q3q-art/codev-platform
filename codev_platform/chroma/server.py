"""Claude Code MCP server — 暴露 Chroma 文档检索给 Claude Code

提供 3 个 tools：
- search_docs       语义搜索 platform markdown 文档
- list_collections  查看 Chroma 知识库统计
- get_by_file       按文件路径精确获取所有 chunks

启动方式：
    python tools/chroma/mcp_server.py

stdio 通信；启动日志写 stderr 不污染协议。
依赖：mcp >= 0.9, chromadb, sentence-transformers
"""

from __future__ import annotations

import asyncio
import os
import sys
import time  # noqa: F401 — re-export: test_chroma_retry monkeypatch s.time.sleep (_helpers._load_with_retry 同此 time 模块单例)

from mcp.server import Server
from mcp.server.stdio import stdio_server
from mcp.types import TextContent, Tool

# 注:chromadb 不在顶层 import —— heavy runtime 依赖, lazy import 进 _get_client。
# 这样 `import codev_platform.chroma.server` 在没装 chromadb 时也不崩(可被静态审计/测试 inspect),
# 缺 runtime 依赖时在真正建连接处给清晰错误。
# 注:不再用 Chroma 的 SentenceTransformerEmbeddingFunction —— 查询侧需要
# prompt_name='query'(Qwen3 instruction-aware),必须自己持有 SentenceTransformer。
# 索引侧同样自己 encode(见 index_docs.py)。

# chroma_collection_name / BM25Index 随 _load_project_state 移到 _project_state.py。

# 2026-06-02 拆包 (file-discipline §1): 日志 / 统计 / 纯 helper / tool schema 抽到 sibling
# 模块, server.py 保留有状态核心 (model/client/projects/reranker/transport)。这些 import
# 同时作向后兼容 re-export (tests 用 server.retry_delays / server._stats / server._torch_dtype 等)。
from codev_platform.chroma._obslog import _flog, _log_recall  # noqa: E402
from codev_platform.chroma._stats import (  # noqa: E402
    _stats, _record_stat, _record_stat_error, _process_info,
)
from codev_platform.chroma._helpers import (  # noqa: E402,F401 — re-export server.<name> 兼容
    _torch_dtype, retry_delays, should_retry, _load_with_retry,
    _is_gpu_error, _gpu_free_info, _gpu_memory_mb, _to_iso,
)
from codev_platform.chroma._schema import (  # noqa: E402
    tool_definitions, _err, _ok, _build_where,
)


# ----------------------------------------------------------------------
# 全局：Chroma 客户端 + collection（lazy init，启动失败也不挂 server）
# ----------------------------------------------------------------------

# 配置常量抽到 _config.py (无环分层叶子, §A.2b)。此处 re-export 保持 server.<NAME> 兼容
# (_tools / _models / _projects / _http 仍可 `from .server import EMBED_MODEL` 等; 也可直接 from _config)。
from codev_platform.chroma._config import (  # noqa: E402,F401 — re-export server.<name> 兼容
    DATA_DIR, _STAMP_PATH, PROJECT_ID, COLLECTION_BASE, COLLECTION_NAME, LEGACY_COLLECTION_NAME,
    _CFG, _LOG_MODE, EMBED_MODEL, EMBED_DEVICE,
    RERANKER_MODEL, RERANKER_DEVICE, RERANKER_DTYPE, RERANKER_ENABLED, RERANKER_TOP_K,
    DEFAULT_RETURN_K, BM25_TOP_K, BM25_ENABLED, RRF_K_CONST, GPU_CONCURRENCY, _BM25_IMPORT_OK,
    GPU_OP_TIMEOUT, EMBED_ENCODE_BATCH,
)
# load_config 仍在 server 用 (handle_sse / platform_status 的 ACL + 中间件构建)。
from codev_platform.core.config import get as _cfg_get, load_config  # noqa: E402

# Reranker 子系统抽到 _reranker.py (无环: 它从 _config 拿常量, 不依赖 server)。re-export
# _ensure_reranker/_rerank_scores (main prewarm + _tools + tests 用); `rr` 供 health 读
# rebound 全局 rr._reranker_model (不可 from import, 否则 stale)。
from codev_platform.chroma import _reranker as rr  # noqa: E402
from codev_platform.chroma._reranker import _ensure_reranker, _rerank_scores  # noqa: E402,F401
# 模型 / chroma client 生命周期抽到 _models.py (无环: 从 _config 拿常量, 不依赖 server)。
# re-export 4 函数 (_tools / _ensure_project / _load_project_state 用); `m` 供 health/healthz
# 读 rebound 全局 m._model / m._global_init_error (不可 from import, 否则 stale)。
from codev_platform.chroma import _models as m  # noqa: E402
from codev_platform.chroma._models import _ensure_model, _get_client, _encode_query, _get_gpu_sem  # noqa: E402,F401
# 项目状态机 (_ProjectState / _projects / _ensure_project / _load_project_state /
# _maybe_reload_project / _project_last_indexed_iso) 抽到 _project_state.py (无环叶子, file-discipline §1)。
# re-export 保 `from chroma.server import _ensure_project` 兼容 (tests / _tools / 本模块 _run_http 用)。
from codev_platform.chroma._project_state import (  # noqa: E402,F401
    _ProjectState, _ensure_project, _load_project_state, _maybe_reload_project,
    _project_last_indexed_iso, _projects,
)
# /embed /rerank 共享 GPU 算力端点 + GPU 算子收口助手 (_gpu_call/_release_cuda_cache/validate_*)
# 已抽到 _embed_api.py (file-discipline §1; 叶子无环)。re-export 保 server.<name> 兼容
# (test_chroma_embed_rerank_validate 用 validate_*); _run_http 按 Route 注册 _embed_api.embed/rerank。
from codev_platform.chroma import _embed_api  # noqa: E402
from codev_platform.chroma._embed_api import (  # noqa: E402,F401
    _gpu_call, _release_cuda_cache, validate_embed_body, validate_rerank_body,
)

# 日志 helper (_flog / _log_recall / _maybe_rotate_log) + 路径常量已抽到 _obslog.py
# (见上方 import re-export)。

import contextvars

# _client / _model / _use_query_prompt / _global_init_error 抽到 _models.py;
# _reranker_model 抽到 _reranker.py。读经 m.<name> / rr.<name> (访问当前值, 防 stale)。

# _STAMP_PATH (构建戳路径) 已抽到 _config.py (上方 import re-export)。


# _ProjectState / _projects 已抽到 _project_state.py (上方 re-export)。


# stats 累加器 + _record_stat / _record_stat_error / _process_info / _DAEMON_START 已抽到
# _stats.py (上方 import re-export)。_sse_sessions 仍在 server: handle_sse 内 rebind (+= / -=),
# 跨模块 rebind 会读到 stale, 故留本模块 (handle_sse 也在本模块)。
_sse_sessions = 0


# retry_delays / should_retry / _load_with_retry / _is_gpu_error / _gpu_free_info /
# _gpu_memory_mb 已抽到 _helpers.py (上方 import re-export, tests 仍用 server.<name>)。


# _project_last_indexed_iso 已抽到 _project_state.py (上方 re-export)。
# _to_iso 已抽到 _helpers.py (上方 import re-export)。

# contextvar 把 SSE session 跟 project_id 绑定; tool handler 通过它路由
_current_project_id: contextvars.ContextVar[str | None] = contextvars.ContextVar(
    "_current_project_id", default=None
)
# 调用方来源: 区分 web 端 agent 调用 vs 开发端 Claude Code 直调 (采纳率分桶用)。
# SSE ?client= 指定; agent 的 search_docs 工具传 client=agent, 开发端 .mcp.json 不传 → 默认 dev。
_current_client: contextvars.ContextVar[str] = contextvars.ContextVar(
    "_current_client", default="dev"
)


# _ensure_model / _get_client 已抽到 _models.py (上方 import re-export, _ensure_project /
# _load_project_state 仍调 server.<name> -> _models 函数)。


# _load_project_state / _maybe_reload_project / _ensure_project 已抽到 _project_state.py (上方 re-export)。
# _encode_query / _get_gpu_sem 已抽到 _models.py (上方 import re-export, _tools 仍 from server import)。


# Reranker 子系统 (_ensure_reranker / _rerank_scores + 全局 + prompt 常量) 已抽到
# _reranker.py (无环: 它从 _config 拿 RERANKER_*, 不依赖 server)。函数经上方 import re-export;
# rebound 全局 (_reranker_model 等) 由读者经 `rr.<name>` 访问 (health 用 rr._reranker_model)。


# ----------------------------------------------------------------------
# MCP server 定义
# ----------------------------------------------------------------------

server: Server = Server("platform-docs")


@server.list_tools()
async def list_tools() -> list[Tool]:
    # tool 定义 + CATEGORIES / MODULES + _err / _ok / _build_where 已抽到 _schema.py
    # (上方 import re-export)。
    return tool_definitions()


# call_tool (search_docs / list_collections / get_by_file 执行逻辑) 已抽到 _tools.py
# (file-discipline §1)。本模块末尾 `from . import _tools` 触发其 @server.call_tool() 注册。


async def _run_stdio() -> None:
    """传统 stdio transport: Claude Code 直接 spawn cmd 当 stdio server。"""
    async with stdio_server() as (read_stream, write_stream):
        await server.run(read_stream, write_stream, server.create_initialization_options())


async def _run_http(port: int) -> None:
    """HTTP/SSE transport: daemon 模式, 供多 Claude Code 会话共享。

    单 daemon 常驻, N 个会话各自通过 platform_docs_launcher.py 的 stdio↔HTTP
    proxy 连过来。8GB GPU 上多会话场景必走此模式 (每进程独立 load Qwen 模型
    会立刻 OOM)。

    端口默认 18083 (PLATFORM_DOCS_DAEMON_PORT 环境变量覆盖)。绑 127.0.0.1
    仅本地访问, 不暴露网络。
    """
    from mcp.server.sse import SseServerTransport
    from mcp.server.streamable_http_manager import StreamableHTTPSessionManager
    from starlette.applications import Starlette
    from starlette.responses import JSONResponse, Response
    from starlette.routing import Mount, Route
    import uvicorn
    from codev_platform.mcp_streamable import (
        ContextualStreamableHTTPASGIApp,
        streamable_lifespan,
    )

    sse_transport = SseServerTransport("/messages/")

    def bind_mcp_context(request):
        pid_raw = request.query_params.get("project_id")
        if pid_raw:
            try:
                from codev_platform.core.project_id import validate as _pid_validate
                pid = _pid_validate(pid_raw)
            except Exception as exc:  # noqa: BLE001
                _flog(f"[mcp] reject: invalid project_id {pid_raw!r}: {exc!s}")
                return JSONResponse({"error": "invalid project_id"}, status_code=400)
        else:
            pid = None

        from codev_platform.core.acl import can_access
        from codev_platform.core.audit import audit_access
        _ident = getattr(request.state, "identity", None)
        _dec = can_access(load_config(), _ident, pid)
        audit_access("platform-docs", _ident, pid, _dec)
        if not _dec.allowed:
            _flog(f"[mcp] DENY project_id={pid} via={getattr(_ident,'via',None)}: {_dec.reason}")
            return JSONResponse({"error": "forbidden"}, status_code=403)

        if pid is None:
            pid = PROJECT_ID
            _flog(f"[mcp] no ?project_id= in query, fallback to default {pid}")

        state = _ensure_project(pid)
        if state is None or state.collection is None:
            err = (state.init_error if state else None) or "project init failed"
            _flog(f"[mcp] reject: cannot load project {pid}: {err}")
            return JSONResponse({"error": f"cannot load project: {err}"}, status_code=503)

        client = request.query_params.get("client") or "codex"
        token = _current_project_id.set(pid)
        ctok = _current_client.set(client)

        def reset() -> None:
            _current_project_id.reset(token)
            _current_client.reset(ctok)

        return reset

    async def handle_sse(request):
        # MCP SSE 双向流: GET /sse?project_id=<pid> 建立 stream + 绑定 project_id 到 contextvar
        # multi-tenant: 同一 daemon 服务多个 project_id, 每 session 独立路由
        pid_raw = request.query_params.get("project_id")
        if pid_raw:
            try:
                from codev_platform.core.project_id import validate as _pid_validate
                pid = _pid_validate(pid_raw)
            except Exception as exc:  # noqa: BLE001
                _flog(f"[sse] reject: invalid project_id {pid_raw!r}: {exc!s}")
                # 返显式 400 (裸 return 会让 Starlette 收到 None -> TypeError)
                return JSONResponse({"error": "invalid project_id"}, status_code=400)
        else:
            # 缺显式 project_id: 先置 None 过 ACL —— token 模式 can_access(None)=deny;
            # passthrough 放行后才回退默认 PROJECT_ID(向后兼容)。防 token 省略 project_id 静默命中默认项目。
            pid = None

        # 项目级 ACL 闸(在回退默认 *之前*): passthrough(dev) 放行 / token 越权或无显式 project → 403。
        from codev_platform.core.acl import can_access
        from codev_platform.core.audit import audit_access
        _ident = getattr(request.state, "identity", None)
        _dec = can_access(load_config(), _ident, pid)
        audit_access("platform-docs", _ident, pid, _dec)
        if not _dec.allowed:
            _flog(f"[sse] DENY project_id={pid} via={getattr(_ident,'via',None)}: {_dec.reason}")
            return JSONResponse({"error": "forbidden"}, status_code=403)

        # ACL 放行后再回退默认(仅 passthrough 会到这; token 无显式 project 已被拒)
        if pid is None:
            pid = PROJECT_ID
            _flog(f"[sse] no ?project_id= in query, fallback to default {pid}")

        # eager load 让 SSE 建立前发现 collection 缺失类问题
        state = _ensure_project(pid)
        if state is None or state.collection is None:
            err = (state.init_error if state else None) or "project init failed"
            _flog(f"[sse] reject: cannot load project {pid}: {err}")
            return JSONResponse({"error": f"cannot load project: {err}"}, status_code=503)

        client = request.query_params.get("client") or "dev"
        global _sse_sessions
        token = _current_project_id.set(pid)
        ctok = _current_client.set(client)
        _sse_sessions += 1
        _flog(f"[sse] session start project_id={pid} client={client} (active={_sse_sessions})")
        try:
            async with sse_transport.connect_sse(
                request.scope, request.receive, request._send
            ) as (read_stream, write_stream):
                await server.run(read_stream, write_stream, server.create_initialization_options())
        finally:
            _current_project_id.reset(token)
            _current_client.reset(ctok)
            _sse_sessions = max(0, _sse_sessions - 1)
            _flog(f"[sse] session end project_id={pid} (active={_sse_sessions})")
        # SDK 强制: SSE 结束后必返 Response (否则 starlette await None → TypeError 噪声)。
        return Response()

    async def healthz(_request):
        # PUBLIC 存活/就绪探针: 仅最小信息, 不泄敏 (审计 #4 — /health 旧版泄露
        # default_project_id / loaded_projects / backends)。launcher 仅需就绪状态码
        # (200 ready / 503 starting) + tenant_mode (非敏感常量); 详情走鉴权的
        # /platform/health。
        model_ready = m._model is not None
        any_collection_ready = any(p.collection is not None for p in _projects.values())
        all_ready = model_ready and any_collection_ready
        return JSONResponse(
            {
                "status": "ok" if all_ready else "starting",
                "service": "platform-docs",
                "tenant_mode": "multi",
            },
            status_code=200 if all_ready else 503,
        )

    async def health(_request):
        # 鉴权后详情面 (挂 /platform/health, 不在 public_paths): 报所有 loaded projects +
        # 默认 project_id + stats + GPU。token 模式需 Bearer; passthrough 模式本机放行。
        # multi-tenant: 报告所有 loaded projects + 默认 project_id (向后兼容字段保留)
        model_ready = m._model is not None
        loaded = []
        stale_pids: list[str] = []
        for p in _projects.values():
            chunks: int | None
            init_error = p.init_error
            if p.collection is None:
                chunks = 0
            else:
                try:
                    chunks = p.collection.count()
                except Exception as exc:  # noqa: BLE001
                    # 常见: chromadb.errors.NotFoundError 来自 reindex 期间底层 collection 被重建
                    # daemon 仍持有 stale handle。退化为 None + 标错 + 排队 evict, 下次 ensure 重新加载。
                    chunks = None
                    if not init_error:
                        init_error = f"count failed: {type(exc).__name__}: {exc}"
                    stale_pids.append(p.project_id)
                    _flog(
                        f"[health] stale collection for {p.project_id}: {type(exc).__name__}: {exc}"
                    )
            loaded.append(
                {
                    "project_id": p.project_id,
                    "chunks": chunks,
                    "collection_name": p.active_collection_name,
                    "bm25": (p.bm25_index.ready() if p.bm25_index is not None else False),
                    "init_error": init_error,
                    "last_request_at": _to_iso(p.last_request_at),
                    "last_indexed_at": _project_last_indexed_iso(p.project_id),
                }
            )
        for pid in stale_pids:
            _projects.pop(pid, None)
        any_collection_ready = any(p.collection is not None for p in _projects.values())
        reranker_ready = rr._reranker_model is not None
        all_ready = model_ready and any_collection_ready
        # stats avg = ms_total / calls (None when calls=0)
        emb = _stats["embedding"]
        rer = _stats["reranker"]
        stats_payload = {
            "embedding": {
                "calls": int(emb["calls"]),
                "ms_avg": round(emb["ms_total"] / emb["calls"], 2) if emb["calls"] else None,
                "errors": int(emb.get("errors", 0)),
                "last_error": emb.get("last_error"),
                "load_retries": int(emb.get("load_retries", 0)),
            },
            "reranker": {
                "calls": int(rer["calls"]),
                "ms_avg": round(rer["ms_total"] / rer["calls"], 2) if rer["calls"] else None,
                "errors": int(rer.get("errors", 0)),
                "last_error": rer.get("last_error"),
                "load_retries": int(rer.get("load_retries", 0)),
            },
            "gpu_memory_mb": _gpu_memory_mb(),
        }
        return JSONResponse(
            {
                "status": "ok" if all_ready else "starting",
                "model": "loaded" if model_ready else "loading",
                "collection": "ready" if any_collection_ready else "init",
                "reranker": "loaded" if reranker_ready else "loading_or_disabled",
                "init_error": m._global_init_error,
                "project_id": PROJECT_ID,  # backward-compat: 启动默认 project_id
                "tenant_mode": "multi",
                "loaded_projects": loaded,
                "stats": stats_payload,
                "process": _process_info(),
                "gpu": _gpu_free_info(),
                "sse_sessions": _sse_sessions,
            },
            status_code=200 if all_ready else 503,
        )

    async def platform_status(_request):
        # 平台控制面: 所有项目 x 三库 + 记忆 + 使用率, 以 JSON 暴露 (服务端读本机资源)。
        # 原则: 访问平台数据走 HTTP/HTTPS, 客户端 (health --all / 子应用) 不碰文件路径。
        try:
            from codev_platform.platform_status import build_platform_status
            from codev_platform.core.config import load_config
            return JSONResponse(build_platform_status(load_config()))
        except Exception as exc:  # noqa: BLE001
            return JSONResponse({"error": f"{type(exc).__name__}: {exc}"}, status_code=500)

    # /embed /rerank handler 已抽到 _embed_api (上方 import; 下面 Route 注册 _embed_api.embed/rerank)。

    # 统一认证拦截: 复用 gateway 的纯 ASGI 中间件 (SSE 安全 + 高并发, 不缓冲 /sse 长连接)。
    # passthrough 模式非破坏 (无身份头 → local/default); token 模式对外按 Bearer 鉴权。
    # public_paths 仅 /healthz (最小存活探针, 不泄敏); 详情面 /platform/health + /platform/status
    # 受鉴权保护 (审计 #4)。/health 保留为 /healthz 的 public 别名 (老探针向后兼容, 同样最小)。
    from starlette.middleware import Middleware
    from codev_platform.gateway import (
        AuthMiddleware, build_authenticator, maybe_rate_limit_middleware, startup_policy_error,
    )
    from codev_platform.mcp_serve import mcp_bind_host

    _cfg = load_config()
    # bind host 走单一真值源(与 graph/codegraph/memory 三套 MCP 对齐): config mcp.bind_host > loopback。
    # 启动统一认证策略闸: 多 dev 串号 / prod 暴露 / 非 loopback bind 未认证(passthrough)→ 任一命中硬拒。
    # 此前 daemon 写死 127.0.0.1 且不挂闸: 多机设 0.0.0.0 时 platform-docs 静默不生效(其余三套生效),
    # 且反代+passthrough 拓扑下无 startup 护栏(对抗审计 P2)。/embed /rerank 仍按 TCP 对端 loopback 豁免。
    _host = mcp_bind_host(_cfg)
    _policy_err = startup_policy_error(_cfg, _host)
    if _policy_err:
        _flog(f"[daemon][startup] REFUSE: {_policy_err}")
        raise SystemExit(f"platform-docs daemon 拒绝启动:{_policy_err}")
    _mw = [
        Middleware(
            AuthMiddleware,
            authenticator=build_authenticator(_cfg),
            public_paths={"/healthz", "/health"},
            # 平台单一内部信物(与 web→agent X-Identity 同源)。配了它时, 下面 loopback 豁免额外要求
            # X-Internal-Call 信物 → 同机反代转发的远程请求带不出 secret 拿不到豁免(防白嫖 GPU);
            # 未配(单机 passthrough)→ 维持纯 loopback 豁免不变。
            internal_secret=_cfg_get(_cfg, "agent.internal_secret", "") or None,
            # /embed /rerank 是无租户数据的纯算力接口: 本机 loopback 内部调用 (code_vec 索引 /
            # agent-memory 写 复用 daemon GPU 模型) 免 token; 远程访问仍按 token 鉴权 (审计 R: 防白嫖)。
            loopback_exempt_paths={"/embed", "/rerank"},
        ),
    ]
    # 限流挂在 Auth 之后 (内层读 identity); dev 默认关 (工厂返回 None)。
    _rl = maybe_rate_limit_middleware(_cfg)
    if _rl is not None:
        _mw.append(_rl)

    # stateless=True: 每请求新建 transport + 复制当前请求 context → bind_mcp_context 设的
    # project_id/client contextvar 每请求都新鲜生效 (与 /sse 语义等价)。stateful 模式只在
    # session initialize 时绑定一次, 会让多租户路由退化成"一 session 锁一个项目"。
    mcp_session_manager = StreamableHTTPSessionManager(server, stateless=True)
    mcp_http_app = ContextualStreamableHTTPASGIApp(mcp_session_manager, bind_mcp_context)

    app = Starlette(
        debug=False,
        routes=[
            Route("/healthz", healthz, methods=["GET"]),
            Route("/health", healthz, methods=["GET"]),  # backward-compat public alias (最小)
            Route("/platform/health", health, methods=["GET"]),  # 鉴权: daemon 详情
            Route("/platform/status", platform_status, methods=["GET"]),
            Route("/embed", _embed_api.embed, methods=["POST"]),  # 鉴权: 共享嵌入(复用 GPU 模型)
            Route("/rerank", _embed_api.rerank, methods=["POST"]),  # 鉴权: 共享重排(复用 GPU reranker)
            Route("/mcp", mcp_http_app, methods=["GET", "POST", "DELETE"]),
            Route("/sse", handle_sse, methods=["GET"]),
            Mount("/messages/", app=sse_transport.handle_post_message),
        ],
        middleware=_mw,
        lifespan=streamable_lifespan(mcp_session_manager),
    )

    _flog(f"[daemon] HTTP SSE/MCP server starting on {_host}:{port}")
    config = uvicorn.Config(
        app,
        host=_host,
        port=port,
        log_level="warning",
        access_log=False,
    )
    server_inst = uvicorn.Server(config)
    await server_inst.serve()


async def main() -> None:
    # 两种 transport:
    #   stdio (默认): 每个 Claude Code 会话各起一份 mcp_server, lazy load 模型
    #   --http (daemon 模式): 单进程跑, 多会话通过 launcher 共享 (8GB GPU 必走)
    is_http = "--http" in sys.argv

    # Prewarm: HTTP daemon 模式下强制开启 (cold-start 风险只 daemon 启动时承担一次);
    # stdio 模式下按 PLATFORM_DOCS_PREWARM 环境变量决定 (兼容旧行为)
    should_prewarm = is_http or os.getenv("PLATFORM_DOCS_PREWARM", "false").lower() in (
        "true",
        "1",
        "yes",
    )
    if should_prewarm:
        # Embedding 模型 + collection 一次加载 (lazy 兜底仍存在, 这里只是提前)
        _ensure_project(PROJECT_ID)  # prewarm daemon 启动默认 project
        # Reranker 必须显式 prewarm: _ensure_project 不会调它, 不 prewarm 会让
        # 首次 search_docs 命中 reranker 二次冷启动 (~15-30s), 加上 embedding 已
        # 经 warmed 但 reranker 还没 → 整体超过 60s client timeout 概率高。
        try:
            _ensure_reranker()
        except Exception as exc:  # noqa: BLE001
            _flog(f"[init] reranker prewarm skipped: {exc!s}")
        # 模型已加载 ≠ CUDA kernel 已暖: 首次真实 inference 还要编译 kernel(数秒)。
        # 跑一次哑查询(embed + rerank)把 kernel 也预热, 让任意消费者(agent / Claude Code /
        # 人)首查就快, 不因首查超时被推回 grep(提高 MCP 命中可靠性, 不止靠 agent 端重试)。
        try:
            from codev_platform.chroma._models import _encode_query
            from codev_platform.chroma._reranker import _rerank_scores
            _encode_query("warmup")
            _rerank_scores("warmup", ["warmup document"])
            _flog("[init] CUDA kernel warmed (dummy embed + rerank)")
        except Exception as exc:  # noqa: BLE001
            _flog(f"[init] kernel warmup skipped: {exc!s}")

    if is_http:
        port = int(os.getenv("PLATFORM_DOCS_DAEMON_PORT", "18083"))
        await _run_http(port)
    else:
        await _run_stdio()


# 触发 _tools.py 的 @server.call_tool() 注册 (放文件末尾: 此时 server + 所有 _tools 依赖的
# 函数 / 常量 / dict / contextvar 均已定义, 避免循环 import 取到未定义符号)。
from codev_platform.chroma import _tools  # noqa: E402,F401


if __name__ == "__main__":
    # `python -m codev_platform.chroma.server` 把本文件作为 `__main__` 加载, 而 _tools.py
    # (line 565 触发, 其内 `from ...chroma.server import server`) 会把本模块作为 *规范名*
    # codev_platform.chroma.server 再加载一次 → 两个不同的 server 实例。@server.call_tool()
    # 注册在规范实例上, 若直接 asyncio.run(main()) 服务的是 __main__ 实例 (只有 list_tools,
    # 无 call_tool → tools/call 报 "Method not found")。故委派到规范模块的 main, 确保所服务的
    # server 与 _tools 注册 call_tool 的是同一实例。
    from codev_platform.chroma.server import main as _canonical_main
    asyncio.run(_canonical_main())
