"""问答路由:POST /chat(A 只读能力主入口). 后续 /chat/stream 也加这里.

传输层:只做 HTTP DTO <-> domain 映射 + 错误码转换;编排逻辑在 services.ChatService。
"""
from __future__ import annotations

from fastapi import APIRouter, HTTPException, Request

from codev_platform.agent import deps
from codev_platform.agent.schemas import ChatRequest, ChatResponse, StepOut
from codev_platform.agent.services.chat_service import ChatOutcome
from codev_platform.core import identity

router = APIRouter()


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


@router.post("/chat", response_model=ChatResponse)
def chat(req: ChatRequest, request: Request) -> ChatResponse:
    user_id = identity.resolve_from_request(request.headers)  # X-User-Id > env > 'local'
    try:
        outcome = deps.get_chat_service().ask(
            req.question, req.session_id, req.max_steps, user_id=user_id)
    except RuntimeError as e:  # provider 缺 key 等 -> 503
        raise HTTPException(status_code=503, detail=str(e)) from e
    return _to_response(outcome)
