# AI 入口文档瘦身与全栈工作流改造方案

> 日期：2026-05-24
> 范围：`AGENTS.md`、`CLAUDE.md`、各子模块 `CLAUDE.md`、`.claude/rules/workflow.md`、pre-push 机器红线。
> 目标：降低长会话 token 成本，减少规则漂移，同时不削弱 Java / Python / Web / DB / AI 工具链的全栈安全边界。

---

## 1. 背景

当前 AI 协作入口文件存在职责混杂：

- `AGENTS.md` 同时写行为准则、MCP、命令、规则索引、代码风格、红线和参考来源。
- `CLAUDE.md` 同时写角色、历史进度、路线图、命令、规则、工作流和子模块说明。
- 子模块 `CLAUDE.md` 又重复写架构、编码习惯和命令。
- 同一条规则在入口文件、`.claude/rules/`、子模块规则中多处出现，容易漂移。
- 高风险全栈改动缺少统一的“改前 / 改中 / 改后”工作流文件。

`D:\FrontendWorkJusd\kms-web\.claude\rules\workflow.md` 提供了有价值的工作流结构，但它是单前端项目，不能直接照搬。本仓库是全栈系统，必须覆盖：

- Java DTO / Controller / Swagger / Mapper
- React 前端生成 API / 页面 / 枚举
- Python Pipeline 写库 / 跑批 / 幂等
- Flyway / 表结构 / DB 约束
- cross-link / codegraph / Chroma 等 AI 工具索引
- PIT、影子隔离、资金语义、快照三件套等业务红线

---

## 2. 改造目标

核心原则：

> 入口短，规则集中，工作流独立，硬红线脚本化。

目标效果：

- `AGENTS.md`：给 Codex / 其它 agent 的短入口，保留 Codex 必要 inline 规则。
- `CLAUDE.md`：给 Claude Code 的短入口，只做路由和当前项目入口。
- `.claude/rules/workflow.md`：新增全栈协作工作流，承接改前门禁、跨层边界、验证矩阵。
- `.claude/rules/*.md`：继续作为具体业务规则真值源。
- 子模块 `CLAUDE.md`：改成规则路由表，不再写长篇教程。
- `tools/dev/pre-push-audit.ps1`：承接低误报硬红线。

---

## 3. 最终文档结构

```text
<repo-root>
├── AGENTS.md
│   └── 所有 agent 入口。Codex 不展开 @，所以保留少量 inline 核心规则。
├── CLAUDE.md
│   └── Claude Code 入口。只做路由和当前工作入口。
├── .claude/rules/
│   ├── workflow.md                  # 新增：全栈协作工作流
│   ├── ai-tools-mcp.md              # MCP 触发规则
│   ├── verification-checklist.md     # 验证清单
│   ├── api-contracts.md
│   ├── shadow-isolation.md
│   ├── model-field-consistency.md
│   └── ...
├── .claude/skills/
│   └── 多步流程真值源
├── apps/stock-admin-web/CLAUDE.md
│   └── 前端规则路由表
├── apps/stock-admin-api/CLAUDE.md
│   └── Java API 规则路由表
├── python/stock-pipeline/CLAUDE.md
│   └── Python Pipeline 规则路由表
└── tools/dev/
    ├── pre-push-audit.ps1
    ├── ai-health.ps1
    └── dirty-index-check.ps1
```

---

## 4. 文件职责边界

| 文件 | 职责 | 不再负责 |
|---|---|---|
| `AGENTS.md` | 给 Codex / 其它 agent 的最小入口和硬规则 | 不展开所有业务规则正文 |
| `CLAUDE.md` | 给 Claude Code 的短入口、路由、当前工作入口 | 不保存长历史、完整路线图 |
| `.claude/rules/workflow.md` | 全栈协作流程：改前、改中、改后、边界、验证 | 不写具体业务规则 |
| `.claude/rules/*.md` | 具体业务红线和规范 | 不写入口说明 |
| 子模块 `CLAUDE.md` | 本模块规则路由表和命令 | 不写长架构教程 |
| `pre-push-audit.ps1` | 低误报硬红线拦截 | 不做语义审查 |

---

## 5. 新增 `.claude/rules/workflow.md`

