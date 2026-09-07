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
| 项目图谱 / 模块依赖 / 数据流 | `graph` `search_nodes` / `find_impact` / 项目图谱工具 | 多文件 |

**grep+Read 仅 4 种兜底场景合法**(详见 §2.1):未提交改动命中查询范围 / 索引滞后 / MCP 不可用 / 核对最新源码行号。
**自检(挂 §3.3 门禁)**:本轮用 grep 找了上面任一类?→ 先确认真命中兜底场景,否则改用 MCP 重来。

---

## 一、三套工具职责（互补不重复）

| 问题类型 | 用谁 | 关键工具 |
|---|---|---|
| 找代码定义 / 函数源码 / 调用关系 | **CodeGraph** | `codegraph_search` / `codegraph_context`（PRIMARY，组合 search+node+callers+callees）/ `codegraph_callers` / `codegraph_callees` / `codegraph_impact`（blast radius）/ `codegraph_node` / `codegraph_explore` / `codegraph_files` / `codegraph_status` |
| 找规则 / 设计文档 / 事故复盘 / 操作手册 | **platform-docs / Chroma** | `search_docs(query, category?, module?)` / `get_by_file` / `list_collections` |
| 找模块依赖 / 项目图谱 / 页面-接口-数据流 | **graph**(统一图谱) | `find_impact` / `find_page_dependencies` / `find_impacted_pages` / `find_node_domain` / `list_domain_members` / `search_nodes` |
| 跨会话用户偏好 / 反馈 / 项目状态(本地自带) | **MEMORY** | 本地 MEMORY.md 自动加载,无需调工具 |
| 跨机 / 跨开发者共享的团队记忆(平台 PG) | **agent-memory** | `recall`(query-aware 去冲突 top-N)/ `list_scope`(诊断单作用域)。换机/重 clone 后同 token 召回回本人记忆 |

**反例**:不要用 `Grep` + `Read` 循环找代码或文档 —— MCP 已预索引,grep 50 文件 + Read 消耗上下文 10× 且不如索引精确。

### 一 a、platform-docs 过滤语义

- `category` / `module` 是索引器按**文件路径**生成的精确过滤字段，不是文档内容主题。仓库根
  `docs/**` 通常归 `module=platform`；`docs/plans/**` 通常是 `category=dev_log`，
  `docs/architecture/roadmap-*/**` 通常是 `category=design`。
- 子模块查询中的 `module=<子模块>` 只用于查该子目录内的规则 / 事故；查仓库级 roadmap 或架构记录
  要另调 `module=platform`，不确定时先用 `module=all`。
- 收到 `filter_miss` 或过滤查询返回空时，先看 `list_collections`，按提示放宽单个过滤字段重试；
  已知路径用 `get_by_file`。只有这些复核仍失败且 owner-side manifest 落后，才能判断索引滞后。

---

## 一 b、MCP 接入方式 —— 按客户端 surface 选择传输

四套 MCP 都由平台常驻服务提供，但客户端配置不可混用：

| 客户端 | 配置真值 | 传输与验收 |
|---|---|---|
| Codex | 项目/用户 `.codex/config.toml` | streamable HTTP `/mcp`；用 `codex mcp list` / `codex mcp get <name>` 验证 |
| Claude Code 兼容层 | 项目 `.mcp.json` | SSE `/sse`；重启对应 Claude Code 会话后用 `/mcp` 验证 |

端点地址必须来自 `~/.codev-platform/config.json` 的 `mcp_sources.<target>`，不得把 18xxx/19xxx 写死到规则：本机 owner 通常使用 18xxx，Windows 访问 WSL owner 默认使用 platform source 19xxx。认证 token 只走对应客户端的环境变量引用，不写入项目文件。

> **agent-memory 是开发端共享记忆前门**:把平台分层记忆(personal/project + RBAC + redline)接给 IDE 编程 agent。当前**只读**(`recall`/`list_scope`),写侧(remember/forget/supersede)与迁移在后续阶段。**身份红线**:`org_id`/`user_id` 取认证 token 身份,**绝不由 client 传**;多 dev 共用同一 WSL 须用 token 模式(passthrough 会 personal 串号)。本地 MEMORY.md 与平台记忆**分层共存**(本地=草稿/离线 fallback,平台=团队真值层),非替代。

