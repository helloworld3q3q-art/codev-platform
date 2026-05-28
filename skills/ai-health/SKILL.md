---
name: ai-health
description: 检查本地 AI 工具栈状态 —— 12 项体检 (ai-health) + 工作树索引一致性 (dirty-check)。支持 health / dirty / both 三档,用于排查 MCP 召回怪/索引滞后/重启电脑后验栈/升级模型后确认生效
---

# AI 工具栈体检

**触发场景**:
- 用户问 "AI 工具有没有挂" / "MCP 工具状态" / "三个库还好吗" / "Qwen3 生效了吗"
- 重启电脑 / 升级模型 / 改 hook 配置后想确认基础设施正常
- AI 召回质量怪,先排除"工具栈本身坏了"
- 改了一堆代码没 commit,想问 AI 决策前先确认 MCP 索引是否同步

## 🚨 mode 选择 (重要)

**当用户只说"AI 工具状态"/"检查工具栈"不指明具体诉求时,必须用 AskUserQuestion 让用户选**:

| 选项 | 含义 | 适用场景 |
|---|---|---|
| `health` (工具栈体检) | 跑 ai-health.ps1 — 12 项检查 (Qwen3 模型 / Chroma chunks / CodeGraph db / GPU / git) | 重启 / 升级后,怀疑底层 |
| `dirty` (索引一致性) | 跑 dirty-index-check.ps1 — 列工作树命中索引范围的 dirty 文件 | 改了没 commit,要让 AI 决策前 |
| `both` (推荐) | 两个都跑 — 完整体检 + 索引一致性 | 不确定时默认 |

直接信号词:
- "工具栈坏了 / 模型加载了吗 / GPU 有没有用" → `health`
- "MCP 看到我刚改的代码吗 / 索引滞后吗 / 没 commit 的会怎样" → `dirty`
- "整体看一下" / 没说清 → `both`

## 用法

```powershell
# health 体检 — 12 项,~5 秒
tools\dev\ai-health.ps1

# dirty 检查 — 列受影响文件 + 兜底建议
tools\dev\dirty-index-check.ps1

# dirty 给脚本 / AI 调
tools\dev\dirty-index-check.ps1 -Json

# dirty 只看退出码 (CI 用)
tools\dev\dirty-index-check.ps1 -Quiet
```

## 输出解读

### ai-health 关键项

| 项 | 看什么 |
|---|---|
| `embed model` | Qwen3-Embedding-0.6B 路径 + 11 个文件齐全 |
| `embed load` | dim=1024 / max_seq=32768 / query_prompt=True (instruction-aware 已激活) |
| `torch cuda` | cuda=True + GPU 名 (GPU 没识别 → 模型走 CPU,慢 10×) |
| `chroma collection` | chunks 数 / 维度 / 模型名匹配 |
| `chroma freshness` | 索引比最新 .md 新 → 不滞后 |
| `codegraph db` | nodes / edges 数变化反映代码改动 |
| `codegraph-api` | jar 已编译 (没 jar → mvn package 一次) |
| `hook missed?` | HEAD commit 在 reindex.log 找到 → OK;命中索引但没记录 → WARN 提示重跑 |

**退出码**:全 OK → 0 / 任一 WARN → 2 / 任一 FAIL → 1

**注意**:exit 2 是 WARN 等级不是失败,Claude Code harness 会标"Error"但其实工具栈正常,看 SUMMARY 行才是真相。

### dirty-check 输出

```
WARN: dirty files in AI index scope - MCP results may be STALE
[CodeGraph] N 文件:  ...
[cross-link] N 文件:  ...
[Chroma   ] N 文件:  ...

Next steps:
  1. allow grep/read fallback for affected files
  2. or commit + let post-commit hook reindex
  3. or run tools/dev/update-local-ai.ps1 manually
```

**退出码**:索引干净 → 0 / 命中索引范围 → 1 / 非 git 仓库 → 2

## 故障应对速查

| 症状 | 处理 |
|---|---|
| `embed model` 路径错 / 文件不全 | 检查 `D:\models\Qwen3-Embedding-0.6B` 是否完整 |
| `torch cuda` cuda=False | 显卡驱动 / torch CUDA 版本不匹配,需重装 |
| `chroma freshness` lag > 7d | 跑 `tools\dev\update-local-ai.ps1 -SkipCodeGraph` |
| `codegraph db` nodes 数比上次少很多 | 索引出错,跑 `tools\dev\update-local-ai.ps1 -SkipChroma` |
| `codegraph-api no jar` | `mvn -f apps\codegraph-api\pom.xml package -DskipTests` |
| `hook missed?` WARN | 跑 `powershell -File tools/dev/post-commit.ps1` 补触发 |
| dirty 命中 CodeGraph / cross-link | 允许 grep/read 兜底,或 commit 让 hook 跑 |
| dirty 命中 Chroma | 同上,文档场景影响小,可先用 MCP |

## 相关规则

- 工具栈架构:`docs/ai-toolchain-guide.html` (完整说明 + SVG 节点图)
- 触发指南:`.claude/rules/ai-tools-mcp.md` (CodeGraph / Chroma / cross-link 何时用)
- 重建逻辑:`tools/dev/update-local-ai.ps1` + `tools/dev/post-commit.ps1`

## 5 视角

- **架构**:基础设施健康直接影响 AI 输出质量,需要可观测
- **工程**:dirty-check 防止"AI 用过时索引误导决策",特别是关键路径改动
- **维护**:重启 / 升级后 5 秒确认所有组件在位,避免长时间盲跑
