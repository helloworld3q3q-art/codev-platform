"""AgentLoop — 循环引擎(plan -> tool -> observe -> act -> stop).

只依赖 brain.base 的中性类型 + tools.base 的 Tool 抽象,不知道底下是哪家模型。
这是"换模型零改核心"的落点。

护栏(2026-06-05 重构, agent-loop-guard-redesign plan): 按工具语义三分类施策, **模型无关**,
模型差异 100% 落在 LoopPolicy 数值/开关上(agent-provider §1 铁律, loop.py 无任何模型名 if-else):
  - 只读类(READONLY): 读不同文件=确定进展 → 归一化 distinct-path 上限(高)+ 总读软顶, 同 path 判零增量。
  - 检索类(RETRIEVAL): query 换词可绕指纹 → distinct-args 上限 + 输出侧零增量(结果哈希不变即无进展)。
  - 无效调用类: 连续 K 次参数报错(路径不存在/非法 module 等)→ 回灌合法值 + 强制换路, 不放行无限重试。
所有阈值/开关来自 LoopPolicy(registry 按 provider 逐字段解析), 本文件只读策略不判模型。
"""
from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass, field
from typing import Any

from codev_platform.agent.brain import (
    AssistantTurn,
    LLMProvider,
    Message,
    ToolResult,
)
from codev_platform.agent.planner import plan_query, render_plan_preamble
from codev_platform.agent.policy import LoopPolicy
from codev_platform.agent.prompts import CODE_UNDERSTANDING_SYSTEM
from codev_platform.agent.tools.base import ToolRegistry
from codev_platform.agent.trace import Trace

# 工具语义分类(loop 内常量, 不进 config —— 这是"哪个工具是哪类"的事实, 非可调策略)。
# 不在两集合里的工具(remember / 未知工具)按"其它"处理: 套 distinct-args 上限, 不做输出侧 novelty。
READONLY_TOOLS = frozenset({"read_file", "list_dir"})
RETRIEVAL_TOOLS = frozenset({
    "search_docs",
    "codegraph_search", "codegraph_callers", "codegraph_callees",
    "impact_analysis", "table_usage", "page_dependencies", "api_callers",
})


def _classify(name: str) -> str:
    if name in READONLY_TOOLS:
        return "readonly"
    if name in RETRIEVAL_TOOLS:
        return "retrieval"
    return "other"


def _norm_path_arg(args: Any) -> str | None:
    """只读类按归一化 path 判 distinct(忽略 offset/limit/max_bytes), 同文件改 offset 刷读=零增量。"""
    if not isinstance(args, dict):
        return None
    p = args.get("path")
    if not isinstance(p, str) or not p.strip():
        return None
    return p.strip().replace("\\", "/").strip("/").lower()


def _result_hash(content: str) -> str:
    """归一化结果哈希(裁全部空白 + 小写)。仅用于检索类输出侧零增量判定 —— 换空格/标点/大小写绕不过。"""
    norm = re.sub(r"\s+", "", content or "").lower()
    return hashlib.sha1(norm.encode("utf-8", "replace")).hexdigest()


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


@dataclass
class _GuardState:
    """单次 run 的护栏累积状态。"""
    seen_calls: set[str] = field(default_factory=set)          # exact (tool,args) 指纹 → 精确重复拦
    tool_counts: dict[str, int] = field(default_factory=dict)  # 非只读工具 distinct-args 执行次数
    readonly_paths: set[str] = field(default_factory=set)      # 成功读到的归一化 distinct path(distinct cap + 充分性门)
    readonly_total: int = 0                                    # 只读工具总执行次数(含失败尝试; 软顶)
    retrieval_hashes: dict[str, set[str]] = field(default_factory=dict)   # name -> 见过的结果哈希集
    retrieval_no_progress: dict[str, int] = field(default_factory=dict)   # name -> 连续零增量次数
    consecutive_invalid: int = 0                               # 连续无效调用(参数报错/路径不存在)计数
    executed_tools: int = 0                                    # 实际执行的工具调用数(planner 软预算计数)
    budget_warned: bool = False                                # 软预算提示已回灌(只灌一次, 不刷屏)


def _summarize(text: str, limit: int = 280) -> str:
    text = (text or "").strip().replace("\n", " ")
    return text if len(text) <= limit else text[:limit] + "…"


