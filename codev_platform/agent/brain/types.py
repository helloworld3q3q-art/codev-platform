"""中性类型 — agent loop 与 provider 之间的通用货币.

独立成文件:被 loop / tools / 各 provider / service 广泛 import,
与 LLMProvider 抽象(base.py)分开,避免类型改动牵动抽象、降低耦合。
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass
class ToolCall:
    """模型要求调用一个工具."""
    id: str
    name: str
    args: dict[str, Any]


@dataclass
class ToolResult:
    """工具执行结果,回灌给模型."""
    call_id: str
    content: str
    is_error: bool = False


@dataclass
class Message:
    """中性会话消息.

    role: "user" | "assistant" | "tool"
    - user/assistant: content 为文本
    - assistant 发起工具调用: tool_calls 非空
    - tool: 工具结果,tool_call_id 指向对应 ToolCall.id,content 为结果文本
    """
    role: str
    content: str | None = None
    tool_calls: list[ToolCall] = field(default_factory=list)
    tool_call_id: str | None = None


@dataclass
class AssistantTurn:
    """provider.chat 的一轮返回(中性).

    stop_reason: "tool_use"(要调工具) | "end"(最终答案) | "max_tokens" | "error"
    """
    text: str | None
    tool_calls: list[ToolCall]
    stop_reason: str
    usage: dict[str, Any] = field(default_factory=dict)


@dataclass
class StreamEvent:
    """流式事件(SSE 用). kind: "token" | "tool_call" | "done" | "error"."""
    kind: str
    data: Any
