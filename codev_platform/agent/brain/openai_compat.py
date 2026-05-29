"""OpenAI 兼容适配器 — 覆盖 GPT / DeepSeek / 通义千问.

三家都走 OpenAI chat-completions 协议,靠 base_url + model + key 区分;
本适配器只干一件事:中性 Message/ToolCall/ToolResult <-> OpenAI tools/tool_calls 双向翻译。
"""
from __future__ import annotations

import json
from typing import Any

from codev_platform.agent.brain import (
    AssistantTurn,
    LLMProvider,
    Message,
    ToolCall,
)


class OpenAICompatProvider(LLMProvider):
    def __init__(self, api_key: str, model: str, base_url: str | None = None,
                 name: str = "openai") -> None:
        import openai  # 延迟导入:没装 agent extra 时不影响其它子命令
        self._client = openai.OpenAI(api_key=api_key, base_url=base_url or None)
        self.model = model
        self.name = name

    # ---- 中性 -> OpenAI messages ----
    @staticmethod
    def _to_native(system: str, messages: list[Message]) -> list[dict[str, Any]]:
        out: list[dict[str, Any]] = [{"role": "system", "content": system}]
        for m in messages:
            if m.role == "tool":
                out.append({"role": "tool", "tool_call_id": m.tool_call_id, "content": m.content or ""})
            elif m.role == "assistant" and m.tool_calls:
                am: dict[str, Any] = {
                    "role": "assistant",
                    "content": m.content or None,
                    "tool_calls": [
                        {"id": tc.id, "type": "function",
                         "function": {"name": tc.name, "arguments": json.dumps(tc.args, ensure_ascii=False)}}
                        for tc in m.tool_calls
                    ],
                }
                # 思考模型(deepseek-reasoner/v4 等)要求把上一轮 reasoning_content 原样回传
                rc = m.extra.get("reasoning_content")
                if rc:
                    am["reasoning_content"] = rc
                out.append(am)
            else:
                out.append({"role": m.role, "content": m.content or ""})
        return out

    @staticmethod
    def _tools_native(specs: list[dict[str, Any]]) -> list[dict[str, Any]]:
        return [
            {"type": "function",
             "function": {"name": s["name"], "description": s["description"], "parameters": s["input_schema"]}}
            for s in specs
        ]

    def chat(self, system: str, messages: list[Message], tools: list[dict[str, Any]]) -> AssistantTurn:
        kwargs: dict[str, Any] = {
            "model": self.model,
            "messages": self._to_native(system, messages),
        }
        if tools:
            kwargs["tools"] = self._tools_native(tools)
        resp = self._client.chat.completions.create(**kwargs)
        choice = resp.choices[0]
        msg = choice.message

        tool_calls: list[ToolCall] = []
        for tc in (msg.tool_calls or []):
            try:
                parsed = json.loads(tc.function.arguments or "{}")
            except (json.JSONDecodeError, TypeError):
                parsed = {}
            tool_calls.append(ToolCall(id=tc.id, name=tc.function.name, args=parsed))

        stop = "tool_use" if tool_calls else "end"
        usage = {}
        if resp.usage:
            usage = {
                "input_tokens": getattr(resp.usage, "prompt_tokens", 0),
                "output_tokens": getattr(resp.usage, "completion_tokens", 0),
            }
        # 思考模型返回 reasoning_content,存进 extra 以便下一轮原样回传
        extra: dict[str, Any] = {}
        rc = getattr(msg, "reasoning_content", None)
        if rc:
            extra["reasoning_content"] = rc
        return AssistantTurn(
            text=msg.content or None,
            tool_calls=tool_calls,
            stop_reason=stop,
            usage=usage,
            extra=extra,
        )
