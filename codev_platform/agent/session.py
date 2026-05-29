"""会话存储 — 抽象接口 + 内存实现.

分层 / 可扩展:SessionStore 是抽象(接缝),当前 InMemorySessionStore 默认实现;
PG 持久化实现(SqlSessionStore)等 codev_platform_memory 库就绪后插入(memory plan M2),
上层(ChatService)只依赖抽象,换实现零改。

会话按 (org_id, user_id) 作用域:跨 org / 跨 user 的会话互不可见(memory 权限模型第一步隔离)。
org_id 是请求级(plan §3.4:一人多 org 无法从 user 推),故随每次调用传入,默认 'default'(单 org 期)。
"""
from __future__ import annotations

import uuid
from abc import ABC, abstractmethod

from codev_platform.agent.brain.base import Message

_DEFAULT_ORG = "default"


class SessionStore(ABC):
    """会话存储抽象。实现可为内存 / PG / Redis 等,上层不感知。

    所有方法带请求级 org_id(默认 'default'),保证多 org 期会话按 org 物理隔离 ——
    否则不同 org 的同名 user 会撞进同一会话命名空间(plan §3.4)。
    """

    @abstractmethod
    def new(self, user_id: str, org_id: str = _DEFAULT_ORG) -> str: ...

    @abstractmethod
    def get(self, session_id: str, user_id: str, org_id: str = _DEFAULT_ORG) -> list[Message]: ...

    @abstractmethod
    def has(self, session_id: str, user_id: str, org_id: str = _DEFAULT_ORG) -> bool: ...

    @abstractmethod
    def append(self, session_id: str, user_id: str, *messages: Message,
               org_id: str = _DEFAULT_ORG) -> None: ...


class InMemorySessionStore(SessionStore):
    """进程内 dict 实现(默认)。重启即失忆;持久化走 PG 实现(M2)。

    存储键 = (org_id, user_id, session_id),按 org + user 双层隔离。
    """

    def __init__(self) -> None:
        self._store: dict[tuple[str, str, str], list[Message]] = {}

    def new(self, user_id: str, org_id: str = _DEFAULT_ORG) -> str:
        sid = uuid.uuid4().hex
        self._store[(org_id, user_id, sid)] = []
        return sid

    def get(self, session_id: str, user_id: str, org_id: str = _DEFAULT_ORG) -> list[Message]:
        return self._store.get((org_id, user_id, session_id), [])

    def has(self, session_id: str, user_id: str, org_id: str = _DEFAULT_ORG) -> bool:
        return (org_id, user_id, session_id) in self._store

    def append(self, session_id: str, user_id: str, *messages: Message,
               org_id: str = _DEFAULT_ORG) -> None:
        self._store.setdefault((org_id, user_id, session_id), []).extend(messages)
