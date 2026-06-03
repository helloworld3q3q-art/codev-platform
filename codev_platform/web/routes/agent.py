"""Agent 路由 (B1, plan §十五 Agent) —— web 前门把已认证身份代理到 codev-agent /chat。

POST /api/v1/agent/chat  代理对话 (鉴权前门 → AgentClient → codev-agent)

web 只做鉴权 + 身份转发, 绝不重写 agent 业务逻辑 (复用成熟 ChatService):
require_project_access (core.acl 单一真值源) 先校验 X-Project-Id, 通过后把已认证 identity
经 AgentClient 签 X-Identity 信物代理到 agent; 下游不可达 → UPSTREAM_UNAVAILABLE(503) 走 envelope。
"""
from __future__ import annotations

from fastapi import APIRouter, Depends, Request

from codev_platform.core.config import load_config
from codev_platform.core.httpkit.envelope import CommonResult, ok
from codev_platform.core.httpkit.permissions import require_project_access
from codev_platform.web.integrations.agent_client import AgentClient
from codev_platform.web.schemas.agent import ChatData, ChatRequest

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
