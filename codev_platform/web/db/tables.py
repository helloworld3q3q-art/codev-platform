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
    Integer,
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

# 登录会话 (plan §十五 Auth)。只存 token 的 sha256 hash (明文 token 不落库, 同 sessions.py)。
# access/refresh 过期时间走 epoch float (对齐 sessions.py 的 time.time())。backend-deep P1-3:
# 登录态持久化 —— 重启不丢 + 多 worker 共享 + 禁用用户 revoke 跨进程生效。
sessions = Table(
    "sessions",
    metadata,
    Column("session_id", Text, primary_key=True),
    Column("username", Text, nullable=False),
    Column("org_id", Text, nullable=False),
    Column("access_hash", Text, nullable=False),
    Column("refresh_hash", Text, nullable=False),
    Column("access_expires_at", Float, nullable=False),
    Column("refresh_expires_at", Float, nullable=False),
)

# 多机共享 reindex 队列 (PgJobQueue, alembic 0004)。per (project_id,kind) 合并(复合主键);
# lease(claimed_by/lease_expires_at)+ claim_token 做跨机原子认领 + 防接管误删。must 进 metadata,
# 否则 alembic autogenerate 会把它当"DB 有 metadata 无" → 提议 DROP 生产队列表(审计 P1)。
reindex_jobs = Table(
    "reindex_jobs",
    metadata,
    Column("project_id", Text, nullable=False),
    Column("kind", Text, nullable=False),
    Column("enqueued_at", Float, nullable=False),
    Column("status", Text, nullable=False, server_default=text("'pending'")),
    Column("claimed_by", Text),
    Column("lease_expires_at", Float),
    Column("claim_token", Text),
    PrimaryKeyConstraint("project_id", "kind"),
)

# 统一图谱 PG 后端 5 表(PgGraphStore, 2026-06-11)。与 graph/store.py sqlite schema 同构;
# 唯一差异: ingest_meta 加 project_id 入 PK(共享 PG 库按 project_id 隔离, sqlite per-file 无需)。
# 真值源仍是 PgGraphStore._SCHEMA_DDL(运行期 CREATE IF NOT EXISTS 自足); 这里登记进 metadata
# 是为 alembic autogenerate 不误 DROP(同 reindex_jobs 教训)+ parity 守护 + 受管迁移。
graph_nodes = Table(
    "graph_nodes",
    metadata,
    Column("id", Text, nullable=False),
    Column("plugin", Text, nullable=False),
    Column("kind", Text, nullable=False),
    Column("name", Text, nullable=False),
    Column("project_id", Text, nullable=False),
    Column("file", Text),
    Column("line", Integer),
    Column("language", Text),
    Column("meta_json", Text),
    PrimaryKeyConstraint("id", "plugin"),
)

graph_edges = Table(
    "graph_edges",
    metadata,
    Column("project_id", Text, nullable=False),
    Column("plugin", Text, nullable=False),
    Column("source", Text, nullable=False),
    Column("target", Text, nullable=False),
    Column("kind", Text, nullable=False),
    Column("confidence", Float, server_default=text("1.0")),
    Column("meta_json", Text),
    PrimaryKeyConstraint("project_id", "plugin", "source", "target", "kind"),
)

graph_evidences = Table(
    "graph_evidences",
    metadata,
    Column("project_id", Text, nullable=False),
    Column("plugin", Text, nullable=False),
    Column("seq", Integer, nullable=False),
    Column("source", Text, nullable=False),
    Column("detail", Text, nullable=False),
    Column("file", Text),
    Column("line", Integer),
    Column("confidence", Float, server_default=text("1.0")),
    Column("meta_json", Text),
    PrimaryKeyConstraint("project_id", "plugin", "seq"),
)

graph_findings = Table(
    "graph_findings",
    metadata,
    Column("project_id", Text, nullable=False),
    Column("plugin", Text, nullable=False),
    Column("seq", Integer, nullable=False),
    Column("kind", Text, nullable=False),
    Column("severity", Text, nullable=False),
    Column("title", Text, nullable=False),
    Column("detail", Text),
    Column("node_ids_json", Text),
    Column("evidence_ids_json", Text),
    Column("meta_json", Text),
    PrimaryKeyConstraint("project_id", "plugin", "seq"),
)

graph_ingest_meta = Table(
    "graph_ingest_meta",
    metadata,
    Column("project_id", Text, nullable=False),
    Column("plugin", Text, nullable=False),
    Column("plugin_version", Text),
    Column("node_count", Integer),
    Column("edge_count", Integer),
    Column("evidence_count", Integer),
    Column("finding_count", Integer),
    Column("ingested_at", Text),
    PrimaryKeyConstraint("project_id", "plugin"),
)

Index("ix_org_members_user", org_members.c.user_id)
Index("ix_team_members_user", team_members.c.user_id)
Index("ix_teams_org", teams.c.org_id)
Index("ix_jobs_project", jobs.c.project_id)
Index("ix_sessions_access", sessions.c.access_hash)
Index("ix_sessions_refresh", sessions.c.refresh_hash)
Index("ix_sessions_username", sessions.c.username)
Index("ix_reindex_jobs_claim", reindex_jobs.c.status, reindex_jobs.c.enqueued_at)
Index("ix_graph_nodes_kind", graph_nodes.c.project_id, graph_nodes.c.kind)
Index("ix_graph_nodes_name", graph_nodes.c.project_id, graph_nodes.c.name)
Index("ix_graph_edges_source", graph_edges.c.project_id, graph_edges.c.source)
Index("ix_graph_edges_target", graph_edges.c.project_id, graph_edges.c.target)


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
    "sessions",
    "reindex_jobs",
    "graph_nodes",
    "graph_edges",
    "graph_evidences",
    "graph_findings",
    "graph_ingest_meta",
]
