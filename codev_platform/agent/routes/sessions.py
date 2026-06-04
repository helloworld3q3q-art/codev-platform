"""会话域路由:GET /sessions(列会话)/ GET /sessions/messages(取会话消息)。

**独立文件,不塞进 chat.py** —— 会话查询自成一域,与 chat 业务解耦(plan §三)。
路由只编排:解身份 → 调 store → 映射 schema,零 SQL、零业务分支(SQL 在 session store 层)。
会话按 (org_id, user_id) 物理隔离:store WHERE 强制带二者,**绝不跨用户列会话**(plan §七)。
"""
from __future__ import annotations

import logging

from fastapi import APIRouter, HTTPException, Query, Request

from codev_platform.agent import deps
from codev_platform.agent.routes._deps import resolve_user_org
from codev_platform.agent.schemas import MessageOut, SessionOut, StepOut
from codev_platform.core.errors import ErrorCode, to_http_detail

router = APIRouter()
_log = logging.getLogger(__name__)


def _steps_of(msg) -> list[StepOut]:
    """assistant 消息 extra 里持久化的工具调用流 → StepOut(回看历史用)。"""
    raw = (msg.extra or {}).get("steps") or []
    return [
        StepOut(n=s.get("n", 0), thought=s.get("thought"), tool=s.get("tool"),
                args=s.get("args"), result_summary=s.get("result_summary"))
        for s in raw
    ]


def _identity_or_400(request: Request) -> tuple[str, str]:
    try:
        return resolve_user_org(request)
    except ValueError as e:  # 非法 X-User-Id / X-Org-Id → 400;str(e) 仅日志
        _log.warning("sessions identity rejected: %s", e)
        raise HTTPException(status_code=400, detail=to_http_detail(
            "invalid X-User-Id / X-Org-Id", ErrorCode.INVALID_PARAMS)) from e


@router.get("/sessions", response_model=list[SessionOut])
def list_sessions(
    request: Request,
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
    project_id: str | None = Query(None, description="按项目过滤(web 经 X-Project-Id 传入)"),
) -> list[SessionOut]:
    user_id, org_id = _identity_or_400(request)
    metas = deps.get_sessions().list_sessions(
        user_id, org_id, project_id=project_id, limit=limit, offset=offset)
    return [
        SessionOut(
            session_id=m.session_id, title=m.title, message_count=m.message_count,
            created_at=m.created_at, updated_at=m.updated_at,
        )
        for m in metas
    ]


@router.get("/sessions/messages", response_model=list[MessageOut])
def list_session_messages(
    request: Request,
    session_id: str = Query(..., min_length=1, description="会话 id"),
) -> list[MessageOut]:
    user_id, org_id = _identity_or_400(request)
    msgs = deps.get_sessions().get(session_id, user_id, org_id=org_id)
    # 只回 UI 要渲染的对话轮:user / assistant 且有正文(中间纯 tool-call 轮 content 为空,过滤)。
    return [
        MessageOut(role=m.role, content=m.content, steps=_steps_of(m))
        for m in msgs
        if m.role in ("user", "assistant") and (m.content or "").strip()
    ]
