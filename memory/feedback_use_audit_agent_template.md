---
name: use-audit-agent-template
description: "派 subagent 前先抄 workflow.md §12.2 prompt 模板,事前抛 5 个具体 MCP 调用,不现编"
metadata:
  type: feedback
---

派任何 subagent(尤其审计兄弟)前,**先打开 `.claude/rules/workflow.md` §12.2** 抄"事前 MCP 调用清单"模板填空,**不要现编 prompt**。

**Why**:5-26 实战验证 — 事前抛 5 个具体 MCP 调用(精确到 query 字符串 + 验证点)比 prompt 里写"必须用 MCP"空话**有效 10×**。本日 3 次审计兄弟全部 PASS,5/5 MCP 调用合规,拦下 1 个 BLOCKER(`message` vs `detail` 字段名漂移)+ 4 个 WARN 全靠这套模板。现编 prompt 容易漏踩坑经验(如 `codegraph_node` 拿 enum 不返回正文)。

**How to apply**:
- **派审计 agent** → 抄 §12.2 "派审计 agent 模板"
  - 5 个 MCP 调用具体到 query 字符串
  - final report 必含 MCP 调用清单 + Grep 例外声明
- **派实施 agent / 自己合并影响面调研** → 抄 §12.2 "主 agent / 实施 agent 自用模板"
  - L2/L3 任务起手 5 个并发 MCP 调用一次性发
  - 省 ~10 次 Grep + Read(本次实测)
- **关键约束**:模板里"经验坑"那段(`codegraph_context` 查询方式 / `codegraph_node` enum 返回空 / 5 并发不串行)必须保留,不要为了精简删掉

**相关**:CLAUDE.md §0.12(本规则的硬门禁版)/ workflow.md §12.1(事后核查模板,与 §12.2 互补)/ [[reference-mcp-tools]]
