# 文件规模 + 跨语言判重纪律

> Python 特有的目录归位 + 复用清单见 `python/stock-pipeline/CLAUDE.md §2.2`。
> 子模块各自的具体阈值见对应 `.claude/rules/code-quality.md`。
> 本文件只列**跨子模块**的通用纪律。

## 1. 单文件行数(各子模块独立阈值，单一真值源在子模块)

| 子模块 | 阈值 | 真值源 |
|---|---|---|
| Java | ≤ 600 行(硬约束) | `apps/stock-admin-api/.claude/rules/code-quality.md` §单文件行数 |
| Python | ≤ 600 行(硬约束) | `python/stock-pipeline/.claude/rules/code-quality.md` §文件行数 |
| 前端 | ≤ 1200 行(ESLint warn) / index.tsx 超 500 行考虑拆 | `apps/stock-admin-web/.claude/rules/code-quality.md` + `architecture.md` §5 |
| 跨模块兜底 | ≤ 1000 行 | 本文件 §1(无对应子模块时,如 `tools/` `scripts/`) |

超出时按职责拆分:
- Java Controller 按业务域(`StockController` / `DiagnosticsController`)
- Java Facade 按职能(`ConfigurationFacadeService` + `ConfigurationFacadeSupport`)
- Python 仓储读写分离(`postgres_write` / `postgres_read` / `postgres_jobs` / `postgres_helpers`)

## 2. 判断重复代码的简单测试

写完一个函数后用 grep 搜函数名核心词。如果发现已有 80% 相似实现，**重构成一个**，不是把两个都留下。

## 3. 违反时的处理

- 根目录多了不该在的 `.py` → 立刻移走或归档（细则见 `python/stock-pipeline/CLAUDE.md §2.2`）
- 多了重复实现的工具函数 → 删冗余保通用版本

---

## 4. docs/ 目录归类硬规定

`docs/` 是 **8 子目录二分(业务侧 6 + 工具栈侧 2),顶层 0 散文件** 结构,**硬性规定**。详见 `docs/README.md`。

### 4.1 写新文档放哪

| 内容 | 放哪 |
|---|---|
| 业务事故复盘 | `docs/operations/incident-YYYY-MM-DD-<topic>.md` |
| 工具栈 / 协作流程事故复盘 | `docs/incidents/YYYY-MM-DD-<topic>.md` |
| 业务功能 / 模块 plan | `docs/architecture/<feature>-plan-YYYY-MM-DD.md` |
| 工具栈 / 流程改造 plan | `docs/plans/roadmap-YYYY-MM-DD/<topic>-YYYY-MM-DD.md`(按日期分目录,详见 §4.4)|
| 工具栈迭代日报 | `docs/plans/roadmap-YYYY-MM-DD/daily-summary-YYYY-MM-DD.md` |
| 业务周迭代日报 | `docs/architecture/roadmap-<anchor>/daily-summary-YYYY-MM-DD.md` |
| 工具栈 / 协作流程月决策 | `docs/log/YYYY-MM.md`(月聚合) |
| 用户偏好 / 反馈 | `docs/memory/feedback_<slug>.md`(同步加进 `MEMORY.md` 索引) |
| 业务 API 契约 | `docs/api/` |
| 业务规则定义 | `docs/rules/` |
| 合规 | `docs/legal/` |

### 4.2 禁止

- ❌ `docs/` 顶层放散文件 — 必须归子目录
- ❌ `operations/` 放工具栈事故 — 走 `docs/incidents/`
- ❌ `architecture/` 放工具栈设计 — 走 `docs/plans/`
- ❌ `docs/plans/` 根直接放散 plan 文件 — 必须归 `roadmap-YYYY-MM-DD/` 子目录(详见 §4.4)
- ❌ 改 `docs/memory/` 路径 — `CLAUDE.md` autoload 依赖 `docs/memory/MEMORY.md` 写死路径
- ❌ 加新顶级 `docs/` 子目录而不补 `tools/chroma/index_docs.py:DOC_PATTERNS`(pre-push audit 6/6 会拦)

### 4.3 grep 自检

在仓库根目录跑:

```powershell
# 顶层必须 0 散文件(只允许 README.md + 子目录)
Get-ChildItem docs -File | Where-Object Name -ne 'README.md'
# 命中 → 违规

# docs/plans 根必须 0 散文件(只允许 roadmap-* 子目录 + README.md)
Get-ChildItem docs\plans -File | Where-Object Name -ne 'README.md'
# 命中 → 违规, plan 文件必须在 roadmap-YYYY-MM-DD/ 子目录里
```

### 4.4 docs/plans 按日期分目录(roadmap-YYYY-MM-DD)硬规定

工具栈 plan / design / daily 不平铺在 `docs/plans/` 根,**按规划启动日期归入 `roadmap-YYYY-MM-DD/` 子目录**(与业务仓 `docs/architecture/roadmap-*/` 同构;工具栈仓直接用顶层 `docs/plans/`,无 `dev-evolution/` 中间层;业务仓的工具栈/流程 plan 才落 `docs/plans/`,业务 plan 仍走 `docs/architecture/`)。

| 项 | 约定 |
|---|---|
| 目录名 | `roadmap-YYYY-MM-DD/`,日期 = 该轮规划启动当天 |
| 必含 | `README.md`(目录定位 + 文件清单表 + 状态) |
| 收纳范围 | 同一轮迭代的所有 plan / design / completion-report / daily-summary 文件 |
| 文件命名 | 仍带日期后缀(如 `agent-2026-05-28.md`) |
| 历史样例 | `roadmap-2026-05-28/`(平台翻正 + 跨平台化 + agent 起步) |

**为什么**:plan 数量增长后平铺会乱;按日期分目录让"某轮迭代做了什么"一目了然,且与业务仓 roadmap 结构对齐,协作者跨仓无切换成本。

**周期生命周期**(归档 / 继承未完结项 / 写入规则)沿用 `weekly-iteration-cadence.md` 的 SOP —— 那是业务侧真值源,工具栈侧结构同构、流程复用。

---

## 已有 assertion 兜底

⚪ 纯 why 文档（有可补 candidates）

目前无自动断言。本规则全靠人脑 + PR review 守护：
- 单文件 1000 行硬约束 → 靠 `wc -l` 人查
- 跨语言判重 → 靠 grep 自检

盲区（建议未来补）：
- 缺：CI/pre-commit 钩子扫"单文件 > 1000 行"（按子模块限制）
- 缺：约定测试断言"`python/stock-pipeline/` 根目录除白名单外无散落 .py"（详见 sub-module CLAUDE.md）
