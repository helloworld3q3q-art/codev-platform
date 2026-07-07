# 平台产品化迭代 plan(2026-06-14)

> **主题**:代码智能平台线判定到**平台期**(roadmap-2026-06-07 收口),主线转入**产品化** ——
> 从"**平台自己用**的代码智能系统闭环" → "**让别的业务仓/团队也能简单接入并用起来**"。
>
> 本轮**主动解冻** roadmap-2026-06-10 的两个 Gate 项(用户拍板要做,不再等触发)+ 推进
> roadmap-2026-06-07 的 Phase 10 治理。核心驱动 = 用户真实痛点:**"加入项目流程繁琐,平台化就该简单"**。

## 〇、缘起与边界

- **不再用 Gate 冻结当借口**:多组织 web 签发 / 多仓多根索引,用户明确"要做"。
- **但仍守纪律**:不堆码(优先编排/复用现有扩展点)、measure-first、分阶段可验、不自动跑全量索引(`workflow §11`)、web 改 schema 走 `pnpm run api` 链路、复用双 store 模型不另造真值源。
- **继承关系**:P1/P4 = roadmap-2026-06-07 Phase 10;P2 = roadmap-2026-06-10 多组织 Phase 2(解冻);P3 = roadmap-2026-06-10 多仓 Phase 2(解冻)。

---

## P1 — onboard 接入简化(加项目 8 步→1 步)【最高优先,最轻】

> ✅ **已交付(2026-06-14, `76d5ea8`)**。代码核实发现 `ops/onboard.py` **命令已存在**(config/project.json/meta/RBAC/codegraph/reindex 全有,且分层 关键步停/软步 warn)→ 实际工作 = 补 2 个软步(sync rules/skills/hooks + 生成 .mcp.json)+ 抽 repo-aware 公共核心(`sync_resources_to`/`_apply_grep_hook`/`build_mcp_servers`)复用不复制。WSL 真机验证:sync 真拷 9 rules/3 skills/1 hook + settings grep merge;`.mcp.json` 4 套真平台端点。**又一个"代码核实避免从零重造"的例子**。

**痛点**:加入一个新业务仓现在要 ~8-10 步手动命令(`init`→`register`→`sync-rules/skills/hooks`→`codegraph link`→手配 `.mcp.json`→`reindex`→`serve-mcp`),散在文档各处。

**目标**:一条 `codev-platform onboard <pid>` 把**项目级接入**串成一步。

**设计(薄编排壳,不堆码)**:
| 分层 | 进 onboard | 理由 |
|---|---|---|
| init + register + sync-rules/skills/hooks + codegraph link | ✅ 自动(调现有 `cmd_*`)| 项目级、幂等、纯文件操作 |
| 生成 `.mcp.json`(从 config 端口派生指向平台端点)| ✅ 自动 | 接入必需,现靠手抄 |
| 索引(reindex chroma/codegraph/graph)| 🟡 `--index` 才走 worker enqueue | 重操作 + `§11` 禁自动全量;非阻塞 |
| serve-mcp start | ❌ 只提示 | 机器级一次性(已有 `setup` 命令管机器级)|

- **形态**:`onboard <pid> [--repo .] [--index] [--dry-run]` → 逐步打印 ✓ + 末尾打印剩余手动项。
- **纪律**:onboard 只管**顺序 + 进度 + 失败可续**(每步现有命令已幂等),~60 行薄壳;机器级 vs 项目级分开;不做 web 向导/配置 DSL(过度)。
- **验证**:`--dry-run` 单测 + 真机接入一个 throwaway 测试项目走通全链。

---

## P2 — 多组织 web 签发 UI(加用户简化)【解冻 06-10 多组织 Phase 2】

