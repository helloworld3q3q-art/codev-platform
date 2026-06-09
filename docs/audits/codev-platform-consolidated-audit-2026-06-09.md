# codev-platform 综合审计报告（2026-06-09）

本报告合并今天新增的“Bug / Edge Audit”和“全链路非前端审计”两份结果，统一成一个可执行的问题清单。按用户要求，本次审查除 `web-ui/**` 前端目录可跳过外，其它文件不跳过；后端里的前端分析器、图谱插件、脚本、配置、测试和文档均纳入范围。

## 处理状态（2026-06-09 续会话逐条核实 + 修复）

每条都对**当前代码**核实后修，按优先级分批提交、加单测、关键项 WSL 真实验证。

| # | 问题 | 优先级 | 状态 | 处理 |
|---|---|---|---|---|
| 1 | 会话复用未校验 project_id（跨项目泄漏） | P1 | ✅ 已修 | `SessionStore.has/get` 抽象+内存+PG 加 project_id 过滤；`ask` 复用/读历史传 project_id。内存隔离单测 + WSL 51 session 测试绿 |
| 2 | PG refresh token 非原子轮换 | P1 | ✅ 已修 | 单事务 `DELETE...RETURNING` 只删未过期者。WSL 验证旧 refresh 重放被拒 |
| 3 | arch_layer 缓存键漏 is_frontend/is_page | P2 | ✅ 已修 | 纳入 `_cache_key`，字段变即重跑。单测 |
| 4 | spool 坏任务反复处理 | P2 | ✅ 已修 | `complete()` 改 resolved-在-spool-内则删（清残留 + 挡穿越）。单测 |
| 5 | `graph audit --all --json` 忽略 JSON | P2 | ✅ 已修 | `--all` 加 JSON 分支，退出码不变 |
| 6 | pre-push 注释 vs duplicate nodes 是 warning | P2 | ✅ 已修 | 注释修正：duplicate nodes/edges 是 warning |
| 7 | `create_app(cfg)` 与全局服务绑定可能不同配置 | P2 | ⏸️ 延后 | 单实例生产 config 一致（同一 load_config），影响主要在测试注入/多实例；正确修是 DI 重构，改动大、回退风险，按真实需求触发再做 |
| 8 | 多 org membership 但登录取第一个 org | P2 | ✅ 已决策(现状符合) | 产品定**多 org 模型**:登录默认第一个 org(intended)、切换走前端 + 请求级 `X-Org-Id`。验证 `resolve_org_from_request`(core/identity.py)按 X-Org-Id 解析当前 org(非登录默认)→ 选定 org 能正常访问/操作,现状即符合模型,**无需改**。残留 follow-up:`set_roles` 的 `org_id==user.org_id` 单一归属假设(admin 给用户在非首 org 设角色被挡)—— admin RBAC 边界 + 安全敏感,多 org 角色管理真要时单独做 |
| 9 | `find_arch_violations` count 是截断后数量 | P3 | ✅ 已修 | 加 `returnedCount/totalCount/truncated` |
| 10 | graph audit 聚合扫描非只读 | P3 | ⏸️ 延后 | open_store 仅 WAL + 幂等迁移（稳态无副作用）；改 read-only 有旧 schema 迁移失败风险，需谨慎兜底，P3 暂缓 |
| 11 | 生产代码 ruff 2 条 | P3 | ✅ 已修 | `zip(strict=True)`；删未用 `repo` |

