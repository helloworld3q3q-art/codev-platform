---
name: ai-health
description: 检查本地 AI 工具栈状态 —— 当前仓体检 (health) + 工作树索引一致性 (dirty-check) + 平台全局视图 (health --all,所有项目 x 三库 + 记忆)。用于排查 MCP 召回怪 / 索引滞后 / 重启电脑后验栈 / 升级模型后确认生效 / 看全平台各项目库规模
---

# AI 工具栈体检

> 运维已是跨平台 CLI:`codev-platform health` / `dirty-check` / `reindex`(Win/Mac/Linux 通用,机器路径走 `~/.codev-platform/config.json`)。旧 `tools/dev/*.ps1` 仅 Windows 薄 shim,转调 CLI。

**触发场景**:
- 用户问 "AI 工具有没有挂" / "MCP 工具状态" / "三个库还好吗" / "Qwen3 生效了吗"
- "看全平台 / 每个项目的库用了多少 / 三个项目 memory 串没串" → `health --all`
- 重启电脑 / 升级模型 / 改 hook 配置后想确认基础设施正常
- AI 召回质量怪,先排除"工具栈本身坏了"
- 改了一堆代码没 commit,想问 AI 决策前先确认 MCP 索引是否同步

## 🚨 mode 选择 (重要)

**当用户只说"AI 工具状态"/"检查工具栈"不指明具体诉求时，按当前客户端可用的交互能力选择最小模式；无法交互时默认 `both` 并先简短说明**:

| 选项 | 含义 | 适用场景 |
|---|---|---|
| `health` (当前仓体检) | `codev-platform health` — 本机 owner 检查模型/库/GPU；Windows + WSL owner 自动改走平台 HTTP 快照，不打开 UNC 数据库/队列 | 重启 / 升级后,怀疑底层 |
| `health --all` (平台全局) | `codev-platform health --all` — **HTTP 客户端**:GET 平台服务 `/platform/status`,返回所有项目 x 三库 + 记忆 + 使用率。**不读本地路径**(原则:访问平台数据走 HTTP) | 看全平台规模 / 各项目对比 / 记忆隔离 / 子应用查自己 |
| `dirty` (索引一致性) | `codev-platform dirty-check` — 列工作树命中索引范围的 dirty 文件 | 改了没 commit,要让 AI 决策前 |
| `both` (推荐) | health + dirty 都跑 | 不确定时默认 |

直接信号词:
- "工具栈坏了 / 模型加载了吗 / GPU 有没有用" → `health`
- "全平台 / 每个项目 / 三个项目对比 / memory 串没串" → `health --all`
- "MCP 看到我刚改的代码吗 / 索引滞后吗 / 没 commit 的会怎样" → `dirty`
- "整体看一下" / 没说清 → `both`

## 用法

```bash
# 当前仓体检 — 默认 full(~15s,含模型/GPU 探针);light 跳过重探针更快
codev-platform health
codev-platform health --mode light

# 平台全局视图 — 所有项目 x 三库 + 记忆(HTTP 客户端,调平台服务 /platform/status)
codev-platform health --all      # 需 daemon 在跑(平台服务);远程平台配 config.platform.url

# 工作树索引一致性 — 列受影响文件 + 兜底建议
codev-platform dirty-check
codev-platform dirty-check --json   # 给脚本 / AI 调

# 写健康快照 JSON(widget 读):省略路径=platform_meta/health/<pid>.json
codev-platform health --json-out
```

## 恢复门禁

健康检查只报告证据，不自动扩大为重建授权。按以下顺序恢复：

1. `health --all` 连不上平台 HTTP：只运行 `codev-platform serve-mcp start` 和 `serve-mcp status`。这不代表索引陈旧或损坏。
2. `hook missed?`、enqueue 失败或怀疑 HEAD 未覆盖：运行 `git hook run post-commit` 和 `codev-platform wait-for-reindex --commit HEAD --timeout-sec 300`。Windows + WSL owner 用 `serve-mcp status` 查 owner；`reindex-queue status` 只在 owner runtime 执行。
3. 完成以目标 commit 的 manifest/result 为准；`reindex.log` 只用于诊断，缺少 `finished` 不能证明失败。
4. queue pending、daemon 停止、MCP 冷启动/超时、单次等待超时均不得触发 `codev-platform reindex` 或 `update-local-ai`。
5. 只有用户明确要求、批准的模型/schema 迁移、或已证明索引损坏且增量恢复无效时，才使用 `/update-local-ai` 的最小重建 scope。

Windows + WSL file queue 场景禁止直接执行 `codev-platform post-commit`、`reindex-queue status` 或旧 PowerShell wrapper 访问 UNC 队列；必须通过 `git hook run post-commit` 复用已安装 relay。此时 `serve-mcp start/status` 自动管理 platform source 19xxx 的 WSL systemd 服务，不启动 Windows 18xxx 影子进程。

## 输出解读

### health(当前仓)关键项

