# 全栈协作工作流规范

本文档约定 AI 在本仓库改动前 / 中 / 后的协作流程。具体业务规则在 `.claude/rules/*.md` 和各子模块 `.claude/rules/*.md`。本文件只写跨 Java / Python / Web / DB / AI 工具链都适用的工作流。

---

## 1. 默认不读取的文件

除非排查目标直接指向它们:

- 构建物:`apps/stock-admin-web/dist/`、`.umi/`、`.umi-production/`、`apps/stock-admin-api/target/`
- 缓存:`node_modules/`、`__pycache__/`、`.pytest_cache/`、`types/cache/`
- AI 索引产物:`.codegraph/`、`data/chroma/`、`data/codegraph_ext/*.sqlite*`
- 大型 dump:`database/init/*.gz`、大型 `*.sql`
- 锁文件:默认不读 `pnpm-lock.yaml`,排查依赖时再读
- 历史归档:`archive/`、`docs/operations/incident-*.md`(追溯事故再读)
- 自动生成文件全文:`src/services/apis/**`、`enumslocal.tsx`,查接口签名定向读

---

## 2. 可读但默认禁改

### 2.1 自动生成(禁手改)
- `apps/stock-admin-web/src/services/apis/**` — Swagger 生成
- `apps/stock-admin-web/src/models/enumslocal.tsx` — 后端枚举生成

### 2.2 数据库边界
- **已发布 Flyway migration**:禁回改,只能新增 forward migration
- `database/init/**`:初始化 dump

### 2.3 全局基础设施(改前确认)
- `apps/stock-admin-web/src/app.tsx`、`src/models/**`、`src/utils/fetch/**`、`config/routes.ts`
- `apps/stock-admin-api/pom.xml`、`package.json`、`requirements.txt`
- `tools/dev/*.ps1`、`tools/cross_link/**`、`tools/chroma/**`、`.mcp.json`

---

## 3. 改前门禁(强制流程)

### 3.0 任务分级入口

修改文件前先判级,再执行 §3.2 的任务映射。不要把 L3/L4 的重流程套到 L1 小改;一旦范围扩大,立即升级到对应级别并补齐门禁。

| 等级 | 适用范围 | 必做动作 |
|---|---|---|
| L1 小改 | 文案 / 注释 / 局部样式 / 单文件小 bug,且不改 API / DB / DTO / enum / 路由 / 全局配置 / 生成物 | `git status -s` + 定向读文件 + 最小验证;不强制 platform-docs / codegraph / cross-link |
| L2 单层 | 单模块或同一技术层多模块联动,可能影响调用方但不改 API / DB / DTO / enum / 跨语言字段契约 | 相关模块 rules + codegraph 定义 / 调用方 / 影响面 + 定向验证;出现数据链问题时加 cross-link 并评估是否升 L3 |
| L3 跨层 | DTO / API / SQL / Python 写库 / Flyway / enum / 推荐 / 资金 / 百分比 / shadow / snapshot / 前端请求离散筛选值 | platform-docs + codegraph + cross-link + 验证闭环 |
| L4 高风险 | 生成文件 / 已发布 migration / AI 索引 / 依赖 / 全局 runtime / `tools/dev` / `.mcp.json` | 先说明影响范围;禁改项拒绝或走生成 / forward migration 等正确流程 |

§3.2 是 L2/L3/L4 的展开表。L1 若命中 §2 的默认禁改 / 改前确认路径,或出现跨文件契约影响,必须升级到 L3/L4。

### 3.0b MCP 使用校准

MCP 的目标不是"每次都用",而是让 L2/L3 的检索命中率稳定上来。执行时按下面口径判断:

| 场景 | 处理 |
|---|---|
| L1 明确小改 | 不跑完整 MCP 链路;本地定向读 + 最小验证即可 |
| L1 但不知道入口 / 文件位置 | 可用一次 codegraph / 本地搜索定位,定位后停止扩大 |
| L2 单层单模块 | 至少用 codegraph 查定义 / 调用方 / 影响面之一;改子模块代码前补读对应 rules |
| L2 单层多模块 | 用 codegraph impact / callers / callees 看同层联动;不默认 cross-link |
| L2 中追问数据来源 / 去向 | 加一次 cross-link 查 endpoint / table / Python writer / Mapper 链路;若改契约或写库字段,升级 L3 |
| L3 跨层 / 契约 / DB / enum | platform-docs + codegraph + cross-link 都要有结果或说明兜底 |
| dirty 命中索引范围 | MCP 只作导航;关键结论必须回读真实文件 |
| 多会话同时改 | 读侧可并发;写侧重建索引 / 代码生成 / migration 串行 |

