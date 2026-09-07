"""LLMProvider 抽象. 中性类型见 types.py.

每个 provider 适配器只干一件事:中性 Message/ToolCall <-> 自家原生格式 双向翻译。
"""
from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any
from collections.abc import Iterator

from codev_platform.agent.brain.types import AssistantTurn, Message, StreamEvent


class LLMProvider(ABC):
    name: str = "base"
    model: str = ""

    @abstractmethod
    def chat(
        self,
        system: str,
        messages: list[Message],
        tools: list[dict[str, Any]],
    ) -> AssistantTurn:
        """一轮对话. tools 是中性 tool spec 列表(name/description/input_schema)."""
        raise NotImplementedError

    def stream(
        self,
        system: str,
        messages: list[Message],
        tools: list[dict[str, Any]],
    ) -> Iterator[StreamEvent]:
        """流式版本. 默认未实现(P2),子类可覆盖."""
        raise NotImplementedError("stream not implemented for this provider")
