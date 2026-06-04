"""问答路由:POST /chat(A 只读能力主入口). 后续 /chat/stream 也加这里.

传输层:只做 HTTP DTO <-> domain 映射 + 错误码转换;编排逻辑在 services.ChatService。
"""
from __future__ import annotations

import logging

from fastapi import APIRouter, HTTPException, Request

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


@router.post("/chat", response_model=ChatResponse)
def chat(req: ChatRequest, request: Request) -> ChatResponse:
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
    _ident = getattr(request.state, "identity", None)
    _dec = can_access(load_config(), _ident, project_id)  # project_id 可能 None
    audit_access("agent-chat", _ident, project_id, _dec)
    if not _dec.allowed:  # 权限 → ACCESS_DENIED(403)
        raise HTTPException(status_code=403, detail=to_http_detail(
            "forbidden: project access denied", ErrorCode.ACCESS_DENIED))
    try:
        outcome = deps.get_chat_service().ask(
            req.question, req.session_id, req.max_steps,
            user_id=user_id, project_id=project_id, org_id=org_id, task_id=req.task_id,
            identity=_ident)  # P0: 透真 identity 给工具, remember 写 memory 走同一道 scope_decision
    except RuntimeError as e:  # provider 缺 key / 下游不可用 → UPSTREAM_UNAVAILABLE(503);str(e) 仅日志
        _log.warning("chat upstream unavailable: %s", e)
        raise HTTPException(status_code=503, detail=to_http_detail(
            "agent provider unavailable", ErrorCode.UPSTREAM_UNAVAILABLE)) from e
    return _to_response(outcome)
