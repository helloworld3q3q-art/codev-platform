"""账户存储 PG 实现 —— Org/User/OrgMember CRUD,落平台独立库 `codev_platform_memory`。

与 agent/rbac_store_pg.py **同库同表**(orgs/users/org_members,单一真值源, 不另造表):
RBAC 读走 fetch_membership, web CRUD 走本模块。schema(含 web 列 password_hash/status/email)
统一收口到 web/db/tables.py(2026-06-03 SQLAlchemy Core 迁移,原 rbac_store_pg._SCHEMA 删除)。

2026-06-03 迁移:裸 psycopg SQL 字符串 → SQLAlchemy Core select()/insert()/update()/delete()。
upsert 方言感知(PG / SQLite 测试各走各方言 on_conflict_do_update)。接口与 account_store 的内存版
**完全一致**(get/exists/list/create/upsert/...),返回 domain 对象不变,可互换。

环境约束(同 rbac_store_pg):平台 venv 无 psycopg → engine.make_engine 构造期探测 psycopg 缺则
ImportError;create_engine lazy 构造不连库,首次用才连。调用方(providers.bind_account_stores)
缺 psycopg/dsn 优雅回退内存实现。
"""
from __future__ import annotations

from sqlalchemy import delete, select
from sqlalchemy.engine import Engine

from codev_platform.web.db import tables
from codev_platform.web.domain.accounts import Org, OrgMember, User


def _upsert_stmt(table, values: dict, *, index_elements: list[str], update_cols: list[str], dialect_name: str):
    """方言感知 upsert(同 rbac_store_pg._upsert_stmt):PG / SQLite 各用各方言 on_conflict_do_update。"""
    if dialect_name == "postgresql":
        from sqlalchemy.dialects.postgresql import insert as _insert
    elif dialect_name == "sqlite":
        from sqlalchemy.dialects.sqlite import insert as _insert
    else:
        from sqlalchemy.dialects.postgresql import insert as _insert
    stmt = _insert(table).values(**values)
    set_ = {c: stmt.excluded[c] for c in update_cols}
    return stmt.on_conflict_do_update(index_elements=index_elements, set_=set_)


class _PgBase:
    """共享 SQLAlchemy engine + schema 收口 web/db/tables(同库同表)。

    构造期探测 psycopg(缺则 ImportError → 调用方优雅回退);create_engine lazy 不连库。
    也支持直接注入 engine(单测用 sqlite 内存 engine exercise 改写后的逻辑)。
    """

    def __init__(
        self,
        dsn: str | None = None,
        *,
        min_size: int = 1,
        max_size: int = 4,
        engine: Engine | None = None,
    ) -> None:
        if engine is not None:
            self._engine = engine
        else:
            if dsn is None:
                raise ValueError("PG account store 需要 dsn 或 engine 之一")
            from codev_platform.web.db.engine import make_engine

            self._engine = make_engine(dsn, pool_size=max_size)
        self._ready = False

    @property
    def _dialect(self) -> str:
        return self._engine.dialect.name

    def _ensure(self) -> None:
        if self._ready:
            return
        tables.metadata.create_all(self._engine)
        self._ready = True


class PgOrgStore(_PgBase):
    def get(self, code: str) -> Org | None:
        self._ensure()
        o = tables.orgs
        with self._engine.connect() as conn:
            row = conn.execute(
                select(o.c.org_id, o.c.name, o.c.status).where(o.c.org_id == code)
            ).first()
        return Org(code=row[0], name=row[1] or "", status=row[2] or "ACTIVE") if row else None

    def exists(self, code: str) -> bool:
        return self.get(code) is not None

    def list(self) -> list[Org]:
        self._ensure()
        o = tables.orgs
        with self._engine.connect() as conn:
            rows = conn.execute(
                select(o.c.org_id, o.c.name, o.c.status).order_by(o.c.org_id)
            ).all()
        return [Org(code=r[0], name=r[1] or "", status=r[2] or "ACTIVE") for r in rows]

    def create(self, org: Org) -> Org:
        return self.upsert(org)

    def upsert(self, org: Org) -> Org:
        self._ensure()
        stmt = _upsert_stmt(
            tables.orgs, {"org_id": org.code, "name": org.name, "status": org.status},
            index_elements=["org_id"], update_cols=["name", "status"], dialect_name=self._dialect,
        )
        with self._engine.begin() as conn:
            conn.execute(stmt)
        return org


