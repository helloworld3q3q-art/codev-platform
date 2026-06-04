"""HTTP 请求/响应模型(pydantic)."""
from __future__ import annotations

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
