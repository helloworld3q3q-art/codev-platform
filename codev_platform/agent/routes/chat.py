"""问答路由:POST /chat(A 只读能力主入口). 后续 /chat/stream 也加这里."""
from __future__ import annotations

from fastapi import APIRouter, HTTPException

from codev_platform.agent import config as acfg, deps
from codev_platform.agent.brain.base import Message
from codev_platform.agent.loop import AgentLoop
from codev_platform.agent.schemas import ChatRequest, ChatResponse, StepOut
from codev_platform.agent.trace import Trace

router = APIRouter()


@router.post("/chat", response_model=ChatResponse)
def chat(req: ChatRequest) -> ChatResponse:
    cfg = acfg.agent_cfg()
    try:
        provider = deps.get_provider()
    except RuntimeError as e:
        raise HTTPException(status_code=503, detail=str(e)) from e

    sessions = deps.get_sessions()
    sid = req.session_id if (req.session_id and sessions.has(req.session_id)) else sessions.new()
    history = sessions.get(sid)

    loop = AgentLoop(provider, deps.get_registry(), max_steps=req.max_steps or acfg.max_steps(cfg))
    trace = Trace(sid, provider.name, provider.model)
    result = loop.run(req.question, history=history, trace=trace)

    sessions.append(sid, Message(role="user", content=req.question),
                    Message(role="assistant", content=result.answer))

    return ChatResponse(
        session_id=sid,
        answer=result.answer,
        steps=[StepOut(n=s.n, thought=s.thought, tool=s.tool, args=s.args, result_summary=s.result_summary)
               for s in result.steps],
        usage=result.usage,
        stop_reason=result.stop_reason,
    )
