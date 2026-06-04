"""memory 路由(M2):POST /memory 写记忆 / GET /memory 列作用域记忆.

传输层:解析 (org_id, user_id) 上下文 + DTO<->domain;存储在 SqlMemoryStore。
memory PG 未配 → 503。权限校验(allowed)是 M5,这里先不拦(单人期)。
"""
from __future__ import annotations

import logging

from fastapi import APIRouter, HTTPException, Query, Request

from codev_platform.agent import deps
from codev_platform.agent.memory_store import MemoryEntry, SCOPES, TASK_STATES
from codev_platform.agent.schemas import (
    MemoryEntryOut, MemoryWriteRequest, TaskStateRequest, TaskStateResponse,
)
from codev_platform.core import identity
from codev_platform.core.acl import AccessDecision, can_access, memory_scope_access
from codev_platform.core.audit import audit_access
from codev_platform.core.config import load_config
from codev_platform.core.errors import ErrorCode, to_http_detail
from codev_platform.core.rbac import memory_scope_decision
from codev_platform.gateway.auth import Identity

router = APIRouter()
_log = logging.getLogger(__name__)


def _scope_decision(org_id: str, user_id: str, scope: str, scope_ref: str | None,
                    ident) -> AccessDecision:
    """作用域访问判定:
    - **project**: 由 P1 项目闸 `can_access`(token allowlist + org)决定 —— project memory 是项目资源,
      与全栈 project 门禁同源;**不叠加 M5 RBAC project_role**(避免 allowlist 与 RBAC 双重门禁打架,
      见 test_agent_memory_route_acl)。
    - **org/team**: 有 RBAC store → 查真实 Membership 走 core.rbac.memory_scope_decision(角色制);
      否则回退 interim core.acl.memory_scope_access(token 模式 org/team 拒)。
    - **personal**: 两路均做 owner==本人 自校验,同源。
    """
    if scope == "project":
        return can_access(load_config(), ident, scope_ref)
    store = deps.get_rbac_store()
    if store is not None:
        m = store.fetch_membership(org_id, user_id, None)
        return memory_scope_decision(scope, scope_ref, user_id, m)
    return memory_scope_access(load_config(), ident, scope, scope_ref)


def _resolve_identity(request: Request) -> tuple[str, str]:
    """(org_id, user_id):优先用 gateway 中间件认证后写入 request.state.identity 的可信身份,
    没挂 gateway(dev 单机)才回退裸 header 解析。防 token 鉴权下持合法 token 者伪造他人 user/org。"""
    ident = getattr(request.state, "identity", None)
    if ident is not None:
        return ident.org_id, ident.user_id
    # 无中间件(dev 单机):回退 header(X-Org-Id > 'default' / X-User-Id > env > 'local')
    return (
        identity.resolve_org_from_request(request.headers),
        identity.resolve_from_request(request.headers),
    )


def _effective_identity(request: Request, org_id: str, user_id: str):
    """返回用于 ACL 的身份对象。

    gateway 中间件存在时使用可信 identity；dev 单机未挂 gateway 时，按
    PassthroughAuthenticator 语义合成 advisory identity。否则 personal memory
    会因为 identity=None 而把"本人访问本人"误判成 403。
    """
    ident = getattr(request.state, "identity", None)
    if ident is not None:
        return ident
    return Identity(user_id=user_id, org_id=org_id, via="passthrough", all_projects=True)


def _to_out(e: MemoryEntry) -> MemoryEntryOut:
    return MemoryEntryOut(
        id=e.id, scope=e.scope, scope_ref=e.scope_ref, owner_user_id=e.owner_user_id,
        content=e.content, org_id=e.org_id, kind=e.kind, topic_key=e.topic_key,
        is_redline=e.is_redline, status=e.status,
    )


@router.post("/memory", response_model=MemoryEntryOut)
def write_memory(req: MemoryWriteRequest, request: Request) -> MemoryEntryOut:
    store = deps.get_memory_store()
    if store is None:  # PG 未配 → DEPENDENCY_MISSING(503)
        raise HTTPException(status_code=503, detail=to_http_detail(
            "memory store not configured (set memory.pg_dsn + session_backend)",
            ErrorCode.DEPENDENCY_MISSING))
    if req.scope not in SCOPES:  # 入参非法 → INVALID_PARAMS(400)
        raise HTTPException(status_code=400, detail=to_http_detail(
            f"scope must be one of {sorted(SCOPES)}", ErrorCode.INVALID_PARAMS))
    try:  # 非法 X-Org-Id / X-User-Id → 400
        org_id, user_id = _resolve_identity(request)
    except ValueError as e:  # 入参非法 → INVALID_PARAMS;str(e) 仅日志
        _log.warning("memory.write identity rejected: %s", e)
        raise HTTPException(status_code=400, detail=to_http_detail(
            "invalid X-Org-Id / X-User-Id", ErrorCode.INVALID_PARAMS)) from e
    # personal 作用域的 scope_ref 恒为写入者 user_id(plan §3.2 语义)——强制对齐,
    # 不信任 client 传的 scope_ref,杜绝"以别人名义写个人记忆"。其它作用域用 client 给的 ref。
    scope_ref = user_id if req.scope == "personal" else req.scope_ref
    # ACL 统一闸 (单一真值源 core/acl.py): personal/project/org/team 全走 memory_scope_access,
    # 不再对 project 单独 can_access (避免双重判定)。personal 数据归一已在上行完成。
    _ident = _effective_identity(request, org_id, user_id)
    _dec = _scope_decision(org_id, user_id, req.scope, scope_ref, _ident)
    audit_access("agent-memory", _ident, scope_ref, _dec)
    if not _dec.allowed:  # 权限 → ACCESS_DENIED(403)
        raise HTTPException(status_code=403, detail=to_http_detail(
            "forbidden: memory scope access denied", ErrorCode.ACCESS_DENIED))
    entry = MemoryEntry(
        id="", scope=req.scope, scope_ref=scope_ref, owner_user_id=user_id,
        content=req.content, org_id=org_id, kind=req.kind, topic_key=req.topic_key,
        is_redline=req.is_redline,
    )
    try:
        eid = store.write(entry)
    except Exception as e:  # noqa: BLE001 — DB 错转 503 UPSTREAM_UNAVAILABLE;完整异常只进日志,不回客户端(防泄漏拓扑)
        _log.warning("memory.write store error: %s: %s", type(e).__name__, e)
        raise HTTPException(status_code=503, detail=to_http_detail(
            "memory store unavailable", ErrorCode.UPSTREAM_UNAVAILABLE)) from e
    entry.id = eid
    return _to_out(entry)