> 🔬 **2026-06-14 两兄弟代码核实修正范围(又是"大部分已存在")**:建 org/用户/改密/角色 **web 路由 + 前端页全已存在**(`orgs.py`+`users.py`+`pages/{orgs,users}`,且已接 `_guard_home_org`/`_guard_org_member` 安全闸);项目↔org 绑定也有(`projects.create` 收 orgId)。**唯一真缺口 = token 签发 web 化**(现仅 CLI `gateway pg-token-add`)。→ 范围从"中(net-new users/orgs 页)"**收窄到"轻:1 路由 + 1 token 页 + 抽共享 issue service"**。**刻意不做**:审批流/批量导入/角色矩阵编辑器/token 自助轮换/不重做已存在页。
>
> 🚧 **进度(2026-06-14)**:① **`gateway/token_issue.issue_token` 共享纯函数已交付**(`379f42c`,CLI `_cmd_pg_token` 改用之去重,web 路由下一步复用)。② **web 路由设计就绪**(复用点+5 红线落点定死):`schemas/tokens.py` + `services/token_service.py`(复用 `UserService._guard_org_member` + `issue_token` + `PgTokenStore`)+ `routes/tokens.py`(**`org_id=sess.org_id` 不由 client**,`require_org_role("admin")`)+ app.py 注册 + 越权单测 + 前端 `pages/tokens/` + `pnpm run api`。**剩余**:web 路由后端 4 文件 + 越权单测 + 前端页(WSL run api)—— 安全敏感 L4,建议清爽 context 实现。

**现状**(06-10 Phase 1 已建,agent 核实):后端 RbacStore/account_store/PgTokenStore + CLI `org create/add-user/set-password` + `gateway pg-token-*` 全有。**缺**:仅 token 签发的 web 路由 + 前端页(其余 org/user 管理 web 化已完成)。

**目标**:web 控制台建 org / 建用户 / 发 token / 项目→org 绑定,替代 CLI 手敲。

**设计(复用现有 store,不另造真值源)**:
- **后端**:补 user/org/token 管理路由,**复用** `account_store`(身份+密码)/`RbacStore`(授权)/`PgTokenStore`(token)双 store 模型(见记忆 account-rbac-two-store-model),不新建表/store。
- **前端**:users/orgs 管理页 + token 签发表单 + 项目→org 绑定;改 schema 后走 `web 改 schema → 重启 codev-web → pnpm run api` 链路(见记忆 authorized-web-schema-runapi-chain)。
- **安全红线**:全局身份变更(密码/状态)走 `_guard_home_org`(防跨 org 凭据劫持,见 rbac-multi-org-membership-model);token 明文不落库;org_id 取认证身份不由 client 传。
- **范围**:先做"建用户/发 token/绑 org"核心流;不做审批流/批量导入(过度)。
- **验证**:web 路由越权用例(跨 org 拒)+ 前端 tsc + 真机浏览器走通建用户→发 token→该 token 召回。

---

## P3 — 多仓多根索引 fan-out(让多仓项目完整可查)【解冻 06-10 多仓 Phase 2,最重】

> ✅ **进度(2026-07-07)**:核心 fan-out 闭环完成。`0812b8b` 建 RepoSpec 真值源 + code_vec/recall 多仓 fan-out + reindex 逐仓 codegraph sync;`0a94264` 做 extra repo 变更反向触发父项目 reindex;`d00de6f` 做 agent 内置 `codegraph_search/callers/callees/trace` 跨仓查询。今日补齐审计缺口:tagged path 文件工具、rerank 反解 `extra::ref`、集中式 codegraph fallback、extra repo sync freshness、git 输出脱敏和边界补测;随后补运行治理:PG queue webhook fail-closed、extra repo webhook health/startup 校验、fan-out metrics/alert;Web GraphAPI codegraph 六个只读接口已接入多仓 fan-out;外部 `codegraph.server` MCP 代理按 repo 分段合并多后端。验证 `98 passed` + `50 passed` + `13 passed` + `49 passed` + `py_compile` + `git diff --check`。剩余:真实多仓项目端到端验收和按实际输出优化结构化 merge。

**现状**:graph ingest 已支持 `extra_repos`(多仓前端→后端跨仓连边,RepoScope 已建);**但 codegraph / code_vec 只扫主仓**(daily-06-13:codegraph sync 按设计只扫主仓),多仓项目的关联仓代码 `code_recall` 召不到。

**目标**:codegraph / code_vec 也按 `meta.repos[]` 多根 fan-out,多仓项目关联仓代码完整可索引可召回。

