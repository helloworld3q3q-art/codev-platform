"""loop 硬护栏测试:重复调用拦截 + 倒数步收尾提示(弱模型防空转)."""
from __future__ import annotations

from codev_platform.agent.brain import AssistantTurn, LLMProvider, ToolCall, ToolResult
from codev_platform.agent.loop import AgentLoop
from codev_platform.agent.policy import LoopPolicy
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


class VaryingProvider(LLMProvider):
    """每次用不同参数调同一工具(模拟弱模型变参 thrash, 绕过 exact-args 去重)。"""
    name, model = "vary", "m"

    def __init__(self) -> None:
        self.i = 0

    def chat(self, system, messages, tools):
        self.i += 1
        return AssistantTurn(text=None, tool_calls=[ToolCall(f"c{self.i}", "echo", {"v": str(self.i)})],
                             stop_reason="tool_use")


def test_same_tool_varied_args_capped_by_policy():
    # 变参也只放行 per_tool_cap 次(模型无关硬护栏, 防弱模型同义换皮 thrash 同一工具)。
    tool = CountingTool()
    reg = ToolRegistry()
    reg.register(tool)
    loop = AgentLoop(VaryingProvider(), reg, policy=LoopPolicy(max_steps=12, per_tool_cap=3))
    result = loop.run("q")
    assert tool.calls == 3
    assert result.stop_reason == "max_steps"
    assert any("够了" in (s.result_summary or "") for s in result.steps)


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


# ----------------------------------------------------------------------------
# 三分类护栏(2026-06-05 重构)新增测试
# ----------------------------------------------------------------------------

class ScriptedTool(Tool):
    """按名注册的可配置工具: run 返回固定/动态内容, 可标记 is_error。"""
    def __init__(self, name, *, content="r", is_error=False, content_fn=None):
        self.name = name
        self.description = name
        self.input_schema = {"type": "object", "properties": {"path": {"type": "string"},
                                                              "query": {"type": "string"},
                                                              "offset": {"type": "integer"}}}
        self.calls = 0
        self._content = content
        self._is_error = is_error
        self._content_fn = content_fn

    def run(self, args):
        self.calls += 1
        c = self._content_fn(args, self.calls) if self._content_fn else self._content
        return ToolResult(call_id="", content=c, is_error=self._is_error)


class ScriptProvider(LLMProvider):
    """按预编脚本逐步发 tool_calls(脚本耗尽后不再发 → 模型收尾)。"""
    name, model = "script", "m"

    def __init__(self, script):
        self.script = list(script)  # [(tool, args), ...]
        self.i = 0

    def chat(self, system, messages, tools):
        if self.i >= len(self.script):
            return AssistantTurn(text="done", tool_calls=[], stop_reason="end")
        tool, args = self.script[self.i]
        self.i += 1
        return AssistantTurn(text=None, tool_calls=[ToolCall(f"c{self.i}", tool, args)],
                             stop_reason="tool_use")


def _readonly_policy(**kw):
    base = dict(max_steps=20, readonly_distinct_cap=20, readonly_total_cap=30,
                retrieval_distinct_cap=8, no_progress_limit=3, invalid_call_limit=2,
                min_read_for_finish=2)
    base.update(kw)
    return LoopPolicy(**base)


def test_readonly_distinct_files_not_blocked():
    # P1 Gate: 连读 8 个不同文件全部放行(不被 per_tool_cap 误杀)。
    tool = ScriptedTool("read_file", content_fn=lambda a, n: f"content of {a['path']}")
    reg = ToolRegistry()
    reg.register(tool)
    script = [("read_file", {"path": f"src/f{i}.py"}) for i in range(8)]
    loop = AgentLoop(ScriptProvider(script), reg, policy=_readonly_policy())
    result = loop.run("q")
    assert tool.calls == 8
    assert not any("loop guard" in (s.result_summary or "") for s in result.steps)


def test_readonly_same_path_varied_offset_is_zero_progress():
    # P1 Gate: 同文件改 offset 刷读 → 判零增量被拦, 工具只真正执行 1 次。
    tool = ScriptedTool("read_file", content_fn=lambda a, n: "same file body")
    reg = ToolRegistry()
    reg.register(tool)
    script = [("read_file", {"path": "src/a.py", "offset": i * 10}) for i in range(5)]
    loop = AgentLoop(ScriptProvider(script), reg, policy=_readonly_policy())
    result = loop.run("q")
    assert tool.calls == 1
    assert any("loop guard" in (s.result_summary or "") and "已读过" in (s.result_summary or "")
               for s in result.steps)


