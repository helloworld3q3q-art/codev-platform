"""AgentLoop 引擎测试 — 用 FakeProvider 脚本化模型行为,不依赖真 LLM / 网络。

验证循环契约:模型要求调工具 -> 执行 -> 回灌 -> 再问 -> 给最终答;以及 max_steps 兜底。
"""
from __future__ import annotations

from codev_platform.agent.brain import AssistantTurn, LLMProvider, ToolCall, ToolResult
from codev_platform.agent.loop import AgentLoop
from codev_platform.agent.tools.base import Tool, ToolRegistry


class FakeProvider(LLMProvider):
    name = "fake"
    model = "fake-1"

    def __init__(self, script: list[AssistantTurn]) -> None:
        self._script = list(script)
        self.seen_tool_specs: list[dict] | None = None

    def chat(self, system, messages, tools):
        self.seen_tool_specs = tools
        return self._script.pop(0)


class EchoTool(Tool):
    name = "echo"
    description = "echo back"
    input_schema = {"type": "object", "properties": {"v": {"type": "string"}}, "required": ["v"]}

    def __init__(self) -> None:
        self.ran_with: dict | None = None

    def run(self, args):
        self.ran_with = args
        return ToolResult(call_id="", content=f"echoed:{args.get('v')}")


def _registry_with(tool: Tool) -> ToolRegistry:
    r = ToolRegistry()
    r.register(tool)
    return r


def test_loop_runs_tool_then_answers():
    tool = EchoTool()
    provider = FakeProvider([
        AssistantTurn(text=None, tool_calls=[ToolCall("c1", "echo", {"v": "hi"})], stop_reason="tool_use"),
        AssistantTurn(text="final answer", tool_calls=[], stop_reason="end"),
    ])
    loop = AgentLoop(provider, _registry_with(tool), max_steps=5)

    result = loop.run("question")

    assert result.answer == "final answer"
    assert result.stop_reason == "answered"
    assert tool.ran_with == {"v": "hi"}
    # tool spec 确实传给了 provider
    assert provider.seen_tool_specs and provider.seen_tool_specs[0]["name"] == "echo"
    # steps 含一次工具步
    assert any(s.tool == "echo" for s in result.steps)


def test_loop_unknown_tool_is_reported_not_crash():
    provider = FakeProvider([
        AssistantTurn(text=None, tool_calls=[ToolCall("c1", "nope", {})], stop_reason="tool_use"),
        AssistantTurn(text="ok", tool_calls=[], stop_reason="end"),
    ])
    loop = AgentLoop(provider, ToolRegistry(), max_steps=5)
    result = loop.run("q")
    assert result.answer == "ok"
    assert any(s.tool == "nope" for s in result.steps)


def test_loop_max_steps_guard():
    # provider 永远要求调工具 -> 必须被 max_steps 截断,不无限循环
    tool = EchoTool()
    provider = FakeProvider([
        AssistantTurn(text=None, tool_calls=[ToolCall(f"c{i}", "echo", {"v": str(i)})], stop_reason="tool_use")
        for i in range(10)
    ])
    loop = AgentLoop(provider, _registry_with(tool), max_steps=3)
    result = loop.run("q")
    assert result.stop_reason == "max_steps"


def test_usage_accumulates():
    provider = FakeProvider([
        AssistantTurn(text=None, tool_calls=[ToolCall("c1", "echo", {"v": "a"})], stop_reason="tool_use",
                      usage={"input_tokens": 10, "output_tokens": 5}),
        AssistantTurn(text="done", tool_calls=[], stop_reason="end",
                      usage={"input_tokens": 7, "output_tokens": 3}),
    ])
    loop = AgentLoop(provider, _registry_with(EchoTool()), max_steps=5)
    result = loop.run("q")
    assert result.usage["input_tokens"] == 17
    assert result.usage["output_tokens"] == 8
