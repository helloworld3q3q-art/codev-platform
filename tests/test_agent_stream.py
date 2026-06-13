"""端到端流式地基测试: provider.stream() 组装 + loop.run_stream 事件序列 + run() 回退兼容.

不触真 SDK: provider 流用 scripted fake, 两个 provider 的组装走纯逻辑静态方法(mock resp/分片)。
"""
from __future__ import annotations

from codev_platform.agent.brain import AssistantTurn, LLMProvider, ToolCall, ToolResult
from codev_platform.agent.brain.types import StreamEvent
from codev_platform.agent.loop import AgentLoop
from codev_platform.agent.policy import LoopPolicy
from codev_platform.agent.tools.base import Tool, ToolRegistry


class _EchoTool(Tool):
    name = "echo"
    description = "echo"
    input_schema = {"type": "object", "properties": {"v": {"type": "string"}}}

    def run(self, args):
        return ToolResult(call_id="", content="echoed:" + str(args.get("v", "")))


class _StreamProvider(LLMProvider):
    """scripted 流式 provider: 每轮先 yield token 增量, 再 yield 终结 turn。"""
    name, model = "stream", "m"

    def __init__(self, scripted: list[tuple[list[str], AssistantTurn]]) -> None:
        self._scripted = scripted
        self._i = 0

    def chat(self, system, messages, tools):  # 不该被调到(provider 实现了 stream)
        raise AssertionError("chat() should not be called when stream() exists")

    def stream(self, system, messages, tools):
        toks, turn = self._scripted[self._i]
        self._i += 1
        for t in toks:
            yield StreamEvent("token", t)
        yield StreamEvent("turn", turn)


class _ChatOnlyProvider(LLMProvider):
    """只实现 chat() 的旧式 provider → run_stream 应经 NotImplementedError 回退 chat()。"""
    name, model = "chatonly", "m"

    def chat(self, system, messages, tools):
        return AssistantTurn(text="直接答案", tool_calls=[], stop_reason="end")


def _loop(provider) -> AgentLoop:
    reg = ToolRegistry()
    reg.register(_EchoTool())
    return AgentLoop(provider, reg, policy=LoopPolicy(max_steps=8))


def _two_step_script() -> list[tuple[list[str], AssistantTurn]]:
    # 轮1: 思考 token + 调 echo;轮2: 答案 token + 收尾(无工具)。
    return [
        (["让我", "查一下。"], AssistantTurn(text="让我查一下。",
            tool_calls=[ToolCall("c1", "echo", {"v": "x"})], stop_reason="tool_use")),
        (["最终", "答案。"], AssistantTurn(text="最终答案。", tool_calls=[], stop_reason="end")),
    ]


def test_run_stream_emits_token_step_done_in_order():
    events = list(_loop(_StreamProvider(_two_step_script())).run_stream("q"))
    kinds = [e.kind for e in events]
    # token(思考) ... step(工具) ... token(答案) ... done
    assert kinds.count("done") == 1 and kinds[-1] == "done"
    assert kinds.count("step") == 1
    tokens = [e.data for e in events if e.kind == "token"]
    assert tokens == ["让我", "查一下。", "最终", "答案。"]
    step = next(e.data for e in events if e.kind == "step")
    assert step["tool"] == "echo" and step["n"] == 1
    done = events[-1].data  # AgentResult
    assert done.answer == "最终答案。"
    assert done.stop_reason == "answered"
    assert len(done.steps) == 2  # 工具步 + 收尾步


def test_run_drains_run_stream_same_result():
    # run() 应与 run_stream 的 done 一致(单一真值源)。
    result = _loop(_StreamProvider(_two_step_script())).run("q")
    assert result.answer == "最终答案。"
    assert result.stop_reason == "answered"


def test_chat_only_provider_falls_back_no_tokens():
    # 旧式 provider 无 stream(): run_stream 回退 chat(), 不产 token 事件, 仍产 done。
    events = list(_loop(_ChatOnlyProvider()).run_stream("q"))
    assert [e.kind for e in events if e.kind == "token"] == []
    assert events[-1].kind == "done"
    assert events[-1].data.answer == "直接答案"
    # run() 同样可用
    assert _loop(_ChatOnlyProvider()).run("q").answer == "直接答案"


def test_openai_assemble_stream_turn_reassembles_tool_fragments():
    from codev_platform.agent.brain.openai_compat import OpenAICompatProvider
    frags = {0: {"id": "call_1", "name": "echo", "args": '{"v": "x"}'}}
    turn = OpenAICompatProvider._assemble_stream_turn(["hi ", "there"], frags, ["thinking"], None)
    assert turn.text == "hi there"
    assert turn.stop_reason == "tool_use"
    assert turn.tool_calls[0].name == "echo"
    assert turn.tool_calls[0].args == {"v": "x"}
    assert turn.extra["reasoning_content"] == "thinking"


def test_openai_assemble_stream_turn_final_answer_no_tools():
    from codev_platform.agent.brain.openai_compat import OpenAICompatProvider
    turn = OpenAICompatProvider._assemble_stream_turn(["final"], {}, [], None)
    assert turn.text == "final" and turn.stop_reason == "end" and turn.tool_calls == []


def test_anthropic_assemble_turn_pure():
    from codev_platform.agent.brain.anthropic import AnthropicProvider

    class _Blk:
        def __init__(self, **kw):
            self.__dict__.update(kw)

    class _Usage:
        input_tokens, output_tokens = 11, 22

    class _Resp:
        content = [_Blk(type="text", text="hello"),
                   _Blk(type="tool_use", id="t1", name="echo", input={"v": "y"})]
        stop_reason = "tool_use"
        usage = _Usage()

    turn = AnthropicProvider._assemble_turn(_Resp())
    assert turn.text == "hello"
    assert turn.tool_calls[0].name == "echo" and turn.tool_calls[0].args == {"v": "y"}
    assert turn.stop_reason == "tool_use"
    assert turn.usage == {"input_tokens": 11, "output_tokens": 22}