`ai-health` 的 `search_recall` 看检索质量,`mcp usage 7d` 看 platform-docs 使用痕迹,`mcp adoption` 看 L2/L3 候选提交口径。低使用率不直接代表故障;若 L2/L3 候选多但 search_docs 很少,说明执行纪律不足。

### 3.0c 合并影响面门禁

L2/L3/L4 任务在修改文件前,必须先把规则、代码结构、数据链路和真实工作树压成一份"合并影响面"。它不是额外文档,而是改动决策的输入;没有合并影响面就不得进入 Edit。

最小模板:

```markdown
合并影响面:
- 等级:
- 文件范围:
- 规则/skill:
- CodeGraph 结论:
- cross-link 结论:
- dirty-index / 真实文件:
- 禁改项 / 生成项:
- 验证:
```

执行口径:

| 等级 | 合并影响面要求 |
|---|---|
| L1 | 不强制成文;若入口不清或命中受控边界,升级到 L2/L3 后补齐。 |
| L2 单模块 | 至少包含规则/skill、CodeGraph 定位或调用方、dirty-index 状态、定向验证。 |
| L2 多模块 | 在 L2 单模块基础上补同层影响面;若出现 endpoint / table / Python writer-reader / API request 枚举值,加 cross-link 并评估升 L3。 |
| L3 跨层 | 必须同时包含 platform-docs、CodeGraph、cross-link 结论;MCP 不可用时说明一次并用本地规则、源码、SQL、生成类型兜底。 |
| L4 高风险 | 先列受控边界、禁改项、串行写资源和回退方式;生成物、历史 migration、AI 索引产物不得手改。 |

典型拦截:
- 前端筛选 / 下拉 `value` 会进入 API request,且是有限离散集合 → 直接按 L3 枚举链路处理,不得在前端本地 options 自造。
- SQL / Flyway / Python 写库字段 → 必须有 cross-link 的 table / writer / reader 结论。
- 多会话下目标文件已有未知改动 → 合并影响面必须改读真实文件,不得只信索引。

### 3.1 判断本次触及层

Web 前端 / Java API / Python Pipeline / DB-Flyway / 跨层契约 / AI 工具链 / 文档规则。

### 3.2 任务类型 → 必读规则 + MCP 触发映射

下表给出"改什么 → 至少读什么 + 必先调什么 MCP"。`workflow.md`(本文件)是默认底层。
**MCP 优先于 Grep + Read**(Grep 仅在 MCP 不可用 / 索引滞后 / 看未提交改动时兜底)。

#### 单层任务

