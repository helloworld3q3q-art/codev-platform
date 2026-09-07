# codev-platform 深度审计报告 2026-06-03

## 结论

本轮对 `codev_platform/` 后端、`web-ui/src/` 前端、测试集、CI、插件扫描、reindex/webhook/graph 业务链路做了全量静态扫描和重点人工审计。本轮未修改源码，只新增审计文档。

当前后端测试在本地已经恢复稳定：

```text
python -m pytest tests/ -q
674 passed, 5 skipped, 1 warning in 11.25s
```

之前插件扫描超时的问题，在当前工作区已有改动后已不复现：

```text
python -m pytest tests/test_plugins_stack.py -q
12 passed in 0.96s
```

但系统仍存在几类真实风险：Web 控制台权限链路缺口、session/logout 语义不完整、项目注册并发覆盖、reindex worker 可被无超时子进程永久阻塞、前端 TypeScript 工具链失败。

## 审计范围

### 后端

- `codev_platform/web/`：Web 控制台 API、认证、用户、组织、项目、图谱、任务。
- `codev_platform/agent/`：Agent chat、memory、工具调用、provider。
- `codev_platform/gateway/`：统一认证、token、passthrough、限流。
- `codev_platform/core/`：ACL、RBAC、错误、配置、路径、审计、HTTP envelope。
- `codev_platform/plugins/`：插件 registry、executor、builtin stack analyzers。
- `codev_platform/reindex/`：队列、worker、runner、git sync。
- `codev_platform/webhook/`：VCS webhook 接收、验签、入队。
- `codev_platform/graph/`：统一图谱 schema/store/ingest。
- `codev_platform/chroma/`、`cross_link/`、`codegraph/`：索引和 MCP 服务。
- `ops/`、`cli.py`、`mcp_serve.py`：运维入口和长进程管理。

### 前端

- `web-ui/src/`：登录、请求封装、项目/组织/用户/图谱/任务页面。
- `web-ui/package.json`、`tsconfig.json`：工具链和依赖。

### 测试

- `tests/`：共收集 `679` 左右测试项，本轮完整后端结果为 `674 passed, 5 skipped`。
- Web 关键测试切片：`59 passed`。

## 业务链路地图

### Web 登录与会话链路

入口：

- `web-ui/src/pages/user/login/index.tsx`
- `web-ui/src/utils/fetch/fetch.ts`
- `codev_platform/web/routes/auth.py`
- `codev_platform/web/services/auth_service.py`
- `codev_platform/web/security/sessions.py`

链路：

1. 前端拉 `/api/v1/auth/public-key`。
2. 前端用 RSA 加密密码，失败则降级明文。
3. `/api/v1/auth/login` 校验用户和密码。
4. `SessionStore.create()` 生成 access token 和 refresh token。
5. 前端把 token 存入 `localStorage`。
6. 请求拦截器把 `auth_token` 放入 `Authorization: Bearer ...`。

关键问题：

- logout 只撤 refresh，不撤 access。
- 后端动态会话失败返回 403，前端只在 401 清 token 并跳登录。
- gateway token 和 Web session token 是两套机制，默认 passthrough 下部分路由绕过动态 session。

### 项目管理链路

入口：

- `codev_platform/web/routes/projects.py`
- `codev_platform/web/services/project_service.py`
- `codev_platform/web/repositories/project_read_repo.py`
- `codev_platform/web/repositories/project_write_repo.py`
- `platform_meta/projects/<code>/meta.json`

链路：

1. 前端请求 projects list/register/detail/load/unload。
2. routes 直接调用 `ProjectService`。
3. service 做 project code 校验和读写仓库编排。
4. write repo 写 `meta.json`。
5. load/unload 写进程内 `_LOADED`。

关键问题：

- routes 没有 `current_session`、`require_org_role`、`require_platform_admin` 依赖。
- register 是“先查再写”，底层 `write_text()` 可覆盖同 code。
- load 状态只在进程内，重启丢失。

### 索引重建与任务链路

入口：

- `codev_platform/web/routes/indexes.py`
- `codev_platform/web/routes/jobs.py`
- `codev_platform/web/services/index_service.py`
- `codev_platform/web/services/job_service.py`
- `codev_platform/reindex/queue.py`
- `codev_platform/reindex/worker.py`
- `codev_platform/reindex/runners.py`

链路：

1. `/api/v1/indexes/rebuild` 通过 `require_project_access` 判断项目权限。
2. `IndexService` 提交 job。
3. `JobService` 用进程内 lock 防同 project 同 kind 重复提交。
4. reindex worker 从 file spool queue 消费。
5. runner 调 `python -m codev_platform.cli reindex --flag --repo ...`。

