---
name: update-local-ai
description: 手工重建本地 AI 索引 (Chroma 文档库 / CodeGraph 代码图谱 / cross-link 业务链路)。支持 all / chroma / codegraph 三档,日常 commit 后 hook 会自动跑,只在 hook 漏触发 / 大批量改动 / 升级模型后才需要手工跑
---

# 重建本地 AI 索引

**触发场景**:
- 用户说 "更新数据" / "重建索引" / "刷新一下 MCP" / "update local ai"
- 改了大批 .md / 代码后想立刻让 AI 看到 (不等 commit hook)
- post-commit hook 没触发 (sh stub errno 1 / 网络中断) → 补跑
- 升级了 embedding 模型 / Chroma schema 变 → `-ChromaForce` 全量重建

## 🚨 三库勾选 (multiSelect)

**用户说"更新数据"不指明范围时,必须用 AskUserQuestion (multiSelect=true) 让用户勾**,3 个独立选项 = 7 个有效组合(2³−1):

| 选项 | 范围 | 耗时 | 何时勾 |
|---|---|---|---|
| `Chroma` | 文档向量库 (*.md → Qwen3 chunk) | ~80s | 改了 .md / .claude/rules / docs |
| `CodeGraph` | 代码图谱 (*.py / *.java / *.tsx 符号 + 调用) | ~2 min | 改了代码,要查调用关系 / 影响面 |
| `cross-link` | 业务调用链 (Mapper / Controller / Flyway / api 客户端) | ~5s | 改了 endpoint / SQL / Flyway |

**直接信号词跳过提问**:
- "改了文档 / 规则 / 日报" → 直接勾 `Chroma`
- "改了代码 / Python / Java / 前端" → `CodeGraph` + `cross-link`(代码改动一般两个一起跑)
- "改了 endpoint / SQL / 表结构" → `cross-link`(或加 `CodeGraph`)
- "升级了 Qwen3 模型 / 换 embedding" → `Chroma` 且加 `-ChromaForce`(用 AskUserQuestion 再确认一次)
- "全部 / 都更新 / 周末维护" / 不确定 → 三个全勾

**多选转 Skip 标志**:用户没勾的库,给 `update-local-ai.ps1` 加对应 Skip:
- 没勾 Chroma → `-SkipChroma`
- 没勾 CodeGraph → `-SkipCodeGraph`
- 没勾 cross-link → `-SkipCrossLink`

**特殊场景**:升级 embedding 模型时,Chroma 勾上后还要追加 `-ChromaForce` 全删重建(普通增量会因维度不匹配失败)。这种场景在多选勾完后**单独再问一次**:"是要全删重建吗?(只有升级模型 / collection 损坏才需要)"

## 用法

```powershell
# 7 个组合示例 — 按勾选生成 Skip 标志

# 1. 全勾 (Chroma + CodeGraph + cross-link, ~3 min)
tools\dev\update-local-ai.ps1

# 2. 只 Chroma (~80s)
tools\dev\update-local-ai.ps1 -SkipCodeGraph -SkipCrossLink

# 3. 只 CodeGraph (~2 min)
tools\dev\update-local-ai.ps1 -SkipChroma -SkipCrossLink

# 4. 只 cross-link (~5s)
tools\dev\update-local-ai.ps1 -SkipChroma -SkipCodeGraph

# 5. Chroma + cross-link (~85s,文档+SQL/Flyway 改动)
tools\dev\update-local-ai.ps1 -SkipCodeGraph

# 6. CodeGraph + cross-link (~2 min,代码+业务接口改动)
tools\dev\update-local-ai.ps1 -SkipChroma

# 7. Chroma + CodeGraph (~3 min,不常用 - 跳 cross-link 只省 5s)
tools\dev\update-local-ai.ps1 -SkipCrossLink

# Chroma-force — 全删重建 (慎用,只在模型升级 / collection 损坏后)
tools\dev\update-local-ai.ps1 -SkipCodeGraph -SkipCrossLink -ChromaForce
```

## 输出解读

每档跑完会打出:
```
=== step 1/4: codegraph rebuild ===   (or "-- skipped")
=== step 2/4: chroma reindex ===
=== step 3/4: cross_link rebuild ===
=== step 4/4: ai-health summary ===
```

第 4 步自动调 `ai-health.ps1` 复检,跑完看 `SUMMARY: all green` 即成功。

## 与 post-commit hook 的关系

| 触发源 | 自动 / 手工 | 范围 |
|---|---|---|
| **commit .md 改动** | hook 自动 (~80s) | chroma (post-commit.ps1 自动算 scope) |
| **commit 代码改动** | hook 自动 (~2 min) | codegraph + cross-link |
| **commit 文档+代码** | hook 自动 (~3 min) | 全量 |
| **手工 update-local-ai** | 用户主动 | 按选项 |

正常情况下 hook 会接管。**这个 skill 只在异常情况用** —— hook 没触发 / 想立刻验证 / 升级模型。

## 故障应对速查

| 症状 | 处理 |
|---|---|
| `step 2/4 chroma reindex` 报错 | 看 `tools/chroma/reindex.log`,常见 GPU OOM → 改 `EMBEDDING_BATCH_SIZE=8` 重试 |
| `step 1/4 codegraph rebuild` 失败 | `scripts/codegraph/rebuild_index.ps1` 单独跑看详细错 |
| `ai-health` 显示 chunks 数没变 | reindex 跑了但没新内容,正常;若期望有 → 检查 `DOC_PATTERNS` 是否覆盖新文件类型 |
| `ChromaForce` 后召回质量变差 | 维度 / 模型不一致,跑 `tools/dev/embed_ab_test.py` 对比 |

## 相关

- 三库说明:`docs/ai-toolchain-guide.html`
- 触发指南:`.claude/rules/ai-tools-mcp.md`
- 验栈 skill:`/ai-health` (跑完后建议跑一次确认)
- hook 实现:`tools/dev/post-commit.ps1`