def test_readonly_total_soft_cap_blocks_runaway_reads():
    # P1 Gate: 乱读不同文件触总量软顶。total_cap=5 → 最多真正读 5 次。
    tool = ScriptedTool("read_file", content_fn=lambda a, n: f"c{n}")
    reg = ToolRegistry()
    reg.register(tool)
    script = [("read_file", {"path": f"src/f{i}.py"}) for i in range(12)]
    loop = AgentLoop(ScriptProvider(script), reg,
                     policy=_readonly_policy(readonly_total_cap=5, readonly_distinct_cap=20))
    result = loop.run("q")
    assert tool.calls == 5
    assert any("够了" in (s.result_summary or "") for s in result.steps)


def test_retrieval_identical_results_force_finish():
    # P2 Gate: 换词查但结果相同 → 连续 no_progress_limit 次后硬拒(同工具不再执行)。
    tool = ScriptedTool("search_docs", content_fn=lambda a, n: "IDENTICAL RESULT BODY")
    reg = ToolRegistry()
    reg.register(tool)
    # 每次换 query(distinct-args 绕指纹), 但结果恒同。
    script = [("search_docs", {"query": f"q {i}"}) for i in range(10)]
    loop = AgentLoop(ScriptProvider(script), reg,
                     policy=_readonly_policy(no_progress_limit=3, novelty_check=True))
    result = loop.run("q")
    # 首次 novel + 之后命中哈希: 第1次记哈希, 第2/3/4次零增量计数到 3 → 第5次起被 precheck 硬拒。
    # 真正执行次数应远小于 10。
    assert tool.calls <= 5
    assert any("无新增" in (s.result_summary or "") or "相同的结果" in (s.result_summary or "")
               for s in result.steps)
    assert any("无新增信息" in (s.result_summary or "") for s in result.steps)


def test_retrieval_novel_results_not_blocked_by_novelty():
    # P2 Gate: 正常换查法(结果不同)放行, 不被零增量误杀(仅受 distinct-args cap 限)。
    tool = ScriptedTool("search_docs", content_fn=lambda a, n: f"distinct result {n}")
    reg = ToolRegistry()
    reg.register(tool)
    script = [("search_docs", {"query": f"q {i}"}) for i in range(6)]
    loop = AgentLoop(ScriptProvider(script), reg,
                     policy=_readonly_policy(retrieval_distinct_cap=8, no_progress_limit=3))
    result = loop.run("q")
    assert tool.calls == 6
    assert not any("无新增" in (s.result_summary or "") for s in result.steps)


def test_retrieval_novelty_off_disables_output_guard():
    # 强档 novelty_check=False: 结果恒同也不触发零增量(只受 distinct-args cap)。
    tool = ScriptedTool("search_docs", content_fn=lambda a, n: "SAME")
    reg = ToolRegistry()
    reg.register(tool)
    script = [("search_docs", {"query": f"q {i}"}) for i in range(6)]
    loop = AgentLoop(ScriptProvider(script), reg,
                     policy=_readonly_policy(retrieval_distinct_cap=8, novelty_check=False))
    result = loop.run("q")
    assert tool.calls == 6  # distinct-args 都放行, novelty 关闭不拦
    assert not any("无新增" in (s.result_summary or "") for s in result.steps)


def test_invalid_calls_inject_guidance():
    # P0 Gate: 连续无效调用(is_error)→ 第 invalid_call_limit 次起回灌合法值/换路提示。
    tool = ScriptedTool("read_file", content="文件不存在: x", is_error=True)
    reg = ToolRegistry()
    reg.register(tool)
    script = [("read_file", {"path": f"nope/{i}.py"}) for i in range(4)]
    loop = AgentLoop(ScriptProvider(script), reg, policy=_readonly_policy(invalid_call_limit=2))
    result = loop.run("q")
    # 第2次起 content 追加 "连续 ... 次调用参数无效" 回灌
    assert any("参数无效" in (s.result_summary or "") and "list_collections" in (s.result_summary or "")
               for s in result.steps)


def test_finish_sufficiency_gate_flags_no_reads():
    # P3 Gate: 跑满 max_steps 且几乎没真读到文件 → 收尾文案点明疑似卡无效调用。
    tool = ScriptedTool("read_file", content="文件不存在", is_error=True)
    reg = ToolRegistry()
    reg.register(tool)
    # 永远调无效路径, 从不成功读 → readonly_paths 为空。
    prov = VaryingPathProvider()
    loop = AgentLoop(prov, reg, policy=_readonly_policy(max_steps=6, min_read_for_finish=2))
    result = loop.run("q")
    assert result.stop_reason == "max_steps"
    assert "疑似卡在无效调用" in result.answer


class VaryingPathProvider(LLMProvider):
    name, model = "vp", "m"

    def __init__(self):
        self.i = 0

    def chat(self, system, messages, tools):
        self.i += 1
        return AssistantTurn(text=None,
                             tool_calls=[ToolCall(f"c{self.i}", "read_file", {"path": f"bad/{self.i}.py"})],
                             stop_reason="tool_use")
