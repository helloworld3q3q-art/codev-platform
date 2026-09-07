"""RbacStore —— RBAC 身份/成员/作用域表的 PostgreSQL 实现(memory M5 ACL 底座)。

承载 plan §3.4 的 orgs / users / org_members / teams / team_members / projects /
project_access 七张表(全在平台独立库 `codev_platform_memory`,与 memory_entries 并排)。

职责切分(与 core/rbac.py 配套):
  - core/rbac.py    纯权限逻辑(Membership / compute_visible_scopes / role_allows),无 IO,可单测。
  - 本模块          PG 取数 —— 写 (add_*/grant_*) + 读 fetch_membership 组装 core.rbac.Membership。

2026-06-03 SQLAlchemy Core 迁移:裸 psycopg SQL 字符串 → select()/insert()/update()/delete()。
表定义统一收口到 web/db/tables.py(原 `_SCHEMA` 大字符串删除,单一真值源)。upsert 方言感知:
PG 走 dialects.postgresql.insert(...).on_conflict_do_update,SQLite(测试)走 sqlite 同名方言,
按 engine.dialect.name 分支。接口签名 + 返回类型完全不变(上层 core/rbac 无感)。

环境约束:平台 venv 无 psycopg。engine.make_engines 构造期探测 psycopg,缺则 ImportError = 正常;
调用方(deps.get_rbac_store)lazy import + 缺 psycopg/dsn 优雅回退 None,不在此处兜。
构造期不连库(create_engine lazy)，首次用时只读验证 Alembic 已迁移 schema。
"""
from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.engine import Engine

from codev_platform.core.rbac import Membership
from codev_platform.web.db import tables


def _upsert_stmt(table, values: dict, *, index_elements: list[str], update_cols: list[str], dialect_name: str):
    """方言感知 upsert:PG / SQLite 各用各自方言的 insert().on_conflict_do_update()。

    index_elements: 冲突判定的主键列;update_cols: 冲突时用 EXCLUDED 覆盖的列。
    """
    if dialect_name == "postgresql":
        from sqlalchemy.dialects.postgresql import insert as _insert
    elif dialect_name == "sqlite":
        from sqlalchemy.dialects.sqlite import insert as _insert
    else:  # 兜底:其它方言(理论上不会走到,RBAC 库仅 PG / 测试 SQLite)
        from sqlalchemy.dialects.postgresql import insert as _insert
    stmt = _insert(table).values(**values)
    set_ = {c: stmt.excluded[c] for c in update_cols}
    return stmt.on_conflict_do_update(index_elements=index_elements, set_=set_)


