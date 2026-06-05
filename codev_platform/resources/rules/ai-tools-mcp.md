# AI 辅助工具栈（MCP）触发指南

> **完整说明**（架构原理 / 模型细节 / 故障手册 / 升级路线）由 codev-platform 平台方集中维护,不随规则分发到消费仓。
> 本文件只保留 AI 推理时**必需的强制规则 + 触发速查**,不重复完整文档内容。

---

## 〇、MCP-first 决策卡（最高频,动手前先扫这一条）

**判据**:要查的东西"codegraph/chroma 索引完整也答得上吗"——答得上就**别 grep**。

| 我要找 | 用 | 别 grep |
|---|---|---|
| 符号定义 / 签名 / 位置 | `codegraph_search`(onboarding/架构问题用 `codegraph_context`)| 函数名 |
| 调用方 / 影响面 / 改动波及 | `codegraph_callers` / `codegraph_impact` | 引用 |
| 规则 / 设计 / 事故文档 | `search_docs` | `docs/` |
| 前端↔端点↔表 跨层链路 / 业务域 | `graph` `find_table_usage` / `find_api_callers` | 多文件 |

**grep+Read 仅 4 种兜底场景合法**(详见 §2.1):未提交改动命中查询范围 / 索引滞后 / MCP 不可用 / 核对最新源码行号。
**自检(挂 §3.3 门禁)**:本轮用 grep 找了上面任一类?→ 先确认真命中兜底场景,否则改用 MCP 重来。

---

## 一、三套工具职责（互补不重复）

| 问题类型 | 用谁 | 关键工具 |
|---|---|---|
| 找代码定义 / 函数源码 / 调用关系 | **CodeGraph** | `codegraph_search` / `codegraph_context`（PRIMARY，组合 search+node+callers+callees）/ `codegraph_callers` / `codegraph_callees` / `codegraph_impact`（blast radius）/ `codegraph_node` / `codegraph_explore` / `codegraph_files` / `codegraph_status` |
| 找规则 / 设计文档 / 事故复盘 / 操作手册 | **platform-docs / Chroma** | `search_docs(query, category?, module?)` / `get_by_file` / `list_collections` |
| 找前端 API ↔ endpoint ↔ Table 跨层链路 / 业务域 | **graph**(统一图谱) | `find_impact` / `find_table_usage` / `find_api_callers` / `find_page_dependencies` / `find_impacted_pages` / `find_node_domain` / `list_domain_members` / `search_nodes` |
| 跨会话用户偏好 / 反馈 / 项目状态(本地自带) | **MEMORY** | 本地 MEMORY.md 自动加载,无需调工具 |
| 跨机 / 跨开发者共享的团队记忆(平台 PG) | **agent-memory** | `recall`(query-aware 去冲突 top-N)/ `list_scope`(诊断单作用域)。换机/重 clone 后同 token 召回回本人记忆 |

**反例**:不要用 `Grep` + `Read` 循环找代码或文档 —— MCP 已预索引,grep 50 文件 + Read 消耗上下文 10× 且不如索引精确。

---

## 一 b、MCP 接入方式 —— 平台 SSE 服务地址(2026-05-30 服务化)

四套 MCP 现在**都是平台 SSE 服务**,业务仓 `.mcp.json` 走 `type:sse` 连**服务地址**(不再 stdio 文件路径 launcher),支撑多用户 / 多机共享同一平台。

| 平台 MCP 服务 | 端点(默认端口) | 起法 |
|---|---|---|
| **platform-docs**(chroma) | `http://127.0.0.1:18083/sse?project_id=<id>` | daemon,`serve-mcp start` 拉起(预热 Qwen ~30-60s) |
| **graph**(统一图谱) | `http://127.0.0.1:18092/sse?project_id=<id>` | `serve-mcp start` 拉起 |
| **codegraph** | `http://127.0.0.1:<per-project 端口>/sse` | mcp-proxy 包 `codegraph serve --mcp`,`serve-mcp start` 拉起(每项目一端口) |
| **agent-memory**(2026-06-04 新增,读侧 MVP) | `http://127.0.0.1:18087/sse?project_id=<id>` | `serve-mcp start` 拉起;`?project_id=` 仅用于 project-scope 记忆,org/user 走 token 身份 |

