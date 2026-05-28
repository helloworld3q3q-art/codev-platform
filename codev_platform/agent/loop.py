"""AgentLoop — 循环引擎(plan -> tool -> observe -> act -> stop).

只依赖 brain.base 的中性类型 + tools.base 的 Tool 抽象,不知道底下是哪家模型。
这是"换模型零改核心"的落点。
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from codev_platform.agent.brain import (
    AssistantTurn,
    LLMProvider,
    Message,
    ToolResult,
)
from codev_platform.agent.prompts import CODE_UNDERSTANDING_SYSTEM
from codev_platform.agent.tools.base import ToolRegistry
from codev_platform.agent.trace import Trace


@dataclass
class Step:
    n: int
    thought: str | None
    tool: str | None
    args: Any
    result_summary: str | None


@dataclass
class AgentResult:
    answer: str
    steps: list[Step] = field(default_factory=list)
    usage: dict[str, Any] = field(default_factory=dict)
    stop_reason: str = "answered"


def _summarize(text: str, limit: int = 280) -> str:
    text = (text or "").strip().replace("\n", " ")
    return text if len(text) <= limit else text[:limit] + "…"


class AgentLoop:
    def __init__(self, provider: LLMProvider, registry: ToolRegistry, max_steps: int = 12) -> None:
        self.provider = provider
        self.registry = registry
        self.max_steps = max_steps

    def run(self, question: str, history: list[Message] | None = None, trace: Trace | None = None) -> AgentResult:
        messages: list[Message] = list(history or [])
        messages.append(Message(role="user", content=question))
        specs = self.registry.specs()
        steps: list[Step] = []
        total_usage: dict[str, int] = {"input_tokens": 0, "output_tokens": 0}

        for n in range(1, self.max_steps + 1):
            turn: AssistantTurn = self.provider.chat(CODE_UNDERSTANDING_SYSTEM, messages, specs)
            for k in ("input_tokens", "output_tokens"):
                total_usage[k] = total_usage.get(k, 0) + int(turn.usage.get(k, 0) or 0)

            if not turn.tool_calls:
                answer = turn.text or "(模型未返回文本)"
                steps.append(Step(n, turn.text, None, None, None))
                if trace:
                    trace.step(n, _summarize(turn.text or ""), None, None, None)
                    trace.done("answered", n)
                return AgentResult(answer, steps, total_usage, "answered")

            # 有工具调用:记录 assistant 这轮,执行每个 call,把结果回灌
            messages.append(Message(role="assistant", content=turn.text, tool_calls=turn.tool_calls))
            for call in turn.tool_calls:
                tool = self.registry.get(call.name)
                if tool is None:
                    result = ToolResult(call_id=call.id, content=f"未知工具: {call.name}", is_error=True)
                else:
                    result = tool.run(call.args)
                    result.call_id = call.id
                summary = _summarize(result.content)
                steps.append(Step(n, turn.text, call.name, call.args, summary))
                if trace:
                    trace.step(n, _summarize(turn.text or ""), call.name, call.args, summary)
                messages.append(Message(role="tool", content=result.content, tool_call_id=call.id))

        # 用尽 step 仍未收尾
        if trace:
            trace.done("max_steps", self.max_steps)
        return AgentResult(
            answer="(达到 max_steps 上限仍未得出最终答案;可提高 max_steps 或缩小问题)",
            steps=steps, usage=total_usage, stop_reason="max_steps",
        )
