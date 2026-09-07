# 任务计划落点治理实施计划

> **状态：✅ 已完成（2026-07-20）。** 三组独立复审均通过；任务真值已统一到
> `docs/plans/roadmap-*`，去重后的唯一活动队列剩余 27 项。
>
> **供自动化执行者使用：** 按本计划逐项执行；步骤使用复选框跟踪，不得在 `.superpowers/**` 创建任务 brief、plan、progress 或 report。

**目标：** 强制每个会修改文件或外部状态的独立任务先建立计划，把计划与任务真值统一保存到 `docs/plans/roadmap-YYYY-MM-DD/`，并迁移 `.superpowers` 中已有长期任务信息、去重剩余工作后按依赖顺序继续执行。

**架构：** `AGENTS.md` 只声明入口级硬门禁，`.codex/rules/workflow.md` 定义任务生命周期，`.codex/rules/file-discipline.md` 负责路径归位，`docs/plans/README.md` 记录文档侧约定。历史已跟踪的错误落点迁回对应 roadmap；根目录 `.superpowers/**` 只保留非真值的临时运行证据。

**技术栈：** Markdown、Git 跟踪路径扫描、PowerShell 只读验证。

## 全局约束

- 每个独立变更任务的首个允许写入必须是任务计划；计划落盘后第二步登记对应 roadmap
  `README.md`，两步完成前不得写其他文件或改变外部状态。
- 同一任务的“继续、修复复审问题、补验证”更新原计划，不重复创建新计划。
- 只读问答、状态查询和诊断不修改状态时不生成空计划；一旦决定修改或执行外部操作，必须先建计划。
- plan、design、task brief、progress、completion report 的真值只能位于 `docs/plans/roadmap-YYYY-MM-DD/`。
- `.superpowers/**` 与 `docs/superpowers/**` 不得作为任务文档真值源；skill 的默认保存路径被本仓规则覆盖。
- 不删除未知未跟踪文件，不回滚当前 runtime store 草稿，不提交或推送 GitHub。
- `.superpowers` 存量按“长期任务真值 / 临时证据 / 可丢弃缓存”分类；本任务只迁移长期真值，删除缓存必须另有明确授权。

---

### 任务 1：建立规则硬门禁

**文件：**

- 修改：`AGENTS.md`
- 修改：`.codex/rules/workflow.md`
- 修改：`.codex/rules/file-discipline.md`
- 修改：`.codex/rules/weekly-iteration-cadence.md`
- 修改：`docs/plans/README.md`

**接口：**

- 输入：用户要求“每次任务都建 plan，且不得写入 `.superpowers`”。
- 输出：入口规则、工作流规则和文件归位规则使用同一口径。

- [x] 在 `AGENTS.md` 核心协议增加“每个变更任务先建计划”的入口门禁。
- [x] 在 `workflow.md` 定义独立任务、首个允许写入、继续任务复用计划和只读例外。
- [x] 在 `file-discipline.md` 固化唯一目录、禁用路径和验证命令。
- [x] 在 `weekly-iteration-cadence.md` 废止 `docs/architecture/roadmap-*` 的新任务落点。
- [x] 在 `docs/plans/README.md` 记录同一真值约定。

### 任务 2：盘点并迁移错误落点

**文件：**

- 移动：`docs/superpowers/plans/*.md` → `docs/plans/roadmap-2026-07-19/`
- 移动：`docs/superpowers/specs/*.md` → `docs/plans/roadmap-2026-07-19/`
- 移动：`.superpowers/sdd/runtime-generation-foundation-task-3-report.md` → `docs/plans/roadmap-2026-07-19/`
- 移动：`.superpowers/sdd/task-3-report.md`、`.superpowers/sdd/task-4-report.md` → `docs/plans/roadmap-2026-07-09/`
- 创建或修改：对应 roadmap `README.md`
- 创建：`docs/plans/roadmap-2026-07-20/superpowers-artifact-migration-inventory-2026-07-20.md`

**接口：**

- 输入：`.superpowers/**` 全量文件清单、Git 跟踪状态、文档标题、关联计划和提交历史。
- 输出：已完成/进行中/待执行/临时证据分类清单；长期任务文档归入对应 `docs/plans/roadmap-*`，链接仍可解析。

- [x] 并行按 reindex/codegraph、runtime/systemd、runtime-generation 三组只读分类，主线统一去重。
- [x] 按原计划启动日期移动文件，不改正文语义。
- [x] 修复移动后相对链接及仓库内旧路径引用。
- [x] 已完成且有长期审计价值的 brief/report/progress 迁入对应 roadmap；重复 brief 合并为计划引用，不复制多份真值。
- [x] review diff、JUnit、临时 smoke/probe 只登记为临时证据，不迁成计划。
- [x] 未跟踪的可丢弃缓存只登记，不在无单独清理授权时删除。

### 任务 3：建立唯一剩余任务队列

**文件：**

- 修改：相关 `docs/plans/roadmap-*/README.md`
- 修改：迁移后的活动实施计划与进度记录
- 创建或修改：`docs/plans/roadmap-2026-07-20/superpowers-artifact-migration-inventory-2026-07-20.md`

**接口：**

- 输入：所有迁移文档、Git 已完成提交和现有计划状态。
- 输出：去重后的完成项、进行中项、未完成项、被新计划覆盖项及唯一执行顺序。

- [x] 以 Git 提交和已通过复审证据判定完成，不能仅凭文件名或旧进度文字。
- [x] 合并重复任务；旧任务被新 runtime-generation 计划覆盖时标记“已承接”，不重复执行。
- [x] 明确当前任务数、下一项、依赖和验证门禁，并写入活动 roadmap。
- [x] 治理任务完成后，从唯一活动计划的首个未完成任务开始连续执行，不再读取 `.superpowers` 作为进度真值。

### 任务 4：验证规则、迁移和执行队列

**文件：**

- 验证：本计划涉及的所有 Markdown 与 Git 路径。

**接口：**

- 输入：规则和迁移后的工作树。
- 输出：关键字、路径、链接和差异检查证据。

- [x] 运行 `git ls-files -- '.superpowers/**' 'docs/superpowers/**'`，结果为 0 个跟踪文件。
- [x] 运行 `Get-ChildItem docs\plans -File | Where-Object Name -ne 'README.md'`，结果无输出。
- [x] 运行 `rg -n "docs/superpowers|\.superpowers/(sdd/)?[^ ]*(plan|task|brief|progress|report)" AGENTS.md .codex docs/plans`，命中仅为禁止性说明或历史迁移记录。
- [x] 分别运行 `git diff --check` 与 `git diff --cached --check`，均无输出。
- [x] 核对 runtime store 草稿仍只位于未暂存/未跟踪工作树；`HEAD` 与 `origin/dev` 均为
  `8db1fc6c79ae7a810b0fced64898343bea50a4d9`，`origin/dev` 仍为
  `bcbbdfd0c70917dc54a9d9c850c09a25ae7e82fc`，本任务未提交、未推送。

### 复审结论

- 规则一致性：APPROVE；计划首写、周协调只引用原任务 plan、唯一落点均无冲突。
- 迁移完整性：APPROVE；迁移前 127 项分类准确，迁移后 `.superpowers/sdd` 剩余 124 个非真值历史文件。
- 路径与差异：APPROVE；8 个跟踪文档完成 Git rename，4 个相对链接有效，runtime 草稿未进入暂存区。
