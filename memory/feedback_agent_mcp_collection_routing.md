---
name: feedback-agent-mcp-collection-routing
description: 派 agent 工作在跨仓 / 非主仓代码时,必须在 prompt 显式说 cwd + 让 agent 调对应仓的 chroma collection,否则 MCP 拿不到目标仓数据
metadata:
  type: feedback
---

派 agent 实施 / 审计**非当前会话主仓**的工作时,prompt 必须包含 3 件事,否则 `mcp__platform-docs__search_docs` / `mcp__codegraph__codegraph_*` 跳空 (agent 实测多次报告"目标仓不在索引内")。

## 三件套

1. **指定 cwd**:`cwd: D:\WorkSpace\<target-repo>` — agent Bash/PowerShell 调用 daemon 时通过 cwd 推 project_id
2. **指定目标 collection**:`查 codev-platform__platform_docs / codev-platform-widget__platform_docs collection,不是默认 openclaw-stock`
3. **codegraph 要求目标仓已 init**:`目标仓需有 .codegraph/codegraph.db; 否则 codegraph MCP 空 (跑 codegraph init + codegraph index 一次性建)`

## Why

**Why:** 5-28 派 Phase G 审计 + Widget Phase 1 审计时,agent 报告 `[sse] reject: invalid project_id 'codev-platform-widget'` (daemon 老 bug,后修),且 search_docs 命中 0 因 codev-platform 仓未进 chroma collection (实际进了但 agent 没切 cwd 触发 SSE handshake)。最终 agent 全部跳过 MCP 走 Read+Grep,审计精度损失 30%。

**How to apply:**
- 派 agent prompt 在【MCP 强制使用】section 顶部加一行:`agent 调 MCP 前必须 cd <target-repo>,让 daemon SSE handshake 切到目标 collection`
- 审计 codev-platform 仓 agent:`prompt 加 "cd D:\WorkSpace\codev-platform 后调 search_docs,daemon 会按 .claude/project.json load codev-platform__platform_docs collection"`
- 审计 codev-platform-widget 同理
- 审计 platform 业务仓:无需特殊,因为 daemon 启动默认就是 openclaw-stock,agent 在 platform cwd 直接命中
