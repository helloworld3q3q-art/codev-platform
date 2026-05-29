# Memory 基建权限模型 Plan(2026-05-29)

> **定位**:agent memory 基础设施的**身份 + 权限 + 多作用域**底座。对标大厂 "AI Agent Memory 基础设施"(长期/会话/任务记忆统一平台 + 写入/索引/检索/更新/压缩/遗忘 + 召回排序/冲突消解/摘要融合/生命周期)。
>
> **战略前提(2026-05-29 用户拍板)**:业务与基建双轮驱动;数据积累期业务在「等样本」,空窗投基建,两线并行不抢资源。见 memory `platform-strategy-business-plus-infra`。
>
> **关联**:`agent-2026-05-28.md`(P2/P3 承载 memory 演进)/ `team-deploy-2026-05-27`(多人部署雏形)/ 现有 `core/project_id.py` + `core/paths.py`(project_id 隔离地基)。

---

## 一、设计原则

### 1.1 两个正交轴(最关键,防把"人/团队"和"数据归谁"混成一棵树)

**Org 是根**:user / team / project / memory 全挂在某个 org 下,跨 org 完全不可见(最外硬隔离)。两轴都在 org 之内展开:

```
            ┌─ Org 组织(租户根,最外隔离边界)──────────────────┐
轴 A:身份  │  Users(成员,org_members 多对多)                  │
(认证)     │   └ Teams(team_members 多对多)                     │
            │  + Service/Agent 机器身份                          │
轴 B:作用域│  org memory > team memory > project memory > personal│
(授权资源) └────────────────────────────────────────────────────┘
请求上下文 = (org_id, user_id, project_id)   ← 三元组,org 最外
```

**为什么正交**:org 内,一个人属于多团队、做多项目 → 不是树,是图。Person ∈ 多 Team,Team 拥有多 Project。认证认到**人**(在某 org 内);数据按**作用域** namespace 分;两轴用 `role(person, scope)` 桥接。**org 之上跨租户不可见**,team/project/personal 是 org 内的细分。

### 1.2 记忆的特殊性

**越共享越有用,越个人越敏感** → 权限模型必须同时支持两端:个人记忆强隔离 + 共享记忆跨人聚合。这是 memory 权限比普通数据难的根本。

### 1.3 不跳级

阶段 1-3 单人即可做且立刻有用(agent 现在就缺分层记忆);ACL/RBAC(4-5)等"真有第二个人"才动,杜绝无痛点造基建。

---

## 二、记忆作用域阶梯

| 作用域 | 读 | 写 | recall(被 agent 用) | 例子 | 现状 |
|---|---|---|---|---|---|
| **org** | 所有人 | admin | 全员会话 | 合规红线 / commit 不留 AI 痕迹 | 🟡 散在 rules |
| **team** | 团队成员 | team lead | 团队成员会话 | 团队工作流 / 联调约定 | ❌ |
| **project** | 项目成员 | 项目成员 | 项目会话 | 项目枚举/字段约定 | ✅ docs/memory/ |
| **personal** | 仅本人 | 仅本人 | 仅本人会话 | 个人偏好/风格 | 🟡 混在全局 |

`recall` 是 memory 独有的第三种动作:允许 agent 在我会话里用某条记忆 ≠ 允许别人直接读它。

---

## 三、技术方案

### 3.1 身份传播(沿用现有 project_id 模式)

现有:`project_id` 经 `env > .claude/project.json > X-Project-Id header`(`project_id.resolve_local` / `resolve_from_request`)。

新增**对称**的 user 身份:

```
user_id: X-User-Id header  (P1 先采集不拦截)
         > env CODEV_USER_ID
         > config.agent.user_id
         > "local"(单人默认)
后续(P5): Bearer token -> user_id, 防伪
```

新增 `core/identity.py`:`resolve_user(headers) -> str`,与 `project_id` 解析对称。

**请求上下文是三元组 `(org_id, user_id, project_id)`**(org 是根,见 §3.4):
```
org_id:     X-Org-Id header(必须请求带,因一人多 org 无法推导单一)> "default"(单 org 期)
user_id:    X-User-Id header > env > config > "local"
project_id: X-Project-Id header > body > cwd 回退
校验(M5):(org_id, user_id) ∈ org_members 且 user 对 project(同 org)有 access,否则 403。
```
注:user↔org 多对多(§3.4),所以"当前 org"是请求级选择(widget 需 org 选择器),不能从 user 推。

