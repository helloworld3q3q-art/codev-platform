"""QueryPlanner(Phase 7)单测:确定性分类 + 预算/lane 映射 + loop 软预算集成。

分类纯逻辑(不调 LLM)→ 可像 eval 一样回归。loop 集成测 planner 开/关行为差异:
关 = 与重构前逐字节一致(不注入计划、不触发软预算);开 = 达预算回灌"收尾"提示。
"""
from __future__ import annotations

from codev_platform.agent.brain import AssistantTurn, LLMProvider, ToolCall, ToolResult
from codev_platform.agent.loop import AgentLoop
from codev_platform.agent.planner import (
    QueryType,
    classify_query,
    plan_query,
    render_plan_preamble,
)
from codev_platform.agent.policy import LoopPolicy
from codev_platform.agent.tools.base import Tool, ToolRegistry


# ------------------------------------------------------------------ 分类

def test_classify_overview():
    assert classify_query("这个项目是做什么的?") == QueryType.OVERVIEW
    assert classify_query("give me an overview of this project") == QueryType.OVERVIEW


def test_classify_impact():
    assert classify_query("改这张表会影响哪些前端和接口?") == QueryType.IMPACT
    assert classify_query("who calls this endpoint and what breaks if I change it") == QueryType.IMPACT


def test_classify_symbol():
    # "类/在哪/定义" 三命中 > impact "谁调用" 一命中 -> symbol
    assert classify_query("EnumMetadataService 这个类在哪定义?") == QueryType.SYMBOL
    assert classify_query("where is build_default_registry defined") == QueryType.SYMBOL


def test_classify_doc_rule():
    assert classify_query("提交代码要遵守什么规则?") == QueryType.DOC_RULE
    assert classify_query("why is it designed this way") == QueryType.DOC_RULE


def test_classify_general_on_no_keyword():
    assert classify_query("你好") == QueryType.GENERAL
    assert classify_query("") == QueryType.GENERAL


# ------------------------------------------------------------------ 计划

def test_plan_budget_and_lanes_filtered_to_available():
    avail = ["search_docs", "read_file", "codegraph_search"]  # 缺 list_dir
    plan = plan_query("这个项目干啥的", max_steps=12, available_tools=avail)
    assert plan.query_type == QueryType.OVERVIEW
    assert plan.tool_budget == 5
    # overview lanes = list_dir/search_docs/read_file; list_dir 不可用被过滤掉
    assert plan.preferred_lanes == ["search_docs", "read_file"]


def test_plan_budget_clamped_to_max_steps():
    # impact 原始预算 12; max_steps=6 时 clamp 到 6
    plan = plan_query("改这个表影响谁", max_steps=6, available_tools=None)
    assert plan.query_type == QueryType.IMPACT
    assert plan.tool_budget == 6


def test_plan_general_budget_equals_max_steps():
    # general -> 不额外约束(budget == max_steps, 软预算不会先于 max_steps 触发)
    plan = plan_query("你好", max_steps=9)
    assert plan.query_type == QueryType.GENERAL
    assert plan.tool_budget == 9
    assert plan.preferred_lanes == []


def test_render_preamble_mentions_type_and_budget():
    plan = plan_query("改这个表影响谁", max_steps=12)
    text = render_plan_preamble(plan)
    assert QueryType.IMPACT in text
    assert "工具预算" in text


# ------------------------------------------------------------------ loop 集成

class _EchoTool(Tool):
    name = "echo"
    description = "echo"
    input_schema = {"type": "object", "properties": {"v": {"type": "string"}}}

    def __init__(self) -> None:
        self.calls = 0

    def run(self, args):
        self.calls += 1
        return ToolResult(call_id="", content="r")


class _VaryingProvider(LLMProvider):
    """每步用不同参数调 echo(绕 exact-dup 去重, 持续执行直到 cap/budget/max_steps)。"""
    name, model = "vary", "m"

    def __init__(self) -> None:
        self.i = 0

    def chat(self, system, messages, tools):
        self.i += 1
        return AssistantTurn(text=None, tool_calls=[ToolCall(f"c{self.i}", "echo", {"v": str(self.i)})],
                             stop_reason="tool_use")


def _run(planner_enabled: bool):
    reg = ToolRegistry()
    reg.register(_EchoTool())
    # max_steps=10 给软预算(overview=5)留出先于 max_steps 触发的空间
    loop = AgentLoop(_VaryingProvider(), reg,
                     policy=LoopPolicy(max_steps=10, retrieval_distinct_cap=20),
                     planner_enabled=planner_enabled)
    return loop.run("这个项目是做什么的?")  # -> overview, budget 5


def test_loop_budget_warns_when_planner_on():
    res = _run(planner_enabled=True)
    hints = [s.result_summary or "" for s in res.steps]
    assert any("[query plan]" in h for h in hints), "开 planner 达预算应回灌 [query plan] 收尾提示"


def test_loop_no_plan_hint_when_planner_off():
    res = _run(planner_enabled=False)
    hints = [s.result_summary or "" for s in res.steps]
    assert not any("[query plan]" in h for h in hints), "关 planner 不应注入软预算提示(行为不变)"


class _ReadFileTool(Tool):
    name = "read_file"
    description = "read"
    input_schema = {"type": "object", "properties": {"path": {"type": "string"}}}

    def __init__(self) -> None:
        self.calls = 0

    def run(self, args):
        self.calls += 1
        return ToolResult(call_id="", content=f"content of {args.get('path')}")


class _ReadVaryingProvider(LLMProvider):
    """每步读不同 path(只读类, 模拟 overview 目录/文件 spelunking 失控)。"""
    name, model = "rv", "m"

    def __init__(self) -> None:
        self.i = 0

    def chat(self, system, messages, tools):
        self.i += 1
        return AssistantTurn(text=None,
                             tool_calls=[ToolCall(f"c{self.i}", "read_file", {"path": f"f{self.i}.py"})],
                             stop_reason="tool_use")


def test_readonly_hard_capped_at_budget_when_planner_on():
    # Phase 7 优化: planner 开 + 超预算后只读类硬拦。overview 预算 5 -> 只读最多执行 5 次。
    tool = _ReadFileTool()
    reg = ToolRegistry()
    reg.register(tool)
    loop = AgentLoop(_ReadVaryingProvider(), reg,
                     policy=LoopPolicy(max_steps=12, readonly_distinct_cap=20, readonly_total_cap=30),
                     planner_enabled=True)
    res = loop.run("这个项目是做什么的?")  # overview, budget 5
    assert tool.calls == 5, f"只读类应被硬拦在预算 5, 实际执行 {tool.calls}"
    assert res.stop_reason == "max_steps"
    hints = [s.result_summary or "" for s in res.steps]
    assert any("[query plan]" in h and "只读" in h for h in hints), "超预算只读应回灌硬拦提示"


def test_readonly_not_capped_when_planner_off():
    # planner 关: 只读类不受 budget 硬拦, 走原有 readonly cap(distinct 20)-> 执行远超 5。
    tool = _ReadFileTool()
    reg = ToolRegistry()
    reg.register(tool)
    loop = AgentLoop(_ReadVaryingProvider(), reg,
                     policy=LoopPolicy(max_steps=12, readonly_distinct_cap=20, readonly_total_cap=30),
                     planner_enabled=False)
    res = loop.run("这个项目是做什么的?")
    assert tool.calls > 5, f"关 planner 时只读不应被 budget 拦(原行为), 实际 {tool.calls}"