> **codegraph 也是平台服务**:它本是外部 stdio-only 工具,用 mcp-proxy 包成 SSE,**保全 9 个工具**(callers/impact/context...,不退化成 codegraph-api REST)。codegraph-api(:18082)只承担平台 status/统计的 HTTP 面,**不**承担 AI 的 MCP 查询。

**🚨 常驻依赖(运维必读)**：开机、重启或端点异常后运行：

```powershell
codev-platform serve-mcp start     # 一键拉起 4 端点(chroma 预热 ~30-60s)
codev-platform serve-mcp status    # 确认全 OK(或 health --all 的 MCP 端点段)
```

Codex 与 Claude Code 可以连接同一服务，但各自只维护自己的配置入口。Claude 的 `.mcp.json.stdio.bak` 仅是 Claude 兼容回退，不得复制到 Codex 配置。

**codegraph 索引数据也已集中到平台**(2026-05-30):
- 数据物理在 `data/codegraph_ext/<pid>/codegraph/`,业务仓 `<repo>/.codegraph` 是 **junction/symlink** 指向平台 —— 第三方 codegraph 工具透明无感。
- **读 / 服务 / 更新都走平台**:serve-mcp 的 codegraph 端点(cwd=repo)经 junction 读平台;`reindex --codegraph` 跑的 `codegraph sync` 写穿 junction 落平台。
- 命令:`codev-platform codegraph status|link|unlink`(`link --all` 把所有项目索引搬进平台 + 建联接,幂等)。
- **junction 是本机状态(不进 git)**:换机器 / 重 clone 业务仓后,跑一次 `codev-platform codegraph link --all` 重建联接(数据还在平台就只补联接,秒级)。

---

## 二、强制规则

### 2.1 优先 MCP,允许 grep+Read 兜底（4 种场景）

| 允许兜底场景 | 原因 |
|---|---|
| 未提交改动命中查询范围 | 索引最新到 HEAD,工作树新改的 MCP 看不到 |
| 有证据怀疑索引滞后 | manifest 未覆盖目标 commit；先走 queue/hook/wait 增量恢复，不直接重建 |
| MCP 工具不可用 | server 崩 / db locked / 网络问题 |
| 需要确认最新源码 | MCP 返回片段后核对行号、与最近编辑后的真实状态 |

**判 SOP**:调 CodeGraph / graph 前先 `git status -s` 看 dirty 文件,或直接跑 `tools\dev\dirty-index-check.ps1`(exit 0 = MCP 可信,exit 1 = 有 dirty 命中索引范围,建议兜底)。

### 2.1a 索引恢复边界（HIGHEST PRIORITY）

1. `health --all` 失败只表示平台 HTTP 服务不可达。运行 `codev-platform serve-mcp start` / `serve-mcp status`，不得用 reindex 启服务。
2. hook enqueue 失败或 HEAD 未覆盖时，运行 `codev-platform reindex-queue status`、`git hook run post-commit`、`codev-platform wait-for-reindex --commit HEAD --timeout-sec 300`。
3. Windows 不得直接运行 `codev-platform post-commit` 或旧 PowerShell wrapper 写 WSL file queue；已安装 Git hook 是唯一手工 relay 入口。
   Windows + WSL owner 下也不得运行 `reindex-queue status` 直读 UNC；用 `serve-mcp status` 查 WSL owner、用 `wait-for-reindex` 经 HTTP 查 manifest。`serve-mcp start/status` 只管理 platform source 19xxx，不启动 Windows 18xxx 影子服务。
4. manifest/result 是完成真值；`reindex.log` 没有 `finished`、queue pending、daemon 停止、MCP 冷启动/超时、一次 wait 超时都不证明索引损坏。
5. full/scoped rebuild 只允许用户明确要求、批准的模型/schema 迁移、或完整性证据证明损坏且增量恢复无效；先登记计划并选择最小 scope。