@router.get("/memory", response_model=list[MemoryEntryOut])
def list_memory(
    request: Request,
    scope: str = Query(..., description="org|team|project|personal"),
    scope_ref: str = Query(..., description="该 scope 的 ref"),
    limit: int = Query(100, ge=1, le=500),
) -> list[MemoryEntryOut]:
    store = deps.get_memory_store()
    if store is None:  # PG 未配 → DEPENDENCY_MISSING(503)
        raise HTTPException(status_code=503, detail=to_http_detail(
            "memory store not configured", ErrorCode.DEPENDENCY_MISSING))
    # ACL 统一闸 (单一真值源 core/acl.py): personal 只能读本人 / project token 越权 → 403,
    # org/team passthrough 放行 token deny。recall ≠ read,隐私边界与 write 同源。
    try:  # 非法 X-Org-Id / X-User-Id → 400
        org_id, user_id = _resolve_identity(request)
    except ValueError as e:  # 入参非法 → INVALID_PARAMS;str(e) 仅日志
        _log.warning("memory.list identity rejected: %s", e)
        raise HTTPException(status_code=400, detail=to_http_detail(
            "invalid X-Org-Id / X-User-Id", ErrorCode.INVALID_PARAMS)) from e
    _ident = _effective_identity(request, org_id, user_id)
    _dec = _scope_decision(org_id, user_id, scope, scope_ref, _ident)
    audit_access("agent-memory", _ident, scope_ref, _dec)
    if not _dec.allowed:  # 权限 → ACCESS_DENIED(403)
        raise HTTPException(status_code=403, detail=to_http_detail(
            "forbidden: memory scope access denied", ErrorCode.ACCESS_DENIED))
    try:
        entries = store.list_scope(scope, scope_ref, org_id=org_id, limit=limit)
    except Exception as e:  # noqa: BLE001 — 同 write:完整异常只进日志,转 503 UPSTREAM_UNAVAILABLE
        _log.warning("memory.list store error: %s: %s", type(e).__name__, e)
        raise HTTPException(status_code=503, detail=to_http_detail(
            "memory store unavailable", ErrorCode.UPSTREAM_UNAVAILABLE)) from e
    return [_to_out(e) for e in entries]


@router.post("/memory/task/state", response_model=TaskStateResponse)
def set_task_state(req: TaskStateRequest, request: Request) -> TaskStateResponse:
    """更新任务状态(M1 状态机)。owner 限定: 只能改自己写的任务记忆状态(防改他人), org 隔离。"""
    store = deps.get_memory_store()
    if store is None:  # PG 未配 → DEPENDENCY_MISSING(503)
        raise HTTPException(status_code=503, detail=to_http_detail(
            "memory store not configured", ErrorCode.DEPENDENCY_MISSING))
    if req.task_state not in TASK_STATES:  # 入参非法 → INVALID_PARAMS(400)
        raise HTTPException(status_code=400, detail=to_http_detail(
            f"task_state must be one of {list(TASK_STATES)}", ErrorCode.INVALID_PARAMS))
    try:  # 非法 X-Org-Id / X-User-Id → 400
        org_id, user_id = _resolve_identity(request)
    except ValueError as e:  # 入参非法 → INVALID_PARAMS;str(e) 仅日志
        _log.warning("memory.task-state identity rejected: %s", e)
        raise HTTPException(status_code=400, detail=to_http_detail(
            "invalid X-Org-Id / X-User-Id", ErrorCode.INVALID_PARAMS)) from e
    try:
        # owner 限定即鉴权: 持 token 者只能改自己 org + 自己写的任务(store 内 WHERE owner_user_id)
        n = store.set_task_state(req.task_id, req.task_state, org_id=org_id, owner_user_id=user_id)
    except Exception as e:  # noqa: BLE001 — DB 错转 503;完整异常只进日志, 不回客户端
        _log.warning("memory.task-state store error: %s: %s", type(e).__name__, e)
        raise HTTPException(status_code=503, detail=to_http_detail(
            "memory store unavailable", ErrorCode.UPSTREAM_UNAVAILABLE)) from e
    return TaskStateResponse(task_id=req.task_id, task_state=req.task_state, updated=n)
