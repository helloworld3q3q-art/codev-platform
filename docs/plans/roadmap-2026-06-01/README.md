# roadmap-2026-06-01 - 模块化核心 + 插件化扩展规划

> 本目录记录 codev-platform 从固定工具栈升级为“模块化核心 + 插件化扩展”的全链路 AI 平台的落地计划。

## 本轮主题

**模块化核心 + 插件化扩展**: 平台核心先做稳定模块化，负责租户、项目、权限、审计、调度、图谱、检索、Memory、Agent 编排和报告；客户差异能力通过插件扩展，包括语言、框架、前端、后端、数据库和外部系统接入。

目标链路:

```text
Vue/React 页面
-> API 调用
-> 后端接口
-> Service/函数
-> SQL/ORM
-> DB 表/字段
-> Wiki/Jira/飞书文档
-> Agent 影响分析报告
```

## 文件清单

| 文件 | 内容 | 状态 |
|---|---|---|
| [pluginized-fullstack-ai-platform-2026-06-01.md](pluginized-fullstack-ai-platform-2026-06-01.md) | 模块化核心 + 插件化扩展落地 plan: 安全底座、统一图谱、插件协议、Vue/React、Java/FastAPI、Wiki/Jira/飞书、Demo、POC | 规划中 |
| [refactor-largefile-errorcode-2026-06-01.md](refactor-largefile-errorcode-2026-06-01.md) | 来自 0601 审计的可维护性整改: A 拆大文件 (chroma/server.py 1286 / ops/health.py 1119 / mcp_serve.py 701) + B 错误码结构化 (对外 HTTP/MCP 收窄 except, 5 类机器可读 code); 4 批排期 + 向后兼容 + 验证门 | 规划中 |
| [agent-memory-platform-plan-2026-06-01.md](agent-memory-platform-plan-2026-06-01.md) | Agent Memory 平台化落地 plan: 任务记忆、Context Engineering、生命周期治理、权限审计、性能评测、飞书/Jira/Wiki/Git 接入 | 规划中 |
| [modular-core-plugin-extension-decision-2026-06-02.md](modular-core-plugin-extension-decision-2026-06-02.md) | 架构命名和边界决策: 哪些能力做核心模块，哪些能力做客户插件，codegraph/cross-link/Memory/报告如何归类 | 已定 |

## 关联

- 上一轮服务器加固: `../roadmap-2026-05-31/`
- Token 启用 runbook: `../roadmap-2026-05-31/token-auth-enablement-2026-06-01.md`
- 当前代码审计结论: token 模式需强制 project_id、memory org/team ACL 需补齐、入口级 ACL 集成测试不足。
