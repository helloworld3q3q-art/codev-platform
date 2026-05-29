"""会话存储 — 抽象接口 + 内存实现.

分层 / 可扩展:SessionStore 是抽象(接缝),当前 InMemorySessionStore 默认实现;
PG 持久化实现(SqlSessionStore)等 codev_platform_memory 库就绪后插入(memory plan M2),
上层(ChatService)只依赖抽象,换实现零改。

会话按 user_id 作用域:不同 user 的会话互不可见(memory 权限模型的第一步隔离)。
"""
from __future__ import annotations

import uuid
from abc import ABC, abstractmethod

from codev_platform.agent.brain.base import Message


class SessionStore(ABC):
    """会话存储抽象。实现可为内存 / PG / Redis 等,上层不感知。"""

    @abstractmethod
    def new(self, user_id: str) -> str: ...

    @abstractmethod
    def get(self, session_id: str, user_id: str) -> list[Message]: ...

    @abstractmethod
    def has(self, session_id: str, user_id: str) -> bool: ...

    @abstractmethod
    def append(self, session_id: str, user_id: str, *messages: Message) -> None: ...


class InMemorySessionStore(SessionStore):
    """进程内 dict 实现(默认)。重启即失忆;持久化走 PG 实现(M2)。

    存储键 = (user_id, session_id),天然按 user 隔离:A 用户拿不到 B 的会话。
    """

    def __init__(self) -> None:
        self._store: dict[tuple[str, str], list[Message]] = {}

    def new(self, user_id: str) -> str:
        sid = uuid.uuid4().hex
        self._store[(user_id, sid)] = []
        return sid

    def get(self, session_id: str, user_id: str) -> list[Message]:
        return self._store.get((user_id, session_id), [])

    def has(self, session_id: str, user_id: str) -> bool:
        return (user_id, session_id) in self._store

    def append(self, session_id: str, user_id: str, *messages: Message) -> None:
        self._store.setdefault((user_id, session_id), []).extend(messages)
