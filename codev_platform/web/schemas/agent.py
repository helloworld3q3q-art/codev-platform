"""Agent 对话路由请求 / 响应模型 (B1, plan §十五 Agent)。

web 前门代理到 codev-agent /chat 的对外契约 (camelCase, 对齐前端)。字段语义对齐
agent.schemas.ChatRequest/ChatResponse, 但走 web 命名规范 (questionId 风格 camelCase)。
"""
from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field


class ChatRequest(BaseModel):
    """POST /api/v1/agent/chat 请求体。"""

    question: str = Field(..., min_length=1, description="问题")
    sessionId: str | None = Field(None, description="多轮会话 id; 省略=新会话")
    maxSteps: int | None = Field(None, ge=1, description="本次循环 step 上限; 省略走 agent config")


class ChatStep(BaseModel):
    """单步轨迹 (对齐 agent.schemas.StepOut)。"""

    n: int
    thought: str | None = None
    tool: str | None = None
    args: Any = None
    resultSummary: str | None = None


class ChatData(BaseModel):
    """对话结果 (agent ChatResponse 的 web 投影)。"""

    sessionId: str = Field(..., description="会话 id (回填, 用于下轮)")
    answer: str = Field(..., description="最终回答")
    steps: list[ChatStep] = Field(default_factory=list, description="推理 / 工具调用轨迹")
    usage: dict[str, Any] = Field(default_factory=dict, description="token / 调用统计")
    stopReason: str = Field(..., description="收尾原因 (final / max_steps / ...)")

    @classmethod
    def of(cls, raw: dict) -> ChatData:
        """agent /chat 返回体 (snake_case) → web 投影 (camelCase)。"""
        return cls(
            sessionId=raw.get("session_id", ""),
            answer=raw.get("answer", ""),
            steps=[
                ChatStep(
                    n=s.get("n", 0),
                    thought=s.get("thought"),
                    tool=s.get("tool"),
                    args=s.get("args"),
                    resultSummary=s.get("result_summary"),
                )
                for s in (raw.get("steps") or [])
            ],
            usage=raw.get("usage") or {},
            stopReason=raw.get("stop_reason", ""),
        )


# ---- 会话(sessions 域)----

class SessionItem(BaseModel):
    """会话摘要 (agent SessionOut 的 web 投影, camelCase)。"""

    sessionId: str = Field(..., description="会话 id")
    title: str = Field("", description="标题 (首条 user 消息派生)")
    messageCount: int = Field(0, description="消息条数")
    createdAt: str | None = Field(None, description="创建时间 (ISO8601)")
    updatedAt: str | None = Field(None, description="最近活跃时间 (ISO8601)")

    @classmethod
    def of(cls, raw: dict) -> SessionItem:
        return cls(
            sessionId=raw.get("session_id", ""),
            title=raw.get("title", ""),
            messageCount=raw.get("message_count", 0),
            createdAt=raw.get("created_at"),
            updatedAt=raw.get("updated_at"),
        )


class SessionMessageItem(BaseModel):
    """历史消息 (agent MessageOut 的 web 投影)。assistant 携带工具调用流 steps。"""

    role: str = Field(..., description="user | assistant")
    content: str = Field("", description="消息正文")
    steps: list[ChatStep] = Field(default_factory=list, description="工具调用流(assistant)")

    @classmethod
    def of(cls, raw: dict) -> SessionMessageItem:
        return cls(
            role=raw.get("role", ""),
            content=raw.get("content", ""),
            steps=[
                ChatStep(
                    n=s.get("n", 0),
                    thought=s.get("thought"),
                    tool=s.get("tool"),
                    args=s.get("args"),
                    resultSummary=s.get("result_summary"),
                )
                for s in (raw.get("steps") or [])
            ],
        )
