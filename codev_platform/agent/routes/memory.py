"""memory 路由(M2):POST /memory 写记忆 / GET /memory 列作用域记忆.

传输层:解析 (org_id, user_id) 上下文 + DTO<->domain;存储在 SqlMemoryStore。
memory PG 未配 → 503。权限校验(allowed)是 M5,这里先不拦(单人期)。
"""
from __future__ import annotations

from fastapi import APIRouter, HTTPException, Query, Request

from codev_platform.agent import deps
from codev_platform.agent.memory_store import MemoryEntry, SCOPES
from codev_platform.agent.schemas import MemoryEntryOut, MemoryWriteRequest
from codev_platform.core import identity

router = APIRouter()


def _org_id(request: Request) -> str:
    for key in ("X-Org-Id", "x-org-id", "X-ORG-ID"):
        v = request.headers.get(key)
        if v:
            return v.strip()
    return "default"


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
    org_id = _org_id(request)
    user_id = identity.resolve_from_request(request.headers)
    entry = MemoryEntry(
        id="", scope=req.scope, scope_ref=req.scope_ref, owner_user_id=user_id,
        content=req.content, org_id=org_id, kind=req.kind, topic_key=req.topic_key,
        is_redline=req.is_redline,
    )
    try:
        eid = store.write(entry)
    except Exception as e:  # noqa: BLE001 — DB 错转 503
        raise HTTPException(status_code=503, detail=f"memory write 失败: {e}") from e
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
    org_id = _org_id(request)
    try:
        entries = store.list_scope(scope, scope_ref, org_id=org_id, limit=limit)
    except Exception as e:  # noqa: BLE001
        raise HTTPException(status_code=503, detail=f"memory list 失败: {e}") from e
    return [_to_out(e) for e in entries]
