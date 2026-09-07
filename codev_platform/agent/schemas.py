"""HTTP 请求/响应模型(pydantic)."""
from __future__ import annotations

from datetime import datetime
from typing import Any

from pydantic import BaseModel, Field


class ChatRequest(BaseModel):
    question: str = Field(..., description="问题")
    session_id: str | None = Field(None, description="多轮会话 id;省略=新会话")
    max_steps: int | None = Field(None, description="本次循环 step 上限;省略走 config")
    project_id: str | None = Field(None, description="按此 project 路由工具(P2);省略走 X-Project-Id 头 / cwd")
    task_id: str | None = Field(None, description="M1:绑定的任务 id;同 task_id 记忆优先召回, 跨会话恢复任务上下文")


class StepOut(BaseModel):
    n: int
    thought: str | None = None
    tool: str | None = None
    args: Any = None
    result_summary: str | None = None


class ChatResponse(BaseModel):
    session_id: str
    answer: str
    steps: list[StepOut]
    usage: dict[str, Any]
    stop_reason: str


# ---- 会话(sessions 域,plan §四)----
# 与 chat schema 同文件但独立分节(内聚到"会话域");路由实现在 routes/sessions.py,不塞 chat.py。

class SessionOut(BaseModel):
    """会话摘要(GET /sessions 单项)。session_pg.SessionMeta 的 HTTP 投影。"""

    session_id: str
    title: str
    message_count: int
    created_at: datetime | None = None
    updated_at: datetime | None = None


class MessageOut(BaseModel):
    """历史消息(GET /sessions/messages 单项)。assistant 携带工具调用流 steps + usage(从 extra 还原)。"""

    role: str
    content: str
    steps: list[StepOut] = Field(default_factory=list)
    usage: dict[str, Any] = Field(default_factory=dict)  # token/缓存(历史可观测,Phase 8)


class ProviderOut(BaseModel):
    name: str
    model: str
    configured: bool


class HealthOut(BaseModel):
    status: str
    provider: str
    model: str


# ---- memory(M2)----

class MemoryWriteRequest(BaseModel):
    scope: str = Field(..., description="org | team | project | personal")
    scope_ref: str = Field(..., description="org='org' / team_id / project_id / user_id")
    content: str = Field(..., description="记忆内容")
    kind: str | None = Field(None, description="preference | fact | task ...")
    topic_key: str | None = Field(None, description="冲突检测键(M3 用)")
    is_redline: bool = Field(False, description="org 硬约束(冲突最高优先)")


class MemoryEntryOut(BaseModel):
    id: str
    scope: str
    scope_ref: str
    owner_user_id: str
    content: str
    org_id: str
    kind: str | None = None
    topic_key: str | None = None
    is_redline: bool = False
    status: str = "active"


# ---- 任务状态机(M1)----

class TaskStateRequest(BaseModel):
    task_id: str = Field(..., min_length=1, description="任务 id")
    task_state: str = Field(..., description="active | blocked | done | archived")


class TaskStateResponse(BaseModel):
    task_id: str
    task_state: str
    updated: int = Field(..., description="更新的记忆条数(0=无匹配/无权改)")
