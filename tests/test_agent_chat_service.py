"""ChatService(application 层)测试 — 注入假 provider/registry,不触网不依赖 HTTP."""
from __future__ import annotations

from codev_platform.agent.brain import AssistantTurn, LLMProvider
from codev_platform.agent.services.chat_service import ChatService
from codev_platform.agent.session import SessionStore
from codev_platform.agent.tools.base import ToolRegistry


class _FakeProvider(LLMProvider):
    name, model = "fake", "m"

    def chat(self, system, messages, tools):
        return AssistantTurn(text="answer", tool_calls=[], stop_reason="end",
                             usage={"input_tokens": 1, "output_tokens": 1})


def _service() -> ChatService:
    return ChatService(
        sessions=SessionStore(),
        registry=ToolRegistry(),
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

    svc = ChatService(SessionStore(), ToolRegistry(), factory, lambda: 5)
    svc.ask("a")
    svc.ask("b")
    assert calls["n"] == 2  # 每次 ask 重解析 provider(支持运行中切换)