这是本次改造的核心。它吸收 `kms-web` 的协作纪律，但内容必须全栈化。

### 5.1 建议结构

```md
# 全栈协作工作流规范

本文档约定 AI 在本仓库改动前 / 中 / 后的协作流程。
具体编码规则在 `.claude/rules/*.md` 和各子模块 `.claude/rules/*.md`。
本文件只写跨 Java / Python / Web / DB / AI 工具链都适用的工作流。

---

## 1. 不要读取的文件

默认不要读取这些文件，除非用户明确要求或排查目标直接指向它们：

- 前端构建物：`apps/stock-admin-web/dist/`、`.umi/`、`.umi-production/`、`node_modules/`
- Java 构建物：`apps/stock-admin-api/target/`
- Python 缓存：`__pycache__/`、`.pytest_cache/`
- AI 索引产物：`.codegraph/`、`data/chroma/`、`data/codegraph_ext/*.sqlite*`
- 大型数据库文件 / dump：`database/init/*.gz`、大型 `*.sql`
- 依赖锁文件：默认不读 `pnpm-lock.yaml`；只有排查依赖版本、安装冲突、锁文件变更时读取
- 历史归档：`archive/`、历史 incident、大型日志；只有追溯事故时读取
- 生成 API 文件全文：默认不读全文，查接口签名可定向读取

---

## 2. 可读但默认禁改

以下文件可读，但默认不修改。确需修改时必须先说明原因和影响范围。

### 2.1 前端生成和全局边界

- `apps/stock-admin-web/src/services/apis/**`
  - Swagger 生成文件，禁止手改
  - 字段不对改后端 Swagger / DTO，或在后端启动后重新生成 API

- `apps/stock-admin-web/src/models/enumslocal.tsx`
  - 枚举参考文件，禁止手改
  - 枚举变更走后端枚举真值源和生成流程

- `apps/stock-admin-web/src/app.tsx`
- `apps/stock-admin-web/src/models/**`
- `apps/stock-admin-web/src/utils/fetch/**`
- `apps/stock-admin-web/config/routes.ts`
  - 全局运行时、状态、请求、路由边界
  - 改前必须说明影响范围

### 2.2 数据库边界

- 已发布 Flyway migration
  - 禁止回改历史 migration
  - 需要修正时新增 migration

- `database/init/**`
  - 初始化 dump / 快照，不因业务代码改动顺手修改

### 2.3 依赖和工具边界

- `package.json` / `pnpm-lock.yaml`
- `pom.xml`
- `requirements.txt`
- `.mcp.json`
- `tools/dev/*.ps1`
- `tools/cross_link/**`
- `tools/chroma/**`

这些文件会影响全局工具链或依赖，改前必须说明原因、风险和验证方式。

---

## 3. 改动前门禁

涉及修改文件前，必须先完成门禁。

### 3.1 判断触及层

先判断本次触及哪些层：

- Web 前端
- Java API
- Python Pipeline
- DB / Flyway
- 跨层契约
- AI 工具链
- 文档 / 规则

### 3.2 读取规则

按触及层读取规则：

- Web：`apps/stock-admin-web/CLAUDE.md` + 相关 `.claude/rules/*.md`
- Java：`apps/stock-admin-api/CLAUDE.md` + 相关 `.claude/rules/*.md`
- Python：`python/stock-pipeline/CLAUDE.md` + 相关 `.claude/rules/*.md`
- DB / Flyway：根 `.claude/skills/add-flyway-migration/SKILL.md` + 相关 DB 规则
- 跨层契约：`.claude/rules/api-contracts.md`、`.claude/rules/model-field-consistency.md`、`.claude/rules/ai-tools-mcp.md`
- AI 工具链：`.claude/rules/ai-tools-mcp.md` + 对应工具源码

优先通过 `platform-docs` 检索；不可用时本地读取文件兜底。

### 3.3 门禁声明

首次修改前，用一句话说明：

- 本次触及层
- 已读取 / 适用的规则
- 关键约束
- 验证方式

示例：

> 本次触及 Java Mapper + cross-link 索引工具；已读 Java SQL 规则和 ai-tools-mcp；关键约束是不回改历史 Flyway、不破坏表引用索引；验证跑 cross-link tests + ai-health。

同一轮任务范围未变化时，不重复读规则；范围变化或触及新边界时补读并重新声明。

### 3.4 未过门禁不得修改

如果发现已经在未读规则或未声明边界的情况下改了文件，必须停止继续扩散，先说明：

- 已发生的不合规点
- 影响文件
- 建议处理方式

未经用户确认，不继续叠改。

---

## 4. 跨层改动定义

出现任一情况，即视为跨层改动：

- Java DTO / Controller / Swagger 响应字段变化
- 前端 API 调用参数或响应字段变化
- Python 写库字段变化
- Flyway 表结构、索引、约束变化
- 枚举新增、删除、改名、改值
- 推荐结果、交易计划、回测、影子规则、资金语义相关字段变化
- endpoint / Mapper / table / Python repository 链路变化
- AI 索引工具影响代码理解结果

跨层改动必须做影响面检查：

- 查规则：`platform-docs`
- 查调用链：`codegraph`
- 查 endpoint / Mapper / Flyway / table / Python 链路：`cross-link`
- MCP 不可用时说明一次，并用本地搜索兜底

---

## 5. 多步任务

3 步以上任务必须先列简短计划：

1. 目标
2. 文件范围
3. 验证方式

计划应短，不写长篇背景。

---

## 6. 改动中原则

- 只改本次任务范围
- 不顺手重构、格式化、清理无关代码
- 新增 / 修改行必须遵守当前 rules
- 存量违规不主动扩散
- 生成代码不手改
- 已发布 migration 不回改
- AI 索引产物不手改，只通过脚本重建
- 必须跨层同步时，先列受影响文件

---

## 7. 存量代码与 rules 冲突

当前 rules 是新增代码和本次改动行的规范基线。

- 新文件 / 新模块：严格遵守 rules
- 存量文件新增 / 修改行：必须遵守 rules
- 必须修改的旧行：只做最小修正
- 未触碰旧违规：不主动治理，不顺手格式化
- 扩散会明显变大时，先让用户选择范围

范围选择格式：

```text
发现扩散点，请选择：
[A] 仅本次需求最小改动
[B] 本次需求 + 直接受影响调用方
[C] 完整治理
[D] 用户指定范围

推荐：[X]，原因：...
```

---

## 8. 验证矩阵

| 改动类型 | 最小验证 |
|---|---|
| Web 页面 / 组件 | `pnpm --dir apps/stock-admin-web exec eslint <files>` |
| Web API 调用 | ESLint + 确认生成 API 类型 |
| Java Mapper / Service | `mvn -f apps/stock-admin-api/pom.xml compile` 或相关 test |
| Java DTO / Controller | backend-dto-change skill + compile / test |
| Flyway | 新增 migration + Java compile + cross-link 检查 |
| Python repository / job | `python -m pytest python/stock-pipeline/tests/<target>` |
| Python 跑批写侧 | pytest + 幂等 / 同日重跑规则检查 |
| 枚举 | add-enum skill + 前后端生成链路检查 |
| endpoint / SQL / Mapper 链路 | cross-link 查询或重建 |
| AI 工具脚本 | 对应 tests + `tools/dev/ai-health.ps1` |
| 文档 / 规则 | 引用路径存在 + pre-push audit |

无法验证时必须说明原因。

---

## 9. 错误处理

- 不重复跑同一个失败命令
- 不用 `--no-verify`、`eslint-disable`、`ts-ignore` 绕过
- lint / type / test 报错先看根因
- DB / migration 错误优先保护数据一致性
- AI 索引重建失败先看锁、dirty 状态和日志，不手删 sqlite
- MCP 不可用只说明一次，不反复重试
- 发现用户未提交改动，不覆盖、不回滚

---

## 10. 改动后输出

最终输出保持短而具体：

- 改了什么
- 验证了什么
- 剩余风险 / 未验证项

涉及文件修改时，说明本次实际检查过的关键规则；未验证必须说明原因。
```

---

## 6. 重写 `AGENTS.md`

`AGENTS.md` 面向 Codex / 其它 agent。它不能完全依赖 `@`，因为 Codex 不展开引用，所以必须保留少量 inline 核心规则。

### 6.1 目标

- 目标行数：100-160 行
- 保留 Codex 适配说明
- 移除长规则正文、长命令清单、历史参考

### 6.2 建议内容

```md
# AGENTS.md

> AI agent 协作入口。规则真值源在 `.claude/rules/` 和各子模块 `.claude/rules/`。
> Codex / 其它 agent 不会自动展开 `@xxx.md`，必须按场景显式读取规则文件。

---

## 0. 必读核心

- 默认简洁回答；涉及改代码、跨模块、API、DB、安全、线上风险时必须说明检查和风险。
- 不回滚用户未明确要求回滚的改动。
- Windows PowerShell 读中文 Markdown 必须加 `-Encoding UTF8`。
- 修改文件前必须完成工作流门禁，见 `.claude/rules/workflow.md`。
- 子模块代码改动前必须读取子模块 `CLAUDE.md` 和相关 rules，或通过 `platform-docs` 检索。
- 生成代码、已发布 migration、AI 索引产物默认禁手改。
- MCP 不可用时说明一次，退回本地文件和搜索兜底。

---

## 1. 项目入口

- Web: `apps/stock-admin-web/CLAUDE.md`
- Java API: `apps/stock-admin-api/CLAUDE.md`
- Python Pipeline: `python/stock-pipeline/CLAUDE.md`

---

## 2. 工作流

- 全栈协作流程：`.claude/rules/workflow.md`
- 验证清单：`.claude/rules/verification-checklist.md`
- MCP 指南：`.claude/rules/ai-tools-mcp.md`
- 提交规范：`.claude/rules/commit-pr-conventions.md`

Codex 执行时必须显式读取这些文件；不要假设 `@` 引用已展开。

---

## 3. MCP 使用

| 场景 | 工具 |
|---|---|
| 改子模块代码前查规则 | `platform-docs` |
| 找符号 / 调用链 / 影响范围 | `codegraph` |
| 查 endpoint / Mapper / Flyway / table / Python 链路 | `cross-link` |
| MCP 超时 / 不可用 | 说明一次，本地规则 + 搜索兜底 |

---

## 4. Skills

| 场景 | 入口 |
|---|---|
| 后端 DTO / 接口改动 | `.claude/skills/backend-dto-change/SKILL.md` |
| 新增业务枚举 | `.claude/skills/add-enum/SKILL.md` |
| 新增 Flyway | `.claude/skills/add-flyway-migration/SKILL.md` |
| 新增 Python Job | `.claude/skills/add-python-job/SKILL.md` |
| 新增前端页面 | `.claude/skills/add-frontend-page/SKILL.md` |

---

## 5. 常用命令

```powershell
# Java
mvn -f apps/stock-admin-api/pom.xml compile
mvn -f apps/stock-admin-api/pom.xml test

# Web
pnpm --dir apps/stock-admin-web exec eslint <file>
pnpm --dir apps/stock-admin-web run lint:fix

# Python
python -m pytest python/stock-pipeline/tests/
python -m stock_pipeline.config.validate

# AI 工具
powershell -ExecutionPolicy Bypass -File tools/dev/pre-push-audit.ps1
powershell -ExecutionPolicy Bypass -File tools/dev/dirty-index-check.ps1
powershell -ExecutionPolicy Bypass -File tools/dev/ai-health.ps1
```

---

## 6. 高优先级规则索引

- API 契约：`.claude/rules/api-contracts.md`
- 字段一致性：`.claude/rules/model-field-consistency.md`
- 影子隔离：`.claude/rules/shadow-isolation.md`
- 同日重跑：`.claude/rules/same-day-rerun.md`
- 资金语义：`.claude/rules/capital-amount-semantics.md`
- 快照三件套：`.claude/rules/snapshot-trio-write.md`
- 百分比符号：`.claude/rules/pct-sign-convention.md`
- MCP 工具：`.claude/rules/ai-tools-mcp.md`
- Windows PowerShell：`.claude/rules/windows-powershell.md`

---

## 7. 子模块规则

- Web rules: `apps/stock-admin-web/.claude/rules/`
- Java rules: `apps/stock-admin-api/.claude/rules/`
- Python rules: `python/stock-pipeline/.claude/rules/`

处理子模块代码时，以子模块规则为准。
```

### 6.3 从当前 `AGENTS.md` 删除 / 下沉

- 详细后端 / 前端 / Python 命令长清单
- 联调 / 灌数据详细流程
- 大量 `@.claude/rules/*.md` 引用列表
- 代码风格正文
- 参考来源和历史说明
- 变更日志

这些内容应下沉到 `.claude/rules/`、子模块 `CLAUDE.md` 或 runbook。

---

## 7. 重写根 `CLAUDE.md`

根 `CLAUDE.md` 面向 Claude Code，可以更短，因为 Claude Code 能展开 `@`。

### 7.1 目标

- 目标行数：80-120 行
- 保留角色、工作流、子模块入口、MCP、skills、常用命令、当前规划入口
- 删除历史进度正文和长路线图

### 7.2 建议内容

```md
# CLAUDE.md

> Claude Code 工作入口。规则真值源在 `.claude/rules/`，流程见 `.claude/rules/workflow.md`。
> 历史进度看 `docs/architecture/changelog.md`；当前规划看 `docs/architecture/roadmap-2026-05-23/README.md`。

---

## 1. 角色

@.claude/rules/roles-5-perspectives.md

---

## 2. 工作流

@.claude/rules/workflow.md

---

## 3. 子模块入口

- Web: `apps/stock-admin-web/CLAUDE.md`
- Java API: `apps/stock-admin-api/CLAUDE.md`
- Python Pipeline: `python/stock-pipeline/CLAUDE.md`

---

## 4. MCP

@.claude/rules/ai-tools-mcp.md

---

## 5. Skills

| 场景 | Skill |
|---|---|
| DTO / API 改动 | `.claude/skills/backend-dto-change/SKILL.md` |
| 新增枚举 | `.claude/skills/add-enum/SKILL.md` |
| 新增 Flyway | `.claude/skills/add-flyway-migration/SKILL.md` |
| 新增 Python Job | `.claude/skills/add-python-job/SKILL.md` |
| 新增前端页面 | `.claude/skills/add-frontend-page/SKILL.md` |
| 验证跑批 | `.claude/skills/verify-pipeline-run/SKILL.md` |
| AI 工具体检 | `.claude/skills/ai-health/SKILL.md` |
| 更新 AI 索引 | `.claude/skills/update-local-ai/SKILL.md` |

---

## 6. 常用命令

```powershell
mvn -f apps/stock-admin-api/pom.xml compile
mvn -f apps/stock-admin-api/pom.xml test
pnpm --dir apps/stock-admin-web exec eslint <file>
python -m pytest python/stock-pipeline/tests/
powershell -ExecutionPolicy Bypass -File tools/dev/pre-push-audit.ps1
powershell -ExecutionPolicy Bypass -File tools/dev/ai-health.ps1
```

---

## 7. 高优先级规则索引

- `.claude/rules/api-contracts.md`
- `.claude/rules/model-field-consistency.md`
- `.claude/rules/shadow-isolation.md`
- `.claude/rules/same-day-rerun.md`
- `.claude/rules/capital-amount-semantics.md`
- `.claude/rules/snapshot-trio-write.md`
- `.claude/rules/pct-sign-convention.md`
- `.claude/rules/not-null-write-guard.md`
- `.claude/rules/business-sanity-alerts.md`
- `.claude/rules/security.md`

---

## 8. 当前规划入口

- 当前 roadmap: `docs/architecture/roadmap-2026-05-23/README.md`
- 历史 changelog: `docs/architecture/changelog.md`
- AI 工具链说明: `docs/dev-evolution/ai-toolchain-guide.md`
```

### 7.3 从当前 `CLAUDE.md` 下沉

- “当前进度”整个大段 → `docs/architecture/changelog.md`
- “关键设计文档大表” → roadmap README 或 `project-structure.md`
- “M3-M6 节奏” → roadmap README
- 长命令清单 → 子模块 `CLAUDE.md` 或 runbook
- 派 agent 三段式 → 并入 `workflow.md` 的 subagent 小节

---

## 8. 子模块 `CLAUDE.md`

统一改成规则路由表。

### 8.1 Web：`apps/stock-admin-web/CLAUDE.md`

```md
# CLAUDE.md — stock-admin-web

> 前端子模块入口。具体规则在 `.claude/rules/`；全栈流程见仓库根 `.claude/rules/workflow.md`。

---

## 改前必查

| 场景 | 必读 |
|---|---|
| 通用质量 | `.claude/rules/code-quality.md` |
| React / Hooks | `.claude/rules/react-patterns.md` |
| 组件 / 表格 / Drawer / Modal | `.claude/rules/component-patterns.md` |
| Antd 6 | `.claude/rules/antd6-adapt.md` |
| API 调用 | `.claude/rules/api-service.md` |
| 样式 | `.claude/rules/styles.md` |
| 命名 | `.claude/rules/component-naming.md` |
| 涨跌 / PnL 颜色 | `.claude/rules/stock-color-convention.md` |

---

## 硬红线

- 不使用 `useRequest`
- 不手改 `src/services/apis/**`
- 不手改 `src/models/enumslocal.tsx`
- 时间显示直接用 `dayjs(value).format('YYYY/MM/DD ...')`
- 不使用 `@/utils/datetime`
- JSX props 不传内联函数；需要 `useCallback`
- 样式优先 UnoCSS 原子类

---

## 命令

```powershell
pnpm --dir apps/stock-admin-web exec eslint <file>
pnpm --dir apps/stock-admin-web run lint:fix
pnpm --dir apps/stock-admin-web run api
pnpm --dir apps/stock-admin-web run enums
```

`api` / `enums` 会生成文件，运行前需确认时机。
```

### 8.2 Java：`apps/stock-admin-api/CLAUDE.md`

```md
# CLAUDE.md — stock-admin-api

> Java API 子模块入口。具体规则在 `.claude/rules/`；全栈流程见仓库根 `.claude/rules/workflow.md`。

---

## 改前必查

| 场景 | 必读 |
|---|---|
| 通用质量 | `.claude/rules/code-quality.md` |
| SQL / Mapper | `.claude/rules/sql-patterns.md` |
| 枚举 | `.claude/rules/enum-patterns.md` + 根 `add-enum` skill |
| DTO / API | 根 `backend-dto-change` skill |
| 注释 | `.claude/rules/comments.md` |
| Flyway | 根 `add-flyway-migration` skill |

---

## 硬红线

- 不回改已发布 Flyway migration
- Mapper NULL 判断遵守 PostgreSQL cast 规则
- DTO / Controller 字段变化必须同步前端生成链路
- 表字段变化必须查 cross-link
- 资金、影子、PIT、快照字段必须读根红线规则
- 不绕过测试和 pre-push audit

---

## 命令

```powershell
mvn -f apps/stock-admin-api/pom.xml compile
mvn -f apps/stock-admin-api/pom.xml test
mvn -f apps/stock-admin-api/pom.xml -Dtest=<TestName> test
```
```

### 8.3 Python：`python/stock-pipeline/CLAUDE.md`

```md
# CLAUDE.md — stock-pipeline

> Python Pipeline 子模块入口。具体规则在 `.claude/rules/`；全栈流程见仓库根 `.claude/rules/workflow.md`。

---

## 改前必查

| 场景 | 必读 |
|---|---|
| 通用质量 | `.claude/rules/code-quality.md` |
| 测试 | `.claude/rules/testing.md` |
| 跑批 / Job | `.claude/rules/pipeline-patterns.md` + 根 `add-python-job` skill |
| 注释 | `.claude/rules/comments.md` |
| 文件纪律 | 根 `.claude/rules/file-discipline.md` |
| 同日重跑 / 幂等 | 根 `.claude/rules/same-day-rerun.md` |

---

## 硬红线

- 业务代码不使用 `print`
- public 方法有 docstring
- 写库字段必须和 Flyway / Java Entity / DTO 一致
- 跑批写侧必须考虑幂等和同日重跑
- 真实行情抓取改动必须考虑集成测试红线
- PIT / 影子 / 资金语义字段必须读根红线规则

---

## 命令

```powershell
python -m pytest python/stock-pipeline/tests/<target>
python -m pytest python/stock-pipeline/tests/
python -m stock_pipeline.config.validate
```
```

---

## 9. pre-push 机器化红线

文档瘦身后，硬规则要尽量机器化，避免安全性下降。

### 9.1 Hard fail：低误报规则

- 前端禁止 `@/utils/datetime`
- 前端禁止 `useRequest`
- 前端业务代码禁止 `YYYY-MM-DD HH`
- Java Mapper 禁止 `#{x} is null`
- Python 业务代码禁止 `print(`
- 生成 API 文件禁止手改（可基于 diff 范围实现）

### 9.2 Warn：高误报规则

- `Record<string,string>`
- TODO
- 大文件行数
- 临时 mock

`Record<string,string>` 不建议 hard fail，因为 UI 颜色映射、tag 文案映射、enum fallback map 都是合法场景。

---

## 10. 实施拆分

不要一次性全改，推荐 4 个 commit。

### Commit 1：新增全栈 workflow

```text
docs(rules): add fullstack AI workflow
```

文件：

- `.claude/rules/workflow.md`

### Commit 2：瘦根入口

```text
docs(agents): slim root AI entry docs
```

文件：

- `AGENTS.md`
- `CLAUDE.md`

### Commit 3：瘦子模块入口

```text
docs(claude): slim submodule rule routing docs
```

文件：

- `apps/stock-admin-web/CLAUDE.md`
- `apps/stock-admin-api/CLAUDE.md`
- `python/stock-pipeline/CLAUDE.md`

### Commit 4：补机器红线

```text
chore(audit): enforce high-signal workflow red-lines
```

文件：

- `tools/dev/pre-push-audit.ps1`

如果 Gate 5 已经提交，Commit 4 可以只补 `YYYY-MM-DD HH` 或暂缓。

---

## 11. 验收标准

每个 commit 后运行：

```powershell
powershell -ExecutionPolicy Bypass -File tools\dev\pre-push-audit.ps1
powershell -ExecutionPolicy Bypass -File tools\dev\ai-health.ps1
```

引用存在检查：

```powershell
Test-Path .claude\rules\workflow.md
Test-Path .claude\rules\ai-tools-mcp.md
Test-Path .claude\rules\verification-checklist.md
Test-Path apps\stock-admin-web\CLAUDE.md
Test-Path apps\stock-admin-api\CLAUDE.md
Test-Path python\stock-pipeline\CLAUDE.md
```

入口文件行数检查：

```powershell
Get-Content AGENTS.md -Encoding UTF8 | Measure-Object -Line
Get-Content CLAUDE.md -Encoding UTF8 | Measure-Object -Line
Get-Content apps\stock-admin-web\CLAUDE.md -Encoding UTF8 | Measure-Object -Line
Get-Content apps\stock-admin-api\CLAUDE.md -Encoding UTF8 | Measure-Object -Line
Get-Content python\stock-pipeline\CLAUDE.md -Encoding UTF8 | Measure-Object -Line
```

目标行数：

| 文件 | 目标行数 |
|---|---:|
| `AGENTS.md` | 100-160 |
| `CLAUDE.md` | 80-120 |
| Web `CLAUDE.md` | 60-100 |
| Java `CLAUDE.md` | 60-100 |
| Python `CLAUDE.md` | 60-100 |
| `workflow.md` | 180-260 |

---

## 12. 预期收益

保守估算：

- 根 `CLAUDE.md` 从约 14K 字符降到约 3K。
- `AGENTS.md` 从约 9K 字符降到约 4K。
- 三个子模块 `CLAUDE.md` 从 5-9K 字符降到约 2K。
- 长会话上下文成本下降约 15-30%。
- 规则漂移下降，因为正文集中在 rules。
- 高风险全栈改动更稳，因为 `workflow.md` 明确了跨层门禁和验证矩阵。

---

## 13. 关键取舍

`kms-web` 提供的是协作纪律模板，不是内容模板。

本仓库的工作流必须围绕全栈风险：

- Java DTO / Swagger / 前端生成 API 一致
- Flyway / Java Entity / Python 写库字段一致
- Mapper / endpoint / table / Python repository 跨层引用
- 跑批幂等和同日重跑
- 影子隔离、PIT、资金语义、快照三件套
- AI 索引重建和 MCP stale 风险

因此，本方案只吸收 `kms-web` 的结构：

- 不要读哪些文件
- 可读但禁改
- 改前门禁
- 改动半径
- 存量违规不扩散
- 错误处理
- 最终总结要短

但内容必须按 `platform` 的全栈边界重写。

