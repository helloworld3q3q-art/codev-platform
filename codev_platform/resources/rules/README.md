# codev-platform 跨项目通用规则

这里的规则适用于**任何**接入 codev-platform 工具栈的业务项目,不绑定具体业务领域。

业务项目专属规则(stock 量化的 PIT 红线 / 影子隔离 / 推荐三件套等)仍在各业务仓 `.claude/rules/`。

## 清单(11 条)

| 规则 | 性质 |
|---|---|
| `workflow.md` | 任务分级 + MCP 选型 + 改前门禁 + Subagent 模板(跨语言/层通用工作流) |
| `file-discipline.md` | 单文件行数 + 跨语言判重 + docs/ 目录归类 |
| `commit-pr-conventions.md` | 项目红线:禁 AI 痕迹 / Co-Authored-By |
| `windows-powershell.md` | `.ps1` ASCII / 中文 UTF8 / Claude Code 安全检查友好写法 |
| `ai-tools-mcp.md` | CodeGraph / platform-docs / graph MCP 触发指南 |
| `verification-checklist.md` | 改动后验证清单 + pre-push 6 gates |
| `weekly-iteration-cadence.md` | 每周迭代节奏 + 归档 SOP |
| `security.md` | 敏感信息 + 免责声明 + token 处理 |
| `cross-layer-enum-consistency.md` | 跨语言枚举值字面量一致性硬约束(Python / Java / TS / DTO 四层) |
| `frontend-backend-handoff.md` | 后端 DTO / 枚举 / 端点改动必先通知前端 + `pnpm run api/enums` |
| `not-null-write-guard.md` | DB NOT NULL 三道防线(SQL COALESCE / dataclass 透传 / sanity assert) |

## 同步策略

当前阶段:platform 仓 `.claude/rules/` 内保留**完整拷贝**(双份共存),改 codev-platform 版后**手动同步**回 platform 直至全切到从 codev-platform 拉取。

后续机制 candidate:
- platform 仓 `.claude/rules/` 中跨项目规则 → 软链或 git submodule 指 codev-platform
- 或 chroma `index_docs.py` 同时扫两个目录,前端搜的时候不感知
- 或 CLI `codev-platform sync-rules` 把 codev-platform/rules/ 复制到 cwd 业务仓
