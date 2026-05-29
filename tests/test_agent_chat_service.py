"""ChatService(application 层)测试 — 注入假 provider/registry,不触网不依赖 HTTP."""
from __future__ import annotations

from codev_platform.agent.brain import AssistantTurn, LLMProvider
from codev_platform.agent.memory_store import MemoryEntry
from codev_platform.agent.services.chat_service import ChatService
from codev_platform.agent.session import InMemorySessionStore
from codev_platform.agent.tools.base import ToolRegistry


class _FakeProvider(LLMProvider):
    name, model = "fake", "m"

    def chat(self, system, messages, tools):
        return AssistantTurn(text="answer", tool_calls=[], stop_reason="end",
                             usage={"input_tokens": 1, "output_tokens": 1})


class _CapturingProvider(LLMProvider):
    """记录收到的 system,用于断言召回记忆是否注入。"""
    name, model = "cap", "m"

    def __init__(self):
        self.seen_system = None

    def chat(self, system, messages, tools):
        self.seen_system = system
        return AssistantTurn(text="answer", tool_calls=[], stop_reason="end",
                             usage={"input_tokens": 1, "output_tokens": 1})


class _FakeRecall:
    """返回固定记忆的假召回服务(duck-typed RecallService)。"""
    def __init__(self, entries):
        self._entries = entries

    def recall(self, *, org_id, user_id, project_id, query="", limit=8, policy=None):
        return self._entries


class _RaisingRecall:
    def recall(self, **kwargs):
        raise RuntimeError("DB down")


def _service() -> ChatService:
    return ChatService(
        sessions=InMemorySessionStore(),
        registry_factory=lambda pid: ToolRegistry(),
        provider_factory=_FakeProvider,
        default_max_steps=lambda: 5,
    )


def test_ask_returns_outcome_with_session():
    svc = _service()
    out = svc.ask("q")
    assert out.session_id
    assert out.result.answer == "answer"
    assert out.result.stop_reason == "answered"


def test_session_continuity():
    svc = _service()
    out1 = svc.ask("q1")
    out2 = svc.ask("q2", session_id=out1.session_id)
    # 同 session 复用,历史累积(q1 问答 + q2 问 + q2 答)
    assert out2.session_id == out1.session_id


def test_provider_factory_called_per_ask(monkeypatch):
    calls = {"n": 0}

    def factory():
        calls["n"] += 1
        return _FakeProvider()

    svc = ChatService(InMemorySessionStore(), lambda pid: ToolRegistry(), factory, lambda: 5)
    svc.ask("a")
    svc.ask("b")
    assert calls["n"] == 2  # 每次 ask 重解析 provider(支持运行中切换)


def test_sessions_isolated_per_user():
    svc = _service()
    out_a = svc.ask("q", user_id="alice")
    # bob 用 alice 的 session_id 拿不到她的会话 -> 服务给 bob 新建 session
    out_b = svc.ask("q", session_id=out_a.session_id, user_id="bob")
    assert out_b.session_id != out_a.session_id


def test_registry_factory_called_with_project_id():
    seen: list[str | None] = []

    def reg_factory(pid):
        seen.append(pid)
        return ToolRegistry()

    svc = ChatService(InMemorySessionStore(), reg_factory, _FakeProvider, lambda: 5)
    svc.ask("q", project_id="proj-x")
    svc.ask("q2")  # 无 project_id -> None
    assert seen == ["proj-x", None]  # P2: project_id 透传到工具组装


# ---- M3 召回注入(成功路径 + 失败静默退化)----

def _entry(content, scope="personal", is_redline=False):
    return MemoryEntry(id=content, scope=scope, scope_ref="x", owner_user_id="u",
                       content=content, is_redline=is_redline)


def test_recall_memories_injected_into_system():
    prov = _CapturingProvider()
    svc = ChatService(InMemorySessionStore(), lambda pid: ToolRegistry(), lambda: prov,
                      lambda: 5, recall=_FakeRecall([_entry("用户喜欢钴蓝色")]))
    svc.ask("随便问")
    assert prov.seen_system is not None
    assert "用户喜欢钴蓝色" in prov.seen_system  # 召回记忆进了 system prompt


def test_recall_failure_does_not_block_answer():
    prov = _CapturingProvider()
    svc = ChatService(InMemorySessionStore(), lambda pid: ToolRegistry(), lambda: prov,
                      lambda: 5, recall=_RaisingRecall())
    out = svc.ask("q")  # 召回抛异常,但问答必须照常返回
    assert out.result.answer == "answer"
    assert prov.seen_system is not None  # 退化为不注入记忆,system 仍是基础 prompt


def test_no_recall_service_works():
    # recall=None(memory 未启用)→ 不注入,正常问答
    svc = _service()
    out = svc.ask("q")
    assert out.result.answer == "answer"
