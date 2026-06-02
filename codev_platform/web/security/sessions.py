"""会话存储 —— 登录态 access/refresh token (plan §十五 Auth)。

与 gateway 静态 config token 解耦: 登录动态签发的 token 走本 store。第一版内存实现
(进程内, 单实例); PG 实现 (codev_platform_memory 库) 留 TODO, 接口不变。
只存 token 的 sha256 hash (明文 token 不落 store, 与 gateway.token_hash 同范式)。
now 由调用方传入 (可测)。
"""
from __future__ import annotations

import hashlib
import secrets
import time
from dataclasses import dataclass

_ACCESS_TTL = 3600          # 1h
_REFRESH_TTL = 7 * 86400    # 7d


def _hash(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


@dataclass(frozen=True)
class Session:
    username: str
    org_id: str
    access_expires_at: float
    refresh_expires_at: float


@dataclass(frozen=True)
class IssuedTokens:
    access_token: str
    refresh_token: str
    session: Session


class SessionStore:
    """内存会话表。access_hash/refresh_hash → Session。TODO: PG 实现。"""

    def __init__(self, access_ttl: int = _ACCESS_TTL, refresh_ttl: int = _REFRESH_TTL) -> None:
        self._by_access: dict[str, Session] = {}
        self._by_refresh: dict[str, Session] = {}
        self._access_ttl = access_ttl
        self._refresh_ttl = refresh_ttl

    def create(self, username: str, org_id: str, now: float | None = None) -> IssuedTokens:
        now = time.time() if now is None else now
        access = secrets.token_urlsafe(32)
        refresh = secrets.token_urlsafe(32)
        sess = Session(
            username=username, org_id=org_id,
            access_expires_at=now + self._access_ttl,
            refresh_expires_at=now + self._refresh_ttl,
        )
        self._by_access[_hash(access)] = sess
        self._by_refresh[_hash(refresh)] = sess
        return IssuedTokens(access_token=access, refresh_token=refresh, session=sess)

    def resolve(self, access_token: str, now: float | None = None) -> Session | None:
        now = time.time() if now is None else now
        sess = self._by_access.get(_hash(access_token or ""))
        if sess is None or sess.access_expires_at < now:
            return None
        return sess

    def refresh(self, refresh_token: str, now: float | None = None) -> IssuedTokens | None:
        """refresh token 换新 access (+ 轮换 refresh)。失效/过期 → None。"""
        now = time.time() if now is None else now
        h = _hash(refresh_token or "")
        sess = self._by_refresh.get(h)
        if sess is None or sess.refresh_expires_at < now:
            return None
        self._by_refresh.pop(h, None)  # 轮换: 旧 refresh 失效
        return self.create(sess.username, sess.org_id, now=now)

    def revoke(self, refresh_token: str) -> None:
        """logout: 使 refresh 失效 (access 到期自然失效)。"""
        self._by_refresh.pop(_hash(refresh_token or ""), None)

    def clear(self) -> None:
        """清空所有会话 (测试隔离用)。"""
        self._by_access.clear()
        self._by_refresh.clear()

    def revoke_user(self, username: str) -> int:
        """禁用用户时: 撤销其所有会话。返回撤销数。"""
        n = 0
        for store in (self._by_access, self._by_refresh):
            for k in [k for k, s in store.items() if s.username == username]:
                store.pop(k, None)
                n += 1
        return n


# 进程内单实例 (重资源单例原则; 多会话经轻量代理共享 —— PG 实现后跨进程共享)。
session_store = SessionStore()