| 任务类型 | 必读规则 + skill | 必先调 MCP |
|---|---|---|
| 改前查规则 | workflow.md §3.2 | `search_docs(query, module=?)` |
| 找类 / 函数定义 / 接口 | - | `codegraph_search` / `codegraph_node` |
| 找调用链 / 影响面 | - | `codegraph_callers` / `codegraph_callees` / `codegraph_impact` |
| 前端 .tsx 业务组件 / 页面 | 子模块 react-patterns + component-patterns + stock-color-convention | `search_docs(module='stock-admin-web')` |
| 前端样式 / 颜色 | 子模块 styles + stock-color-convention(涨跌)| - |
| 前端 API 调用 / model | 子模块 api-service + react-patterns | - |
| 前端枚举使用 | 子模块 architecture §4(useModel) + cross-layer-enum-consistency | - |
| 前端筛选 / 下拉 value 会进入 API request | cross-layer-enum-consistency + skill add-enum | `cross-link find_endpoint_link` |
| Java Controller / Facade | java-layering + api-contracts | `codegraph_callers` |
| Java Mapper 自定义 SQL | 子模块 sql-patterns + shadow-isolation(读 recommend_result 必带过滤) | `cross-link find_table_refs` |
| Java DTO 字段改动 | skill backend-dto-change + frontend-backend-handoff | `cross-link find_endpoint_link` + `codegraph_impact` |
| Java 新建 / 改枚举 | 子模块 enum-patterns + skill add-enum + cross-layer-enum-consistency | `codegraph_callers` |
| 新增 Flyway migration | skill add-flyway-migration + same-day-rerun + not-null-write-guard | `cross-link find_table_refs` |
| 改既有 Flyway | ❌ 禁止,新增 forward migration | - |
| Python repository 读写 | 子模块 pipeline-patterns + not-null-write-guard | `cross-link find_table_refs` |
| Python 新 Job / 跑批入口 | skill add-python-job(含幂等性 + fetcher 限速) + same-day-rerun | - |
| Python fetcher 入口 | skill add-python-job §🚨 fetcher 强制 + windows-powershell | - |
| Python model/core.py 字段 | model-field-consistency + 必跑 `check_entity_dataclass_parity.py` | `cross-link find_table_refs` + `codegraph_callers` |
| 影响推荐 / 4W / track 链路 | shadow-isolation + snapshot-trio-write + pit-redline-and-tracks | `cross-link find_table_refs` |
| 影响资金 / capital 字段 | capital-amount-semantics(4 套口径) | `codegraph_impact` |
| 影响百分比字段(stop_loss_pct 等)| pct-sign-convention(负值止损正值止盈)| `codegraph_callers` |
| 业务 sanity check / 告警 | business-sanity-alerts | - |
| 同日重跑 / 节假日重跑 | same-day-rerun + pit-redline-and-tracks | - |
| 新增前端页面 + 菜单 | skill add-frontend-page(含 sys_menu Flyway 联动)| - |
| 股票上下文展示 | stock-name-display(必带 stockName) | - |
| 回测 / shadow 实验 | shadow-isolation §2 白名单 | - |
| 提交 commit | commit-pr-conventions + skill git-commit | - |
| AI 工具链 / cross-link / chroma | ai-tools-mcp | - |
| PowerShell 脚本 | windows-powershell(ASCII only / UTF8) | - |
| 文档 / 规则修改 | weekly-iteration-cadence + 引用路径存在检查 | - |

#### 跨层业务链场景(必先调 cross-link 看全链路依赖,不许只改单层)

凡涉及以下"业务标识符跨多层传递"的改动,**改前必查 cross-link**,确认全链路依赖再下手:

| 跨层场景 | 必查链路 | MCP 工具组合 |
|---|---|---|
| **sql ↔ py** | Flyway column ↔ Python `core.py` / writer / reader | `cross-link find_table_refs` |
| **sql ↔ java** | Flyway column ↔ Java Entity / Mapper SQL | `cross-link find_table_refs` |
| **sql ↔ java ↔ 前端** | Flyway → Java DTO → 前端 `typings.d.ts` | `cross-link find_table_refs` + `find_endpoint_link` |
| **sql ↔ py ↔ java ↔ 前端** | Flyway → Python writer → Java reader → DTO → typings → .tsx | `cross-link find_table_refs` + `find_endpoint_link` + `check_entity_dataclass_parity.py` |
| **py ↔ java** | Python `core.py` ↔ Java Entity(同表) | `cross-link find_table_refs` + `check_entity_dataclass_parity.py` |
| **py ↔ java ↔ 前端** | Python writer → Java reader → 前端展示 | `cross-link find_table_refs` + `find_endpoint_link` |
| **java ↔ 前端** | Java DTO/Controller ↔ `typings.d.ts`(用户跑 `pnpm run api` 重生) | `cross-link find_endpoint_link` + `codegraph_impact` |
| **跨语言枚举** | Python enum ↔ Java enum ↔ 前端 `useModel('enum')` | `cross-layer-enum-consistency` + `audit_fetcher_registry_parity.py` |
| **前端请求筛选枚举** | `.tsx Columns/valueEnum/options` → request DTO 字段 → Java enum → `EnumMetadataService` → `pnpm run enums` | `cross-layer-enum-consistency` + `find_endpoint_link` |

**未调 cross-link 直接动跨层链路** = 视为门禁未过(workflow.md §3.4 处理)。

不在表内 → 至少读 workflow + 涉及目录 1-2 条最相关规则。

### 3.3 门禁声明

每次任务首次修改前必须说一句:**本次触及 \<层\>;适用规则 \<文件名\>;关键约束 \<一句话\>;验证方式 \<命令\>**。

