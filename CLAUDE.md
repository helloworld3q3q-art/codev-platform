# CLAUDE.md — codev-platform

> AI 协作工具栈本体仓。通过 `pip install -e .` 被各业务仓引用。
> **禁止在本文件 autoload 规则全文**(避免新会话即占满上下文)。规则按需读取:先看 `.claude/rules/workflow.md` §3 任务分级 + §6 任务映射,按本次任务读对应规则;规则清单见下方「规则索引」。

---

## 0. 核心协议(不可漂移,inline 必读)

1. **改文件前**:先读 `.claude/rules/workflow.md` §3 任务分级 + §6 任务映射,按表读对应源规则,**不得只凭记忆**
2. **门禁声明**:首次修改前一句话说明 — 触及层 / 适用规则 / 关键约束 / 验证方式
3. **未过门禁不得修改**:如已先改后说,停手 → 说明不合规点 + 影响 + 建议,等用户确认
4. **不回滚用户未提交改动**:发现未知文件 / 未知分支先问
5. **commit 不带 AI 痕迹**:禁 `Co-Authored-By: Claude` / `Generated with`(详见 `.claude/rules/commit-pr-conventions.md`)
6. **PowerShell 脚本**:`.ps1` 禁含非 ASCII;读中文 MD/JSON/log 必须 `-Encoding UTF8`(`.claude/rules/windows-powershell.md`)
7. **默认禁手改**:`.venv/` / `data/` / `.codegraph/`(运行态)/ `codev_platform/resources/{rules,skills,hooks}/`(分发真值源,改后必验 sync)/ `~/.codev-platform/config.json`(用户主权)/ `web-ui/src/services/**`(生成 API 层)。详见 `.claude/rules/workflow.md §2`
8. **MCP 必须优先于 Grep,且按 workflow.md §4/§6 选对应 MCP**:**所有阶段**(开发 / 设计 / 分析文档 / 分析代码 / 审计)找 symbol / 调用链 / 规则 / 数据流,必须按任务类型主动选 MCP:
   - 找代码符号 / 调用链 / 影响面 → `codegraph_search` / `codegraph_callers` / `codegraph_callees` / `codegraph_context` / `codegraph_impact`
   - 找规则 / 设计文档 / 事故复盘 → `search_docs(query, module=?)`
   - 找跨层链路 / 业务域(CLI ↔ resources、graph/chroma/codegraph 数据流)→ `graph` `find_table_usage` / `find_api_callers` / `find_impact` / `search_nodes`
   - **选错 MCP** 视同没调(详见 workflow.md §6 任务映射表)
9. **禁止不声明直接 Grep**:调 Grep 前必须在响应内显式声明例外类型 — `[Grep 例外: 索引滞后 / 看未提交改动 / 查 log 或归档非索引文件 / MCP 不可用 / Edit 前定位精确字符串 / 验证刚 Edit 的结果 / 查 git diff 或 git log 输出 / 其他<必填具体理由>]`。本仓已装 PreToolUse(Grep) hook 提醒,但 hook 只提醒、**不替代声明**。未声明直接 Grep = 视同违反 §8。**审计 agent 必查所有阶段 Grep 调用是否带例外声明 + MCP 选型是否对应 §6**
10. **MCP 不可用**:说明一次,grep + Read 兜底,不反复重试
11. **改完同步业务仓 shim**:业务仓内有 thin shim re-export 自本仓,新增 symbol 要在 shim 加导出
12. **派 subagent 必查 workflow.md §12**:派审计 / 实施 agent 时直接抄 §12 的 prompt 模板(给边界 + 抛具体 MCP 调用清单 + final report 自报),**不要现编**
13. **动手前自报 trigger**(HIGHEST PRIORITY):会话内**首次** Bash(非 `ls`/`git status`/`git log`/`git diff`/`cat`/`pwd`/`echo` 等只读探索)/ Edit / Write **之前**,必须先输出一行声明:`[L1|L2|L3|L4] 任务: <一句话描述> → MCP 计划: <列出本次要调的 MCP 工具名,或"无 — 因为 L1 小改/纯探索/已用过 MCP">`。未自报直接动手 = 视同违反 §0.8。**判级标准**见 workflow.md §3;L2/L3/L4 必须按 §6 / §12 抛具体 MCP 调用,不允许写"看情况"

