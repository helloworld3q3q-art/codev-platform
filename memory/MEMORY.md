# MEMORY.md — codev-platform (跨项目通用偏好真值源)

任何业务项目接入 codev-platform 工具栈后,这 12 条 memory 都适用。改进点直接改本目录 .md 文件 — 业务仓通过 `codev-platform sync-memory` (TODO) 或 chroma external_doc_paths 召回。

## 跨项目偏好(12 条)

- [开发顺序规范](feedback_dev_order.md) — Python → Java → Web,前端最后整体开发,不交替
- [断言优先于规则](feedback_assertions_over_rules.md) — 自动 sanity check / 测试断言 / 消除双写点 >> 新规则文档;扩 check 前先观察现有命中率
- [MCP 工具入口速查](reference_mcp_tools.md) — CodeGraph 找代码 / platform-docs 找文档 / cross-link 找业务链路;禁止 grep+Read 循环
- [每周迭代节奏](feedback_weekly_iteration_cadence.md) — 每周新建 roadmap-YYYY-MM-DD/(大概率周末)+ 上周归档 + 继承未完结项;AI 不主动催
- [前端禁单库 wrapper](feedback_no_wrapper_utils.md) — dayjs/lodash 这类一律不做;业务规则的 wrapper 是例外
- [分阶段提交 git](feedback_commit_phasing.md) — ≥3 文件 + 跨 ≥2 子模块就该拆 commit
- [前后端联调先通知](feedback_fe_be_handoff_notification.md) — 后端 DTO/枚举改先通知前端 pnpm run api/enums
- [规则文件一屏 / agent prompt 50 行](feedback_rules_concise.md) — `.claude/rules/*.md` 90-150 行;subagent prompt ≤50 行
- [派 agent 必查 §12.2 模板](feedback_use_audit_agent_template.md) — 派审计 / 实施 agent 前抄 workflow.md §12.2 prompt 模板,事前抛 5 个具体 MCP 调用
- [禁止 cargo cult 日期](feedback_no_cargo_cult_dates.md) — 不要反射写 "(YYYY-MM-DD 起)";只有事故/cut-off/任务编号配的日期才能留
- [cross-link 重建 Windows lock](reference_cross_link_lock_fix.md) — 必须用 SQLite backup API 而非 os.replace
- [禁止绝对路径](feedback_no_absolute_paths.md) — 文档/SKILL/rule/命令例子用相对路径或 `<repo-root>`;Scheduler / runtime .py / archive 例外
- [派 agent 切 collection 路由](feedback_agent_mcp_collection_routing.md) — 派跨仓 agent 工作必须 prompt 含 cd + 目标 collection + codegraph init 要求,否则 MCP 跳空

## 业务专属 memory 在哪

不在本仓 — 各业务仓自己的 `docs/memory/`。例(platform 量化):
- stock 名称显示规范
- A 股行情快照保留
- 融券 T+1 披露
- 跨语言枚举值字面量
- 业务 DOC_PATTERNS 白名单

业务 memory 不能搬本仓,会污染其它项目接入。
