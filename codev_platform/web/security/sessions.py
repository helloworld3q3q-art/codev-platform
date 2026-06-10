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

from sqlalchemy import delete, insert, select

from codev_platform.core.config import load_config
from codev_platform.web.db import tables as _t
from codev_platform.web.repositories.account_store_pg import _PgBase

_ACCESS_TTL = 3600          # 1h
_REFRESH_TTL = 7 * 86400    # 7d


def _hash(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


@dataclass(frozen=True)
class Session:
    session_id: str
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
        self._by_session: dict[str, tuple[str, str]] = {}  # session_id -> (access_hash, refresh_hash)
        self._access_ttl = access_ttl
        self._refresh_ttl = refresh_ttl

    def create(self, username: str, org_id: str, now: float | None = None) -> IssuedTokens:
        now = time.time() if now is None else now
        access = secrets.token_urlsafe(32)
        refresh = secrets.token_urlsafe(32)
        sid = secrets.token_urlsafe(16)
        sess = Session(
            session_id=sid, username=username, org_id=org_id,
            access_expires_at=now + self._access_ttl,
            refresh_expires_at=now + self._refresh_ttl,
        )
        ah, rh = _hash(access), _hash(refresh)
        self._by_access[ah] = sess
        self._by_refresh[rh] = sess
        self._by_session[sid] = (ah, rh)
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
        # 清理旧 session 映射 (旧 access 同步失效, 避免 _by_session 泄漏陈旧条目)
        old = self._by_session.pop(sess.session_id, None)
        if old is not None:
            self._by_access.pop(old[0], None)
        return self.create(sess.username, sess.org_id, now=now)

    def revoke(self, refresh_token: str) -> None:
        """logout: 撤销整个会话 (access + refresh 一并失效, 防 access 残留可用)。"""
        rh = _hash(refresh_token or "")
        sess = self._by_refresh.get(rh)
        if sess is not None:
            old = self._by_session.pop(sess.session_id, None)
            if old is not None:
                ah, rh2 = old
                self._by_access.pop(ah, None)
                self._by_refresh.pop(rh2, None)
                return
        # 映射缺失时回退: 至少 pop refresh_hash (幂等, 未知 token 静默)
        self._by_refresh.pop(rh, None)

    def clear(self) -> None:
        """清空所有会话 (测试隔离用)。"""
        self._by_access.clear()
        self._by_refresh.clear()
        self._by_session.clear()

    def revoke_user(self, username: str) -> int:
        """禁用用户时: 撤销其所有会话。返回撤销的**会话数**(与 PgSessionStore.revoke_user 的
        rowcount 对齐 —— 非 token entry 数; 两路实现返回契约一致)。"""
        sids = {s.session_id for s in self._by_access.values() if s.username == username}
        sids |= {s.session_id for s in self._by_refresh.values() if s.username == username}
        for store in (self._by_access, self._by_refresh):
            for k in [k for k, s in store.items() if s.username == username]:
                store.pop(k, None)
        for sid in sids:
            self._by_session.pop(sid, None)
        return len(sids)


class PgSessionStore(_PgBase):
    """PG 会话存储 —— 登录态落库(重启不丢 / 多 worker 共享 / revoke 跨进程, backend-deep P1-3)。
    复用 account_store_pg._PgBase; 接口与内存 SessionStore 完全一致(create/resolve/refresh/revoke/
    clear/revoke_user), 可互换。只存 token 的 sha256 hash(明文不落库)。"""

    def __init__(self, dsn: str | None = None, *, access_ttl: int = _ACCESS_TTL,
                 refresh_ttl: int = _REFRESH_TTL, engine=None) -> None:
        super().__init__(dsn, engine=engine)
        self._access_ttl = access_ttl
        self._refresh_ttl = refresh_ttl

    @staticmethod
    def _row_to_session(row) -> Session:
        return Session(session_id=row[0], username=row[1], org_id=row[2],
                       access_expires_at=row[3], refresh_expires_at=row[4])

    def create(self, username: str, org_id: str, now: float | None = None) -> IssuedTokens:
        now = time.time() if now is None else now
        access, refresh = secrets.token_urlsafe(32), secrets.token_urlsafe(32)
        sid = secrets.token_urlsafe(16)
        sess = Session(session_id=sid, username=username, org_id=org_id,
                       access_expires_at=now + self._access_ttl,
                       refresh_expires_at=now + self._refresh_ttl)
        self._ensure()
        with self._engine.begin() as conn:
            conn.execute(insert(_t.sessions).values(
                session_id=sid, username=username, org_id=org_id,
                access_hash=_hash(access), refresh_hash=_hash(refresh),
                access_expires_at=sess.access_expires_at,
                refresh_expires_at=sess.refresh_expires_at))
        return IssuedTokens(access_token=access, refresh_token=refresh, session=sess)

    def resolve(self, access_token: str, now: float | None = None) -> Session | None:
        now = time.time() if now is None else now
        self._ensure()
        s = _t.sessions
        with self._engine.connect() as conn:
            row = conn.execute(
                select(s.c.session_id, s.c.username, s.c.org_id,
                       s.c.access_expires_at, s.c.refresh_expires_at)
                .where(s.c.access_hash == _hash(access_token or ""))).first()
        if row is None or row[3] < now:
            return None
        return self._row_to_session(row)

    def refresh(self, refresh_token: str, now: float | None = None) -> IssuedTokens | None:
        now = time.time() if now is None else now
        self._ensure()
        s = _t.sessions
        rh = _hash(refresh_token or "")
        # 原子消费(安全审计 P1#2): 单事务 DELETE ... RETURNING 只删**未过期**的该 refresh。
        # 并发下只有一个请求删到行(RETURNING 拿身份), 其余得空 → None, 杜绝 refresh 重放/双签。
        # 原先 SELECT(连接 A)+ 另起事务 DELETE 非原子, 两 worker 可同时读到旧 refresh 各自签发。
        with self._engine.begin() as conn:
            row = conn.execute(
                delete(s)
                .where(s.c.refresh_hash == rh, s.c.refresh_expires_at >= now)
                .returning(s.c.username, s.c.org_id)).first()
        if row is None:   # 没删到(已被消费 / 过期 / 不存在)→ 拒绝轮换
            return None
        return self.create(row[0], row[1], now=now)

    def revoke(self, refresh_token: str) -> None:
        self._ensure()
        s = _t.sessions
        with self._engine.begin() as conn:
            conn.execute(delete(s).where(s.c.refresh_hash == _hash(refresh_token or "")))

    def clear(self) -> None:
        self._ensure()
        with self._engine.begin() as conn:
            conn.execute(delete(_t.sessions))

    def revoke_user(self, username: str) -> int:
        self._ensure()
        s = _t.sessions
        with self._engine.begin() as conn:
            res = conn.execute(delete(s).where(s.c.username == username))
        return res.rowcount or 0


def bind_session_store(cfg: dict | None = None):
    """按 config 选会话存储后端 —— memory.pg_dsn + psycopg 可用 → PG, 否则内存(优雅回退, 复刻
    bind_account_stores)。prod 配 PG 却缺 psycopg / 初始化失败 → fail-fast。backend-deep P1-3。"""
    from codev_platform.core.config import get as _cfg_get
    dsn = _cfg_get(cfg or {}, "memory.pg_dsn", None)
    if not dsn:
        return SessionStore()
    try:
        return PgSessionStore(dsn)
    except Exception:  # noqa: BLE001 — ImportError(缺 psycopg) 或 engine 初始化失败
        mode = _cfg_get(cfg or {}, "deployment.mode", "dev")
        if mode == "prod":
            raise  # prod 配 PG 却失败 → fail-fast(防登录态静默走内存、重启即丢)
        return SessionStore()


# 活动会话存储: import 时 bind(dev 无 dsn 回退内存 = 行为不变; prod 配 dsn 用 PG —— 重启不丢 /
# 多 worker 共享 / revoke 跨进程)。
session_store = bind_session_store(load_config())


def get_session_store():
    """取活动会话存储(经 getter 而非 `from ... import session_store` 捕获对象)。

    复刻 account_store 的 getter 范式: 跨模块消费方(deps/authenticator/auth_service/user_service)
    走本 getter, 才能在 `rebind_web_services(cfg)` 重绑后看到新后端 —— 直接 import 单例会捕获旧对象,
    create_app(cfg) 传显式 cfg 时就会 authenticator 用一份、session 用另一份(本次修复目标)。
    返回调用期的模块全局, 故重绑(`sessions.session_store = ...`)对本 getter 可见。
    """
    return session_store
