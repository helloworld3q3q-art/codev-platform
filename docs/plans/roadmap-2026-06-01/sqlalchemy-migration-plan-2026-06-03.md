# SQLAlchemy Core + Alembic 迁移 plan(2026-06-03)

> 来源:`docs/audits/deep-audit-2026-06-03-review.md` 第三波技术债 #6(原审计 P2#6,从 bug 清单拆出独立 plan)。
> 性质:大重构,**只迁 PG 业务写库**,本 plan 仅设计,不改任何 `.py` 源码。

## 一、范围界定

### 迁移目标(PG 业务库 `codev_platform_memory`)

| 文件 | 现状 | 表 |
|---|---|---|
| `codev_platform/web/repositories/account_store_pg.py` | psycopg 裸 SQL,`PgOrgStore` / `PgUserStore` / `PgMemberStore` | orgs / users / org_members |
| `codev_platform/agent/rbac_store_pg.py` | `_SCHEMA` 大字符串(CREATE/ALTER)+ 7 表读写 | orgs / users / org_members / teams / team_members / projects / project_access |

两文件**同库同表**(orgs/users/org_members 单一真值源):`account_store_pg._PgBase._ensure()` 直接复用 `rbac_store_pg._SCHEMA`。这是迁移的关键耦合点 —— `_SCHEMA` 一动,两边都受影响。

### 明确不迁(保留 raw SQL)

- 图谱 SQLite 只读查询:`codegraph_client` / `cross_link_client`(本 plan 范围外,不 ORM 化,不 Core 化)。
- `memory_store_pg` / `session` PG 存储:若本轮不在 audit #6 范围,**不顺手带**,留独立后续(它们用相同范式,可复用本 plan 的 engine/tables 接缝)。

### 约束

- **只用 SQLAlchemy Core**(`Table` / `select()` / `insert()` / `update()` / `delete()`),不引入 ORM(无 declarative Base / Session / relationship)。
- repository 接口(`get`/`list`/`upsert`/`fetch_membership`/`add_*`/`grant_*` 等)**签名与返回类型不变**,上层 service(`org_service` / `auth_service` / `deps.get_rbac_store`)无感。
- 平台 venv 无 psycopg 的**优雅回退**契约不破(`bind_account_stores` 缺 psycopg/dsn 回退内存;构造期 `open=False` 不连库)。

## 二、分阶段(每阶段 = 一个可独立 commit 的边界)

### 阶段 ①  新增 `web/db/{engine,tables}.py` 集中表定义(commit 边界:纯新增,无调用方改动)

- 新建 `codev_platform/web/db/__init__.py`。
- `web/db/tables.py`:用 `sqlalchemy.MetaData` + `Table(...)` 定义 7 表(orgs/users/org_members/teams/team_members/projects/project_access),列定义**逐字对齐** `rbac_store_pg._SCHEMA`(含默认值 `status DEFAULT 'ACTIVE'`、`created_at TIMESTAMPTZ DEFAULT now()`、PK、FK、3 个索引)。
- `web/db/engine.py`:lazy `create_engine(dsn)`(`pool_pre_ping=True`),与现有 `ConnectionPool open=False` 等价的"构造不连库"语义;保留 `read_dsn` 读写分离接缝(对应 `RbacStore._read_pool`)。SQLAlchemy 仍走 psycopg driver(`postgresql+psycopg://`),与现有依赖一致。
- 本阶段**不**接线到两个 store,仅落地定义 + 一个 `metadata.create_all(engine)` 等价 schema 出口。
- 验证:`pytest tests/` 不回归(无调用方,纯加文件);新增 `tests/test_web_db_tables.py` 断言 7 表名 + 列名集合 ≡ `_SCHEMA` 解析结果。

### 阶段 ②  Alembic 初始化 + baseline migration(commit 边界:工具链 + baseline,不改 store)

- `alembic init`(放 `codev_platform/web/db/alembic/` 或仓根 `alembic/`,二选一在本阶段拍定),`env.py` 的 `target_metadata = web.db.tables.metadata`,dsn 从 `core/config` 取(env > config)。
- 生成 **baseline migration**:`alembic revision --autogenerate`,内容须与现有 `_SCHEMA` 完全等价(含幂等 `ADD COLUMN IF NOT EXISTS` 三列已经历过的 orgs/users 补列 —— baseline 直接含全列,旧库走 `alembic stamp` 标记 baseline 而非重建)。
- **存量库兼容**:已有真机库已被 `_SCHEMA` 建好 → 提供 `alembic stamp <baseline>` 流程文档,标记为"已在 baseline",不重复建表。
- 验证:`alembic upgrade head` 在空 PG 建出的 schema = `_SCHEMA` 建出的 schema(列/约束/索引 diff 为空);`alembic check`(autogenerate 无 pending diff)。

