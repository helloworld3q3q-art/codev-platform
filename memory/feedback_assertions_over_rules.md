---
name: feedback-assertions-over-rules
description: "用户认同\"规则不防 bug，断言才防 bug\"工程哲学 — 倾向自动 sanity check / 测试断言而非新写规则文档"
metadata: 
  node_type: memory
  type: feedback
  originSessionId: 68d7ccb4-afcd-4bfc-bf34-36bf56bb9617
---

用户对治理 bug 的偏好排序：自动断言（sanity check / 测试 / 入库护栏）> 单一真值源（消除双写点）> 规则文档化。

**Why**：项目已积累 17 条 `.claude/rules/`，新 bug 仍频发（5-14 model drift / 5-15 编造接口 / 5-20 Chain C）。用户判断规则文档边际收益递减 —— 17 条没人记全 + AI 也不会主动查 + 规则会老化。assertion 一次写永久跑批触发，ROI 是规则的 ~100x。

**How to apply**：
- 发现新 bug 类型时，首选问"能不能写成 SQL 断言挂到 [[业务闭环 sanity check 体系]]"，再考虑写规则
- 引用现有规则文件时优先看哪些已经有 assertion 兜底（如 `pct-sign-convention §3` / `not-null-write-guard §1 防线`），不要再重复手动 grep
- 不该走极端 —— 5 视角框架 / PIT 红线哲学 / commit 规范这类"why 层"必须保留文档化，不能转 assertion
- 加新 sanity check 前**先观察现有的真实命中率**（连续 3 天 0 命中 → 别为扩而扩，避免假阳性刷屏淹没真违规）
- 修双写点（如 Chain C 类跨层公式）比加规则更治本，但合并前必须先决策语义（快照 vs 动态）写进规则文件留痕

**Why "先观察再扩"**：用户明确接受这个收尾约束，避免典型反模式 = 加 10 条新 check 结果 8 条假阳性 + 真违规被淹。