> **agent-memory 是开发端共享记忆前门**:把平台分层记忆(personal/project + RBAC + redline)接给 IDE 编程 agent。当前**只读**(`recall`/`list_scope`),写侧(remember/forget/supersede)与迁移在后续阶段。**身份红线**:`org_id`/`user_id` 取认证 token 身份,**绝不由 client 传**;多 dev 共用同一 WSL 须用 token 模式(passthrough 会 personal 串号)。本地 MEMORY.md 与平台记忆**分层共存**(本地=草稿/离线 fallback,平台=团队真值层),非替代。

> **codegraph 也是平台服务**:它本是外部 stdio-only 工具,用 mcp-proxy 包成 SSE,**保全 9 个工具**(callers/impact/context...,不退化成 codegraph-api REST)。codegraph-api(:18082)只承担平台 status/统计的 HTTP 面,**不**承担 AI 的 MCP 查询。

**🚨 常驻依赖(运维必读)**:SSE 失去 stdio 的 per-session auto-spawn → **开机 / 重启电脑后必须跑一次**:

```powershell
codev-platform serve-mcp start     # 一键拉起 4 端点(chroma 预热 ~30-60s)
codev-platform serve-mcp status    # 确认全 OK(或 health --all 的 MCP 端点段)
```

不跑 → 业务仓 `/mcp` 四套全红(连不上端点)。**急救回退到旧 stdio 自 spawn 模式**:

```powershell
copy <业务仓>\.mcp.json.stdio.bak <业务仓>\.mcp.json   # 覆盖即恢复, 重启 Claude Code
```

端口配在 `~/.codev-platform/config.json`(`mcp.graph_sse_port` / `projects.<id>.codegraph_sse_port`;业务仓连固定 URL 须显式 pin,防自动分配漂移)。单机图省事也可直接用 `.stdio.bak` 退回文件路径模式(略快 + 自动拉起);本套收益在**跨机共享**。

**codegraph 索引数据也已集中到平台**(2026-05-30,与 cross-link 对称):
- 数据物理在 `data/codegraph_ext/<pid>/codegraph/`(与 `cross_layer.sqlite` 并排),业务仓 `<repo>/.codegraph` 是 **junction/symlink** 指向平台 —— 第三方 codegraph 工具透明无感。
- **读 / 服务 / 更新都走平台**:serve-mcp 的 codegraph 端点(cwd=repo)经 junction 读平台;`reindex --codegraph` 跑的 `codegraph sync` 写穿 junction 落平台。
- 命令:`codev-platform codegraph status|link|unlink`(`link --all` 把所有项目索引搬进平台 + 建联接,幂等)。
- **junction 是本机状态(不进 git)**:换机器 / 重 clone 业务仓后,跑一次 `codev-platform codegraph link --all` 重建联接(数据还在平台就只补联接,秒级)。

---

## 二、强制规则

### 2.1 优先 MCP,允许 grep+Read 兜底（4 种场景）

| 允许兜底场景 | 原因 |
|---|---|
| 未提交改动命中查询范围 | 索引最新到 HEAD,工作树新改的 MCP 看不到 |
| 怀疑索引滞后 | `update-local-ai.ps1` 没跑 / hook 漏触发 / `ai-health` 报 stale |
| MCP 工具不可用 | server 崩 / db locked / 网络问题 |
| 需要确认最新源码 | MCP 返回片段后核对行号、与最近编辑后的真实状态 |

**判 SOP**:调 CodeGraph / graph 前先 `git status -s` 看 dirty 文件,或直接跑 `tools\dev\dirty-index-check.ps1`(exit 0 = MCP 可信,exit 1 = 有 dirty 命中索引范围,建议兜底)。

### 2.2 commit 后 60 秒新鲜度检查（HIGHEST PRIORITY）

post-commit hook 后台跑 ~30s,**窗口期内 MCP 可能拿到 HEAD~1 数据**。违反这条会让 agent 改代码时凭空捏造"还存在的函数",或漏看刚加的字段。

```
触发: git log -1 --format=%cr 显示 "X seconds ago" 且 X < 60
必走: 调 MCP 前先 codegraph_status,看 last_indexed_at >= 上次 commit 时间
  ├─ 对齐 → 正常用 MCP
  └─ 未对齐 → 等 30s 重试 / 退回 grep+Read 兜底
```

或调 `tools\dev\wait-for-reindex.ps1`(默认等 HEAD 对齐,超时 120s)。

