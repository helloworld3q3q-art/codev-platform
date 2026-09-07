---
name: feedback-rules-concise
description: ".claude/rules 规则文件控制在一屏 / 90 行内,subagent prompt 50 行内;不接受冗长事故复盘式文档"
metadata: 
  node_type: memory
  type: feedback
  originSessionId: 07722d89-6bc3-42bf-a872-2afc6d86c0c3
---

新建 `.claude/rules/*.md` 规则文件 **默认控制在 90-150 行内**,不超过 200 行。subagent prompt **必须 50 行内**。

**Why**:
- 用户原话(2026-05-23):"那个规则太复杂了就简短一点就好"
- 380 行规则文档需要被砍到 90 行才合用 → 浪费 30K tokens
- 规则要好用 = 一屏看完,触发条件 / 反例正例 / grep 自检 / PR 清单四件套即可
- 详细事故复盘放 `docs/operations/incident-*.md`,不放规则文件

**How to apply**:
- 写规则时强制目录:**触发条件 / 强制原则 / 反例正例对照 / grep 自检 / PR 清单**(5 章节内)
- 派 subagent 写规则时 prompt 明确写"输出 150 行内"
- 详细背景 / 事故复盘单独写一份 `docs/operations/incident-YYYY-MM-DD-*.md`,规则文件只一句话引用

**典型案例(2026-05-23)**:
- N12 教训写 `cross-layer-enum-consistency.md` 252 行(过长,可砍 30%)
- 同日 `frontend-backend-handoff.md` 第一版 380 行被用户砍到 90 行(✓ 一屏)
- 同日 `code-quality.md §日期时间格式化` 加 47 行内嵌(✓ 简洁)

关联:`ai-tools-mcp.md §四 b` Subagent prompt 模板 / [[feedback-assertions-over-rules]]