class RbacStore:
    """RBAC 表的 PG 存储。读写分离接缝(同 SqlMemoryStore):写走 dsn 主库,读走 read_dsn 副本。

    2026-06-03 起底层是 SQLAlchemy Core engine(原 psycopg ConnectionPool)。也支持直接注入
    已建好的 (write, read) engine —— 单测用 sqlite 内存 engine exercise 改写后的逻辑。
    """

    def __init__(
        self,
        dsn: str | None = None,
        read_dsn: str | None = None,
        *,
        min_size: int = 1,
        max_size: int = 4,
        engine: Engine | None = None,
        read_engine: Engine | None = None,
    ) -> None:
        if engine is not None:
            self._write_engine = engine
            self._read_engine = read_engine if read_engine is not None else engine
            self._split = read_engine is not None and read_engine is not engine
        else:
            if dsn is None:
                raise ValueError("RbacStore 需要 dsn 或 engine 之一")
            from codev_platform.web.db.engine import make_engines

            self._write_engine, self._read_engine = make_engines(dsn, read_dsn, pool_size=max_size)
            self._split = self._read_engine is not self._write_engine
        self._schema_verified = False

    @property
    def _dialect(self) -> str:
        return self._write_engine.dialect.name

    def ensure_schema(self) -> None:
        """只读验证七张 RBAC 表；生产 DDL 只能由迁移协调器执行。"""
        if self._schema_verified:
            return
        from codev_platform.web.db.runtime_schema import verify_sqlalchemy_tables

        verify_sqlalchemy_tables(
            self._write_engine,
            (
                tables.orgs,
                tables.users,
                tables.org_members,
                tables.teams,
                tables.team_members,
                tables.projects,
                tables.project_access,
            ),
        )
        self._schema_verified = True

    # ---- 写路径(主库,全参数化 + ON CONFLICT 幂等)----

    def add_org(self, org_id: str, name: str | None = None) -> None:
        self.ensure_schema()
        stmt = _upsert_stmt(
            tables.orgs, {"org_id": org_id, "name": name},
            index_elements=["org_id"], update_cols=["name"], dialect_name=self._dialect,
        )
        with self._write_engine.begin() as conn:
            conn.execute(stmt)

    def add_user(self, user_id: str, name: str | None = None) -> None:
        self.ensure_schema()
        stmt = _upsert_stmt(
            tables.users, {"user_id": user_id, "display_name": name},
            index_elements=["user_id"], update_cols=["display_name"], dialect_name=self._dialect,
        )
        with self._write_engine.begin() as conn:
            conn.execute(stmt)

    def add_org_member(self, org_id: str, user_id: str, role: str = "member") -> None:
        self.ensure_schema()
        stmt = _upsert_stmt(
            tables.org_members, {"org_id": org_id, "user_id": user_id, "org_role": role},
            index_elements=["org_id", "user_id"], update_cols=["org_role"], dialect_name=self._dialect,
        )
        with self._write_engine.begin() as conn:
            conn.execute(stmt)

    def add_team(self, team_id: str, org_id: str, name: str | None = None) -> None:
        self.ensure_schema()
        stmt = _upsert_stmt(
            tables.teams, {"team_id": team_id, "org_id": org_id, "name": name},
            index_elements=["team_id"], update_cols=["org_id", "name"], dialect_name=self._dialect,
        )
        with self._write_engine.begin() as conn:
            conn.execute(stmt)

    def add_team_member(self, team_id: str, user_id: str, role: str = "member") -> None:
        self.ensure_schema()
        stmt = _upsert_stmt(
            tables.team_members, {"team_id": team_id, "user_id": user_id, "role": role},
            index_elements=["team_id", "user_id"], update_cols=["role"], dialect_name=self._dialect,
        )
        with self._write_engine.begin() as conn:
            conn.execute(stmt)

    def upsert_project(self, project_id: str, org_id: str, name: str | None = None) -> None:
        self.ensure_schema()
        stmt = _upsert_stmt(
            tables.projects, {"project_id": project_id, "org_id": org_id, "display_name": name},
            index_elements=["project_id"], update_cols=["org_id", "display_name"], dialect_name=self._dialect,
        )
        with self._write_engine.begin() as conn:
            conn.execute(stmt)

    def grant_project(self, project_id: str, principal: str, principal_kind: str, role: str = "member") -> None:
        """授权 principal(user_id 或 team_id)对 project 的 role。principal_kind in {user, team}。"""
        self.ensure_schema()
        stmt = _upsert_stmt(
            tables.project_access,
            {"project_id": project_id, "principal": principal, "principal_kind": principal_kind, "role": role},
            index_elements=["project_id", "principal"], update_cols=["principal_kind", "role"],
            dialect_name=self._dialect,
        )
        with self._write_engine.begin() as conn:
            conn.execute(stmt)

    # ---- 读路径(副本,若配置)----

    def fetch_membership(self, org_id: str, user_id: str, project_id: str | None = None) -> Membership:
        """组装 core.rbac.Membership:该 (org, user) 的 org_role + 本 org 内 teams + 当前 project_role。

        - org_role:    org_members 里该 (org_id, user_id) 的 org_role(无 → None,非成员)。
        - teams:       team_members ∩ (本 org 的 teams) ∩ user_id → [(team_id, role), ...]。
        - project_role: project_access 对该 user(principal_kind='user')或其所属 team
                        (principal_kind='team')的 role;多来源取最高(admin>member>viewer)。
        """
        self.ensure_schema()
        om = tables.org_members
        tm = tables.team_members
        t = tables.teams
        pa = tables.project_access
        p = tables.projects

        with self._read_engine.connect() as conn:
            row = conn.execute(
                select(om.c.org_role).where(om.c.org_id == org_id, om.c.user_id == user_id)
            ).first()
            org_role = row[0] if row else None

            team_rows = conn.execute(
                select(tm.c.team_id, tm.c.role)
                .select_from(tm.join(t, t.c.team_id == tm.c.team_id))
                .where(t.c.org_id == org_id, tm.c.user_id == user_id)
                .order_by(tm.c.team_id)
            ).all()
            teams = tuple((r[0], r[1]) for r in team_rows)

            project_role = None
            if project_id:
                team_ids = [t_[0] for t_ in teams]
                # 该 project 必须属于本 org(跨 org 不可见);principal 命中 user 或其 team。
                principal_match = (pa.c.principal_kind == "user") & (pa.c.principal == user_id)
                if team_ids:
                    principal_match = principal_match | (
                        (pa.c.principal_kind == "team") & (pa.c.principal.in_(team_ids))
                    )
                acc_rows = conn.execute(
                    select(pa.c.role)
                    .select_from(pa.join(p, p.c.project_id == pa.c.project_id))
                    .where(pa.c.project_id == project_id, p.c.org_id == org_id, principal_match)
                ).all()
                project_role = _highest_role([r[0] for r in acc_rows])

        return Membership(org_role=org_role, teams=teams, project_role=project_role)


# 角色等级(与 core.rbac.ROLE_RANK 一致,本地复制避免在读层 import 计算细节)。
_ROLE_RANK = {"viewer": 1, "member": 2, "admin": 3}


def _highest_role(roles: list[str | None]) -> str | None:
    """多来源 role 取最高(admin>member>viewer);全空 → None。"""
    best: str | None = None
    best_rank = 0
    for r in roles:
        rank = _ROLE_RANK.get(r or "", 0)
        if rank > best_rank:
            best_rank = rank
            best = r
    return best
