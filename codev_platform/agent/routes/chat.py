"""问答路由:POST /chat(A 只读能力主入口). 后续 /chat/stream 也加这里.

传输层:只做 HTTP DTO <-> domain 映射 + 错误码转换;编排逻辑在 services.ChatService。
"""
from __future__ import annotations

import json
import logging

import anyio
from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import StreamingResponse

from codev_platform.agent import deps
from codev_platform.agent.schemas import ChatRequest, ChatResponse, StepOut
from codev_platform.agent.services.chat_service import ChatOutcome
from codev_platform.core import identity
from codev_platform.core.acl import can_access
from codev_platform.core.audit import audit_access
from codev_platform.core.config import load_config
from codev_platform.core.errors import ErrorCode, to_http_detail
from codev_platform.core.project_id import ProjectIdError, validate as validate_project_id

router = APIRouter()
_log = logging.getLogger(__name__)


def _to_response(outcome: ChatOutcome) -> ChatResponse:
    r = outcome.result
    return ChatResponse(
        session_id=outcome.session_id,
        answer=r.answer,
        steps=[StepOut(n=s.n, thought=s.thought, tool=s.tool, args=s.args, result_summary=s.result_summary)
               for s in r.steps],
        usage=r.usage,
        stop_reason=r.stop_reason,
    )


def _resolve_project_id(req: ChatRequest, request: Request) -> str | None:
    """X-Project-Id 头 > body.project_id > None(工具回退 cwd,单项目兼容)。

    非空值必须过 validate()(防路径穿越 / chroma collection 污染);
    非法抛 ProjectIdError, 由 chat() 转 400。空值仍返 None 保留 cwd 回退。
    """
    raw: str | None = None
    for key in ("X-Project-Id", "x-project-id", "X-PROJECT-ID"):
        v = request.headers.get(key)
        if v:
            raw = v.strip()
            break
    if raw is None:
        raw = req.project_id
    if raw is None or not str(raw).strip():
        return None
    return validate_project_id(raw)


def _resolve_identity(request: Request) -> tuple[str, str]:
    """(user_id, org_id):优先用 gateway 中间件认证后写入 request.state.identity 的可信身份,
    没挂 gateway(dev 单机)才回退裸 header 解析。防 token 鉴权下持合法 token 者伪造他人 user/org。"""
    ident = getattr(request.state, "identity", None)
    if ident is not None:
        return ident.user_id, ident.org_id
    # 无中间件(dev 单机):回退 header(X-User-Id > env > 'local' / X-Org-Id > 'default')
    return (
        identity.resolve_from_request(request.headers),
        identity.resolve_org_from_request(request.headers),
    )


def _authorize(req: ChatRequest, request: Request) -> tuple[str, str, str | None, object | None]:
    """鉴权 + 入参解析(chat / chat_stream 单一真值源)。返回 (user_id, org_id, project_id, ident);
    非法身份/项目 → 400, 项目 ACL 拒 → 403(抛 HTTPException)。"""
    try:  # 非法 X-User-Id / X-Org-Id(含非法字符)→ 400,而非未捕获 500
        user_id, org_id = _resolve_identity(request)
    except ValueError as e:  # 入参非法 → INVALID_PARAMS;str(e) 仅日志
        _log.warning("chat identity rejected: %s", e)
        raise HTTPException(status_code=400, detail=to_http_detail(
            "invalid X-User-Id / X-Org-Id", ErrorCode.INVALID_PARAMS)) from e
    try:  # 非法 project_id(路径穿越 / 格式违规)→ 400,而非 500
        project_id = _resolve_project_id(req, request)  # X-Project-Id > body > None(cwd)
    except ProjectIdError as e:  # 入参非法 → INVALID_PARAMS;str(e) 仅日志
        _log.warning("chat project_id rejected: %s", e)
        raise HTTPException(status_code=400, detail=to_http_detail(
            "invalid project_id", ErrorCode.INVALID_PARAMS)) from e
    # 项目 ACL 闸(恒查):token 越权 / token 模式无显式 project_id → 403;passthrough 放行
    ident = getattr(request.state, "identity", None)
    dec = can_access(load_config(), ident, project_id)  # project_id 可能 None
    audit_access("agent-chat", ident, project_id, dec)
    if not dec.allowed:  # 权限 → ACCESS_DENIED(403)
        raise HTTPException(status_code=403, detail=to_http_detail(
            "forbidden: project access denied", ErrorCode.ACCESS_DENIED))
    return user_id, org_id, project_id, ident