def _precheck(policy: LoopPolicy, specs: list[dict], st: _GuardState,
              name: str, fp: str, args: Any, tool_budget: int = 0) -> str | None:
    """执行前护栏: 返回拦截提示(不执行)或 None(放行)。"""
    klass = _classify(name)

    # 精确重复(同 tool+args): 任何类都拦, 结果不会变。
    if fp in st.seen_calls:
        return (f"[loop guard] 你已用相同参数调用过 {name},结果不会变。"
                f"请换不同查法,或用已掌握的证据给出(部分)最终答案,不要重复同一调用。")

    # planner 超预算后**只读类硬拦**(Phase 7 优化): 实测软预算对 deepseek 不够 ——
    # overview 类越界后继续刷 list_dir/read_file(探索型空读)。预算耗尽后, 只读类直接拒
    # (检索类仍走软提示, 因其有 distinct/零增量上限自限)。精准打 overview/general 的目录
    # spelunking 失控, 不碰 impact/symbol(走检索类)。tool_budget<=0 = planner 关, 不生效。
    if tool_budget > 0 and klass == "readonly" and st.executed_tools >= tool_budget:
        return (f"[query plan] 本轮工具预算({tool_budget})已用尽, 这是探索型只读调用"
                f"(list_dir/read_file)。请基于已读到的内容直接收尾, 不要继续翻目录/读文件; "
                f"确需某关键文件请用检索类工具(codegraph/search_docs)精准定位再读。")

    if klass == "readonly":
        np = _norm_path_arg(args)
        if np is not None and np in st.readonly_paths:
            return (f"[loop guard] 该文件/目录({np})你已读过(忽略 offset/limit 视为同一处),"
                    f"结果不会变。换一个未读的文件,或用现有内容收尾。")
        if st.readonly_total >= policy.readonly_total_cap:
            return (f"[loop guard] 已读取文件/目录 {st.readonly_total} 次,够了。"
                    f"请基于已读到的内容给出最终答案,不要继续漫无目的地读。")
        if np is not None and len(st.readonly_paths) >= policy.readonly_distinct_cap:
            return (f"[loop guard] 已读取 {len(st.readonly_paths)} 个不同文件,够多了。"
                    f"请基于已读内容收尾,或换检索类工具定位关键处再精准读。")
        return None

    # 检索类: 连续零增量已达上限 → 不再放行同工具(硬拒, 逼收尾)。
    if klass == "retrieval" and st.retrieval_no_progress.get(name, 0) >= policy.no_progress_limit:
        return (f"[loop guard] {name} 连续多次检索结果无新增信息,继续查同一工具无意义。"
                f"请换一类工具或换实质不同的查法,或用现有证据立即收尾。")

    # distinct-args 上限(检索类 + 其它类共用 retrieval_distinct_cap; 防变参 thrash 同一工具)。
    if st.tool_counts.get(name, 0) >= policy.retrieval_distinct_cap:
        untried = [s["name"] for s in specs
                   if s["name"] != name and st.tool_counts.get(s["name"], 0) == 0
                   and s["name"] not in READONLY_TOOLS]
        tip = ("；还没试过的工具:" + ", ".join(untried)) if untried else ""
        return (f"[loop guard] {name} 已调用 {st.tool_counts[name]} 次,够了。"
                f"换一类工具(换个视角){tip},或用现有证据给出最终答案,别再堆同一工具。")
    return None


def _postprocess(policy: LoopPolicy, st: _GuardState, name: str, fp: str,
                 args: Any, result: ToolResult, near_limit: bool,
                 tool_budget: int = 0) -> None:
    """执行后: 记账(指纹/计数)+ 无效调用追踪 + 检索输出侧零增量 + 软预算 + 倒数步收尾提示。"""
    klass = _classify(name)
    st.seen_calls.add(fp)
    st.executed_tools += 1

    if klass == "readonly":
        st.readonly_total += 1
        if not result.is_error:
            np = _norm_path_arg(args)
            if np is not None:
                st.readonly_paths.add(np)  # 仅成功读计入 distinct(充分性门只认真读到的)
    else:
        st.tool_counts[name] = st.tool_counts.get(name, 0) + 1

    if result.is_error:
        # 无效调用类: 参数报错 / 路径不存在 / 非法 module 等 → 连续累计, 达阈值回灌合法值 + 强制换路。
        st.consecutive_invalid += 1
        if st.consecutive_invalid >= policy.invalid_call_limit:
            result.content += (
                f"\n\n[loop guard] 你已连续 {st.consecutive_invalid} 次调用参数无效"
                f"(文件不存在 / 目录不存在 / 非法 module 等)。停止用错误参数重试:"
                f"先用 list_dir 确认路径、用 list_collections 看本项目合法 module,或直接换一类工具。"
                f"不要在无效参数上空转。")
        return

    st.consecutive_invalid = 0
    # 输出侧零增量(仅检索类 + 开了 novelty_check; 只读读不同文件天然 novel, 不套)。
    if klass == "retrieval" and policy.novelty_check:
        h = _result_hash(result.content)
        seen = st.retrieval_hashes.setdefault(name, set())
        if h in seen:
            st.retrieval_no_progress[name] = st.retrieval_no_progress.get(name, 0) + 1
            cnt = st.retrieval_no_progress[name]
            if cnt >= policy.no_progress_limit:
                result.content += (f"\n\n[loop guard] 该检索连续 {cnt} 次返回与之前相同的结果,"
                                   f"没有新信息。立即基于现有证据收尾,不要再换词重查。")
            else:
                result.content += ("\n\n[loop guard] 本次检索结果与之前重复,无新增。"
                                   "换实质不同的查法或换工具,别靠换同义词重查。")
        else:
            seen.add(h)
            st.retrieval_no_progress[name] = 0

    # 软预算(planner 前摄式): 达本轮问题类型的工具预算 → 回灌一次"收尾"提示(软, 非硬断)。
    # tool_budget<=0 表示未启用 planner / general 类不约束。near_limit 已含更强的收尾提示, 不叠。
    if (tool_budget > 0 and not near_limit and not st.budget_warned
            and st.executed_tools >= tool_budget):
        st.budget_warned = True
        result.content += (
            f"\n\n[query plan] 本轮已用约 {st.executed_tools} 次工具,达到该问题类型的建议预算"
            f"({tool_budget})。请基于现有证据收尾;确需继续要有明确理由并说明残余不确定。")

    if near_limit:
        result.content += ("\n\n[loop guard] 步数即将用尽,请基于现有证据立即给出最终答案"
                           "(已解决的部分先答,未解决的标注清楚)。不要脑补/编造未读到的文件内容,"
                           "没读到就如实说明,不要再调用工具。")