### 3.1b 认证层(身份可信)—— M6,登录 / API key

`X-User-Id` 明文头只够"自报身份"(防误操作,挡不了冒充)。**真权限要求身份可信** → 加认证层,在授权(§3.4 校验)之前:

```
请求 → [认证层] 验凭证 → 可信 (org_id, user_id) → [授权层 §3.4] → 路由
```

两类凭证,都解析成 `(org_id, user_id)`:
| 凭证 | 谁用 | 载体 |
|---|---|---|
| **登录(SSO/账号)** | 人(widget/web)| 登录 → JWT,每请求 `Authorization: Bearer` |
| **API key/token** | 程序/CLI/headless | config 持有方的 key → 映射到一个 user/service 身份 |

- 账号 + key 存 **PG `users` 表 / key 表**(`codev_platform_memory` 库),**不进 config**(config 只放"本机调用方自己的那把 key")。
- **身份可信度 = 权限可信度**:自报身份(明文头)只够团队内"软隔离";对外/敏感必须登录 token 验签。
- **widget 怎么知道是谁**:现在写死 `local`;团队期 config/设置填或 API key;对外期 widget 登录拿 token。

**agent project 绑定 = memory 权限同一套**:agent 选项目(§agent plan P2)与 memory 召回都走 `allowed(org_id, user_id, project_id, action)`,共用本节认证 + §3.4 授权,不各搞一套。

### 3.2 记忆数据模型(结构化存储 + 向量召回 双写)

记忆 = **结构化记录(PostgreSQL,真值 + 治理元数据)** + **向量(chroma,召回)**。

> **2026-05-29 定:直接用 PostgreSQL,不经 SQLite 过渡**。理由:memory 基建是长期资产,SQLite→PG 迁移是确定要发生的返工,直接落 PG 省掉它。**但必须是平台独立 database,严禁与任何业务库同库**(见 §3.2b 红线)。

```sql
-- database: codev_platform_memory(平台独立库,见 §3.2b)
CREATE TABLE memory_entries (
  id            UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  scope         TEXT NOT NULL,           -- org | team | project | personal
  scope_ref     TEXT NOT NULL,           -- org=固定'org' / team=team_id / project=project_id / personal=user_id
  owner_user_id TEXT NOT NULL,           -- 写入者
  topic_key     TEXT,                    -- 冲突检测键(同 key 跨作用域 = 潜在冲突)
  kind          TEXT,                    -- preference | fact | task | session ...
  content       TEXT NOT NULL,
  is_redline    BOOLEAN NOT NULL DEFAULT FALSE,  -- org 硬约束不可被低层覆盖
  status        TEXT NOT NULL DEFAULT 'active',  -- active | superseded | archived | forgotten
  supersedes    UUID REFERENCES memory_entries(id),  -- 被本条取代的旧 id(更新留痕)
  ttl_at        TIMESTAMPTZ,             -- 遗忘:到期自动 archive(NULL=永久)
  created_at    TIMESTAMPTZ NOT NULL DEFAULT now(),
  updated_at    TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX ix_mem_scope ON memory_entries(scope, scope_ref, status);
CREATE INDEX ix_mem_topic ON memory_entries(topic_key, status);
```

向量侧:chroma 每条 entry 带 metadata `{scope, scope_ref, owner_user_id, topic_key, is_redline}`,召回时 where-filter。(向量暂留 chroma —— 暖 daemon 现成;未来量到百万级可评估 pgvector 统一进 PG,但**不预先做**。)

### 3.2b 存储归属红线(不可退)

**memory 数据严禁与任何业务库同 database。** 这条是平台/业务分离的底线 —— 本仓整个 session 就在做"平台从业务仓翻正、自包含",memory 进业务库 = 翻正白做 + 多租户隔离崩 + 故障域耦合。