@router.post("/chat", response_model=ChatResponse)
def chat(req: ChatRequest, request: Request) -> ChatResponse:
    user_id, org_id, project_id, ident = _authorize(req, request)
    try:
        outcome = deps.get_chat_service().ask(
            req.question, req.session_id, req.max_steps,
            user_id=user_id, project_id=project_id, org_id=org_id, task_id=req.task_id,
            identity=ident)  # P0: 透真 identity 给工具, remember 写 memory 走同一道 scope_decision
    except RuntimeError as e:  # provider 缺 key / 下游不可用 → UPSTREAM_UNAVAILABLE(503);str(e) 仅日志
        _log.warning("chat upstream unavailable: %s", e)
        raise HTTPException(status_code=503, detail=to_http_detail(
            "agent provider unavailable", ErrorCode.UPSTREAM_UNAVAILABLE)) from e
    return _to_response(outcome)


def _sse_frame(kind: str, data: object) -> str:
    """SSE 帧: 一行 data: {json}\\n\\n。前端按 kind 分发(token 增量 / step 工具步 / done / error)。"""
    return "data: " + json.dumps({"kind": kind, "data": data}, ensure_ascii=False) + "\n\n"


@router.post("/chat/stream")
def chat_stream(req: ChatRequest, request: Request) -> StreamingResponse:
    """流式问答(SSE)。鉴权/入参与 /chat 完全一致(_authorize 共享); 仅响应形态不同。

    鉴权失败(400/403)在开流前抛 HTTPException → 正常状态码。开流后下游 provider 错误无法
    再改 HTTP 状态(头已发) → 改发 error 事件帧, 前端据此回退非流式 /chat。
    """
    user_id, org_id, project_id, ident = _authorize(req, request)
    svc = deps.get_chat_service()

    # ask_stream 是同步生成器, 内部用 contextvar(RunContext)管运行身份。Starlette 默认迭代同步
    # 生成器会丢进 threadpool 且每次 __next__ 可能换线程 → set/reset_run_context 跨 Context 报
    # ValueError、loop 内工具也可能读不到 RunContext。故整段同步消费固定在**单个**工作线程
    # (to_thread.run_sync), SSE 帧经 memory stream 桥回事件循环逐帧发, contextvar 全程同线程一致。
    async def _events():
        send, recv = anyio.create_memory_object_stream(64)

        def _produce() -> None:
            try:
                for ev in svc.ask_stream(
                    req.question, req.session_id, req.max_steps,
                    user_id=user_id, project_id=project_id, org_id=org_id,
                    task_id=req.task_id, identity=ident):
                    anyio.from_thread.run(send.send, _sse_frame(ev.kind, ev.data))
            except RuntimeError as e:  # provider 缺 key / 下游不可用
                _log.warning("chat_stream upstream unavailable: %s", e)
                anyio.from_thread.run(send.send, _sse_frame(
                    "error", {"message": "agent provider unavailable",
                              "code": ErrorCode.UPSTREAM_UNAVAILABLE.value}))
            except Exception as e:  # noqa: BLE001 — 开流后任何异常都转 error 帧, 不让连接裸断
                _log.exception("chat_stream failed: %s", e)
                anyio.from_thread.run(send.send, _sse_frame("error", {"message": "internal error"}))
            finally:
                anyio.from_thread.run(send.aclose)

        async with anyio.create_task_group() as tg:
            tg.start_soon(anyio.to_thread.run_sync, _produce)
            async with recv:
                async for frame in recv:
                    yield frame

    # X-Accel-Buffering: no → 反代(nginx/caddy)不缓冲 SSE; no-cache 防中间层缓存。
    return StreamingResponse(_events(), media_type="text/event-stream",
                             headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})