### 2.3 子模块规则强制触发（HIGHEST PRIORITY）

子模块 `.claude/rules/`(约 2200 行)**不会自动加载**。改子模块代码**前**必须先:

| 改动范围 | 必走查询 |
|---|---|
| `apps/stock-admin-api/**/*.java` | `search_docs(query="<主题>", module="stock-admin-api")` |
| `apps/stock-admin-web/**/*.{ts,tsx,less}` | `search_docs(query="<主题>", module="stock-admin-web")` |
| `python/stock-pipeline/**/*.py` | `search_docs(query="<主题>", module="stock-pipeline")` |

例外:纯格式化 / 拼写修正 / 单测断言数字微调。

### 2.4 onboarding / 架构问题先用 codegraph_context

"如何理解 X" / "X 模块干啥的" / "怎么改 Y" 类问题,第一步必 `codegraph_context`(PRIMARY)。一次返回 search + node + callers + callees 组合,等价 4-5 次单调用。

---

## 三、标准工作流（速查）

| 场景 | 链 |
|---|---|
| 修 bug | `codegraph_search` → `codegraph_callers` → `codegraph_impact` → `search_docs(相关规则)` → `find_table_usage(相关表)` → Edit |
| 写新功能 | `search_docs(类似设计)` → `codegraph_context(类似实现)` → `find_api_callers(类似 endpoint)` → 实现 → 测试 |
| 跨层改动 | `find_api_callers` → `find_table_usage` → 按 **Python → Java → 前端** 顺序 → `pnpm run api` → `search_nodes` 验证 |

---

## 四、故障应急速查

| 现象 | 第一步处理 |
|---|---|
| **业务仓 `/mcp` 全红**(切 SSE 后,连不上 18083/18087/18092/codegraph 端口) | 平台端点没常驻 —— 跑 `codev-platform serve-mcp start` 拉起端点,`serve-mcp status` 确认全 OK。**重启电脑后必跑一次**(§一 b 常驻依赖) |
| **单独 codegraph SSE 红**(platform-docs/graph 正常) | mcp-proxy 没起 / `codegraph` 命令缺。看 `codev_platform/mcp_serve_logs/codegraph_<pid>.log`;`serve-mcp start` 重拉。确认 venv 有 `mcp-proxy.exe`(`ai-health` 的 `mcp-proxy` 行) |
| **想退回旧 stdio 文件路径模式** | `copy <业务仓>\.mcp.json.stdio.bak <业务仓>\.mcp.json` → 重启 Claude Code,恢复 per-session 自 spawn(单机略快,无需 serve-mcp start) |
| `codegraph database is locked` | 看 `.codegraph/codegraph.db.lock` stale(0 字节 + 数小时未变)即删 |
| platform-docs 召回质量差 | `update-local-ai.ps1 -SkipCodeGraph`(只重建 Chroma) |
| platform-docs **第一次 search_docs 超时** | 冷启动模型加载(embedding + reranker 各 ~15-30s)。已加 `PLATFORM_DOCS_PREWARM=true` + reranker 显式 prewarm 应消除,若仍超时:不要凭超时下"索引没数据"结论 → 等 30-60s 重试一次再判断 |
| platform-docs MCP `-32602 Invalid params` 持续 | 重启 Claude Code;若仍在,检查 `.cmd` 行尾 CRLF |
| platform-docs **多会话 CUDA OOM** | 8GB GPU 上每个 stdio mcp_server 占 ~3GB,第 2 个会话 OOM。已切 daemon mode(2026-05-24):cmd 走 `platform_docs_launcher.py` → 第一个会话 spawn detached daemon (`mcp_server.py --http` 端口 18083),后续会话 stdio↔HTTP proxy 连同一 daemon,GPU 占用恒定 ~3GB 不随会话数增长。`ai-health` 的 `platform-docs daemon` / `platform-docs servers` 报告状态;真要回退旧 stdio 模式:`set PLATFORM_DOCS_DAEMON_MODE=false` |
| platform-docs daemon 起不来 | 看 `tools/chroma/daemon.log`(launcher spawn 的 daemon 日志重定向于此);常见原因:端口 18083 被占(`netstat -ano \| findstr 18083` 查 PID)/ Qwen 模型路径不对(`D:\models\Qwen3-Embedding-0.6B` 缺失) |
| platform-docs **daemon 运行中崩了** (search_docs 突然全失败) | mcp-proxy 还连着死的 daemon,所有 search 报错。**重启 Claude Code** 让 launcher 重检测 + 自动 spawn 新 daemon。`ai-health` 的 `platform-docs daemon` 会报 `not running`,`platform-docs servers` 会报 `0 servers`,组合判断 = daemon 真死了。常见诱因:GPU 驱动更新 / Windows 系统重启 / 手动 kill |
| platform-docs **chroma reindex 撞锁** (`update-local-ai.ps1` 报"另一个 reindex 已在跑") | 正常拒绝行为(并发写 chroma 会损坏 db)。等先前 reindex 完成,或确认 stale(`data/chroma/.reindex.lock` 文件 > 30 分钟未变)后手动 `Remove-Item data\chroma\.reindex.lock` 重试 |
| launcher 多 session **同时 cold start** | launcher 用 `tools/chroma/.daemon.spawn.lock` 串行化 spawn,后到的等 120s 让先到的 spawn 完成再 hand off。stale lock(> 120s 未变)自动抢占。**无需人工介入**,日志看 `[platform-docs-launcher] another launcher is spawning daemon, waiting...` |
| graph 数据陈旧 | 重建图谱(`update-local-ai` graph 档);用 `search_nodes` 抽样核对节点是否更新 |
| MCP 完全连不上 | `~/.claude.json` 检查;重启 Claude Code(**不是 /clear**) |
| post-commit hook 静默失败 | 跑 `ai-health` 看 `hook missed?`;WARN 则手动 `powershell -File tools/dev/post-commit.ps1` |
| 需等 reindex 完成再调 MCP | `powershell -File tools/dev/wait-for-reindex.ps1`(秒回 exitCode 0/1/2) |

