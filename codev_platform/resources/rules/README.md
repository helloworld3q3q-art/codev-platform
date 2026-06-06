# codev-platform 跨项目通用规则

这里的规则会通过 `codev-platform sync-rules` 分发给各项目。它们只维护跨项目通用纪律,不绑定具体业务领域或技术栈。

项目专属规则必须留在项目自己的 `.claude/rules/`,例如业务表、领域枚举、接口生成方式、框架约定、合规文案、部署流程。

## 清单(9 条)

| 规则 | 性质 |
|---|---|
| `workflow.md` | 架构中立的任务分级、MCP 选型、改前门禁、Subagent 模板 |
| `ai-tools-mcp.md` | CodeGraph / platform-docs / graph / agent-memory MCP 触发指南 |
| `file-discipline.md` | 单文件规模、重复代码、docs 目录归类 |
| `verification-checklist.md` | 改动后验证清单和测试基线口径 |
| `security.md` | 敏感信息、日志、认证、MCP/RBAC 安全边界 |
| `windows-powershell.md` | `.ps1` ASCII、中文 UTF-8、Windows 命令安全写法 |
| `commit-pr-conventions.md` | commit / PR 文案约定,禁 AI 痕迹 |
| `weekly-iteration-cadence.md` | 迭代目录生命周期和跨周继承 SOP |
| `agent-provider-architecture.md` | agent provider / brain 架构约束 |

## 同步策略

真值源在 `codev_platform/resources/rules/`。改完后:

1. 跑资源/打包相关测试,确认普通 wheel 也能带走规则。
2. 跑关键字残留扫描,确认没有单项目架构画像。
3. 需要分发到业务仓时,在业务仓跑 `codev-platform sync-rules` 并由业务仓提交。