关键问题：

- passthrough 默认下 `require_project_access` 是 advisory allow，未登录也能提交重建 job。
- runner 子进程没有 timeout，单个挂起任务会阻塞 worker。
- job store 是进程内，重启丢任务状态。

### Webhook 到 reindex 链路

入口：

- `codev_platform/webhook/server.py`
- `codev_platform/webhook/providers.py`
- `codev_platform/reindex/queue.py`

链路：

1. VCS POST `/{provider}`。
2. provider 验签并解析 push event。
3. repo 映射 project_id。
4. changed files 分类为 reindex scopes。
5. scopes 入队。

风险：

- `webhook.allow_insecure=true` 时跳过验签，虽然 `run_http` 绑定 internal.example.invalid，但如果被反代暴露会变成远程未验签入口。
- webhook 日志写在包目录 `codev_platform/webhook/webhook.log`，部署为只读包或 wheel 后不稳。

### Graph 查询链路

入口：

- `codev_platform/web/routes/graph.py`
- `codev_platform/web/integrations/codegraph_client.py`
- `codev_platform/web/integrations/cross_link_client.py`
- `codev_platform/graph/store.py`

链路：

1. Web graph routes 通过 `require_project_access` 取 project_id。
2. codegraph/cross-link 直接只读 SQLite。
3. cross-link graph/stats 优先读统一 graph store。
4. store 无数据时 fallback 到 legacy cross_layer.sqlite。

风险：

- `graph.store.load_graph(conn, project_id)` 参数校验了 project_id，但 SQL 不按 `project_id` 过滤。
- `upsert_result()` 写入节点时使用 `n.project_id`，没有强制等于入参 project_id。
- 当前 per-project DB 约束能缓解，但测试或显式 path 场景会出现跨项目数据污染。

### 插件扫描链路

入口：

- `codev_platform/plugins/registry.py`
- `codev_platform/plugins/executor.py`
- `codev_platform/plugins/builtin/_stack_scan.py`
- `codev_platform/plugins/builtin/*.py`

链路：

1. registry 自动发现 builtin plugins。
2. executor 隔离 detect/analyze 异常。
3. stack scanner 扫描 repo，生成统一 `AnalyzerResult`。
4. reindex ingest 写入 graph store。

现状：

- 当前工作区已使用 `os.walk` 剪枝，`node_modules/.venv/data/target` 等不会下钻。
- 插件相关测试已恢复快速通过。

## 已复现问题

### P1：未登录或低权限用户可访问项目列表、提交索引重建

涉及文件：

- `codev_platform/web/routes/projects.py:38`
- `codev_platform/web/routes/projects.py:63`
- `codev_platform/web/routes/projects.py:83`
- `codev_platform/web/routes/projects.py:98`
- `codev_platform/web/routes/projects.py:113`
- `codev_platform/web/routes/indexes.py:28`
- `codev_platform/core/acl.py`

现象：

`projects.py` 的 list/register/detail/load/unload 只注入 `ProjectService`，没有 `current_session` 或角色依赖。`indexes.py` 虽然用了 `require_project_access`，但默认 passthrough 模式下 `core.acl.can_access()` 返回 advisory allow。

本轮复现：

```text
GET /api/v1/auth/session -> 403
POST /api/v1/projects/list -> 200
POST /api/v1/indexes/rebuild, X-Project-Id: demo -> 200
```

影响：

- 未登录状态能枚举项目列表。
- 在默认 passthrough 配置下，只要传 `X-Project-Id` 就能提交索引重建 job。
- register/load/unload 也缺少角色控制，属于管理面接口权限缺口。

建议：

- `projects/list/detail` 至少依赖 `current_session`。
- `projects/register/load/unload` 依赖 `require_org_role("admin")` 或 `require_platform_admin`。
- `indexes/rebuild` 除项目 ACL 外，还应校验项目写权限或 admin 权限。
- 增加未登录/低权限访问 projects/indexes 的回归测试。

### P1：logout 后 access token 仍可用

涉及文件：

- `codev_platform/web/security/sessions.py:72`
- `codev_platform/web/services/auth_service.py:51`
- `codev_platform/web/routes/auth.py:75`

现象：

`SessionStore.revoke(refresh_token)` 只删除 refresh token，没有删除同一 session 的 access token。access token 在 TTL 内继续有效。

本轮复现：

```text
before_logout_access_resolves= True
after_logout_access_resolves= True
after_logout_refresh_works= False
```

影响：

