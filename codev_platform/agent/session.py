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
from dataclasses import dataclass
from datetime import datetime, timezone

from codev_platform.agent.brain.base import Message

_DEFAULT_ORG = "default"
_TITLE_MAX = 40


@dataclass
class SessionMeta:
    """会话摘要(列会话用)。**用 dataclass 不用裸 dict** —— 以后加 pinned / model
    等字段时调用方零破坏(扩展性,plan §六)。created_at / updated_at 在不追踪时间的实现
    (理论上)可为 None;title 由首条 user 消息派生,不落 schema 列。
    """

    session_id: str
    title: str
    message_count: int
    created_at: datetime | None = None
    updated_at: datetime | None = None


def derive_title(content: str | None) -> str:
    """首条 user 消息 → 会话标题(截断 + 兜底)。store 两实现共用,语义单一真值源。"""
    text = (content or "").strip().replace("\n", " ")
    if not text:
        return "新会话"
    return text[:_TITLE_MAX] + ("…" if len(text) > _TITLE_MAX else "")


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

    @abstractmethod
    def list_sessions(self, user_id: str, org_id: str = _DEFAULT_ORG,
                      *, limit: int = 50, offset: int = 0) -> list[SessionMeta]:
        """列出 (org_id, user_id) 名下会话,按最近活跃倒序。**绝不跨 user / 跨 org**
        (隔离红线:实现层 WHERE 强制带 org_id + user_id,plan §七)。
        """
        ...


class InMemorySessionStore(SessionStore):
    """进程内 dict 实现(默认)。重启即失忆;持久化走 PG 实现(M2)。

    存储键 = (org_id, user_id, session_id),按 org + user 双层隔离。
    """

    def __init__(self) -> None:
        self._store: dict[tuple[str, str, str], list[Message]] = {}
        # 并行存 created/updated,让 list_sessions 与 PG 实现返回同形 SessionMeta(timestamps 非 None)。
        self._meta: dict[tuple[str, str, str], tuple[datetime, datetime]] = {}

    def new(self, user_id: str, org_id: str = _DEFAULT_ORG) -> str:
        sid = uuid.uuid4().hex
        self._store[(org_id, user_id, sid)] = []
        now = datetime.now(timezone.utc)
        self._meta[(org_id, user_id, sid)] = (now, now)
        return sid

    def get(self, session_id: str, user_id: str, org_id: str = _DEFAULT_ORG) -> list[Message]:
        return self._store.get((org_id, user_id, session_id), [])

    def has(self, session_id: str, user_id: str, org_id: str = _DEFAULT_ORG) -> bool:
        return (org_id, user_id, session_id) in self._store

    def append(self, session_id: str, user_id: str, *messages: Message,
               org_id: str = _DEFAULT_ORG) -> None:
        key = (org_id, user_id, session_id)
        self._store.setdefault(key, []).extend(messages)
        created = self._meta.get(key, (datetime.now(timezone.utc),) * 2)[0]
        self._meta[key] = (created, datetime.now(timezone.utc))

    def list_sessions(self, user_id: str, org_id: str = _DEFAULT_ORG,
                      *, limit: int = 50, offset: int = 0) -> list[SessionMeta]:
        rows: list[SessionMeta] = []
        for (o, u, sid), msgs in self._store.items():
            if o != org_id or u != user_id:  # 隔离红线:只取本 org + 本 user
                continue
            first_user = next((m.content for m in msgs if m.role == "user"), None)
            created, updated = self._meta.get((o, u, sid), (None, None))
            rows.append(SessionMeta(
                session_id=sid, title=derive_title(first_user), message_count=len(msgs),
                created_at=created, updated_at=updated,
            ))
        # 最近活跃倒序(updated 缺失排末尾),再分页
        rows.sort(key=lambda r: (r.updated_at or datetime.min.replace(tzinfo=timezone.utc)), reverse=True)
        return rows[offset:offset + limit]
