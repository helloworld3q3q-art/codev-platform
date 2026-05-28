"""HTTP 请求/响应模型(pydantic)."""
from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field


class ChatRequest(BaseModel):
    question: str = Field(..., description="问题")
    session_id: str | None = Field(None, description="多轮会话 id;省略=新会话")
    max_steps: int | None = Field(None, description="本次循环 step 上限;省略走 config")


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
