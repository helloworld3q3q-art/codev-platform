# codev-platform/memory/

跨项目通用的协作偏好 / 工具栈引用。任何业务项目接入 codev-platform 工具栈后这些 memory 都适用。

## 清单(11)

| 文件 | 性质 |
|---|---|
| `feedback_assertions_over_rules.md` | 偏好 assertion / sanity / 测试 >> 写更多规则文档 |
| `feedback_commit_phasing.md` | 大改 ≥3 文件跨 ≥2 子模块 → 拆 commit |
| `feedback_no_absolute_paths.md` | 文档/规则不写 `D:\WorkSpace\...`,用相对或 `<repo-root>` |
| `feedback_no_cargo_cult_dates.md` | 别反射写"(YYYY-MM-DD 起)",git blame 是真值 |
| `feedback_no_wrapper_utils.md` | 单库二次封装(dayjs/lodash)一律不做 |
| `feedback_rules_concise.md` | `.claude/rules/*.md` 90-150 行;subagent prompt ≤50 行 |
| `feedback_use_audit_agent_template.md` | 派 agent 必抄 workflow.md §12.2 模板 |
| `feedback_weekly_iteration_cadence.md` | 每周新 roadmap-YYYY-MM-DD/,AI 不主动催 |
| `feedback_fe_be_handoff_notification.md` | 后端 DTO/枚举改 → 先通知前端 `pnpm run api/enums` |
| `feedback_dev_order.md` | 后端 → 前端 (Python → Java → Web 顺序) |
| `reference_mcp_tools.md` | CodeGraph / platform-docs / graph 统一图谱 MCP 入口速查 |

## 同步策略

业务仓的 `tools/dev/sync-memory.ps1` 同步 `docs/memory/` 到 `~/.claude/projects/.../memory/`,让 Claude Code 自动加载。codev-platform 的这套通过 `codev-platform sync-rules` / 或后续 `sync-memory` CLI 同步到业务仓 + 用户级 autoload。

## 业务专属

不要把业务专属 memory(stock 名称 / 融券 T+1 / 行情快照保留 / DOC_PATTERNS 白名单 / 跨语言枚举链路)抽过来 — 留各业务仓 `docs/memory/`。
