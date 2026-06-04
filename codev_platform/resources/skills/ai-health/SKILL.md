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

**当用户只说"AI 工具状态"/"检查工具栈"不指明具体诉求时,必须用 AskUserQuestion 让用户选**:

| 选项 | 含义 | 适用场景 |
|---|---|---|
| `health` (当前仓体检) | `codev-platform health` — 当前仓 ~17 项检查 (Qwen3 模型 / Chroma chunks / CodeGraph db / GPU / daemon / git / 使用率) | 重启 / 升级后,怀疑底层 |
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

## 输出解读

### health(当前仓)关键项

| 项 | 看什么 |
|---|---|
| `embed model` / `embed load` | Qwen3-Embedding-0.6B 在 + dim=1024 / query_prompt=True(instruction-aware 已激活)|
| `torch cuda` | cuda=True + GPU 名(cuda=False → 模型走 CPU,慢 10×)|
| `chroma collection` | 当前仓 chunks 数 / 维度 / 模型名 |
| `chroma freshness` | 索引比最新 .md 新 → 不滞后 |
| `platform-docs daemon` | :18083 daemon 活着 + 模型/reranker 已 loaded |
| `cross_layer` | 全栈仓才有(configured/not built/nodes 数)|
| `codegraph db` | nodes / edges + integrity=ok + WAL |
| `hook missed?` | HEAD commit 命中索引但没进 reindex.log → WARN 提示重跑 |
| `usage stats` | search_recall 命中率 / reindex 7d / platform-docs 采纳率 / cross-link 调用 |

**退出码**:全 OK → 0 / 任一 WARN → 2 / 任一 FAIL → 1。
**注意**:exit 2 是 WARN 不是失败,harness 会标"Error"但工具栈正常,看 SUMMARY 行为准。

### health --all(平台全局)每项目一块

```
[<project_id>]
    chroma 文档 = N chunks
    codegraph 代码 = nodes=N edges=N        # 每仓 .codegraph,经 config.projects.<id>.repo_path 定位
    cross-link 链路 = nodes=N / 未建        # 仅全栈仓建
    memory 项目专属 = N 条  (+ org 共享 M)  # project 作用域,只该项目召回
    使用率(7d) = search_docs N / cross-link N / codegraph N  # 按 project_id 拆
合计: chroma 全项目总数 ; memory M org + K project
```
> 使用率按 project_id 分:chroma 召回日志加了 project_id 字段(**daemon 重启后**新查询才分项目;旧日志归 "legacy 无 project_id");cross-link / codegraph 日志本就带 project_id(codegraph 自写代理 server.py 的 codegraph_usage.jsonl 每次调用打点)。

- **访问走 HTTP 服务地址**:`--all` 是客户端,GET 平台 daemon 的 `/platform/status`(`config.platform.url` 或默认 `http://127.0.0.1:<daemon.port>`);服务端跑在平台主机上聚合本机 data/+PG,客户端不碰路径。**子应用 / 远程机器查平台数据用同一个地址** —— 这才能多用户多项目共享。
- **chroma / cross-link / memory** 中心化(`data/` + PG 一个库),服务端直读。
- **codegraph**:平台读本机 `repo_path` 下 `.codegraph` sqlite 取统计(标 "本地 sqlite")。(Java codegraph-api :18082 HTTP 取数路径已退役 2026-06-04 —— 查询面由 codev web routes graph 接口替代;跨机取统计未来走 web routes,不再用 Java api。)
- **org 共享记忆**全项目通用(有意共享);**project 记忆**只该项目召回(隔离),`--all` 一眼看出谁有几条、串没串。
- daemon 没起 → `--all` 报连不上 + 提示(访问平台数据一律走 HTTP,不退回本地读)。

### dirty-check 输出
列 `[CodeGraph] / [cross-link] / [Chroma]` 命中索引范围的 dirty 文件。**退出码**:干净→0 / 命中→1 / 非 git→2。命中时:允许 grep/read 兜底,或 commit 让 post-commit hook 重建。

## 故障应对速查

| 症状 | 处理 |
|---|---|
| `embed model` 路径错 / 文件不全 | 检查 `config.models.embed_path` 指向的目录是否完整 |
| `torch cuda` cuda=False | 显卡驱动 / torch CUDA 版本不匹配,需重装 |
| `chroma freshness` lag > 7d | `codev-platform reindex --chroma` |
| `codegraph db` nodes 骤降 | 索引出错,`codegraph sync` / `codegraph init -i` 重建 |
| `hook missed?` WARN | `codev-platform post-commit` 补触发 |
| `--all` 某项目 codegraph "仓路径未登记" | 在 `~/.codev-platform/config.json` 配 `projects.<id>.repo_path` |
| dirty 命中 CodeGraph / cross-link | 允许 grep/read 兜底,或 commit 让 hook 跑 |

## 相关规则

- 触发指南:`.claude/rules/ai-tools-mcp.md`(CodeGraph / Chroma / cross-link 何时用)
- 重建:`codev-platform reindex`(四档:all / chroma / codegraph / cross-link)+ `post-commit`(提交后台重建)

## 5 视角

- **架构**:基础设施健康直接影响 AI 输出质量,需要可观测;`--all` 让平台方一眼看全各项目库规模
- **工程**:dirty-check 防止"AI 用过时索引误导决策",特别是关键路径改动
- **维护**:重启 / 升级后几秒确认所有组件在位,避免长时间盲跑
