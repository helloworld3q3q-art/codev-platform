"""Anthropic 适配器的中性->原生翻译测试.

_to_native / _tools_native 是 staticmethod,不需实例化(不触发 anthropic SDK 导入),
故可纯逻辑测试 —— 这正是多模型抽象的核心(翻译正确才换得了模型)。
"""
from __future__ import annotations

from codev_platform.agent.brain import Message, ToolCall
from codev_platform.agent.brain.anthropic import AnthropicProvider


def test_assistant_tool_calls_become_tool_use_blocks():
    msgs = [
        Message(role="assistant", content="thinking",
                tool_calls=[ToolCall("c1", "echo", {"v": "x"})]),
    ]
    native = AnthropicProvider._to_native(msgs)
    assert native[0]["role"] == "assistant"
    blocks = native[0]["content"]
    assert {"type": "text", "text": "thinking"} in blocks
    assert {"type": "tool_use", "id": "c1", "name": "echo", "input": {"v": "x"}} in blocks


def test_consecutive_tool_results_merge_into_one_user_message():
    msgs = [
        Message(role="assistant", content=None,
                tool_calls=[ToolCall("c1", "a", {}), ToolCall("c2", "b", {})]),
        Message(role="tool", content="r1", tool_call_id="c1"),
        Message(role="tool", content="r2", tool_call_id="c2"),
    ]
    native = AnthropicProvider._to_native(msgs)
    # 最后一条应是一个 user 消息, 含两个 tool_result block(Anthropic 要求)
    last = native[-1]
    assert last["role"] == "user"
    ids = [b["tool_use_id"] for b in last["content"]]
    assert ids == ["c1", "c2"]


def test_plain_user_message_passthrough():
    native = AnthropicProvider._to_native([Message(role="user", content="hi")])
    assert native == [{"role": "user", "content": "hi"}]


def test_tools_native_shape():
    specs = [{"name": "echo", "description": "d", "input_schema": {"type": "object", "properties": {}}}]
    native = AnthropicProvider._tools_native(specs)
    assert native == [{"name": "echo", "description": "d", "input_schema": {"type": "object", "properties": {}}}]