### 阶段 ③  store 内部 SQL 字符串 → `select()/insert()/update()/delete()`(commit 边界:可拆两 commit,先 rbac 后 account,或反之)

- `rbac_store_pg.py`:`add_org`/`add_user`/`add_org_member`/`add_team`/`add_team_member`/`upsert_project`/`grant_project` 改 `insert(t).on_conflict_do_update(...)`(`sqlalchemy.dialects.postgresql.insert`);`fetch_membership` 三段查询改 `select(...).join(...).where(...)`(含 `principal = ANY(team_ids)` → `.in_(team_ids)`)。`_SCHEMA` 大字符串**删除**,`ensure_schema` 改调 `web.db.tables.metadata` 出口(或 Alembic 已建则 no-op)。
- `account_store_pg.py`:`_PgBase._ensure()` 不再 import `_SCHEMA`,改用 `web/db` 接缝;`PgOrgStore`/`PgUserStore`/`PgMemberStore` 的 get/list/exists/upsert/remove 全部改 Core 表达式。`PgUserStore.get` 的两段查询(users + org_members LIMIT 1)、`list(org_id)` 的 JOIN 保持等价语义。
- **接口零变更**:返回 `Org`/`User`/`OrgMember` domain 对象不变;`_highest_role` / `Membership` 组装逻辑不动。
- 拆 commit 建议:③a 先迁只读路径(get/list/fetch_membership),③b 再迁写路径(upsert/insert),各自可单独验证回滚。
- 验证:`pytest tests/` 不回归;真机 PG 上 `org_service` CRUD + `fetch_membership` 行为 diff 为空。

### 阶段 ④  测试 fixture(PG schema 一变测试即失败)(commit 边界:测试增强)

- 现状:`tests/test_web_account_persistence.py` / `test_account_store_pg_failfast.py` 走内存路径 + 优雅回退,**不连真 PG**(`第 1-5 行` 注释明示)。
- 新增 schema-parity 守护(不依赖真 PG,纯静态/内存级):
  - `tests/test_web_db_schema_parity.py`:断言 `web.db.tables.metadata` 的表/列/约束 ≡ Alembic head migration 生成的 schema(用 SQLAlchemy `compare_metadata` 或 sqlite 内存 engine `create_all` 后反射比对)。schema 任一处漂移(改列名/删表/改默认值)→ 测试红。
  - 可选:若 CI 有 PG,加 `@pytest.mark.pg` 真连冒烟(`create_all` → CRUD → `fetch_membership`),无 PG 自动 skip,不破 702 基线。
- 验证:故意改 `tables.py` 一个列名 → parity 测试必失败(反向验证守护生效)。

### 阶段 ⑤  回滚 / 兼容策略 + 风险点(commit 边界:文档 + 清理,不再有源码逻辑改动)

- **回滚**:每阶段独立 commit,阶段 ③ 出问题可单 revert(①②保留,store 退回 raw SQL 不影响已建 schema)。Alembic `downgrade` 仅用于本地/测试,生产**禁 downgrade 删表**(对齐已发布 Flyway 不回改的同源纪律)。
- **存量库**:首次上线走 `alembic stamp baseline`,绝不在真机 `upgrade` 重建已有表。
- **向前**:后续加列只 forward migration(`alembic revision`),不改 baseline。

## 三、风险点

| 风险 | 缓解 |
|---|---|
| `_SCHEMA` 被两文件复用,迁移漏一边 → schema 双源漂移 | 阶段 ① 一次性把 7 表收口到 `web/db/tables.py` 单一真值源,③ 同 PR 改两文件 |
| `ON CONFLICT DO UPDATE` Core 写法(postgresql dialect 专属) | 用 `dialects.postgresql.insert(...).on_conflict_do_update`,parity 测试覆盖 upsert 路径 |
| `ADD COLUMN IF NOT EXISTS` 幂等补列语义在 Alembic 丢失 | baseline 含全列;旧库 `stamp` 而非重跑,文档显式写流程 |
| SQLAlchemy 进 platform venv(无 psycopg 优雅回退) | engine lazy 化,`bind_account_stores` 回退逻辑不动;新增依赖只进 `requirements-runtime.txt` PG extra |
| 读写分离 `read_dsn` 接缝遗漏 | `engine.py` 显式保留双 engine,对齐 `RbacStore._read_pool` |

## 四、702 测试不回归保障

- 阶段 ①②④ 纯加文件/测试,不碰调用方 → 现有 702 用例零影响。
- 阶段 ③ 改 store 内部实现但**接口签名不变**,现有 `test_web_account_persistence` / `test_account_store_pg_failfast` / `test_rbac_wire` / `test_org_cli` 走内存路径不受影响;PG 路径本就不在单测连库,行为契约由阶段 ④ parity 测试新增守护。
- 每阶段独立 commit + `pytest tests/` 绿灯门;新增测试只增不减,基线 ≥702。
