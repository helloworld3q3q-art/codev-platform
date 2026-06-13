"""OpenAI 兼容适配器 — 覆盖 GPT / DeepSeek / 通义千问.

三家都走 OpenAI chat-completions 协议,靠 base_url + model + key 区分;
本适配器只干一件事:中性 Message/ToolCall/ToolResult <-> OpenAI tools/tool_calls 双向翻译。
"""
from __future__ import annotations

import json
from collections.abc import Iterator
from typing import Any

from codev_platform.agent.brain import (
    AssistantTurn,
    LLMProvider,
    Message,
    ToolCall,
)
from codev_platform.agent.brain.types import StreamEvent


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

    @staticmethod
    def _extract_usage(u: Any) -> dict[str, int]:
        """resp.usage -> 中性 usage(含 prompt 缓存命中拆分)。纯逻辑, 可单测不触发 SDK。

        缓存命中价 ~1/50(deepseek hit $0.0028 vs miss $0.14), 是云成本最大杠杆 → 必须可观测
        才能优化。deepseek 直接给 prompt_cache_{hit,miss}_tokens; OpenAI 给 prompt_tokens_details
        .cached_tokens(只命中, miss = input - cached)。两种都不报的 provider 取 0, 中性不报噪。
        """
        if not u:
            return {}
        out = {
            "input_tokens": int(getattr(u, "prompt_tokens", 0) or 0),
            "output_tokens": int(getattr(u, "completion_tokens", 0) or 0),
        }
        hit = getattr(u, "prompt_cache_hit_tokens", None)
        miss = getattr(u, "prompt_cache_miss_tokens", None)
        if hit is None:  # OpenAI 风格
            details = getattr(u, "prompt_tokens_details", None)
            cached = getattr(details, "cached_tokens", None) if details else None
            if cached is not None:
                hit = cached
                miss = out["input_tokens"] - int(cached)
        out["cache_hit_tokens"] = int(hit or 0)
        out["cache_miss_tokens"] = int(miss or 0)
        return out

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
        usage = self._extract_usage(resp.usage)
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

    @staticmethod
    def _assemble_stream_turn(text_parts: list[str], tool_frags: dict[int, dict],
                              reasoning_parts: list[str], usage_obj: Any) -> AssistantTurn:
        """流式分片 -> 中性 AssistantTurn。tool_calls 在流里按 index 分片到达, 此处重组。纯逻辑可单测。"""
        tool_calls: list[ToolCall] = []
        for frag in tool_frags.values():
            try:
                parsed = json.loads(frag["args"] or "{}")
            except (json.JSONDecodeError, TypeError):
                parsed = {}
            tool_calls.append(ToolCall(id=frag["id"], name=frag["name"], args=parsed))
        extra: dict[str, Any] = {}
        if reasoning_parts:
            extra["reasoning_content"] = "".join(reasoning_parts)
        return AssistantTurn(
            text="".join(text_parts) or None,
            tool_calls=tool_calls,
            stop_reason="tool_use" if tool_calls else "end",
            usage=OpenAICompatProvider._extract_usage(usage_obj),
            extra=extra,
        )

    def stream(self, system: str, messages: list[Message],
               tools: list[dict[str, Any]]) -> Iterator[StreamEvent]:
        """流式: content 增量逐个 yield token, 结束 yield 终结 turn(重组 tool_calls 分片 + usage)。

        OpenAI 协议流式: 每 chunk 的 delta 带 content 片段或 tool_calls 分片(按 index 累积
        id/name/arguments)。usage 需 stream_options.include_usage 才在末 chunk 出现(deepseek/
        OpenAI 均支持; 不支持的 provider usage 取 0, 不报噪)。
        """
        kwargs: dict[str, Any] = {
            "model": self.model,
            "messages": self._to_native(system, messages),
            "stream": True,
            "stream_options": {"include_usage": True},
        }
        if tools:
            kwargs["tools"] = self._tools_native(tools)
        text_parts: list[str] = []
        reasoning_parts: list[str] = []
        tool_frags: dict[int, dict] = {}
        usage_obj: Any = None
        for chunk in self._client.chat.completions.create(**kwargs):
            if getattr(chunk, "usage", None):
                usage_obj = chunk.usage
            if not chunk.choices:
                continue
            delta = chunk.choices[0].delta
            if getattr(delta, "content", None):
                text_parts.append(delta.content)
                yield StreamEvent("token", delta.content)
            rc = getattr(delta, "reasoning_content", None)
            if rc:
                reasoning_parts.append(rc)
            for tcd in (getattr(delta, "tool_calls", None) or []):
                slot = tool_frags.setdefault(tcd.index, {"id": "", "name": "", "args": ""})
                if tcd.id:
                    slot["id"] = tcd.id
                fn = getattr(tcd, "function", None)
                if fn is not None:
                    if getattr(fn, "name", None):
                        slot["name"] = fn.name
                    if getattr(fn, "arguments", None):
                        slot["args"] += fn.arguments
        yield StreamEvent("turn", self._assemble_stream_turn(
            text_parts, tool_frags, reasoning_parts, usage_obj))
