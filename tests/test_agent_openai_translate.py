"""OpenAI 兼容适配器(DeepSeek/GPT/Qwen)的中性->原生翻译测试.

_to_native / _tools_native 是 staticmethod,不实例化(不触发 openai SDK 导入),
纯逻辑测翻译正确性 —— 多模型抽象的核心。
"""
from __future__ import annotations

import json

from codev_platform.agent.brain import Message, ToolCall
from codev_platform.agent.brain.openai_compat import OpenAICompatProvider


def test_system_prepended_and_user_passthrough():
    native = OpenAICompatProvider._to_native("SYS", [Message(role="user", content="hi")])
    assert native[0] == {"role": "system", "content": "SYS"}
    assert native[1] == {"role": "user", "content": "hi"}


def test_assistant_tool_calls_become_openai_tool_calls():
    msgs = [Message(role="assistant", content="t",
                    tool_calls=[ToolCall("c1", "echo", {"v": "x"})])]
    native = OpenAICompatProvider._to_native("S", msgs)
    a = native[-1]
    assert a["role"] == "assistant"
    tc = a["tool_calls"][0]
    assert tc["id"] == "c1"
    assert tc["function"]["name"] == "echo"
    assert json.loads(tc["function"]["arguments"]) == {"v": "x"}


def test_tool_result_message():
    native = OpenAICompatProvider._to_native("S", [Message(role="tool", content="r1", tool_call_id="c1")])
    t = native[-1]
    assert t == {"role": "tool", "tool_call_id": "c1", "content": "r1"}


def test_tools_native_shape():
    specs = [{"name": "echo", "description": "d", "input_schema": {"type": "object", "properties": {}}}]
    native = OpenAICompatProvider._tools_native(specs)
    assert native[0]["type"] == "function"
    assert native[0]["function"]["name"] == "echo"
    assert native[0]["function"]["parameters"] == {"type": "object", "properties": {}}
