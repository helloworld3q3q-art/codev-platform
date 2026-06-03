"""Memory 路由 (B1) —— web 鉴权前门, 代理到 codev-agent /memory。

web 不重写记忆业务逻辑: 经 require_project_access 拿到可信身份, 交 AgentClient 签 X-Identity
代理给成熟的 agent 子系统; scope 准入决策在 agent 侧 (不复制 ACL)。本层只守两条红线:
  - org_id 取 web 已认证身份 (经 X-Identity 签发), 绝不信 client;
  - personal 的 scopeRef 强制 = 本人 user_id, 杜绝以他人名义写个人记忆。
agent 不可达 / 超时 → AgentClient 抛 PlatformError(UPSTREAM_UNAVAILABLE) → 统一异常处理器转 503。
"""
from __future__ import annotations

from fastapi import APIRouter, Depends, Query, Request

from codev_platform.core.config import load_config
from codev_platform.core.httpkit.envelope import CommonResult, ok
from codev_platform.core.httpkit.permissions import require_project_access
from codev_platform.web.integrations.agent_client import AgentClient
from codev_platform.web.schemas.memory import MemoryItem, MemoryWriteRequest

router = APIRouter()

_TAG = "MemoryAPI-记忆"

# 进程内默认 client (无状态, 每请求签新令牌; 测试 monkeypatch 本符号)。
agent_client = AgentClient(load_config())


def _rid(request: Request) -> str | None:
    return getattr(request.state, "request_id", None)


@router.post(
    "/api/v1/memory",
    tags=[_TAG],
    summary="记忆-写入",
    operation_id="writeMemory",
    response_model=CommonResult[MemoryItem],
)
def write_memory(
    request: Request,
    body: MemoryWriteRequest,
    ctx=Depends(require_project_access),
) -> CommonResult[MemoryItem]:
    identity, _project_id = ctx
    # personal scopeRef 强制 = 本人 (红线); 其它 scope 用 client 给的 ref, 准入由 agent 决策。
    scope_ref = identity.user_id if body.scope == "personal" else body.scopeRef
    payload = {
        "scope": body.scope, "scope_ref": scope_ref, "content": body.content,
        "kind": body.kind, "topic_key": body.topicKey, "ttl": body.ttl,
    }
    entry = agent_client.memory_write(identity, payload)
    return ok(MemoryItem.of(entry), request_id=_rid(request))


@router.get(
    "/api/v1/memory",
    tags=[_TAG],
    summary="记忆-列表(按作用域)",
    operation_id="listMemory",
    response_model=CommonResult[list[MemoryItem]],
)
def list_memory(
    request: Request,
    scope: str = Query(..., min_length=1, description="org|team|project|personal"),
    scopeRef: str = Query(..., min_length=1, description="该 scope 的 ref"),
    limit: int = Query(100, ge=1, le=500, description="返回上限"),
    ctx=Depends(require_project_access),
) -> CommonResult[list[MemoryItem]]:
    identity, _project_id = ctx
    ref = identity.user_id if scope == "personal" else scopeRef
    entries = agent_client.memory_list(identity, {"scope": scope, "scope_ref": ref, "limit": limit})
    items = entries if isinstance(entries, list) else entries.get("data", entries)
    return ok([MemoryItem.of(e) for e in items], request_id=_rid(request))