| 项 | 规定 |
|---|---|
| database | 平台独立库 `codev_platform_memory`,schema/迁移归平台 |
| 物理位置 | 可与 openclaw 共一个 PG **server**(省运维),但**必须独立 database**;按"自包含"精神,独立实例/容器更优 |
| 连接串 | 走平台 config `config.memory.pg_dsn`,**禁止**复用任何业务库 DSN |
| 库内隔离 | 仍按 project_id 多租户隔离(openclaw / widget / 未来项目的 memory 在平台库内分租户,不混) |
| 复用边界 | 复用的是"PG 这套技术栈/实例运维能力",**不是** openclaw 的那个 business database |

### 3.2c PG 部署形态(2026-05-29 修订:用现有 PG server + 独立 database)

> **修订原因**:开发机已有原生 PostgreSQL 17 在跑(localhost:5432,服务 `postgresql-x64-17`)。无需再装 Docker —— 直接在现有 server 里建**独立 database** 即可,这是本节"共 server + 独立 database"的可接受形态(§3.2b 红线只要求 database 独立,不要求 server 独立)。原 docker 方案(`docker-compose.yml`)保留作**备选**(无原生 PG 的机器 / 团队服务器仍可用)。

```
现有 PG server (localhost:5432)
├── (openclaw 等业务库)            ← 不碰(红线)
└── codev_platform_memory          ← 平台独立库,OWNER = codev_platform 角色
```

建库(用户执行,需 postgres 超级用户;codev-platform 代码无密码不自动建):
```sql
CREATE ROLE codev_platform LOGIN PASSWORD '<设一个>';
CREATE DATABASE codev_platform_memory OWNER codev_platform;
```

| 阶段 | 落地 |
|---|---|
| 单人开发(现在) | 现有 PG 5432 建独立库 `codev_platform_memory` |
| 团队 / 无原生 PG 的机器 | 备选 `docker-compose.yml`(postgres:16,端口 5433,数据卷 data/postgres/)|
| 组织级 | 独立实例 / 托管 PG(RDS 等),改 `config.memory.pg_dsn` 一行 |

**红线不变**:独立 **database**(非业务库),连接串走 `config.memory.pg_dsn`,密码走 env / config(不进 git)。共 server 省运维 + 红线满足,二者兼得。

**config**:`config.memory.pg_dsn = "postgresql://codev_platform:***@localhost:5432/codev_platform_memory"`(密码走 env,不进 git;复用 §3.2 secrets 红线)。

**A(会话持久化 M2)前置**:DB 待用户建(上面 SQL);M2 代码动工时建库即可,现在不强制(建了也空着到 M2)。

### 3.3 作用域 → collection 映射

个人/团队/org 跨 project,故 memory 不复用 `<project_id>__platform_docs`,独立命名:

```
org__memory                 org 级
team_<team_id>__memory      团队级
<project_id>__memory        项目级(沿用 project_id)
user_<user_id>__memory      个人级
```

`paths.py` 加 `memory_collection_name(scope, scope_ref)`。复用现有多租户 daemon + 暖模型,不新起服务。

### 3.4 权限 = 身份 × 作用域 × 动作(scoped RBAC)

```
allowed(person, scope_ref, action) = role(person, scope_ref) 覆盖 action
role ∈ {admin, member, viewer}     action ∈ {read, write, recall, admin}
```

权限表(M5/M6,单人前用默认全通过;同在 `codev_platform_memory` 库):

**org 是租户根**:user / team / project 全挂 org,每表带 `org_id`;跨 org 完全不可见(最外硬隔离边界)。

```sql
CREATE TABLE orgs         (org_id TEXT PRIMARY KEY, name TEXT, created_at TIMESTAMPTZ DEFAULT now());

CREATE TABLE users        (user_id TEXT PRIMARY KEY, display_name TEXT, created_at TIMESTAMPTZ DEFAULT now());
-- user↔org 多对多(2026-05-29 定):一个人可属多个 org,一个 org 可有多个人。
-- user 独立实体,通过 org_members 加入 org(像 GitHub/Slack 一个账号多 workspace)。
CREATE TABLE org_members  (org_id TEXT REFERENCES orgs, user_id TEXT REFERENCES users,
                           org_role TEXT DEFAULT 'member',  -- admin | member | viewer(org 级)
                           PRIMARY KEY (org_id, user_id));

CREATE TABLE teams        (team_id TEXT PRIMARY KEY, org_id TEXT NOT NULL REFERENCES orgs, name TEXT);
CREATE TABLE team_members (team_id TEXT REFERENCES teams, user_id TEXT REFERENCES users,
                           role TEXT, PRIMARY KEY (team_id, user_id));   -- 多对多 + 角色

CREATE TABLE projects     (project_id TEXT PRIMARY KEY, org_id TEXT NOT NULL REFERENCES orgs,
                           display_name TEXT);                          -- 项目挂 org
CREATE TABLE project_access(project_id TEXT REFERENCES projects, principal TEXT, principal_kind TEXT,
                            role TEXT, PRIMARY KEY (project_id, principal)); -- principal = user_id 或 team_id

-- memory_entries(§3.2)也加 org_id 列:作用域链最外是 org,所有查询带 WHERE org_id=?
```