class AgentLoop:
    def __init__(self, provider: LLMProvider, registry: ToolRegistry,
                 policy: LoopPolicy | None = None, max_steps: int | None = None,
                 planner_enabled: bool = False) -> None:
        self.provider = provider
        self.registry = registry
        # policy 优先(每模型策略,见 agent-provider §1/§4);未给则从 max_steps 兜底建一个
        # (向后兼容旧 max_steps= 调用 + 测试)。max_steps 既给又给 policy 时以 policy 为准。
        if policy is None:
            policy = LoopPolicy(max_steps=max_steps) if max_steps is not None else LoopPolicy()
        self.policy = policy
        # planner(Phase 7): 默认关 → 行为与重构前逐字节一致。开启则前摄式规划工具 + 软预算。
        self.planner_enabled = planner_enabled

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
        guard = _GuardState()

        # planner(Phase 7, 默认关): 按问题类型规划工具 + 软预算。计划注入 system 作引导,
        # tool_budget 在 _postprocess 作软停止条件。关闭时 budget=0 → 全程不约束(原行为)。
        tool_budget = 0
        if self.planner_enabled:
            plan = plan_query(question, max_steps=self.policy.max_steps,
                              available_tools=[s["name"] for s in specs])
            system_prompt = system_prompt + "\n\n" + render_plan_preamble(plan)
            tool_budget = plan.tool_budget
            if trace:
                trace.plan(plan.query_type, plan.tool_budget, plan.preferred_lanes)

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
                else:
                    block = _precheck(self.policy, specs, guard, call.name, fp, call.args,
                                      tool_budget=tool_budget)
                    if block is not None:
                        result = ToolResult(call_id=call.id, content=block, is_error=True)
                    else:
                        result = tool.run(call.args)
                        result.call_id = call.id
                        _postprocess(self.policy, guard, call.name, fp, call.args, result,
                                     near_limit, tool_budget=tool_budget)
                summary = _summarize(result.content)
                steps.append(Step(n, turn.text, call.name, call.args, summary))
                if trace:
                    trace.step(n, _summarize(turn.text or ""), call.name, call.args, summary)
                messages.append(Message(role="tool", content=result.content, tool_call_id=call.id))

        # 用尽 step 仍未收尾。读取充分性门:几乎没真读到文件 **且尾部在连续无效调用** → 判卡无效调用。
        # (加 consecutive_invalid 判据: 纯检索类任务可合法地从不 read_file, 不能仅凭"没读文件"误判空转。)
        if trace:
            trace.done("max_steps", self.policy.max_steps)
        if len(guard.readonly_paths) < self.policy.min_read_for_finish and guard.consecutive_invalid > 0:
            answer = ("(达到 max_steps 上限仍未收尾;且几乎没读到文件、尾部在连续无效调用——疑似卡在参数错误。"
                      "建议核对工具参数:路径用 list_dir 确认、module 用 list_collections 看合法值,"
                      "改对参数后再试,而非凭不足的信息下结论。)")
        else:
            answer = "(达到 max_steps 上限仍未得出最终答案;可提高 max_steps 或缩小问题)"
        return AgentResult(answer=answer, steps=steps, usage=total_usage, stop_reason="max_steps")
