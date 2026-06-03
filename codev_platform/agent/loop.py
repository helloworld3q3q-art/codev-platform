"""AgentLoop — 循环引擎(plan -> tool -> observe -> act -> stop).

只依赖 brain.base 的中性类型 + tools.base 的 Tool 抽象,不知道底下是哪家模型。
这是"换模型零改核心"的落点。
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any

from codev_platform.agent.brain import (
    AssistantTurn,
    LLMProvider,
    Message,
    ToolResult,
)
from codev_platform.agent.policy import LoopPolicy
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
    def __init__(self, provider: LLMProvider, registry: ToolRegistry,
                 policy: LoopPolicy | None = None, max_steps: int | None = None) -> None:
        self.provider = provider
        self.registry = registry
        # policy 优先(每模型策略,见 agent-provider §1/§4);未给则从 max_steps 兜底建一个
        # (向后兼容旧 max_steps= 调用 + 测试)。max_steps 既给又给 policy 时以 policy 为准。
        if policy is None:
            policy = LoopPolicy(max_steps=max_steps) if max_steps is not None else LoopPolicy()
        self.policy = policy

    def run(self, question: str, history: list[Message] | None = None, trace: Trace | None = None,
            system: str | None = None) -> AgentResult:
        # system 默认基础 prompt;ChatService 会传入注入了 (org/user/project) 上下文的版本,
        # 让模型"知道自己在为谁、在哪个项目工作"(否则问"哪个项目"会照写死 prompt 瞎猜)。
        system_prompt = system or CODE_UNDERSTANDING_SYSTEM
        messages: list[Message] = list(history or [])
        messages.append(Message(role="user", content=question))
        specs = self.registry.specs()
        steps: list[Step] = []
        total_usage: dict[str, int] = {"input_tokens": 0, "output_tokens": 0}
        seen_calls: set[str] = set()  # 硬护栏:记录已执行过的 (tool, args) 指纹
        tool_counts: dict[str, int] = {}  # 硬护栏:每工具实际执行次数 (防变参 thrash 同一工具)

        for n in range(1, self.policy.max_steps + 1):
            turn: AssistantTurn = self.provider.chat(system_prompt, messages, specs)
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
            messages.append(Message(role="assistant", content=turn.text,
                                    tool_calls=turn.tool_calls, extra=turn.extra))
            near_limit = n >= self.policy.max_steps - 1  # 倒数一步:提示强制收尾
            for call in turn.tool_calls:
                fp = f"{call.name}:{json.dumps(call.args, sort_keys=True, ensure_ascii=False)}"
                tool = self.registry.get(call.name)
                if tool is None:
                    result = ToolResult(call_id=call.id, content=f"未知工具: {call.name}", is_error=True)
                elif fp in seen_calls:
                    # 硬护栏:同 tool+args 重复调用 → 不再执行,回灌提示逼其换路或收尾
                    result = ToolResult(
                        call_id=call.id, is_error=True,
                        content=(f"[loop guard] 你已用相同参数调用过 {call.name},结果不会变。"
                                 f"请换不同查法,或用已掌握的证据给出(部分)最终答案,不要重复同一调用。"),
                    )
                elif tool_counts.get(call.name, 0) >= self.policy.per_tool_cap:
                    # 硬护栏:同一工具调够上限 (变参 thrash 也算) → 拒绝执行,点名未试过的互补工具逼换视角
                    untried = [s["name"] for s in specs
                               if s["name"] != call.name and tool_counts.get(s["name"], 0) == 0]
                    tip = ("；还没试过的互补工具:" + ", ".join(untried)) if untried else ""
                    result = ToolResult(
                        call_id=call.id, is_error=True,
                        content=(f"[loop guard] {call.name} 已调用 {tool_counts[call.name]} 次,够了。"
                                 f"换一类工具(换个视角){tip},或用现有证据给出最终答案,别再堆同一工具。"),
                    )
                else:
                    seen_calls.add(fp)
                    tool_counts[call.name] = tool_counts.get(call.name, 0) + 1
                    result = tool.run(call.args)
                    result.call_id = call.id
                    if near_limit:
                        result.content += ("\n\n[loop guard] 步数即将用尽,请基于现有证据立即给出最终答案"
                                           "(已解决的部分先答,未解决的标注清楚),不要再调用工具。")
                summary = _summarize(result.content)
                steps.append(Step(n, turn.text, call.name, call.args, summary))
                if trace:
                    trace.step(n, _summarize(turn.text or ""), call.name, call.args, summary)
                messages.append(Message(role="tool", content=result.content, tool_call_id=call.id))

        # 用尽 step 仍未收尾
        if trace:
            trace.done("max_steps", self.policy.max_steps)
        return AgentResult(
            answer="(达到 max_steps 上限仍未得出最终答案;可提高 max_steps 或缩小问题)",
            steps=steps, usage=total_usage, stop_reason="max_steps",
        )
