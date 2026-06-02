"""账户存储 PG 实现 —— Org/User/OrgMember CRUD,落平台独立库 `codev_platform_memory`。

与 agent/rbac_store_pg.py **同库同表**(orgs/users/org_members,单一真值源, 不另造表):
RBAC 读走 fetch_membership, web CRUD 走本模块。schema(含 web 列 password_hash/status/email)
由 rbac_store_pg._SCHEMA 统一建/补,本模块复用其 ensure_schema(幂等)。

环境约束(同 rbac_store_pg):平台 venv 无 psycopg → 顶层不 import;ConnectionPool 在构造时 lazy
(open=False),首次用才连。调用方(providers.bind_account_stores)缺 psycopg/dsn 优雅回退内存实现。
接口与 account_store 的内存版**完全一致**(get/exists/list/create/upsert/...),可互换。
"""
from __future__ import annotations

from codev_platform.web.domain.accounts import Org, OrgMember, User


class _PgBase:
    """共享连接池 + schema 复用 rbac_store_pg(同库同表)。"""

    def __init__(self, dsn: str, *, min_size: int = 1, max_size: int = 4) -> None:
        from psycopg_pool import ConnectionPool

        self._pool = ConnectionPool(dsn, min_size=min_size, max_size=max_size, open=False)
        self._ready = False

    def _ensure(self) -> None:
        if self._ready:
            return
        from codev_platform.agent.rbac_store_pg import _SCHEMA

        self._pool.open()
        with self._pool.connection() as conn:
            conn.execute(_SCHEMA)
        self._ready = True


class PgOrgStore(_PgBase):
    def get(self, code: str) -> Org | None:
        self._ensure()
        with self._pool.connection() as conn:
            row = conn.execute(
                "SELECT org_id, name, status FROM orgs WHERE org_id=%s", (code,)
            ).fetchone()
        return Org(code=row[0], name=row[1] or "", status=row[2] or "ACTIVE") if row else None

    def exists(self, code: str) -> bool:
        return self.get(code) is not None

    def list(self) -> list[Org]:
        self._ensure()
        with self._pool.connection() as conn:
            rows = conn.execute("SELECT org_id, name, status FROM orgs ORDER BY org_id").fetchall()
        return [Org(code=r[0], name=r[1] or "", status=r[2] or "ACTIVE") for r in rows]

    def create(self, org: Org) -> Org:
        return self.upsert(org)

    def upsert(self, org: Org) -> Org:
        self._ensure()
        with self._pool.connection() as conn:
            conn.execute(
                "INSERT INTO orgs (org_id, name, status) VALUES (%s,%s,%s) "
                "ON CONFLICT (org_id) DO UPDATE SET name=EXCLUDED.name, status=EXCLUDED.status",
                (org.code, org.name, org.status),
            )
        return org


class PgUserStore(_PgBase):
    def get(self, username: str) -> User | None:
        self._ensure()
        with self._pool.connection() as conn:
            row = conn.execute(
                "SELECT user_id, password_hash, status, display_name, email FROM users WHERE user_id=%s",
                (username,),
            ).fetchone()
        if not row:
            return None
        # org_id 来自 org_members(取任一; 单 org/用户场景足够)。多 org 由请求级 X-Org-Id 决定。
        with self._pool.connection() as conn:
            m = conn.execute(
                "SELECT org_id FROM org_members WHERE user_id=%s ORDER BY org_id LIMIT 1", (username,)
            ).fetchone()
        return User(
            username=row[0], password_hash=row[1] or "", org_id=(m[0] if m else "default"),
            status=row[2] or "ACTIVE", display_name=row[3] or "", email=row[4] or "",
        )

    def exists(self, username: str) -> bool:
        self._ensure()
        with self._pool.connection() as conn:
            return conn.execute(
                "SELECT 1 FROM users WHERE user_id=%s", (username,)
            ).fetchone() is not None

    def list(self, org_id: str | None = None) -> list[User]:
        self._ensure()
        with self._pool.connection() as conn:
            if org_id is None:
                rows = conn.execute("SELECT user_id FROM users ORDER BY user_id").fetchall()
            else:
                rows = conn.execute(
                    "SELECT u.user_id FROM users u JOIN org_members m ON m.user_id=u.user_id "
                    "WHERE m.org_id=%s ORDER BY u.user_id",
                    (org_id,),
                ).fetchall()
        return [u for u in (self.get(r[0]) for r in rows) if u is not None]

    def create(self, user: User) -> User:
        return self.upsert(user)

    def upsert(self, user: User) -> User:
        self._ensure()
        with self._pool.connection() as conn:
            conn.execute(
                "INSERT INTO users (user_id, password_hash, status, display_name, email) "
                "VALUES (%s,%s,%s,%s,%s) ON CONFLICT (user_id) DO UPDATE SET "
                "password_hash=EXCLUDED.password_hash, status=EXCLUDED.status, "
                "display_name=EXCLUDED.display_name, email=EXCLUDED.email",
                (user.username, user.password_hash, user.status, user.display_name, user.email),
            )
        return user


class PgMemberStore(_PgBase):
    def get(self, org_id: str, username: str) -> OrgMember | None:
        self._ensure()
        with self._pool.connection() as conn:
            row = conn.execute(
                "SELECT org_role FROM org_members WHERE org_id=%s AND user_id=%s", (org_id, username)
            ).fetchone()
        return OrgMember(org_id=org_id, username=username, role=row[0]) if row else None

    def list_org(self, org_id: str) -> list[OrgMember]:
        self._ensure()
        with self._pool.connection() as conn:
            rows = conn.execute(
                "SELECT user_id, org_role FROM org_members WHERE org_id=%s ORDER BY user_id", (org_id,)
            ).fetchall()
        return [OrgMember(org_id=org_id, username=r[0], role=r[1]) for r in rows]

    def list_user(self, username: str) -> list[OrgMember]:
        self._ensure()
        with self._pool.connection() as conn:
            rows = conn.execute(
                "SELECT org_id, org_role FROM org_members WHERE user_id=%s ORDER BY org_id", (username,)
            ).fetchall()
        return [OrgMember(org_id=r[0], username=username, role=r[1]) for r in rows]

    def upsert(self, member: OrgMember) -> OrgMember:
        self._ensure()
        with self._pool.connection() as conn:
            conn.execute(
                "INSERT INTO org_members (org_id, user_id, org_role) VALUES (%s,%s,%s) "
                "ON CONFLICT (org_id, user_id) DO UPDATE SET org_role=EXCLUDED.org_role",
                (member.org_id, member.username, member.role),
            )
        return member

    def remove(self, org_id: str, username: str) -> None:
        self._ensure()
        with self._pool.connection() as conn:
            conn.execute(
                "DELETE FROM org_members WHERE org_id=%s AND user_id=%s", (org_id, username)
            )