**完整故障表** + 模型升级历程 + JSONL 召回日志分析 + A/B test 工具 等由 codev-platform 平台方集中维护。

---

## 四 b、Subagent prompt 模板(2026-05-23 起,防过度产出)

派 subagent 时 prompt **必须 50 行内**(以前给到 200+ 行规则,导致 agent 第一版规则文档 380 行,被用户砍到 90 行,浪费 ≈30K tokens)。模板:

```
【目标】 30 字以内,1 行
【文件路径】 1-3 行,绝对路径
【禁止】 ≤5 条
【验证】 grep / pytest / mvn 命令 1-2 条
【输出长度】 显式上限(如 "150-200 行内")
```

**核心原则**:不预塞规则(让 agent 按需 `search_docs`),只给目标 + 边界 + 验证。
agent 自己应该会主动查相关规则,你不需要替它把规则贴在 prompt 里。

❌ 反例(本会话 2026-05-23 N12 派 agent 写规则用了 200 行 prompt → 输出 380 行被砍到 90 行):

```
【唯一方案】
... 200 行说明 ...

【背景上下文】(写入规则文件正文)
- 详细描述事故...
- 详细描述根因...

【规则文件结构】
1. 章节 A 必含...
2. 章节 B 必含...
...10 个章节细节
```

✅ 正例:

```
【目标】 写一份"跨层枚举一致性"规则,沉淀 2026-05-23 N12 教训
【文件路径】 .claude\rules\cross-layer-enum-consistency.md
【背景关键词】 N12 / FETCHER_GROUP_MAP / 单一真值源
【禁止】 不超过 150 行 / 不在 CLAUDE.md 加 @ 引用 / 不抄旧规则原文
【输出】 含事故复盘 + 强制原则 + grep 自检 + PR 清单 4 章节
```

让 agent 自己 `search_docs` 查参考规则,而非你贴进去。

---

## 五、Skill 入口

| Skill | 用途 |
|---|---|
| `/ai-health` | 12 项体检 + dirty-check(三档:health / dirty / both) |
| `/update-local-ai` | 手工重建索引(四档:all / Chroma / CodeGraph / graph) |

---

## 已有 assertion 兜底

⚪ 纯流程约定,无运行时断言。依赖 AI 主动按规则触发工具 + 用户 review tool_use 记录判断是否真用了 MCP。

未来 candidate:metrics 统计 `mcp__*` vs `Grep+Read` 调用比例;长任务 > 10 次 Read 自动提示改用 `codegraph_explore`。