class PgUserStore(_PgBase):
    def get(self, username: str) -> User | None:
        self._ensure()
        u = tables.users
        om = tables.org_members
        with self._engine.connect() as conn:
            row = conn.execute(
                select(u.c.user_id, u.c.password_hash, u.c.status, u.c.display_name, u.c.email)
                .where(u.c.user_id == username)
            ).first()
            if not row:
                return None
            # org_id 来自 org_members(取任一; 单 org/用户场景足够)。多 org 由请求级 X-Org-Id 决定。
            m = conn.execute(
                select(om.c.org_id).where(om.c.user_id == username).order_by(om.c.org_id).limit(1)
            ).first()
        return User(
            username=row[0], password_hash=row[1] or "", org_id=(m[0] if m else "default"),
            status=row[2] or "ACTIVE", display_name=row[3] or "", email=row[4] or "",
        )

    def exists(self, username: str) -> bool:
        self._ensure()
        u = tables.users
        with self._engine.connect() as conn:
            return conn.execute(
                select(u.c.user_id).where(u.c.user_id == username)
            ).first() is not None

    def list(self, org_id: str | None = None) -> list[User]:
        self._ensure()
        u = tables.users
        om = tables.org_members
        with self._engine.connect() as conn:
            if org_id is None:
                rows = conn.execute(select(u.c.user_id).order_by(u.c.user_id)).all()
            else:
                rows = conn.execute(
                    select(u.c.user_id)
                    .select_from(u.join(om, om.c.user_id == u.c.user_id))
                    .where(om.c.org_id == org_id)
                    .order_by(u.c.user_id)
                ).all()
        return [usr for usr in (self.get(r[0]) for r in rows) if usr is not None]

    def create(self, user: User) -> User:
        return self.upsert(user)

    def upsert(self, user: User) -> User:
        self._ensure()
        stmt = _upsert_stmt(
            tables.users,
            {
                "user_id": user.username, "password_hash": user.password_hash,
                "status": user.status, "display_name": user.display_name, "email": user.email,
            },
            index_elements=["user_id"],
            update_cols=["password_hash", "status", "display_name", "email"],
            dialect_name=self._dialect,
        )
        with self._engine.begin() as conn:
            conn.execute(stmt)
        return user


class PgMemberStore(_PgBase):
    def get(self, org_id: str, username: str) -> OrgMember | None:
        self._ensure()
        om = tables.org_members
        with self._engine.connect() as conn:
            row = conn.execute(
                select(om.c.org_role).where(om.c.org_id == org_id, om.c.user_id == username)
            ).first()
        return OrgMember(org_id=org_id, username=username, role=row[0]) if row else None

    def list_org(self, org_id: str) -> list[OrgMember]:
        self._ensure()
        om = tables.org_members
        with self._engine.connect() as conn:
            rows = conn.execute(
                select(om.c.user_id, om.c.org_role).where(om.c.org_id == org_id).order_by(om.c.user_id)
            ).all()
        return [OrgMember(org_id=org_id, username=r[0], role=r[1]) for r in rows]

    def list_user(self, username: str) -> list[OrgMember]:
        self._ensure()
        om = tables.org_members
        with self._engine.connect() as conn:
            rows = conn.execute(
                select(om.c.org_id, om.c.org_role).where(om.c.user_id == username).order_by(om.c.org_id)
            ).all()
        return [OrgMember(org_id=r[0], username=username, role=r[1]) for r in rows]

    def upsert(self, member: OrgMember) -> OrgMember:
        self._ensure()
        stmt = _upsert_stmt(
            tables.org_members,
            {"org_id": member.org_id, "user_id": member.username, "org_role": member.role},
            index_elements=["org_id", "user_id"], update_cols=["org_role"], dialect_name=self._dialect,
        )
        with self._engine.begin() as conn:
            conn.execute(stmt)
        return member

    def remove(self, org_id: str, username: str) -> None:
        self._ensure()
        om = tables.org_members
        with self._engine.begin() as conn:
            conn.execute(delete(om).where(om.c.org_id == org_id, om.c.user_id == username))