---

## 规则索引(按需读取,不 autoload)

> 全部在 `.claude/rules/`;改前按 `workflow.md` §3 分级 → §6 映射,定位本次要读哪几条。

| 规则 | 用途 |
|---|---|
| `workflow.md` | 本仓全栈工作流:§3 任务分级(L1-L4)/ §4 MCP-first / §6 任务映射 / §12 subagent 模板。**改前必看** |
| `ai-tools-mcp.md` | MCP 触发指南(codegraph / platform-docs / graph 选型 + 故障应急) |
| `agent-provider-architecture.md` | agent 多模型接入(按协议族非厂商 / loop 硬护栏) |
| `code-quality-discipline.md` | 写码红线(低耦合 / 单一职责 / 嵌套深度 / 死代码兜底) |
| `commit-pr-conventions.md` | commit 不带 AI 痕迹(项目独有红线) |
| `file-discipline.md` | 单文件规模 + 跨语言判重 + `docs/` 目录归类 |
| `verification-checklist.md` | 改动后验证清单 + 测试基线红线 + pre-push 审计 |
| `security.md` | 敏感信息与安全(密码 / token / 免责文案) |
| `windows-powershell.md` | `.ps1` 编码 + Claude Code 安全检查友好写法 |
| `weekly-iteration-cadence.md` | 每周迭代节奏 / `docs/plans/roadmap-*` 目录生命周期 |

---

## 一、定位

codev-platform = 多项目 AI 协作工具栈基础设施。

- **真值源仓** — `tools/_platform/`、`tools/claude-platform/`、`tools/chroma/{mcp_server,bm25_index}.py` 这些在 platform 业务仓内全是 shim re-export 自本仓(cross_link 已退役删除; 统一图谱 graph 走 SSE 服务, 不经 shim)
- **pip 安装** — `pip install -e D:\WorkSpace\codev-platform` 让 chroma .venv / system python 都能 `import codev_platform.*`
- **CLI 入口** — `codev-platform` console script,6 子命令(init / current / list-projects / validate / sync-rules / sync-skills / config)
- **跨项目共享数据** — `~/.codev-platform/config.json` 单一用户级配置驱动(模型 / 数据基目录 / daemon port / search 参数)

---

## 二、模块组织

```
codev_platform/
├── core/
│   ├── project_id.py   project_id resolver (env > .claude/project.json > 硬失败 + X-Project-Id header)
│   ├── paths.py        data_root / chroma_dir / chroma_collection_name / codegraph_db_path
│   └── config.py       ~/.codev-platform/config.json 加载 (env > config > default)
├── chroma/
│   ├── server.py       multi-tenant MCP daemon (870+ 行, contextvar 路由 + GPU semaphore)
│   ├── bm25.py         BM25 倒排索引 (jieba + rank_bm25 + RRF)
│   └── __init__.py
├── graph/              统一图谱 (替代退役的 cross_link; 跨层血缘 + A1 业务域)
│   ├── store.py        per-project sqlite 存储 (nodes/edges/evidences/findings)
│   ├── impact.py       跨层 BFS + A1 查询 (find_table_usage/find_api_callers/search_nodes...)
│   ├── mcp_server.py   统一图谱 MCP server (第5端点 18092, 8 工具)
│   ├── plugins/        各栈扫描插件 (frontend/backend/sql → GraphNode/Edge)
│   └── analyzers/      A1 业务域软节点 (LLM labeler, second post-pass)
├── cli.py              codev-platform <subcommand>
└── __init__.py

platform_meta/projects/   各 project 登记表 (meta.json)
codev_platform/resources/rules/    跨项目通用规则 (真值源;2026-06-01 relocate 进包, wheel 可交付)
codev_platform/resources/skills/   跨项目通用 skill (真值源;同上)
docs/plans/               team-deploy 系列 design / pr-body / 原 plan
config.example.json       用户级 config 样板 + 字段说明 (仍在仓根, 纯样本)
pyproject.toml            pip 包定义 (package-data 含 resources/**)
```