- 用户点击退出后，旧 access token 最长 1 小时仍能访问 session 保护接口。
- 如果 access token 泄漏，logout 不能立即止血。

建议：

- refresh map 存 session_id，access/refresh 都按 session_id 关联。
- logout 时删除该 session 的所有 access/refresh token。
- 前端 logout 也要清 `refresh_token`。
- 增加测试：logout 后 `/api/v1/auth/session` 必须失败。

### P1：项目注册底层写仓库可覆盖同 code

涉及文件：

- `codev_platform/web/repositories/project_write_repo.py:22`
- `codev_platform/web/repositories/project_write_repo.py:31`
- `codev_platform/web/repositories/project_write_repo.py:45`
- `codev_platform/web/services/project_service.py`

现象：

service 层先查重再写，但 write repo 本身不做原子创建。`target.write_text()` 会覆盖已有 `meta.json`。

本轮复现：

```text
repo.register_project(code='p1', name='A', org_id='o1')
repo.register_project(code='p1', name='B', org_id='o2')

最终 meta.json 变成 B / o2
```

影响：

- 并发请求可能穿透 service 的查重窗口。
- 底层仓库被其它调用方复用时会覆盖项目归属。

建议：

- 用原子创建方式写入：`open(..., "x")` 或临时文件 + `os.replace` + 存在检查。
- write repo 在已存在时抛错，不依赖 service 唯一兜底。
- 增加并发/重复注册测试。

### P2：reindex runner 无 timeout，worker 可永久阻塞

涉及文件：

- `codev_platform/reindex/runners.py:61`
- `codev_platform/reindex/worker.py:90`
- `codev_platform/reindex/worker.py:130`

现象：

`CliReindexRunner.run()` 调用：

```python
subprocess.run(cmd).returncode
```

没有 timeout。worker 是串行消费，任一 runner 卡死，整个 reindex worker 都会停在该 job。

影响：

- 外部 CLI、模型加载、SQLite 锁、网络、git 或子命令异常挂起时，后续所有项目 reindex 都不执行。
- webhook 入队仍成功，但队列堆积。

建议：

- 给 runner 加配置化 timeout，例如 `reindex.runner_timeout_sec`。
- timeout 时返回明确 rc，并保留或丢弃任务要有策略。
- 日志记录 cmd、project_id、kind、elapsed。

### P2：Graph store 没有强制 project_id 隔离

涉及文件：

- `codev_platform/graph/store.py:148`
- `codev_platform/graph/store.py:161`
- `codev_platform/graph/store.py:226`
- `codev_platform/graph/store.py:236`

现象：

`upsert_result(conn, project_id, result)` 校验了入参 project_id，但写入 node 时保留 `n.project_id`。`load_graph(conn, project_id)` 校验了入参 project_id，但查询节点没有 `WHERE project_id = ?`。

影响：

- 插件如果产出错误 project_id，store 会接受。
- 显式传 path 或测试复用同一 SQLite 时，`load_graph(project_id)` 会读出其它项目节点。
- per-project DB 是外部约束，不能替代数据层校验。

建议：

- 写入时强制 `n.project_id = project_id` 或拒绝不一致结果。
- 读取 nodes 时加 `project_id = ?` 条件。
- edges 需要基于过滤后的 node id 二次过滤。

### P2：Web 数据访问层 SQL 散落在 Python 字符串中，后续维护成本偏高

涉及文件：

- `codev_platform/web/repositories/account_store_pg.py`
- `codev_platform/web/integrations/codegraph_client.py`
- `codev_platform/web/integrations/cross_link_client.py`
- `codev_platform/web/routes/graph.py`
- `codev_platform/agent/rbac_store_pg.py`

现象：

`codev_platform/web` 下 SQL 主要分两类：

1. 业务写库 SQL：`account_store_pg.py` 直接用 psycopg 执行 PostgreSQL CRUD，并复用 `agent/rbac_store_pg.py` 中的 `_SCHEMA` 字符串。
2. 图谱只读 SQL：`codegraph_client.py`、`cross_link_client.py` 和 `routes/graph.py` 直接读取 per-project SQLite 库。

当前多数查询已经使用参数化绑定，SQL 注入不是主要风险。真正的问题是 SQL、schema、字段别名、迁移和方言细节都散落在 Python 字符串中。随着账户、组织、RBAC、图谱查询继续扩展，字段改名、schema 漂移、PG/SQLite 方言差异、动态 `IN (...)` 拼接和测试 fixture 不同步会成为主要隐藏 bug 来源。

风险判断：

