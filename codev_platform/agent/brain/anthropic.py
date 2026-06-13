"""Claude 适配器(Anthropic Messages API).

只干一件事:中性 Message/ToolCall/ToolResult <-> Anthropic 原生 blocks 双向翻译。
"""
from __future__ import annotations

from collections.abc import Iterator
from typing import Any

from codev_platform.agent.brain import (
    AssistantTurn,
    LLMProvider,
    Message,
    ToolCall,
)
from codev_platform.agent.brain.types import StreamEvent

_DEFAULT_MAX_TOKENS = 4096


class AnthropicProvider(LLMProvider):
    name = "claude"

    def __init__(self, api_key: str, model: str, max_tokens: int = _DEFAULT_MAX_TOKENS) -> None:
        import anthropic  # 延迟导入:没装 agent extra 时不影响其它子命令
        self._client = anthropic.Anthropic(api_key=api_key)
        self.model = model
        self.max_tokens = max_tokens

    # ---- 中性 -> Anthropic ----
    @staticmethod
    def _to_native(messages: list[Message]) -> list[dict[str, Any]]:
        """连续的 tool 结果合并进一个 user 消息(Anthropic 要求 tool_result 在 user 里)."""
        out: list[dict[str, Any]] = []
        pending_tool_results: list[dict[str, Any]] = []

        def flush_tools() -> None:
            if pending_tool_results:
                out.append({"role": "user", "content": list(pending_tool_results)})
                pending_tool_results.clear()

        for m in messages:
            if m.role == "tool":
                pending_tool_results.append({
                    "type": "tool_result",
                    "tool_use_id": m.tool_call_id,
                    "content": m.content or "",
                })
                continue
            flush_tools()
            if m.role == "assistant" and m.tool_calls:
                blocks: list[dict[str, Any]] = []
                if m.content:
                    blocks.append({"type": "text", "text": m.content})
                for tc in m.tool_calls:
                    blocks.append({"type": "tool_use", "id": tc.id, "name": tc.name, "input": tc.args})
                out.append({"role": "assistant", "content": blocks})
            else:
                out.append({"role": m.role, "content": m.content or ""})
        flush_tools()
        return out

    @staticmethod
    def _tools_native(specs: list[dict[str, Any]]) -> list[dict[str, Any]]:
        return [
            {"name": s["name"], "description": s["description"], "input_schema": s["input_schema"]}
            for s in specs
        ]

    @staticmethod
    def _assemble_turn(resp: Any) -> AssistantTurn:
        """Anthropic 完整 message -> 中性 AssistantTurn。chat() 与 stream() 共用,组装逻辑单一真值源。"""
        text_parts: list[str] = []
        tool_calls: list[ToolCall] = []
        for block in resp.content:
            if block.type == "text":
                text_parts.append(block.text)
            elif block.type == "tool_use":
                tool_calls.append(ToolCall(id=block.id, name=block.name, args=dict(block.input or {})))
        stop = "tool_use" if resp.stop_reason == "tool_use" else "end"
        usage = {
            "input_tokens": getattr(resp.usage, "input_tokens", 0),
            "output_tokens": getattr(resp.usage, "output_tokens", 0),
        }
        return AssistantTurn(
            text="\n".join(text_parts) if text_parts else None,
            tool_calls=tool_calls,
            stop_reason=stop,
            usage=usage,
        )

    def chat(self, system: str, messages: list[Message], tools: list[dict[str, Any]]) -> AssistantTurn:
        resp = self._client.messages.create(
            model=self.model,
            max_tokens=self.max_tokens,
            system=system,
            messages=self._to_native(messages),
            tools=self._tools_native(tools),
        )
        return self._assemble_turn(resp)

    def stream(self, system: str, messages: list[Message],
               tools: list[dict[str, Any]]) -> Iterator[StreamEvent]:
        """流式: 文本增量逐个 yield token, 结束 yield 终结 turn(组装含 tool_use/usage 的完整 AssistantTurn)。

        用 SDK 的 messages.stream 上下文: text_stream 出文本增量(打字), get_final_message
        给组装好的完整消息(含 tool_use 块 + usage), 复用 _assemble_turn 与 chat() 同一组装。
        """
        with self._client.messages.stream(
            model=self.model,
            max_tokens=self.max_tokens,
            system=system,
            messages=self._to_native(messages),
            tools=self._tools_native(tools),
        ) as stream:
            for delta in stream.text_stream:
                if delta:
                    yield StreamEvent("token", delta)
            final = stream.get_final_message()
        yield StreamEvent("turn", self._assemble_turn(final))