> **2026-06-01 relocate(audit #2)**:`rules/` `skills/` 从仓根移入 `codev_platform/resources/`,
> 使普通 `pip install`(非 editable)的 wheel 也带得走 → `sync-rules`/`sync-skills` 在客户机可用。
> CLI 经 `importlib.resources` 定位(editable + wheel 通用),旧仓根布局仍作 fallback。

### 易混命名澄清(同名不同职,勿混)

下列模块名字撞车但职责完全不同(各文件 docstring 有详述),改前先认准是哪一个:

| 名 | 职责 | 关系 |
|---|---|---|
| `codev_platform/recall/` | **代码召回**融合(Phase 6,跨 lane vector/bm25/codegraph/graph → weighted RRF)→ `recall_code` | 与下者无关 |
| `codev_platform/agent/recall/` | **记忆召回**流水线(memory recall:ACL/去重/redline/截断);`agent/recall_service.py` 是其薄 shim | 与上者无关 |
| `codev_platform/reindex/` | reindex **队列引擎**三层(queue/runners/worker,写侧串行) | 引擎 |
| `codev_platform/ops/reindex/` | 4 个 `.ps1` 的**跨平台 CLI 移植**(reindex/post-commit/dirty-check 命令) | CLI 命令 |
| `codev_platform/ops/reindex_queue.py` | `reindex-queue` **CLI 薄壳**(enqueue/status/worker),调 reindex 引擎 | 三者分层不重复 |

---

## 三、当前 project 接入清单

`platform_meta/projects/` 现登记 3 项目:

| project_id | 仓 | 状态 |
|---|---|---|
| `openclaw-stock` | helloworld3q3q-art/platform | 真业务,4427 chunks chroma |
| `codev-platform` | helloworld3q3q-art/codev-platform(本仓)| 工具栈本体,290 chunks |
| `codev-platform-widget` | helloworld3q3q-art/codev-platform-widget | Tray UI 开发中 |

---

## 四、规则 + Skill + Hook 分层策略

`codev_platform/resources/{rules,skills,hooks}/` 是**分发真值源**:给其它项目通过 `codev-platform sync-*` 拉取。这里必须放跨项目通用内容,不要塞 codev-platform 本仓专属规则。

`.claude/rules/` 是**本仓工作规则**。它可以引用/复制通用规则,也可以放 codev-platform 本仓私有覆盖(例如本仓自己的 `workflow.md`)。不要在平台仓随手跑 `sync-rules` 覆盖 `.claude/rules/` 本仓规则;只有明确要刷新分发副本时才这样做。

改规则 / skill 时先分层:
1. **跨项目通用** — 改 `codev_platform/resources/rules/<name>.md` 或 `codev_platform/resources/skills/<name>/SKILL.md`,再到目标业务仓跑 `codev-platform sync-rules` / `sync-skills`
2. **平台本仓专属** — 改 `.claude/rules/<name>.md`,不要同步回 `resources/rules/`
3. **业务仓专属** — 在业务仓自己的 `.claude/rules/` 维护,不进 codev-platform 分发真值源

**Hook(MCP-first 护栏)同步**:`.claude/hooks/mcp-first-guard.js` 是 PreToolUse(Grep)护栏 —— 每次 grep 前注入 MCP-first 提醒,把"定位先走 MCP、grep 最后"从自律变机制(防御三层:文档→hook→grep)。真值源 `codev_platform/resources/hooks/`。
1. **改真值源** — 改 `codev_platform/resources/hooks/mcp-first-guard.js`
2. **sync** — `codev-platform sync-hooks`(复制脚本 + 幂等 merge PreToolUse hook 进 `.claude/settings.json`,保留现有 settings 不覆盖)
3. **各业务仓** — 跑一次 `sync-hooks` 即装。node 脚本跨平台(macOS/Linux/Windows),不依赖 powershell/git-bash

### Skills 入口(多步流程真值源)

| 场景 | Skill |
|---|---|
| AI 工具栈体检(当前仓 / dirty-check / 全平台视图) | `/ai-health` |
| 手工重建 AI 索引(Chroma / CodeGraph / graph) | `/update-local-ai` |
| 标准 commit(合并提交 + post-commit 兜底 + 校验 reindex) | `/git-commit` |

---

## 五、跨仓改动协议

> **2026-05-28 平台所有权翻正后**:venv + 数据 + launcher 全归本仓(`.venv` / `data/` / `tools/`)。daemon 直接从本仓 `.venv` 跑,改源码即生效(本仓 editable 装在本仓 venv)。

改 codev-platform 代码时:

1. **本仓改完 commit + push**
2. **本仓 venv 已是 editable** —— 改 `codev_platform/*.py` 源码即生效,无需重装。仅新增重依赖时才 `uv pip install --python .venv\Scripts\python.exe -r requirements-runtime.txt`
3. **业务仓 shim 文件**(tools/_platform/ 等)— 若加了 symbol 要同步加 re-export 行(业务仓仍 `pip install -e` 本仓到它自己的 venv)
4. **重启 daemon**(daemon 持旧代码时):`Stop-Process -Id (Get-NetTCPConnection -LocalPort 18083 -State Listen).OwningProcess -Force`,下个 Claude 会话自动从新 launcher 重起

---

## 六、常用命令

```powershell
# CLI 自测
codev-platform current
codev-platform list-projects
codev-platform config show

# 业务仓接入演练
cd <new-project-repo>
codev-platform init <new-project-id>
codev-platform sync-rules
codev-platform sync-skills

# 索引自身仓 (codev-platform__platform_docs collection) — daemon 用本仓 .venv
.venv\Scripts\python.exe -m codev_platform.chroma.indexer --force
```

---

## 七、配置真值源

`~/.codev-platform/config.json`(用户级)— 字段全集见 `config.example.json`。

env > config > 代码默认。换机器只改 config,代码不动。

---

## 八、不要改

- `.venv/`(本仓根,heavy ML 依赖 4.66GB,gitignored;**2026-05-28 平台所有权翻正**后归本仓自有,从 `requirements-runtime.txt` 重建,见 `docs/plans/roadmap-2026-05-28/platform-ownership-inversion-2026-05-28.md`)
- `data/`(本仓根,chroma collection + graph_store/<pid>.sqlite 运行态,gitignored;多租户共享,按 project_id 隔离)
- 各业务仓 `.claude/project.json`(各仓自己写,本仓 CLI 只 init / 不远程改)
- 用户 `~/.codev-platform/config.json`(用户主权,本仓代码不主动覆盖,只通过 CLI `config init` 或 `--force`)

---

## 九、修改本文件的规则

- **不要 autoload**:本文件禁止加 `@.claude/rules/*.md` 引用(会触发级联 autoload,新会话即吃上下文);规则一律按需读取
- **新增本仓规则** → 放 `.claude/rules/<name>.md` 并在「规则索引」加一行;跨项目通用规则改 `codev_platform/resources/rules/` 再 `sync-rules`(分层见 §四)
- **新增 skill** → 加到 §四「Skills 入口」表
- **章节号被全仓按号引用,严禁 renumber 既有 §一~九**:`workflow.md` 引 `§四`;`docs/*.html` 引 `§五`/`§八`;`docs/plans/`、`docs/log/` 引 `§八`(部分在已归档目录,按 `weekly-iteration-cadence.md §七` 改不得)。新增段落一律用不编号标题(如「规则索引」)或顺延到下一个号
- **历史进度 / 路线图** → 沉到 `docs/plans/roadmap-*/`,不堆进本文件
- **改前规则真值源永远是** `.claude/rules/workflow.md`(§3 分级 + §6 映射)