### 2.2 commit 后 60 秒新鲜度检查（HIGHEST PRIORITY）

post-commit hook 后台跑 ~30s,**窗口期内 MCP 可能拿到 HEAD~1 数据**。违反这条会让 agent 改代码时凭空捏造"还存在的函数",或漏看刚加的字段。

```
触发: git log -1 --format=%cr 显示 "X seconds ago" 且 X < 60
必走: 调 MCP 前先 codegraph_status,看 last_indexed_at >= 上次 commit 时间
  ├─ 对齐 → 正常用 MCP
  └─ 未对齐 → 等 30s 重试 / 退回 grep+Read 兜底
```

或调 `codev-platform wait-for-reindex --commit HEAD --timeout-sec 120`；该命令以 manifest/result 为主，兼容日志只作辅助。

### 2.3 项目本地规则强制触发（HIGHEST PRIORITY）

项目 `.codex/rules/` 或 `.claude/rules/` 中的本地规则不会总是自动加载。改项目代码前必须先按当前客户端 surface 查项目画像:

| 改动范围 | 必走查询 |
|---|---|
| 前端 / UI / 页面 / 组件 | `search_docs(query="<主题> 前端 组件 API", module="<项目模块>")` 或读本地前端规则 |
| 后端 / API / 服务 | `search_docs(query="<主题> 后端 API 服务", module="<项目模块>")` 或读本地后端规则 |
| 数据 / schema / migration | `search_docs(query="<主题> 数据 schema migration", module="<项目模块>")` 或读本地数据规则 |
| 任务 / 消息 / 异步链路 | `search_docs(query="<主题> 任务 消息 消费者", module="<项目模块>")` 或读本地任务规则 |
| AI 工具 / MCP / 索引 | `search_docs(query="<主题> MCP 索引 graph codegraph")` |

例外:纯格式化 / 拼写修正 / 单测断言数字微调。

### 2.4 onboarding / 架构问题先用 codegraph_context

"如何理解 X" / "X 模块干啥的" / "怎么改 Y" 类问题,第一步必 `codegraph_context`(PRIMARY)。一次返回 search + node + callers + callees 组合,等价 4-5 次单调用。

---

## 三、标准工作流（速查）

| 场景 | 链 |
|---|---|
| 修 bug | `codegraph_search` → `codegraph_callers` / `codegraph_impact` → `search_docs(相关规则)` → Edit → 定向测试 |
| 写新功能 | `search_docs(类似设计)` → `codegraph_context(类似实现)` → 实现 → 测试 |
| 跨层改动 | `search_docs(项目本地契约规则)` → `graph` 链路查询(如已接入) → 生产者/消费者两侧实现 → 契约验证 |

---

## 四、故障应急速查

