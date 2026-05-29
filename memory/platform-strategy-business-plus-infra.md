---
name: platform-strategy-business-plus-infra
description: 平台战略定位 — 业务与基建双轮驱动,memory 基建对标大厂 Agent Memory 平台
metadata:
  type: project
---

用户 2026-05-29 明确平台战略:**业务要做 + 基建要强大,双轮驱动,不二选一**。

触发:看到大厂"AI Agent Memory 基础设施"工程师 JD(长期/会话/任务记忆统一平台 + 写入/存储/索引/检索/更新/压缩/遗忘 + 记忆表征/召回排序/冲突消解/摘要融合/生命周期 + 多模态),用户表态"我们的目标就是 业务要做 基建要强大"。

**How to apply:**
- agent memory 基建按该 JD 的能力清单作**北极星**,分阶段演进(检索→会话持久化→压缩/摘要→遗忘/TTL→冲突消解→表征→多模态),不一次到位。
- 已有强项:chroma 向量 + BM25 + RRF + reranker(召回这块真有底子)。
- 不因"基建要强大"就提前造无真实痛点的远方功能(避免 secrets / team-deploy 式空中楼阁)——每阶段要有 agent 实跑出的痛点驱动。
- 业务侧(openclaw-stock)与基建侧(codev-platform agent + memory)并行,基建成果反哺业务 agent。

关联:[[agent-plan-v2]] 的 P2/P3 阶段承载 memory 演进。
