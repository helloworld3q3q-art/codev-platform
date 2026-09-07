---
name: reference-mcp-tools
description: 平台 MCP 工具入口（CodeGraph / platform-docs / graph 统一图谱）及触发场景
metadata: 
  node_type: memory
  type: reference
  originSessionId: 4cfac47b-d178-4725-ab86-7affec7b3c03
---

平台搭了 3 套 MCP server，会话中**主动调用**而不是用 grep+Read 循环：

- **CodeGraph** `mcp__codegraph__*` — 通用代码图谱（symbol / call / impact），传 `projectPath="D:\\WorkSpace\\platform"`
  - 主入口 `codegraph_context`（onboarding / 理解某区域）
  - 其他 `_search` / `_callers` / `_callees` / `_impact` / `_node` / `_explore` / `_files` / `_status`
- **platform-docs / Chroma** `mcp__platform-docs__*` — 文档语义检索（4531 chunks / 188 markdown）
  - `search_docs(query, category, module)` / `list_collections` / `get_by_file`
  - category ∈ rule/incident/design/operations/claude_md/skill/doc/all
- **graph(统一图谱)** `mcp__graph__*` — 跨层业务架构图（endpoint ↔ 表 ↔ 前端 血缘）
  - `find_table_usage` / `find_api_callers` / `find_impact` / `find_impacted_pages` / `search_nodes`

**完整规则**：[[ai-tools-mcp]]（`.claude/rules/ai-tools-mcp.md`，会随 CLAUDE.md 自动加载）。

边界：找代码 → CodeGraph；找文档 → platform-docs；找业务链路 → graph(统一图谱)。**不要混用**。