**user↔org 多对多(已定)**:一个人可属多个组织、一个组织可有多个人 → `org_members` 多对多表(不在 users 表放 org_id 列)。

**关键推论 —— "当前 org" 也是请求级**:既然一人多 org,那"这次请求在哪个 org 下"必须每请求带 `X-Org-Id`(像 project 一样),不能从 user 推导单一 org。校验:`(org_id, user_id)` 必须在 org_members 里,且该 user 对 project 在此 org 内有 access。widget 需要 **org 选择器**(像项目下拉),或登录后选 workspace。

### 3.5 召回 × 冲突消解 × 权限(三位一体)

召回不是单 collection 查询,是**权限过滤的跨作用域合并**:

```
def recall(user_id, project_id, query):
    # 1. 权限:算该 user 可 recall 的 (scope, scope_ref) 集合
    scopes = visible_scopes(user_id, project_id)        # org + 他的 teams + 当前 project + 他自己
    # 2. 跨 collection 召回 + RRF 融合 + rerank(复用现有检索栈)
    hits = multi_collection_recall(scopes, query)
    # 3. 冲突消解:按 topic_key 分组,同组按作用域优先级取胜
    return resolve_conflicts(hits)

# 冲突优先级(2026-05-29 定:可配 policy,不写死)
#   ① is_redline(org 硬约束)永远最高 —— 不可配(合规底线)
#   ② 非红线部分按 policy 排(org / 个人可选,综合两种治理倾向):
#        "personal_first"(默认): personal > project > team > org   (我的偏好盖团队默认,除非硬红线)
#        "org_first":             org > project > team > personal   (组织规则盖个人,合规强)
def resolve_conflicts(entries, policy="personal_first"):
    # 红线最高 + policy 控非红线;同 topic_key 取最高优先级胜出,其余视为被压。
```

**policy 是数据,分 org / 个人两级配(不是代码写死)**:
| 配置 | 存哪 | 谁改 | 阶段 |
|---|---|---|---|
| 红线最高 | 代码常量(不可配) | — | 现在 |
| org 默认 policy | PG `orgs.conflict_policy` | org admin | M5 |
| 是否允许个人覆盖 | PG `orgs.allow_personal_override` | org admin | M5 |
| 个人覆盖选择 | org_members / personal memory | 个人(org 允许时) | M5 |

**现在(纯逻辑,已落地)**:`resolve_conflicts(entries, policy)` 参数化 policy,A/B 都支持,默认 personal_first,单测覆盖。
**M5(等 DB+多人)**:从 `orgs.conflict_policy` 读 org 策略 + 个人覆盖判定 → 传 policy。接缝已留,加 DB 读取即可。

**关键**:冲突消解逻辑(纯)与 policy 来源(DB)分离 —— 逻辑现在做+可测,policy 来源 M5 接。

### 3.6 生命周期(JD 的 写/更新/压缩/遗忘)

| 动作 | 实现 |
|---|---|
| **write** | append entry(status=active)+ 向量入库 |
| **update** | 新 entry `supersedes` 旧 id,旧 status→superseded(留痕,不物理删) |
| **compress / 摘要融合** | 周期 job:同 scope+topic 多条 → LLM 融合成 1 条 summary,原条 archive |
| **forget** | TTL 到期自动 archive;显式 delete→status=forgotten;recall 只查 active |

### 3.7 HTTP API(挂在 agent 服务,沿用 routes/ 拆分)