**净结果**: P1 两条(安全)全修+验证；P2/P3 清单 6 条已修(#3/#4/#5/#6/#9/#11)+ #8 产品决策确认(多 org 模型,现状即符合,无需改);仅 2 条带理由延后(#7 config DI 重构、#10 P3 只读迁移风险)。

## 覆盖范围

- 非前端 tracked 文件：613 个。
- 主要目录：`codev_platform` 288、`tests` 148、`docs` 94、`scripts` 19、`memory` 16、`eval` 9、`platform_meta` 8、`demo` 7、`tools` 5。
- 文件类型：Python 430、Markdown 131、脚本 14，另含 JSON/TOML/YML/CMD/SH/INI 等配置文件。
- 今日差异审查也覆盖了 2026-06-08 以来的生产代码、测试、门禁脚本和图谱审计相关变更。

## 验证信号

- `python -m compileall -q codev_platform eval scripts tests tools demo`：通过。
- `pytest -q`：通过，`1257 passed, 13 skipped, 1 warning`。
- `python -m codev_platform.cli graph audit --all`：本地 graph store clean。
- `python -m ruff check codev_platform eval scripts tests tools demo`：未通过，共 37 条；生产代码直接相关 2 条，其余主要在测试/验证脚本。
- 合并报告后复测：`compileall` 通过，`pytest -q` 仍为 `1257 passed, 13 skipped, 1 warning`，`git diff --check` 通过；`ruff` 仍为同一批 37 条问题。

## 代码级分析方法

本轮不是只看测试结果，而是按入口到存储的实际调用链逐段核对：

- Agent chat 链路：`agent/routes/chat.py` 解析 `X-Project-Id` / body `project_id`，调用 `can_access()` 过 ACL，然后把 `project_id` 传入 `ChatService.ask()`。断点出现在 `agent/services/chat_service.py`：新建会话时绑定了 `project_id`，但复用旧 `session_id` 时 `has()` 和 `get()` 没传 `project_id`。这说明权限入口是有的，漏洞不是路由漏鉴权，而是 service 层复用会话时丢了项目边界。
- Agent session 存储链路：`agent/session.py` 的内存实现 `get(..., project_id=...)` 已经能按项目过滤；`agent/session_pg.py` 的 PG 实现 `get(..., project_id=...)` 也能 join `agent_sessions` 校验 `project_id`。但是抽象接口 `SessionStore.get()` 仍未声明 `project_id`，`has()` 也没有项目参数，导致 `ChatService.ask()` 主链路没有强制使用现有隔离能力。
- Web refresh 链路：`web/security/sessions.py` 的内存版 refresh 在同一进程 dict 中 pop 后 create，窗口较小；PG 版先用普通连接 `SELECT`，再用新事务 `DELETE`，最后再 `create()`。这三个动作不是同一条原子 SQL，也没有行锁或 rowcount gate，因此并发 worker 可以同时读到旧 refresh。
- Graph analyzer 缓存链路：`architecture_layer.py` 先从硬图节点构造 `FileFact`，其中 `is_frontend` 和 `is_page` 会影响 labeler 词表和角色判断；但 `_cache_key()` 没有纳入这两个字段。缓存命中发生在 labeler 调用前，所以后续 `_to_soft()` 只会把旧 label 写回软边，无法感知事实变化。
- Reindex queue 链路：`enqueue()` 会校验并只写合法 spool 文件，但 `pending()` 是扫描目录中的既有文件，可能读到历史坏文件或人工残留文件。`complete()` 知道非法片段不能用 `_path()`，但直接返回成功且不删除，导致下一轮 `pending()` 继续读到同一坏 job。
- CLI audit 链路：`cli.py` 的单项目 audit 分支处理了 `args.json`；`--all` 分支在进入聚合逻辑后直接打印摘要，没有 JSON 分支。这是明确的参数路径缺失，不是输出格式争议。

## P1 确认 Bug

### 1. Agent chat 复用历史会话时没有校验 `project_id`

- 位置：`codev_platform/agent/services/chat_service.py:66-76`
- 现象：`ChatService.ask()` 复用 `session_id` 时只检查 `user_id/org_id`，没有把 `project_id` 传给 `has/get`。
- 影响：用户在项目 A 创建会话后，如果在项目 B 请求中提交同一个 `session_id`，主 chat 路径会复用 A 的历史上下文，并把 B 的新消息追加到 A 会话里。列表和消息读取接口虽然按项目过滤，但 chat 主链路已经发生上下文泄漏和历史污染。
- 建议：`SessionStore.has/get` 增加并强制使用 `project_id`；`ChatService.ask()` 在复用和读取历史时都传入 `project_id`。抽象接口、内存实现、PG 实现同步更新。

### 2. PG refresh token 轮换不是原子操作

- 位置：`codev_platform/web/security/sessions.py:170-183`
- 现象：`PgSessionStore.refresh()` 先 `SELECT` 旧 refresh 记录，再单独 `DELETE`，最后 `create()` 新 token。
- 影响：两个 worker 或两个并发请求可能同时读到旧 refresh 记录，然后各自创建新会话，破坏 refresh token 单次轮换语义。
- 建议：改成单事务原子消费，例如 `DELETE ... WHERE refresh_hash = ? AND refresh_expires_at >= now RETURNING username, org_id`；只有成功删除一行才签发新 token。SQLite 测试可用方言分支或事务内 delete 后检查 rowcount。

## P2 高风险问题 / 边界 Bug

### 3. 架构分层 analyzer 缓存 key 漏掉 `is_frontend` / `is_page`

- 位置：`codev_platform/graph/analyzers/architecture_layer.py:217-223`
- 现象：`_cache_key()` 只纳入 path、endpoint、table/import/function 计数和 labeler signature，没有纳入 `FileFact.is_frontend` 与 `FileFact.is_page`。
- 影响：文件从普通模块变成前端页面，或页面/前端事实变化时，缓存仍可能复用旧角色标签，导致 `PLAYS_ROLE` 图谱结果陈旧。
- 建议：把 `is_frontend`、`is_page` 纳入 cache payload，并考虑增加缓存版本字段。

### 4. reindex spool 坏任务会被反复处理

- 位置：`codev_platform/reindex/queue.py:112-119`
- 现象：`FileSpoolQueue.complete()` 遇到含 `/`、`\`、`..` 的非法 `pid/kind` 时直接 `return True`，但不删除或隔离坏文件。
- 影响：`pending()` 下一轮仍会读出同一个坏 job，worker 会永久重复丢弃/记录。
- 建议：对非法 spool 文件做 quarantine，或在确认 resolved path 仍位于 spool 目录内后删除。

### 5. `graph audit --all --json` 忽略 JSON 参数

- 位置：`codev_platform/cli.py:249-267`
- 现象：单项目 `graph audit --json` 会输出 JSON，但 `--all` 分支总是打印人读文本。
- 影响：CI/脚本无法稳定消费 `codev-platform graph audit --all --json` 的机器可读输出。
- 建议：`--all` 分支在 `args.json` 为真时输出聚合 `agg` JSON，并保持现有退出码规则。

### 6. pre-push audit 注释和实际门禁语义不一致

- 位置：`tools/dev/pre-push-audit.ps1:3-5`、`codev_platform/graph/audit.py:111-124`
- 现象：脚本注释说 structural ERRORS 包含 duplicate nodes；实现中 duplicate nodes 只是 warning，不参与 `error_count`。
- 影响：开发者会误判 pre-push 门禁覆盖范围。
- 建议：二选一：把 duplicate nodes 升级为 error，或修正脚本注释。

### 7. `create_app(cfg)` 与全局服务绑定可能使用不同配置

- 位置：`codev_platform/web/app.py:11`、`codev_platform/web/routes/jobs.py:28`、`codev_platform/web/routes/agent.py:27`、`codev_platform/web/security/sessions.py:222`、`codev_platform/web/security/session_authenticator.py:41`
- 现象：`web.app` 在模块导入时已导入路由；`job_service`、`agent_client`、`session_store` 在 import 阶段使用 `load_config()` 绑定。之后 `create_app(cfg)` 的显式 cfg 只作用于 app factory 和 authenticator。
- 影响：测试注入 cfg、多实例临时配置或不同 `CODEV_PLATFORM_CONFIG` 启动顺序下，可能出现认证器使用 cfg A，session/job/agent 使用 cfg B。
- 建议：把这些绑定迁移到 app startup 或依赖注入；至少提供统一 `rebind_web_services(cfg)` 并由 `create_app(cfg)` 调用。

### 8. PG 用户表支持多 org membership，但登录态强制取第一个 org

- 位置：`codev_platform/web/repositories/account_store_pg.py:121-127`、`codev_platform/web/schemas/auth.py:11`、`codev_platform/web/services/user_service.py:126-136`
- 现象：`PgUserStore.get()` 对同一用户的 membership 按 `org_id` 排序取第一条；登录请求没有 org 选择字段；服务层又按单一 `user.org_id` 校验角色写入。
- 影响：如果 PG 数据中用户属于多个 org，登录后会话 org 由排序结果决定，用户无法自然切换或管理第二个 org 的权限。这不是直接越权，但模型边界不一致。
- 建议：明确产品模型。若只支持单 org 用户，应从数据库和服务层禁止多 membership；若支持多 org，应增加登录/切换 org 机制，并让 session 绑定选定 org。

## P3 边际问题 / 运维债务

### 9. `find_arch_violations()` 的 `count` 是截断后数量

- 位置：`codev_platform/graph/impact.py:392-394`
- 影响：当实际违规超过 `limit` 时，调用方看不到真实总数，也看不到结果已截断。
- 建议：返回 `total_count`、`returned_count`、`truncated`。

### 10. graph audit 聚合扫描不是只读

- 位置：`codev_platform/graph/audit.py:151-174`、`codev_platform/graph/store.py:183-198`
- 现象：`audit_all_stores()` 使用 `open_store()`；后者会创建目录、设置 WAL、执行迁移和 schema DDL。
- 影响：pre-push / CI audit 名义上是只读结构审计，但可能修改本地 sqlite 或产生 WAL/SHM 文件。
- 建议：审计路径使用 read-only sqlite 连接；迁移和建表放到 ingest 或显式 repair 命令里。

### 11. 生产代码 ruff 问题

- 位置：`codev_platform/index_manifest.py:117`、`codev_platform/ops/index_status.py:57`
- 问题：`B905 zip() without strict=`；`F841 repo assigned but unused`。
- 影响：风险不高，但会阻断 lint 门禁，也暴露状态展示逻辑有未清理变量。
- 建议：`dict(zip(cols, row, strict=True))`；删除未使用的 `repo` 或真正用于展示。

## 静态检查剩余项

ruff 共 37 条：

- 生产代码：2 条，见 P3 #11。
- 验证脚本：`scripts/verify_memory_pg.py`、`scripts/verify_rbac_pg.py` 多处 `E702`。
- 测试代码：多个 `E702`、`F841`、`B008`、`B017`，以及测试里的 `B905`。

这些不影响当前 pytest 结果，但会让 lint 门禁无法直接启用。建议先修生产代码 2 条，再决定测试/脚本是纳入同一 lint gate，还是用 per-file ignore 分阶段治理。

## 建议修复顺序

1. 先修 P1 #1、#2：分别涉及跨项目上下文泄漏和 refresh token 重放窗口。
2. 再修 P2 #3、#4、#5、#7：影响图谱准确性、worker 稳定性、CI 机器读取和配置一致性。
3. P2 #8 先做产品决策：单 org 用户模型还是多 org 会话模型。
4. P3 跟随门禁治理批量处理，尤其是只读 audit 和生产代码 lint。