- `account_store_pg.py` 属于业务持久化路径，应该有明确 migration、表定义、索引和约束版本，不适合长期依赖 `_SCHEMA` 大字符串。
- `codegraph_client.py` / `cross_link_client.py` 属于只读分析查询，SQL 本身复杂、字段别名多、性能敏感，不适合强行 ORM 化。
- 当前 repository/service 分层已经存在，可以在不影响上层 service 的前提下替换底层数据库实现。

建议治理路线：

1. 业务库 PostgreSQL 使用 `SQLAlchemy Core + Alembic`，不必急着上 ORM。
   - 新增 `codev_platform/web/db/tables.py` 集中定义 `orgs`、`users`、`org_members` 等表。
   - 新增 Alembic migration 管理 schema 版本。
   - `account_store_pg.py` 保留 repository 接口，内部从手写 SQL 字符串迁移到 `select()` / `insert()` / `update()`。
2. 图谱 SQLite 查询保留 raw SQL，但集中管理。
   - 将较大的查询拆到 `codev_platform/web/integrations/sql/*.sql` 或集中到 `queries.py`。
   - 动态字段、排序、`IN (...)` 统一走白名单 helper。
   - 给 `codegraph.db`、`cross_layer.sqlite` 增加 fixture DB 测试，schema 一变测试立即失败。
3. 不建议全量 ORM 化。
   - 图谱分析查询如果强行 ORM，会牺牲可读性和性能控制。
   - 当前更合理的边界是：业务写库结构化，分析读库保留参数化 SQL。

推荐目标结构：

```text
codev_platform/web/db/
  engine.py
  tables.py
  migrations/

codev_platform/web/repositories/
  account_store_pg.py   # SQLAlchemy Core

codev_platform/web/integrations/
  queries.py            # 或 sql/*.sql
  codegraph_client.py   # 只读 raw SQL 调用
  cross_link_client.py  # 只读 raw SQL 调用
```

结论：

业务库应逐步迁到 `SQLAlchemy Core + Alembic`，解决 schema 版本、约束和类型治理；图谱库继续保留参数化 raw SQL，但要集中查询、白名单动态片段并补 fixture 测试。这比“全部 ORM 化”更贴合当前项目架构。

### P2：前端 TypeScript 工具链失败

涉及文件：

- `web-ui/package.json`
- `web-ui/tsconfig.json`
- `node_modules/@ant-design/pro-form/.../FormItemRender/index.d.ts`

现象：

`pnpm tsc` 失败：

```text
typescript Version 4.9.5
error TS1139: Type parameter declaration expected
```

依赖声明文件使用了 TypeScript 5.x 的 `const` 类型参数：

```ts
export declare function useControlModel<const T extends readonly string[]>(...)
```

影响：

- 前端类型检查不可用。
- CI 如果加入 `pnpm tsc` 会失败。
- 当前 `skipLibCheck` 无法绕过语法解析错误。

建议：

- 升级 TypeScript 到 5.x，或锁低 `@ant-design/pro-*` 到兼容 TS 4.9 的版本。
- 在 CI 加入 `pnpm tsc`，避免工具链漂移。

### P2：前端 403 不清理过期 session，容易卡在失效登录态

涉及文件：

- `web-ui/src/utils/fetch/fetch.ts:169`
- `web-ui/src/utils/fetch/fetch.ts:178`
- `codev_platform/web/security/deps.py`

现象：

后端 `current_session` 对无效/过期动态 session 抛 `ACCESS_DENIED`，统一 envelope 对应 403。前端只在 HTTP 401 清 `auth_token` 和 `user`，403 只提示“无操作权限”。

影响：

- access token 过期后，前端不会清 token，也不会跳登录页。
- 用户可能一直停留在“无操作权限”的假登录状态。

建议：

- 后端区分未登录/会话失效为 401，授权不足为 403。
- 或前端识别 403 envelope 中的 `access_denied` 且 message 为会话失效时清 token。
- 同时清理 `refresh_token`。

### P2：配置了 PG 但失败时静默回退内存账户存储

涉及文件：

- `codev_platform/web/repositories/account_store.py`

现象：

`bind_account_stores()` 如果 `memory.pg_dsn` 存在，但 PG store 初始化失败，会捕获所有异常并回退到内存。

影响：

- 生产环境数据库不可用时，服务仍启动但账户数据变成进程内临时数据。
- 用户、组织、角色、登录态行为会和预期持久化严重偏离。

建议：

- dev 可以 fallback，prod 应 fail-fast。
- 至少记录 error 级日志，并在 health/status 暴露 backend=`memory`/`pg`。