| 项 | 看什么 |
|---|---|
| `embed model` / `embed load` | Qwen3-Embedding-0.6B 在 + dim=1024 / query_prompt=True(instruction-aware 已激活)|
| `torch cuda` | cuda=True + GPU 名(cuda=False → 模型走 CPU,慢 10×)|
| `chroma collection` | 当前仓 chunks 数 / 维度 / 模型名 |
| `chroma freshness` | 索引比最新 .md 新 → 不滞后 |
| `platform-docs daemon` | :18083 daemon 活着 + 模型/reranker 已 loaded |
| `graph store` | 统一图谱(承接退役的 cross-link);全栈仓才有(configured/not built/nodes 数)|
| `codegraph db` | nodes / edges + integrity=ok + WAL |
| `hook missed?` | manifest 是否覆盖 HEAD；日志仅补充 trigger/enqueue 诊断 |
| `usage stats` | search_recall 命中率 / reindex 7d / platform-docs 采纳率 / codegraph 调用 |

**退出码**:全 OK → 0 / 任一 WARN → 2 / 任一 FAIL → 1。
**注意**:exit 2 是 WARN 不是失败,harness 会标"Error"但工具栈正常,看 SUMMARY 行为准。

### health --all(平台全局)每项目一块

```
[<project_id>]
    chroma 文档 = N chunks
    codegraph 代码 = nodes=N edges=N        # 每仓 .codegraph,经 config.projects.<id>.repo_path 定位
    graph 统一图谱 = nodes=N / 未建         # 跨层链路(承接退役的 cross-link)
    memory 项目专属 = N 条  (+ org 共享 M)  # project 作用域,只该项目召回
    使用率(7d) = search_docs N / graph N / codegraph N  # 按 project_id 拆
合计: chroma 全项目总数 ; memory M org + K project
```
> 使用率按 project_id 分:chroma 召回日志加了 project_id 字段(**daemon 重启后**新查询才分项目;旧日志归 "legacy 无 project_id");graph / codegraph 日志本就带 project_id(codegraph 自写代理 server.py 的 codegraph_usage.jsonl 每次调用打点)。

- **访问走 HTTP 服务地址**:`--all` 是客户端,GET 平台 daemon 的 `/platform/status`(`config.platform.url` 或默认 `https://example.invalid/reference:<daemon.port>`);服务端跑在平台主机上聚合本机 data/+PG,客户端不碰路径。**子应用 / 远程机器查平台数据用同一个地址** —— 这才能多用户多项目共享。
- **chroma / graph / memory** 中心化(`data/` + PG 一个库),服务端直读。
- **codegraph**:平台读本机 `repo_path` 下 `.codegraph` sqlite 取统计(标 "本地 sqlite")。(Java codegraph-api :18082 HTTP 取数路径已退役 2026-06-04 —— 查询面由 codev web routes graph 接口替代;跨机取统计未来走 web routes,不再用 Java api。)
- **org 共享记忆**全项目通用(有意共享);**project 记忆**只该项目召回(隔离),`--all` 一眼看出谁有几条、串没串。
- daemon 没起 → `--all` 报连不上；用 `serve-mcp start/status` 恢复服务，不运行 reindex。

### dirty-check 输出
列 `[CodeGraph] / [Chroma]` 命中索引范围的 dirty 文件。**退出码**:干净→0 / 命中→1 / 非 git→2。命中时:允许 grep/read 兜底,或 commit 让 post-commit hook 重建。

## 故障应对速查

| 症状 | 处理 |
|---|---|
| `embed model` 路径错 / 文件不全 | 检查 `config.models.embed_path` 指向的目录是否完整 |
| `torch cuda` cuda=False | 显卡驱动 / torch CUDA 版本不匹配,需重装 |
| `chroma freshness` lag > 7d | 先核对 queue + manifest + target commit；只有证明损坏/迁移获批才最小重建 |
| `codegraph db` nodes 骤降 | 先查 manifest、完整性和可复现查询；证明损坏后按计划重建 |
| `hook missed?` WARN | `git hook run post-commit`，再用 `wait-for-reindex` 核对 manifest |
| `health --all` 连接失败 | `codev-platform serve-mcp start` → `serve-mcp status` |
| `--all` 某项目 codegraph "仓路径未登记" | 在 `~/.codev-platform/config.json` 配 `projects.<id>.repo_path` |
| dirty 命中 CodeGraph / Chroma | 允许 grep/read 兜底,或 commit 让 hook 跑 |

## 相关规则

- 触发指南：当前客户端 surface 下的 `rules/ai-tools-mcp.md`（CodeGraph / Chroma / graph 何时用）
- 增量恢复：`git hook run post-commit` + `wait-for-reindex`；重建必须满足 `/update-local-ai` 硬门禁

## 5 视角

- **架构**:基础设施健康直接影响 AI 输出质量,需要可观测;`--all` 让平台方一眼看全各项目库规模
- **工程**:dirty-check 防止"AI 用过时索引误导决策",特别是关键路径改动
- **维护**:重启 / 升级后几秒确认所有组件在位,避免长时间盲跑
