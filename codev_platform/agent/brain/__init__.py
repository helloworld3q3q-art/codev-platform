"""LLM provider 抽象层 + 各厂商适配器.

公开 API:中性类型 + LLMProvider 抽象。调用方统一 `from ...agent.brain import X`,
不感知 types.py / base.py 的内部拆分。
"""
from codev_platform.agent.brain.base import LLMProvider
from codev_platform.agent.brain.types import (
    AssistantTurn,
    Message,
    StreamEvent,
    ToolCall,
    ToolResult,
)

__all__ = [
    "LLMProvider",
    "Message",
    "ToolCall",
    "ToolResult",
    "AssistantTurn",
    "StreamEvent",
]