例:`本次触及 Java Mapper + cross-link;适用 sql-patterns + ai-tools-mcp;不回改历史 Flyway,影子隔离 9 处 SQL 不动;验证 mvn test + cross-link rebuild`。

同一轮任务范围不变沿用首次声明;范围扩大补读 + 重新声明。

### 3.4 未过门禁不得修改

发现已在未过门禁情况下改了文件 → 停手,说明已发生不合规点 + 影响 + 建议,经用户确认前不继续。

---

## 4. 跨层改动定义 + MCP 触发

任一情况即跨层:Java DTO/Controller/Swagger 响应字段变化 / 前端 API 调用参数变化 / Python 写库字段变化 / Flyway 表结构变化 / 枚举新增删除改名改值 / 推荐 / 4W / 回测 / 影子 / 资金字段变化 / endpoint↔Mapper↔table 链路变化。

**新增前端下拉 / 筛选项时的硬判定**:只要 `value` 会进入 API request,且取值是有限离散集合,默认按业务枚举处理,不得在前端 `utils.ts` / `Columns.tsx` 本地定义业务 options。必须走 Java `BaseEnum` + `EnumMetadataService` + `pnpm run enums` + `useModel('enum')`。

跨层改动必查:
- **规则**:`platform-docs` MCP / `search_docs`
- **调用链**:`codegraph` MCP(`codegraph_callers` / `codegraph_impact`)
- **endpoint / Mapper / Flyway / table / Python 链路**:`cross-link` MCP(`find_endpoint_link` / `find_table_refs`)

MCP 不可用 → 说明一次,本地 grep + Read 兜底。

---

## 5. 多步任务格式

3 步以上必须先列简短计划:

```
1. [步骤] → 验证:[命令 / 通过标准]
2. [步骤] → 验证:...
3. [步骤] → 验证:...
```

强成功标准让任务闭环;模糊标准("让它跑通")会反复返工。

---

## 6. 改动中原则

- **单一职责**:只改本次范围,不顺手清理 / 重排 / 优化注释
- **匹配现有风格**:rules 未明确时参考邻近代码;邻近违规 rules 时以 rules 为准
- **复杂度检验**:200 行能写 50 行就重写
- **孤儿代码**:自己引入的死代码立即删,既存死代码指出不删等用户决定
- **不写无意义防御**:不滥用 `?? ''`、空 `try-catch`;但用户输入 / 外部 API / 异步 / 权限边界必须校验
- **不为未来写代码**:不加 feature flag / TODO 占位 / "以后可能用到"的工具函数
- **不重复读取**:同一轮已读文件 / 已跑命令不重复

### 6.1 多会话并行开发

多个 AI 会话可以并行工作,但必须按资源类型区分"可并发读"和"必须串行写"。

- **文件所有权**:每个会话修改前先说明负责的文件 / 模块范围;不得并发修改同一文件。发现目标文件已有非本会话改动,先读最新内容再继续。
- **工作树检查**:修改前后都看 `git status -s`;遇到未知改动不回滚,只在本次范围内协作。
- **索引可信度**:调 CodeGraph / cross-link / platform-docs 前先看 dirty 范围。dirty 命中索引范围时,MCP 结果只能作参考,关键决策必须读真实文件确认。
- **读侧并发**:MCP 查询 / SQLite 读连接 / stdio proxy 可以多会话并发。
- **写侧串行**:Chroma reindex / CodeGraph rebuild / cross-link rebuild / 代码生成 / migration / 写库任务必须单实例执行,依赖既有 lock / forward migration / 人工确认。
- **重资源单例**:GPU 模型类 daemon 必须单实例;多会话只能通过轻量 proxy 共享。

---

## 7. 改动半径 + 扩散范围选择

| 半径 | 处理 |
|---|---|
| 新文件 / 新模块 | 严格遵守 rules,不沿用旧违规 |
| 存量文件新增 / 修改行 | 必须遵守 rules |
| 必须修改的旧行 | 最小修正 |
| 本次未触碰的旧违规 | 不主动整改,不顺手格式化 |

跨文件影响分析(仅对外契约 / 公共模块时做):

- 改函数签名 / 类型 / 导出 → 搜调用点
- 改组件 props / context → 搜引用
- 改 utils / hooks / models → 搜 import
- 改路由 / 公共基础设施(`fetch.ts` / `app.tsx` / `models/*`)→ 列影响模块,等用户确认

