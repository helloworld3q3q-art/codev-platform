"""SQLAlchemy Core 表定义 —— RBAC/账户 7 表的单一真值源。

收口原 agent/rbac_store_pg.py 的 `_SCHEMA` 大字符串(CREATE/ALTER)。列定义逐字对齐:
  orgs / users / org_members / teams / team_members / projects / project_access。

只用 Core(MetaData + Table),不引入 ORM(无 declarative Base / Session / relationship)。
列类型用中性 `Text` / `String` / `DateTime(timezone=True)`,跨方言(PostgreSQL 生产 / SQLite 测试)
均可 `metadata.create_all(engine)` 落地。PG 生产 schema 由 Alembic 管(baseline 等价本定义);
SQLite 仅供单测 exercise 改写后的 store 逻辑。

默认值 / NOT NULL / PK / FK / 索引与 _SCHEMA 一一对应:
  - status TEXT NOT NULL DEFAULT 'ACTIVE'           → server_default=text("'ACTIVE'")
  - created_at TIMESTAMPTZ NOT NULL DEFAULT now()   → server_default=func.now()
  - 3 个索引:ix_org_members_user / ix_team_members_user / ix_teams_org
"""
from __future__ import annotations

from sqlalchemy import (
    Column,
    DateTime,
    Float,
    ForeignKey,
    Index,
    MetaData,
    PrimaryKeyConstraint,
    Table,
    Text,
    func,
    text,
)

metadata = MetaData()

_STATUS_DEFAULT = text("'ACTIVE'")


orgs = Table(
    "orgs",
    metadata,
    Column("org_id", Text, primary_key=True),
    Column("name", Text),
    # ACTIVE | DISABLED (web Orgs 波)
    Column("status", Text, nullable=False, server_default=_STATUS_DEFAULT),
    Column("created_at", DateTime(timezone=True), nullable=False, server_default=func.now()),
)

users = Table(
    "users",
    metadata,
    Column("user_id", Text, primary_key=True),
    Column("display_name", Text),
    # web 账户列 (Auth/Users 波): 密码 hash / 状态 / 邮箱。RBAC 读不依赖, 仅 web CRUD 用。
    Column("password_hash", Text),
    # ACTIVE | DISABLED
    Column("status", Text, nullable=False, server_default=_STATUS_DEFAULT),
    Column("email", Text),
    Column("created_at", DateTime(timezone=True), nullable=False, server_default=func.now()),
)

# user <-> org 多对多(一人多 org / 一 org 多人), org_role 为 org 级角色。
org_members = Table(
    "org_members",
    metadata,
    Column("org_id", Text, ForeignKey("orgs.org_id"), nullable=False),
    Column("user_id", Text, ForeignKey("users.user_id"), nullable=False),
    # admin | member | viewer
    Column("org_role", Text, nullable=False, server_default=text("'member'")),
    PrimaryKeyConstraint("org_id", "user_id"),
)

teams = Table(
    "teams",
    metadata,
    Column("team_id", Text, primary_key=True),
    Column("org_id", Text, ForeignKey("orgs.org_id"), nullable=False),
    Column("name", Text),
)

team_members = Table(
    "team_members",
    metadata,
    Column("team_id", Text, ForeignKey("teams.team_id"), nullable=False),
    Column("user_id", Text, ForeignKey("users.user_id"), nullable=False),
    # admin | member | viewer
    Column("role", Text, nullable=False, server_default=text("'member'")),
    PrimaryKeyConstraint("team_id", "user_id"),
)

projects = Table(
    "projects",
    metadata,
    Column("project_id", Text, primary_key=True),
    Column("org_id", Text, ForeignKey("orgs.org_id"), nullable=False),
    Column("display_name", Text),
)

# principal = user_id 或 team_id; principal_kind in {user, team}。
project_access = Table(
    "project_access",
    metadata,
    Column("project_id", Text, ForeignKey("projects.project_id"), nullable=False),
    Column("principal", Text, nullable=False),
    # user | team
    Column("principal_kind", Text, nullable=False),
    Column("role", Text, nullable=False, server_default=text("'member'")),
    PrimaryKeyConstraint("project_id", "principal"),
)

# 长任务 job (plan §十二)。created_at/updated_at = epoch float (对齐 domain.Job 的 time.time(),
# 非 TIMESTAMPTZ —— job 时间戳全程 float, repo 不做 datetime 转换)。codex P2: job 历史持久化,
# 重启/多 worker 下查得到 job 状态 (真重活已在独立 FileSpoolQueue/worker, 此表是状态镜像)。
jobs = Table(
    "jobs",
    metadata,
    Column("job_id", Text, primary_key=True),
    Column("project_id", Text, nullable=False),
    Column("job_type", Text, nullable=False),
    Column("status", Text, nullable=False, server_default=text("'Pending'")),
    Column("created_at", Float, nullable=False),
    Column("updated_at", Float, nullable=False),
    Column("error", Text),
)

Index("ix_org_members_user", org_members.c.user_id)
Index("ix_team_members_user", team_members.c.user_id)
Index("ix_teams_org", teams.c.org_id)
Index("ix_jobs_project", jobs.c.project_id)


__all__ = [
    "metadata",
    "orgs",
    "users",
    "org_members",
    "teams",
    "team_members",
    "projects",
    "project_access",
    "jobs",
]
