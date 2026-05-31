"""RbacStore —— RBAC 身份/成员/作用域表的 PostgreSQL 实现(memory M5 ACL 底座)。

承载 plan §3.4 的 orgs / users / org_members / teams / team_members / projects /
project_access 七张表(全在平台独立库 `codev_platform_memory`,与 memory_entries 并排)。

职责切分(与 core/rbac.py 配套):
  - core/rbac.py    纯权限逻辑(Membership / compute_visible_scopes / role_allows),无 IO,可单测。
  - 本模块          PG 取数 —— 写 (add_*/grant_*) + 读 fetch_membership 组装 core.rbac.Membership。

环境约束:平台 venv 无 psycopg。顶层 import psycopg_pool 在本 venv 会失败 = 正常;
调用方(deps.get_rbac_store)lazy import + 缺 psycopg/dsn 优雅回退 None,不在此处兜。
import / __init__ 期不连库(ConnectionPool open=False),首次用时才 open(同 memory_store_pg 范式)。
"""
from __future__ import annotations

from codev_platform.core.rbac import Membership

_SCHEMA = """
CREATE TABLE IF NOT EXISTS orgs (
  org_id     TEXT PRIMARY KEY,
  name       TEXT,
  created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE TABLE IF NOT EXISTS users (
  user_id      TEXT PRIMARY KEY,
  display_name TEXT,
  created_at   TIMESTAMPTZ NOT NULL DEFAULT now()
);
-- user <-> org 多对多(一人多 org / 一 org 多人), org_role 为 org 级角色。
CREATE TABLE IF NOT EXISTS org_members (
  org_id   TEXT NOT NULL REFERENCES orgs(org_id),
  user_id  TEXT NOT NULL REFERENCES users(user_id),
  org_role TEXT NOT NULL DEFAULT 'member',   -- admin | member | viewer
  PRIMARY KEY (org_id, user_id)
);
CREATE TABLE IF NOT EXISTS teams (
  team_id TEXT PRIMARY KEY,
  org_id  TEXT NOT NULL REFERENCES orgs(org_id),
  name    TEXT
);
CREATE TABLE IF NOT EXISTS team_members (
  team_id TEXT NOT NULL REFERENCES teams(team_id),
  user_id TEXT NOT NULL REFERENCES users(user_id),
  role    TEXT NOT NULL DEFAULT 'member',     -- admin | member | viewer
  PRIMARY KEY (team_id, user_id)
);
CREATE TABLE IF NOT EXISTS projects (
  project_id   TEXT PRIMARY KEY,
  org_id       TEXT NOT NULL REFERENCES orgs(org_id),
  display_name TEXT
);
-- principal = user_id 或 team_id; principal_kind ∈ {user, team}。
CREATE TABLE IF NOT EXISTS project_access (
  project_id     TEXT NOT NULL REFERENCES projects(project_id),
  principal      TEXT NOT NULL,
  principal_kind TEXT NOT NULL,                -- user | team
  role           TEXT NOT NULL DEFAULT 'member',
  PRIMARY KEY (project_id, principal)
);
CREATE INDEX IF NOT EXISTS ix_org_members_user ON org_members (user_id);
CREATE INDEX IF NOT EXISTS ix_team_members_user ON team_members (user_id);
CREATE INDEX IF NOT EXISTS ix_teams_org ON teams (org_id);
"""


