"""ChatService — 问答编排(application 层).

职责:解析会话 -> 取 provider -> 跑 loop -> 持久化 -> 返回 domain 结果。
不含任何 HTTP / DTO 知识(那是 routes 的事),因此可被 HTTP 同步 / 流式 / CLI 复用。
依赖通过构造注入,便于测试替换。
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Callable

from codev_platform.agent.brain.base import LLMProvider, Message
from codev_platform.agent.loop import AgentLoop, AgentResult
from codev_platform.agent.session import SessionStore
from codev_platform.agent.tools.base import ToolRegistry
from codev_platform.agent.trace import Trace


@dataclass
class ChatOutcome:
    """编排结果(domain 对象,非 HTTP DTO)。"""
    session_id: str
    result: AgentResult


class ChatService:
    def __init__(
        self,
        sessions: SessionStore,
        registry: ToolRegistry,
        provider_factory: Callable[[], LLMProvider],
        default_max_steps: Callable[[], int],
    ) -> None:
        # provider_factory 是 callable(每次调用重解析 config,支持运行中切 provider)
        self._sessions = sessions
        self._registry = registry
        self._provider_factory = provider_factory
        self._default_max_steps = default_max_steps

    def ask(self, question: str, session_id: str | None = None,
            max_steps: int | None = None, user_id: str = "local") -> ChatOutcome:
        provider = self._provider_factory()  # 缺 key 抛 RuntimeError,由调用层(route)映射

        if session_id and self._sessions.has(session_id, user_id):
            sid = session_id
        else:
            sid = self._sessions.new(user_id)
        history = self._sessions.get(sid, user_id)

        loop = AgentLoop(provider, self._registry, max_steps=max_steps or self._default_max_steps())
        trace = Trace(sid, provider.name, provider.model)
        result = loop.run(question, history=history, trace=trace)

        self._sessions.append(
            sid, user_id,
            Message(role="user", content=question),
            Message(role="assistant", content=result.answer),
        )
        return ChatOutcome(session_id=sid, result=result)
