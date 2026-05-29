"""loop 硬护栏测试:重复调用拦截 + 倒数步收尾提示(弱模型防空转)."""
from __future__ import annotations

from codev_platform.agent.brain import AssistantTurn, LLMProvider, ToolCall, ToolResult
from codev_platform.agent.loop import AgentLoop
from codev_platform.agent.tools.base import Tool, ToolRegistry


class CountingTool(Tool):
    name = "echo"
    description = "echo"
    input_schema = {"type": "object", "properties": {"v": {"type": "string"}}}

    def __init__(self) -> None:
        self.calls = 0

    def run(self, args):
        self.calls += 1
        return ToolResult(call_id="", content="r")


class RepeatProvider(LLMProvider):
    """永远用相同参数调同一工具(模拟弱模型空转)。"""
    name, model = "rep", "m"

    def chat(self, system, messages, tools):
        return AssistantTurn(text=None, tool_calls=[ToolCall("c", "echo", {"v": "x"})],
                             stop_reason="tool_use")


def test_repeated_call_is_blocked_not_executed():
    tool = CountingTool()
    reg = ToolRegistry()
    reg.register(tool)
    loop = AgentLoop(RepeatProvider(), reg, max_steps=5)
    result = loop.run("q")
    # 同 tool+args 只真正执行 1 次,后续被护栏拦截
    assert tool.calls == 1
    assert result.stop_reason == "max_steps"
    # 后续步的 result_summary 应含 loop guard 提示
    assert any("loop guard" in (s.result_summary or "") for s in result.steps)