### P3：运行日志和数据写在包目录下

涉及文件/路径：

- `codev_platform/chroma/daemon.log`
- `codev_platform/chroma/mcp_server.log`
- `codev_platform/chroma/search_recall.jsonl`
- `codev_platform/codegraph/mcp_server.log`
- `codev_platform/cross_link/cross_link_usage.jsonl`
- `codev_platform/cross_link/mcp_server.log`
- `codev_platform/webhook/webhook.log`
- `codev_platform/mcp_serve_logs/*.log`

影响：

- wheel/只读安装场景可能写失败。
- 运行数据污染源码包。
- 日志进入 git 工作区，影响审计和发布。

建议：

- 全部迁移到 `data_root()/logs/...`。
- `.gitignore` 覆盖已知运行日志路径。

### P3：大量 broad except 和静默 pass

AST 扫描结果：

- `codev_platform/chroma/indexer.py`：12 个 broad except。
- `codev_platform/chroma/server.py`：10 个 broad except。
- `codev_platform/codegraph/server.py`：8 个 broad except。
- `codev_platform/platform_status.py`：7 个 broad except。
- `codev_platform/ops/health/_checks.py`：6 个 broad except。

影响：

- 适合 daemon 容错，但会掩盖真实错误。
- 对索引质量、健康检查、MCP 服务可观测性不利。

建议：

- 按模块区分“允许降级”和“必须暴露”的异常。
- 对静默 pass 增加 debug/warn 级日志，至少带 request_id/project_id。

### P3：前端请求层关闭类型检查且大量 any

涉及文件：

- `web-ui/src/utils/fetch/fetch.ts:3`
- `web-ui/src/services/apis/typings.d.ts`

现象：

`fetch.ts` 使用 `// @ts-nocheck`，生成类型里大量字段为 `any`。

影响：

- API 字段变更无法靠 TypeScript 捕获。
- graph/codegraph/cross-link 这类复杂 DTO 很容易出现运行时空字段或字段名错配。

建议：

- 修复 TS 工具链后，逐步移除 `@ts-nocheck`。
- 重新生成 OpenAPI 类型，减少 `any`。

## 已验证通过项

### 后端完整测试

```text
python -m pytest tests/ -q
674 passed, 5 skipped, 1 warning in 11.25s
```

### 编译检查

```text
python -m compileall -q codev_platform tests
passed
```

### Web 关键测试切片

```text
python -m pytest tests/test_web_projects.py tests/test_web_jobs.py tests/test_web_auth.py tests/test_web_users.py tests/test_web_orgs.py tests/test_web_graph.py -q
59 passed
```

### 插件扫描测试

```text
python -m pytest tests/test_plugins_stack.py -q
12 passed in 0.96s
```

### 前端 lint

```text
pnpm lint:js
passed
```

### 前端类型检查

```text
pnpm tsc
failed
```

失败原因是 TS 4.9.5 不支持依赖声明文件中的 TS 5.x 语法。

## 当前测试覆盖盲区

后端 674 个测试通过，但以下业务缺口没有被现有测试阻止：

- 未登录访问 `/api/v1/projects/list` 返回 200。
- passthrough 下未登录提交 `/api/v1/indexes/rebuild` 返回 200。
- logout 后 access token 仍 resolve 成功。
- `ProjectWriteRepository.register_project()` 可重复覆盖同 code。
- `graph.store.load_graph()` 不按 project_id 过滤。
- reindex runner 没有 timeout。

建议新增测试文件或扩展现有文件：

- `tests/test_web_projects_authz.py`
- `tests/test_web_indexes_authz.py`
- `tests/test_web_auth_logout_access_revoke.py`
- `tests/test_project_write_repo_atomic.py`
- `tests/test_graph_store_project_isolation.py`
- `tests/test_reindex_runner_timeout.py`

## 优先级建议

第一优先级：

1. 给 projects/indexes 管理接口补登录态和角色依赖。
2. 修复 logout 后 access token 仍有效。
3. 修复 project register 底层覆盖和并发窗口。

第二优先级：

1. 给 reindex runner 加 timeout。
2. 修复 graph store project_id 隔离。
3. 治理 Web 数据访问层：业务 PG 迁到 SQLAlchemy Core + Alembic，图谱 SQLite 查询集中管理。
4. 解决前端 TS 版本与依赖声明不兼容。

第三优先级：

1. 运行日志迁移到 data/logs。
2. prod 环境 PG 初始化失败 fail-fast。
3. broad except 增加可观测日志。
4. 前端移除 `@ts-nocheck` 并收紧 API 类型。
