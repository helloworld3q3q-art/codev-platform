"""memory 路由(M2):POST /memory 写记忆 / GET /memory 列作用域记忆.

传输层:解析 (org_id, user_id) 上下文 + DTO<->domain;存储在 SqlMemoryStore。
memory PG 未配 → 503。权限校验(allowed)是 M5,这里先不拦(单人期)。
"""
from __future__ import annotations

import sys

from fastapi import APIRouter, HTTPException, Query, Request

from codev_platform.agent import deps
from codev_platform.agent.memory_store import MemoryEntry, SCOPES
from codev_platform.agent.schemas import MemoryEntryOut, MemoryWriteRequest
from codev_platform.core import identity
from codev_platform.core.acl import memory_scope_access
from codev_platform.core.audit import audit_access
from codev_platform.core.config import load_config

router = APIRouter()


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


def _to_out(e: MemoryEntry) -> MemoryEntryOut:
    return MemoryEntryOut(
        id=e.id, scope=e.scope, scope_ref=e.scope_ref, owner_user_id=e.owner_user_id,
        content=e.content, org_id=e.org_id, kind=e.kind, topic_key=e.topic_key,
        is_redline=e.is_redline, status=e.status,
    )


@router.post("/memory", response_model=MemoryEntryOut)
def write_memory(req: MemoryWriteRequest, request: Request) -> MemoryEntryOut:
    store = deps.get_memory_store()
    if store is None:
        raise HTTPException(status_code=503, detail="memory PG 未启用(配 memory.pg_dsn + session_backend)")
    if req.scope not in SCOPES:
        raise HTTPException(status_code=400, detail=f"scope 须为 {SCOPES}")
    try:  # 非法 X-Org-Id / X-User-Id → 400
        org_id, user_id = _resolve_identity(request)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e
    # personal 作用域的 scope_ref 恒为写入者 user_id(plan §3.2 语义)——强制对齐,
    # 不信任 client 传的 scope_ref,杜绝"以别人名义写个人记忆"。其它作用域用 client 给的 ref。
    scope_ref = user_id if req.scope == "personal" else req.scope_ref
    # ACL 统一闸 (单一真值源 core/acl.py): personal/project/org/team 全走 memory_scope_access,
    # 不再对 project 单独 can_access (避免双重判定)。personal 数据归一已在上行完成。
    _ident = getattr(request.state, "identity", None)
    _dec = memory_scope_access(load_config(), _ident, req.scope, scope_ref)
    audit_access("agent-memory", _ident, scope_ref, _dec)
    if not _dec.allowed:
        raise HTTPException(status_code=403, detail="forbidden: memory scope access denied")
    entry = MemoryEntry(
        id="", scope=req.scope, scope_ref=scope_ref, owner_user_id=user_id,
        content=req.content, org_id=org_id, kind=req.kind, topic_key=req.topic_key,
        is_redline=req.is_redline,
    )
    try:
        eid = store.write(entry)
    except Exception as e:  # noqa: BLE001 — DB 错转 503;完整异常只进 server 日志,不回客户端(防泄漏拓扑)
        print(f"[memory.write] {type(e).__name__}: {e}", file=sys.stderr)
        raise HTTPException(status_code=503, detail="memory store unavailable") from e
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
    if store is None:
        raise HTTPException(status_code=503, detail="memory PG 未启用")
    # ACL 统一闸 (单一真值源 core/acl.py): personal 只能读本人 / project token 越权 → 403,
    # org/team passthrough 放行 token deny。recall ≠ read,隐私边界与 write 同源。
    try:  # 非法 X-Org-Id / X-User-Id → 400
        org_id, user_id = _resolve_identity(request)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e
    _ident = getattr(request.state, "identity", None)
    _dec = memory_scope_access(load_config(), _ident, scope, scope_ref)
    audit_access("agent-memory", _ident, scope_ref, _dec)
    if not _dec.allowed:
        raise HTTPException(status_code=403, detail="forbidden: memory scope access denied")
    try:
        entries = store.list_scope(scope, scope_ref, org_id=org_id, limit=limit)
    except Exception as e:  # noqa: BLE001 — 同 write:完整异常只进 server 日志
        print(f"[memory.list] {type(e).__name__}: {e}", file=sys.stderr)
        raise HTTPException(status_code=503, detail="memory store unavailable") from e
    return [_to_out(e) for e in entries]