```
POST   /memory               写记忆   body{scope,scope_ref,content,kind,topic_key,is_redline,ttl}
GET    /memory/recall        召回(权限过滤)  ?query=&project_id=  (user 来自 header)
PATCH  /memory/{id}          更新(supersede)
DELETE /memory/{id}          遗忘
GET    /memory/scopes        当前 user 在当前 org 可见作用域(调试 + 前端)
身份:X-Org-Id + X-User-Id + X-Project-Id header(三元组,与 chat 一致)
```

agent loop 召回改走 `/memory/recall` 的权限过滤版,替代当前"全量塞 context"。

### 3.8 服务端技术栈

**Python / FastAPI,memory 路由挂进现有 agent 服务(与 `/chat` 同一个 app)**,共用 `deps` / `identity` / provider。一套服务、一个语言、一套运维。

理由:① 全栈已 Python(daemon/indexer/cross_link/agent)② 重活在 Python 生态(embedding/chromadb/LLM SDK)③ 管理端是**控制面 + IO-bound**(DB + 向量 + LLM 调用),async FastAPI 舒适区,GIL 非瓶颈;真正热路径(向量召回)早是 chroma 原生加速,不在 Python 手里。

不为"基建要强"提前引 Go/Java —— 没真实 QPS 压测前是纯负债。

### 3.9 可扩展性预留(留接缝,不预建分布式)

为"未来把召回热点抽成独立微服务"预留**切割线**,但现在**不拆**。预留的是接口边界,不是分布式设施。

```
现在(同进程):  agent ─> RecallService 接口 ─> 本地实现(直查 chroma+PG)
未来(零改上层): agent ─> RecallService 接口 ─> HTTP 实现 ─> 独立召回微服务
```

4 个预留点(都是单进程也该做的好设计,顺手留扩展性,非额外造):

| 预留点 | 现在 | 抽走时 |
|---|---|---|
| **接口边界** | recall/store 走 ABC(`RecallService` / `MemoryStore`),上层只依赖接口 | 加 HTTP 实现,上层不动 |
| **无共享内存状态** | 状态全进 PG/chroma,session 也落库,不靠进程内 dict | 微服务可多副本 |
| **DTO 即契约** | recall 入参/出参用 pydantic 定死(= 未来 API body) | 进程内 DTO 直接变 HTTP body |
| **配置可指远端** | `config.memory.recall_backend = "local"`,预留 `"remote" + url` 分支 | 改配置指向微服务 |

**红线(现在不做,等压测证据)**:不拆进程 / 不引 MQ / 不上服务发现 / 不加分布式锁。预留接缝是免费的,预建分布式是负债。

#### 3.9b 读写分离接缝(已落地,不预建副本)

同理:**留接缝(代码路由读/写到不同连接池),不预建只读副本(那是 infra)**。

- `SqlSessionStore(dsn, read_dsn=None)`:写路径(new/append/建表)走 `dsn` 主库;读路径(get/has)走 `read_dsn` 只读副本;`read_dsn=None` 或同 dsn → **读写同池**(单 PG 现状)。
- config 口子:`memory.pg_dsn`(写/主)+ `memory.pg_dsn_read`(读/副本,预留)。
- **为什么不现在建副本**:副本价值=分摊读压力;单人单机读 QPS≈0,建了是空库复制 + 复制延迟坑(刚写读不到)+ 运维翻倍,**投入>0 收益=0**。
- **何时上副本**(触发条件,届时照做):多人/多 org 并发,读 QPS 实测压主库 / 要"分析查询不拖慢会话写"的强隔离。
- **怎么上**:起一个只读副本 PG(流式复制主库)→ config 填 `pg_dsn_read=<副本>` → 读自动分流,**代码零改**(接缝已留)。
- 同样适用未来 memory_entries 读路径(recall 走读池)。

---

## 四、分阶段(标注单人可做 / 需多人)