**设计(复用 core/repos 单一真值源)**:
- **真值源**:复用 `core/repos.project_repo_roots`(主仓+extra_repos,已建)。
- **codegraph fan-out**:每登记仓各跑一次 codegraph sync,索引落各自或合并(需定:合并 db vs per-repo db + 召回时合并)。
- **code_vec fan-out**:`build_code_vector_index` 枚举多根 codegraph 节点(RepoScope tag 区分)。
- **召回合并**:recall service 跨仓 lane 结果合并(ref 空间用 RepoScope tag 防碰撞)。
- **风险**:codegraph 是外部工具(junction 机制),多根 junction/索引架构改动较大 → **本项最重,分阶段**:先 code_vec 多根(纯本仓代码)→ 再 codegraph 多根(碰外部工具)。
- **验证**:多仓测试项目(如 ideas-v2 + ideas-pda-app)`code_recall` 召回到关联仓代码。

---

## P4 — Phase 10 治理收尾(接入指南 + 能力矩阵 + 治理看板)【轻,文档+聚合】

> ✅ **已交付(2026-06-14)**。代码核实又是"大部分已存在",三子项诚实修正:
> - **接入指南**:`docs/onboarding-new-project.md` 已存在但 **stale**(讲老手动 8 步 + stdio .mcp.json)→ **重写成一键 `onboard` 流程**(配套 P1)+ SSE 服务模式。✅ 真活,做完。
> - **能力矩阵 enabled**:❌ **伪需求**。`describe_capabilities` 是**语言栈插件(默认全开)**,加 enabled 恒 True 无意义;CLI `plugins list` 已展示栈能力。**不做**。
> - **治理看板**:✅ **已存在且完整**。`web-ui/src/pages/dashboard/index.tsx` 已组合 IndexFreshness(P1)/GraphHealth(P3)/SoftQuality/TokenUsage(P8)/McpUsage + 平台健康 + 图谱统计 5+ 卡片。唯一缺 recall 延迟卡(Phase 8 数据无 web 端点)→ 看板已基本全 + CLI `recall-stats` 可看,**记可选触发不堆码**。

**目标**:让接入者"看得懂平台支持什么、索引/图谱健不健康"。

**设计(派生视图 + 文档,几乎不加机制)**:
- **接入指南**:docs/,**引用** `describe_capabilities()` 作"平台支持哪些栈"真值源(不重写);把 P1 的 onboard 流程写成 onboarding 文档。
- **能力矩阵视图**:`describe_capabilities()` 加 `enabled` 列(能力 ∪ config 状态聚合一处)。
- **治理看板**:前端 governance 页**组合已有卡片**(index status + graph audit + soft-quality + token usage + recall-stats),不新建后端 = 派生视图非新真值源。
- **范围**:不做新指标/新存储,纯聚合+文档。
- **验证**:接入指南跟 onboard 实测对齐;看板前端 tsc。

---

## 五、优先级与依赖

| 序 | 项 | 理由 | 重量 |
|---|---|---|---|
| 1 | **P1 onboard** | 直击痛点、最轻、纯编排现有、解锁后续接入演示 | 轻(~60 行)|
| 2 | **P4 Phase 10 治理** | 文档+聚合、轻、和 P1 配套(onboard 流程写进接入指南)| 轻 |
| 3 | **P2 web 签发 UI** | 加用户简化、复用现有 store、net-new 前端 | 中 |
| 4 | **P3 多根索引** | 最重(碰 codegraph 外部工具架构),分阶段 | 重 |

**依赖**:P4 接入指南依赖 P1 onboard 定型;P2/P3 相互独立;P3 的 code_vec 多根可先于 codegraph 多根。

## 六、共同纪律(每项 PR 自检)

- [ ] 编排/复用现有扩展点,不堆新逻辑、不另造真值源(store/config 键/能力视图)
- [ ] 不自动跑全量索引(`§11`);重操作走 worker enqueue + 提示
- [ ] web 改 schema → 重启 codev-web → `pnpm run api`(不手写 API 层)
- [ ] 多组织安全红线:全局身份走 home_org 闸 / token 不落库 / org_id 取认证身份
- [ ] 分阶段可验:每项有 dry-run/单测 + 真机走通
- [ ] commit 无 AI 痕迹;文件 ≤600 行

## 七、验证矩阵

| 项 | 最小验证 |
|---|---|
| P1 onboard | `--dry-run` 单测 + 真机 throwaway 项目全链 |
| P2 web 签发 | 越权用例 + tsc + 真机建用户→发 token→召回 |
| P3 多根索引 | 多仓测试项目 code_recall 召回关联仓代码 |
| P4 治理 | 接入指南对齐 onboard + 看板 tsc |