扩散范围呈现:

```
发现扩散点,请选择:
[A] 仅本次最小改动
[B] 本次需求 + 直接调用方(X 个文件)
[C] 完整治理(Y 个文件,会扩大为模块级重构)
[D] 用户指定

推荐:[X] —— 一句话原因
```

**机械同步豁免**:同模块内无行为语义变化的直接调用方机械同步(props 改名 / import 路径)算预期范围,直接执行,不必走 A/B/C/D。

---

## 8. 错误处理

- **不绕过**:不 `@ts-ignore` / `eslint-disable` / `--no-verify` / `--no-gpg-sign`
- **不重试**:命令失败先看输出,不重复跑
- **不动用户未提交内容**:发现未知文件 / 未知分支先问
- **定向验证优先**:`mvn -Dtest=<X>` / `pytest tests/test_<X>.py` / `eslint <file>`,避免全量
- **node_modules / 工程配置失败**:说明非本次改动导致,不擅自改依赖 / 锁文件

---

## 9. 改动后输出

- **总结短而具体**:改了什么 + 验证了什么 + 风险 / 下一步;简单任务 1-2 句即可
- **规则回放**:涉及修改文件时,按本次实际读取的规则说明已做的针对性检查
- **不重复 diff 内容**:用户能看到 diff,不复述代码
- **UI 看不到效果**:明确说"未在浏览器验证"
- **未跑 lint / tsc / 测试**:说明原因

---

## 10. 验证矩阵

| 改动类型 | 最小验证 |
|---|---|
| Web 页面 / 组件 | `pnpm --dir apps/stock-admin-web exec eslint <files>` |
| Web API 调用 | ESLint + 确认 typings 已含字段 |
| Java Mapper / Service | `mvn -f apps/stock-admin-api/pom.xml compile` 或相关 test |
| Java DTO / Controller | skill backend-dto-change + compile + test |
| Flyway | 新增 migration + Java 重启验证 + cross-link 检查 |
| Python repository / job | `python -m pytest python/stock-pipeline/tests/<target>` |
| Python 跑批写侧 | pytest + 幂等 / 同日重跑检查 |
| 枚举 | skill add-enum + 前后端生成链路 + parity 工具 |
| endpoint / SQL / Mapper 链路 | cross-link 查询或重建 |
| AI 工具脚本 | 对应 tests + `tools/dev/ai-health.ps1` |
| 文档 / 规则 | 引用路径存在 + pre-push audit |

无法验证时必须说明原因。

---

## 11. 禁止 AI 自动执行的命令(用户手动跑)

- 依赖:`pnpm install` / `npm install` / `mvn install`
- 启动 / 长驻:`pnpm dev` / `pnpm start` / `mvn spring-boot:run`
- 编译 / 构建:`pnpm build` / `mvn package`
- 代码生成:`pnpm run api` / `pnpm run enums`(改动 `src/services/apis/`)
- 重大破坏:`git reset --hard` / `git push --force` / `rm -rf`

需要这些命令结果时提示用户手动跑 + 贴回输出。

`pnpm run lint` / `pnpm run tsc` / `mvn compile` / `python -m pytest` 这类一次性检查可按需运行。

---

## 12. Subagent 派遣模板

派 subagent 干跨层 / 关键路径 / 合规字段相关活时,prompt **必须 50 行内**:

```
【目标】 30 字内
【文件路径】 1-3 行绝对路径
【禁止】 ≤5 条
【验证】 grep / pytest / mvn 命令 1-2 条
【输出长度】 显式上限(如 "150 行内")
```

不预塞规则正文,让 agent 按需 `search_docs`。合规字段(影子 / policy_version / pct 符号 / NOT NULL / 快照三件套)改动 → **必须**用此模板,且建议再派独立 review agent 看 diff。

### 12.1 全流程 MCP 优先门禁(CLAUDE.md §0.8 + §0.9 强制)

**执行范围:所有阶段** — 开发 / 设计 / 分析文档 / 分析代码 / 审计,**不分主 agent / 实施 agent / 审计 agent**,都按 §3.2 任务类型映射选对应 MCP,Grep 必带例外声明。

#### 派实施 agent 模板必须加(自报 + 审计可核查):

