"""Agent 路由 (B1, plan §十五 Agent) —— web 前门把已认证身份代理到 codev-agent /chat。

POST /api/v1/agent/chat  代理对话 (鉴权前门 → AgentClient → codev-agent)

web 只做鉴权 + 身份转发, 绝不重写 agent 业务逻辑 (复用成熟 ChatService):
require_project_access (core.acl 单一真值源) 先校验 X-Project-Id, 通过后把已认证 identity
经 AgentClient 签 X-Identity 信物代理到 agent; 下游不可达 → UPSTREAM_UNAVAILABLE(503) 走 envelope。
"""
from __future__ import annotations

from fastapi import APIRouter, Depends, Query, Request

from codev_platform.core.config import load_config
from codev_platform.core.httpkit.envelope import CommonResult, ok
from codev_platform.core.httpkit.permissions import require_project_access
from codev_platform.web.integrations.agent_client import AgentClient
from codev_platform.web.schemas.agent import (
    ChatData,
    ChatRequest,
    SessionItem,
    SessionMessageItem,
)

router = APIRouter()

# 进程内默认 agent 代理客户端 (无状态: 每请求签新令牌 + 短连接)。测试可替换此实例。
agent_client = AgentClient(load_config())


def _rid(request: Request) -> str | None:
    return getattr(request.state, "request_id", None)


@router.post(
    "/api/v1/agent/chat",
    tags=["AgentAPI-对话"],
    summary="对话-代理到 agent 后端",
    operation_id="agentChat",
    response_model=CommonResult[ChatData],
)
def agent_chat(
    request: Request,
    body: ChatRequest,
    ctx=Depends(require_project_access),
) -> CommonResult[ChatData]:
    identity, project_id = ctx
    payload = {
        "question": body.question,
        "session_id": body.sessionId,
        "max_steps": body.maxSteps,
        "project_id": project_id,  # 经鉴权的项目 → agent 按此路由工具
    }
    raw = agent_client.chat(identity, payload)
    return ok(ChatData.of(raw), request_id=_rid(request))


def _as_list(raw) -> list:
    """agent GET 端点直接返 JSON 数组(FastAPI 序列化 list[Model]);兼容偶发 {data:[...]} 包裹。"""
    if isinstance(raw, list):
        return raw
    if isinstance(raw, dict):
        return raw.get("data", []) or []
    return []


@router.get(
    "/api/v1/agent/sessions",
    tags=["AgentAPI-对话"],
    summary="会话-列表(按当前用户)",
    operation_id="agentSessions",
    response_model=CommonResult[list[SessionItem]],
)
def agent_sessions(
    request: Request,
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
    ctx=Depends(require_project_access),
) -> CommonResult[list[SessionItem]]:
    identity, project_id = ctx
    # 按项目隔离:把鉴权后的 project_id 透给 agent, 只列本项目会话(None 时 AgentClient 自动不带该参)。
    raw = agent_client.list_sessions(identity, {"limit": limit, "offset": offset, "project_id": project_id})
    return ok([SessionItem.of(s) for s in _as_list(raw)], request_id=_rid(request))


@router.get(
    "/api/v1/agent/sessions/messages",
    tags=["AgentAPI-对话"],
    summary="会话-历史消息",
    operation_id="agentSessionMessages",
    response_model=CommonResult[list[SessionMessageItem]],
)
def agent_session_messages(
    request: Request,
    sessionId: str = Query(..., min_length=1, description="会话 id"),
    ctx=Depends(require_project_access),
) -> CommonResult[list[SessionMessageItem]]:
    identity, _project_id = ctx
    raw = agent_client.session_messages(identity, {"session_id": sessionId})
    return ok([SessionMessageItem.of(m) for m in _as_list(raw)], request_id=_rid(request))