| 阶段 | 交付 | 单人能做? | 难度 | 状态 |
|---|---|---|---|---|
| **M0 现状** | project_id 隔离 + MEMORY.md(人肉) | — | ✅ | ✅ |
| **M1 身份** | `core/identity.py` + `X-User-Id` 采集(不拦截) | ✅ 单人 | 低 | ✅ 已落地 |
| **M2 作用域存储** | 平台独立 PG 库 `codev_platform_memory` + 4 层 namespace + write/list/supersede/forget + 读写分离接缝 | ✅ 单人 | 中 | ✅ 已落地 + 真机验证(2026-05-29,`scripts/verify_memory_pg.py` 全绿) |
| **M3 召回合并 + 冲突** | 跨作用域召回 + 冲突优先级 + redline 硬约束;agent loop 接入 | ✅ 单人 | 中 | ✅ 已落地 + 真机验证(2026-05-29,`RecallService`/`LocalRecallService`,agent 仅凭注入记忆答出事实) |
| **M4 生命周期** | update/supersede + TTL 自动 archive + forget + 压缩摘要 job | ✅ 单人 | 中 | ✅ 已落地 + 真机验证(2026-05-29,`memory_maintenance.py`:TTL 归档 + 同 topic LLM 融合,注入式 fuse_fn 可单测) |
| **M5 ACL** | 可见性表 + visible_scopes 真实计算(先 ACL)+ orgs.conflict_policy 从 PG 读 | 🟡 需第二人验证 | 中 | ⏳ 接缝已留(`visible_scopes` / `resolve_conflicts(policy)` 参数化) |
| **M6 RBAC + 审计 + secrets/team** | 角色 + 审计日志 + per-team secrets + 认证层(§3.1b) | ❌ 需多团队 | 高 | ⏳ |

**M1-M4 单人就做、立刻有用**(让 agent 有分层记忆 + 不爆 context);M5-M6 等真多人。

**M2/M3 落地实现备忘(2026-05-29)**:
- 存储:`agent/session_pg.py`(会话)+ `agent/memory_store_pg.py`(记忆条目),读写分离接缝(`read_dsn` 可选,默认同池)。
- 召回:`agent/recall_service.py` —— `RecallService` 抽象 + `LocalRecallService`(直查 PG);向量后端(chroma)留 `config.memory.recall_backend="vector"` 接缝,**未预建**(记忆量≈0 时语义排序零边际价值,同读写副本取舍)。
- 冲突:`agent/memory_recall.py:resolve_conflicts(policy)`,红线最高不可配 + policy(personal_first/org_first)控非红线;policy 来源 M5 从 PG 读。
- 注入:`prompts.build_code_understanding_system(memories=...)` 注入召回记忆段(redline 标注);`ChatService.ask` 召回失败静默退化不阻断问答。
- 库:现有 PG 17(localhost:5432)内独立 database `codev_platform_memory`,role `codev_platform`(§3.2b 红线满足:独立 database,非业务库)。