| 现象 | 第一步处理 |
|---|---|
| **业务仓 MCP 全红** | 跑 `codev-platform serve-mcp start` 拉起 owner 对应端点，再用 `serve-mcp status`；不要凭端口失败触发 reindex |
| **单独 codegraph SSE 红**(platform-docs/graph 正常) | mcp-proxy 没起 / `codegraph` 命令缺。看 `<PLATFORM_DATA_DIR>/mcp_serve_logs/codegraph_<pid>.log`;`serve-mcp start` 重拉。确认 venv 有 `mcp-proxy.exe`(`ai-health` 的 `mcp-proxy` 行) |
| **想退回旧 stdio 文件路径模式** | 仅适用于 Claude 兼容入口；Codex 继续使用 `.codex/config.toml` 的 `/mcp` 配置 |
| `codegraph database is locked` | 看 `.codegraph/codegraph.db.lock` stale(0 字节 + 数小时未变)即删 |
| platform-docs 召回质量差 | 先复核 query/filter、manifest 和定向样本；只有证明 Chroma 损坏后才按计划最小重建 |
| platform-docs **第一次 search_docs 超时** | 冷启动模型加载(embedding + reranker 各 ~15-30s)。已加 `PLATFORM_DOCS_PREWARM=true` + reranker 显式 prewarm 应消除,若仍超时:不要凭超时下"索引没数据"结论 → 等 30-60s 重试一次再判断 |
| platform-docs MCP `-32602 Invalid params` 持续 | Codex 检查 `.codex/config.toml` 与 `codex mcp get`；Claude 兼容入口检查 `.mcp.json`，然后重启对应会话 |
| platform-docs **多会话 CUDA OOM** | 8GB GPU 上每个 stdio mcp_server 占 ~3GB,第 2 个会话 OOM。已切 daemon mode(2026-05-24):cmd 走 `platform_docs_launcher.py` → 第一个会话 spawn detached daemon (`mcp_server.py --http` 端口 18083),后续会话 stdio↔HTTP proxy 连同一 daemon,GPU 占用恒定 ~3GB 不随会话数增长。`ai-health` 的 `platform-docs daemon` / `platform-docs servers` 报告状态;真要回退旧 stdio 模式:`set PLATFORM_DOCS_DAEMON_MODE=false` |
| platform-docs daemon 起不来 | 看 `<PLATFORM_DATA_DIR>/logs/chroma_daemon.log`(launcher spawn 的 daemon 日志重定向于此);常见原因:端口 18083 被占(`netstat -ano \| findstr 18083` 查 PID)/ Qwen 模型路径不对(`D:\models\Qwen3-Embedding-0.6B` 缺失) |
| platform-docs **daemon 运行中崩了** (search_docs 突然全失败) | 先用 `serve-mcp start/status` 恢复 owner 服务，再重启当前客户端会话；服务失败不证明索引损坏 |
| platform-docs **chroma reindex 撞锁** | 正常拒绝行为；查 owner/worker 状态并等待，不手删 data 下锁文件，不并发启动第二个 rebuild |
| launcher 多 session **同时 cold start** | launcher 用 `chroma_daemon.spawn.lock` 作为短期启动门；轻量 `daemon_entry` 在加载 Chroma/Torch 前取得 `chroma_daemon.lifecycle.lock`，并持有到 daemon 退出。父进程只在有界时间内等待锁交接和健康状态；失败竞争者不加载模型，任一进程异常退出都由内核释放锁。两个锁文件位于 `<PLATFORM_DATA_DIR>/run/`、长期保留，禁止按 mtime 删除，**无需人工删锁**。 |
| graph 数据疑似陈旧 | 先用 manifest + `search_nodes` 抽样证明；增量恢复无效且有计划时才跑 graph 最小 scope |
| MCP 完全连不上 | Codex 检查 `.codex/config.toml` / `codex mcp list/get`；Claude 兼容入口检查 `.mcp.json` / `~/.claude.json`；随后重启对应会话 |
| post-commit hook 静默失败 | 跑 `ai-health` 看 `hook missed?`;WARN 则运行 `git hook run post-commit` |
| 需等 reindex 完成再调 MCP | `codev-platform wait-for-reindex --commit HEAD --timeout-sec 300` |

**完整故障表** + 模型升级历程 + JSONL 召回日志分析 + A/B test 工具 等由 codev-platform 平台方集中维护。

---

## 四 b、Subagent prompt 模板(2026-05-23 起,防过度产出)

派 subagent 时 prompt **必须 50 行内**(以前给到 200+ 行规则,导致 agent 第一版规则文档 380 行,被用户砍到 90 行,浪费 ≈30K tokens)。模板:

```
【目标】 30 字以内,1 行
【文件路径】 1-3 行,绝对路径
【禁止】 ≤5 条
【验证】本地搜索 / 测试 / lint 命令 1-2 条
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
【目标】 为配置 merge 增加幂等测试
【文件路径】 <repo>\tests\test_config_merge.py
【背景关键词】 settings.json / 保留既有配置 / 重复执行不叠加
【禁止】 不改 CLI 行为 / 不碰无关测试 / 不写临时文件到仓根
【验证】 <项目测试命令>
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