class RbacStore:
    """RBAC 表的 PG 存储。读写分离接缝(同 SqlMemoryStore):写走 dsn 主库,读走 read_dsn 副本。"""

    def __init__(self, dsn: str, read_dsn: str | None = None, *, min_size: int = 1, max_size: int = 4) -> None:
        from psycopg_pool import ConnectionPool
        self._write_pool = ConnectionPool(dsn, min_size=min_size, max_size=max_size, open=False)
        if read_dsn and read_dsn != dsn:
            self._read_pool = ConnectionPool(read_dsn, min_size=min_size, max_size=max_size, open=False)
            self._split = True
        else:
            self._read_pool = self._write_pool
            self._split = False
        self._schema_ready = False

    def ensure_schema(self) -> None:
        """幂等建七张表(CREATE TABLE IF NOT EXISTS)。首次用时才 open 连接池。"""
        if self._schema_ready:
            return
        self._write_pool.open()
        if self._split:
            self._read_pool.open()
        with self._write_pool.connection() as conn:
            conn.execute(_SCHEMA)
        self._schema_ready = True

    # ---- 写路径(主库,全参数化 + ON CONFLICT 幂等)----

    def add_org(self, org_id: str, name: str | None = None) -> None:
        self.ensure_schema()
        with self._write_pool.connection() as conn:
            conn.execute(
                "INSERT INTO orgs (org_id, name) VALUES (%s,%s) "
                "ON CONFLICT (org_id) DO UPDATE SET name=EXCLUDED.name",
                (org_id, name),
            )

    def add_user(self, user_id: str, name: str | None = None) -> None:
        self.ensure_schema()
        with self._write_pool.connection() as conn:
            conn.execute(
                "INSERT INTO users (user_id, display_name) VALUES (%s,%s) "
                "ON CONFLICT (user_id) DO UPDATE SET display_name=EXCLUDED.display_name",
                (user_id, name),
            )

    def add_org_member(self, org_id: str, user_id: str, role: str = "member") -> None:
        self.ensure_schema()
        with self._write_pool.connection() as conn:
            conn.execute(
                "INSERT INTO org_members (org_id, user_id, org_role) VALUES (%s,%s,%s) "
                "ON CONFLICT (org_id, user_id) DO UPDATE SET org_role=EXCLUDED.org_role",
                (org_id, user_id, role),
            )

    def add_team(self, team_id: str, org_id: str, name: str | None = None) -> None:
        self.ensure_schema()
        with self._write_pool.connection() as conn:
            conn.execute(
                "INSERT INTO teams (team_id, org_id, name) VALUES (%s,%s,%s) "
                "ON CONFLICT (team_id) DO UPDATE SET org_id=EXCLUDED.org_id, name=EXCLUDED.name",
                (team_id, org_id, name),
            )

    def add_team_member(self, team_id: str, user_id: str, role: str = "member") -> None:
        self.ensure_schema()
        with self._write_pool.connection() as conn:
            conn.execute(
                "INSERT INTO team_members (team_id, user_id, role) VALUES (%s,%s,%s) "
                "ON CONFLICT (team_id, user_id) DO UPDATE SET role=EXCLUDED.role",
                (team_id, user_id, role),
            )

    def upsert_project(self, project_id: str, org_id: str, name: str | None = None) -> None:
        self.ensure_schema()
        with self._write_pool.connection() as conn:
            conn.execute(
                "INSERT INTO projects (project_id, org_id, display_name) VALUES (%s,%s,%s) "
                "ON CONFLICT (project_id) DO UPDATE SET org_id=EXCLUDED.org_id, "
                "display_name=EXCLUDED.display_name",
                (project_id, org_id, name),
            )

    def grant_project(self, project_id: str, principal: str, principal_kind: str, role: str = "member") -> None:
        """授权 principal(user_id 或 team_id)对 project 的 role。principal_kind ∈ {user, team}。"""
        self.ensure_schema()
        with self._write_pool.connection() as conn:
            conn.execute(
                "INSERT INTO project_access (project_id, principal, principal_kind, role) "
                "VALUES (%s,%s,%s,%s) "
                "ON CONFLICT (project_id, principal) DO UPDATE SET "
                "principal_kind=EXCLUDED.principal_kind, role=EXCLUDED.role",
                (project_id, principal, principal_kind, role),
            )

    # ---- 读路径(副本,若配置)----

    def fetch_membership(self, org_id: str, user_id: str, project_id: str | None = None) -> Membership:
        """组装 core.rbac.Membership:该 (org, user) 的 org_role + 本 org 内 teams + 当前 project_role。

        - org_role:    org_members 里该 (org_id, user_id) 的 org_role(无 → None,非成员)。
        - teams:       team_members ∩ (本 org 的 teams) ∩ user_id → [(team_id, role), ...]。
        - project_role: project_access 对该 user(principal_kind='user')或其所属 team
                        (principal_kind='team')的 role;多来源取最高(admin>member>viewer)。
        """
        self.ensure_schema()
        with self._read_pool.connection() as conn:
            row = conn.execute(
                "SELECT org_role FROM org_members WHERE org_id=%s AND user_id=%s",
                (org_id, user_id),
            ).fetchone()
            org_role = row[0] if row else None

            team_rows = conn.execute(
                "SELECT tm.team_id, tm.role FROM team_members tm "
                "JOIN teams t ON t.team_id = tm.team_id "
                "WHERE t.org_id=%s AND tm.user_id=%s "
                "ORDER BY tm.team_id",
                (org_id, user_id),
            ).fetchall()
            teams = tuple((r[0], r[1]) for r in team_rows)

            project_role = None
            if project_id:
                team_ids = [t[0] for t in teams]
                # 该 project 必须属于本 org(跨 org 不可见);principal 命中 user 或其 team。
                acc_rows = conn.execute(
                    "SELECT pa.role FROM project_access pa "
                    "JOIN projects p ON p.project_id = pa.project_id "
                    "WHERE pa.project_id=%s AND p.org_id=%s AND ("
                    "  (pa.principal_kind='user' AND pa.principal=%s)"
                    "  OR (pa.principal_kind='team' AND pa.principal = ANY(%s))"
                    ")",
                    (project_id, org_id, user_id, team_ids),
                ).fetchall()
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