**M4 落地实现备忘(2026-05-29)**:
- TTL:`MemoryEntry.ttl_at` + write 持久化;`list_scope` 防御性排除已过期(即便归档 job 未跑,过期记忆也绝不召回);`SqlMemoryStore.archive_expired()` 批量归档(留痕,status=archived 非物删)。
- 压缩:`agent/memory_maintenance.py:MemoryMaintenance` —— `compress_topic/compress_scope` 把同 (scope,topic) 多条 active 融合成 1 条 summary(kind='summary',extra.fused_from 指向原条),原条 archived 可回溯(plan 难点 #2);**redline 逐条保留不参与融合**(组织硬约束不被稀释)。
- LLM 解耦:融合走注入式 `fuse_fn`(`list[str]->str`),编排可用 fake fuse_fn 单测;真 LLM 是 `make_llm_fuse(provider)` 薄适配器(走 `MEMORY_FUSION_SYSTEM`)。
- job:`scripts/run_memory_maintenance.py`(手动/cron/Task Scheduler);config `memory.compress_min_entries`(默认 3)。
- 阈值/调度待定:压缩触发阈值现静态 config;定时调度归 ops(未进程内常驻 job)。

**多人/多组织验收(2026-05-29,4 角色:隔离/并发/权限/正确性)**:

已修(单 org 期就该对的隔离接缝 + 并发硬化):
- 会话 org 透传:`SessionStore.new/get/has/append` 加请求级 `org_id`(默认 'default'),`ChatService.ask` 透传 —— 此前 SqlSessionStore org 写死构造期,多 org 同名 user 会撞进同一会话命名空间。
- personal 隐私:`POST /memory` 写 personal 强制 `scope_ref=user_id`(不信 client);`GET /memory` 读 personal 校验 `scope_ref==请求者`,否则 403(recall ≠ read)。
- 连接池:`memory.pool_max_size`(默认 10,可调),deps 透传给两个 store —— 防多人高峰 PoolTimeout(原硬编码 4)。
- maintenance 单实例:`SqlMemoryStore.advisory_lock` + `run_memory_maintenance.py` 起手 `pg_try_advisory_lock`,抢不到即跳过 —— 防并发压同 topic 双写 summary。
- 接缝/收敛:org 解析收敛进 `identity.resolve_org_from_request`(chat/memory 共用,M5 鉴权单点);`visible_scopes` 改 `LocalRecallService._visible_scopes` 可覆写方法(M5 查 org_members/team_members 时子类覆写,recall 调用点零改);路由内联 ⚠️ "M5 前无 ACL" 标注。

延后到 M5/M6(真需认证层 / 真多 org 才有意义,现在做是空跑):
- X-Org-Id 缺失:多 org 启用时从静默 'default' 改强制拒绝(现单 org 期 'default' 正确)。
- `archive_expired(org_id=None)` 跨全 org:多 org 期改按 org 迭代 / admin 守卫(现单 org 期全局口径正确)。
- `forget`/`archive` 按 id 无 org 守卫:待这两个动作有 HTTP 端点(M5)时加(现无端点暴露,UUID 主键不撞)。
- 单例懒建 double-check 锁:当前并发首请求最多多建一个池(浪费,DDL/open 幂等无数据错),扩容后顺手做。
- 统一授权层 `allowed(org,user,project,action)` + 认证层(§3.1b):上述 X-Org-Id / archive / forget 守卫的统一根因,M5/M6 落地。

---

## 五、难点

| # | 难点 | 对策 |
|---|---|---|
| 1 | 冲突消解判定(什么算"同一条记忆冲突") | topic_key 显式标 + LLM 兜底判同义;先粗后精 |
| 2 | 摘要融合丢信息 | 压缩留原条(archived 不删),可回溯;融合走 LLM + 人工抽检 |
| 3 | 跨 collection 召回延迟 | scope 数有限(org+几个 team+project+personal),并发查 + RRF;暖 daemon |
| 4 | 个人记忆隐私 | personal collection 物理隔离;recall 动作 ≠ read 动作;日志不打印 content |
| 5 | redline 不可绕过 | is_redline 在冲突解析强制最高优先;写 redline 需 org admin |
| 6 | 单人期验证多人逻辑 | M1-M4 用"假 user/team"fixture 测权限分支,不等真人 |
| 7 | 与现有 MEMORY.md 迁移 | M2 写迁移器:14 条全局→org/personal 分流;业务仓 docs/memory→project |

---

## 六、待确认

- ~~**PG 部署形态**~~ → **已定(2026-05-29)**:**独立 PG 实例,docker 平台专属容器**(`codev-platform-postgres`)。详见 §3.2c。理由:平台定位团队/组织,故障/资源/权限隔离全是真收益;从开发期就独立(docker 降成本),避免"单人共 server → 团队拆实例"的迁移返工;数据卷归平台 `data/`,契合所有权翻正主线。
- ~~org 单一假设?~~ → **已定**:org 是租户根,**多 org(SaaS 多租户)**,全表带 org_id;user↔org **多对多**(org_members)。详见 §1.1 / §3.4。单 org 期用隐含 `default` org,代码不强制。
- ~~user_id 来源~~ → **已定分层**:现在 `X-User-Id` 明文头(够单人/软隔离)→ 团队 config key/设置填 → 对外登录 SSO/JWT(§3.1b 认证层,M6)。
- **widget org/user 来源待定**:org 选择器 + user 身份(OS 名 / 设置填 / 登录)—— 现在隐含 default org + local user,多人时补。
- 压缩/摘要融合用哪个模型?复用 agent 的 provider(Claude)还是单独配?
- 是否现在就把 MEMORY.md 14 条按作用域重分(org vs personal)?还是 M2 再迁?
- config 新增 `config.memory.pg_dsn`(连接串走平台 config,不碰业务库 DSN)—— 确认字段名?