```
【final report 末尾必须包含】
- MCP 调用清单:列本次每次调用的 MCP 工具 + 入参摘要 + 任务类型(按 §3.2 表对照)
  例:codegraph_callers("StockRecommendationTrackMapper") — 任务类型 "Java Mapper 自定义 SQL"
  无调用则写"无 — 因 <例外理由>"
- Grep 例外声明清单:每个 Grep 调用对应的 [Grep 例外: <类型>]
- §3.2 映射核查:若任务命中 §3.2 表中条目,声明"已按 <任务类型> 行调 <MCP 工具>"
```

#### 派审计 agent 模板必须加:

```
【MCP 选型核查】对照 workflow.md §3.2 任务映射表,核实实施兄弟选的 MCP 工具与任务类型匹配:
- 找代码符号 / 调用链 → 应选 codegraph_*
- 找规则 / 文档 / 事故 → 应选 search_docs
- 找业务链路 → 应选 cross-link find_*
- 选错或漏选 → 审计不通过

【Grep 例外声明核查】扫实施兄弟响应中所有 Grep 调用,每次必带 [Grep 例外: <类型>] 声明。
未声明 / 例外理由不在 §0.9 清单内 → 审计不通过。
```

这两套模板把 §0.8 / §0.9 的执行从"靠自律"转移到"实施兄弟自报 + 审计兄弟按 §3.2 核查",杜绝 MCP adoption 0.1 现象。

### 12.2 事前抛具体 MCP 调用清单(2026-05-26 实战沉淀)

§12.1 是事后核查,这里是事前模板 — **派 agent 时直接在 prompt 抛 5 个具体 MCP 调用**,不靠 agent 自发选。本次 stale_job 审计实测拦下 1 个 BLOCKER(`message` vs `detail` 字段名漂移)。

#### 派审计 agent 模板(在 §12.1 基础上追加这段)

```
【MCP 强制使用】(§3.2 任务映射,本轮必调,不许只用 Grep)
1. mcp__platform-docs__search_docs("<本次改动主题 + 相关规则名>") — 验是否符合 <具体规则>
2. mcp__platform-docs__search_docs("<本次相关 sanity / 闭环 / 流程>") — 验流程对齐
3. mcp__codegraph__codegraph_search("<被调用的关键函数 / 入口>") — 核接口契约 / 入参签名
4. mcp__codegraph__codegraph_node("<本次构造的 dataclass / DTO>") — 核字段名一致性
5. mcp__cross-link__find_table_refs("<本次写入的表>") — 核走标准 writer 路径不旁路
6. mcp__platform-docs__search_docs("<本次场景关键词>", category="memory") — 拿用户偏好 / 踩坑经验 / 决策记录(子 agent 不 autoload MEMORY,这步是它**唯一**看到用户偏好的路径)
```

每条具体到 query 字符串 + 验证点。审计 agent 一进门就调,不靠 prompt 末尾"必须含 MCP 清单"事后兜底。

**第 6 条特别说明**(2026-05-26 新加):子 agent 独立 context,**不 autoload MEMORY.md**。不传这步 → agent 不知"用户决定不做 dark mode" / "commit 必拆 Phase" / "报股票必带名称" 等 14 条偏好,容易踩坑。模板里写死让子 agent 必查。

#### 主 agent / 实施 agent 自用模板(合并影响面调研)

L2/L3 任务起手 5-6 个并发 MCP 调用,**省 ~10 次 Grep + Read**(本次实测):

```
1. cross-link find_table_refs("<核心表>") — 拿全跨层 reader/writer/updater 路径
2. search_docs("<改动主题>") — 拿规则 + 历史事故复盘 + 设计文档(top 5-8)
3. codegraph_search("<目标符号>") — 定位文件:行号
4. codegraph_search("<下游消费函数>") — 看 caller 影响面
5. codegraph_node("<构造的 dataclass>", includeCode=true) — 字段对齐
6. search_docs("<改动主题>", category="memory") — 拿用户对此类改动的偏好(独立 agent 必查;主 agent 自用任务起手也建议查,防漏看)
```

经验坑(本次踩):
- `codegraph_context(task="<场景描述>")` 容易返回不相关结果,改用 `codegraph_context(task="<具体 symbol> 上下游")` 命中率高
- `codegraph_node` 拿 enum / file 不返回正文,要补 Read;拿 method 才有源码
- 5 个并发调用走一个 message,等结果到齐再继续 — 不要串行
