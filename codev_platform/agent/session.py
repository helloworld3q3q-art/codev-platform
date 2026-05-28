"""会话存储. P0 进程内 dict;P2 再考虑持久化."""
from __future__ import annotations

import uuid

from codev_platform.agent.brain.base import Message


class SessionStore:
    def __init__(self) -> None:
        self._store: dict[str, list[Message]] = {}

    def new(self) -> str:
        sid = uuid.uuid4().hex
        self._store[sid] = []
        return sid

    def get(self, sid: str) -> list[Message]:
        return self._store.get(sid, [])

    def has(self, sid: str) -> bool:
        return sid in self._store

    def append(self, sid: str, *messages: Message) -> None:
        self._store.setdefault(sid, []).extend(messages)
