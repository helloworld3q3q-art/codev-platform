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
| [pluginized-fullstack-ai-platform-2026-06-01.md](pluginized-fullstack-ai-platform-2026-06-01.md) | 模块化核心 + 插件化扩展落地 plan: 安全底座、统一图谱、插件协议、Vue/React、Java/FastAPI、Wiki/Jira/飞书、Demo、POC | 主线/先切薄片证价值 |
| [refactor-largefile-errorcode-2026-06-01.md](refactor-largefile-errorcode-2026-06-01.md) | 来自 0601 审计的可维护性整改: A 拆大文件 (chroma/server.py 1286 / ops/health.py 1119 / mcp_serve.py 701) + B 错误码结构化 (对外 HTTP/MCP 收窄 except, 5 类机器可读 code); 4 批排期 + 向后兼容 + 验证门 | 规划中 |
| [web-backend-framework-plan-2026-06-02.md](web-backend-framework-plan-2026-06-02.md) | Web Backend 框架化落地 plan: FastAPI 服务分层、登录退出、组织管理、用户管理、项目管理、读写分离、统一请求验证和拦截、统一字段、OpenAPI、审计、长任务和并发控制。**2026-06-02 校准(见 §零)**: 独立进程服务 + agent 架构一致(共抽 `core/httpkit`)、复用 gateway/core.acl/core.rbac/core.errors、envelope 扁平化(去 result)、错误码收敛 8 类、存储 PG 起步 | P0 先做(Phase 0-3 地基) |
| [agent-memory-platform-plan-2026-06-01.md](agent-memory-platform-plan-2026-06-01.md) | Agent Memory 平台化落地 plan: 任务记忆、Context Engineering、生命周期治理、权限审计、性能评测、飞书/Jira/Wiki/Git 接入 | P1 地基后 |
| [modular-core-plugin-extension-decision-2026-06-02.md](modular-core-plugin-extension-decision-2026-06-02.md) | 架构命名和边界决策: 哪些能力做核心模块，哪些能力做客户插件，codegraph/cross-link/Memory/报告如何归类 | 已定 |
| [stack-adapter-taxonomy-2026-06-02.md](stack-adapter-taxonomy-2026-06-02.md) | 栈适配器三层 taxonomy(语言基座 × 框架适配 × DB 方言)+ 各插件统一 NodeKind/EdgeKind 契约 + detect 约定 + registry 自动发现(扫 builtin/ 目录,新增插件文件即生效,build agent 免改 registry)。供后续 Vue/Express/Spring/ASP.NET/SQL 框架插件并行落地 | 已定/自动发现已落地 |
| [deployment-2026-06-01.md](deployment-2026-06-01.md) | Phase 0 部署骨架: 安全默认、本机最小启动、memory PG (Docker Compose)、systemd 常驻 (serve-mcp install-systemd)、Windows WSL 注意、远程部署 checklist。汇总既有命令真值源, 不发明命令 | Phase 0 底座 |

## 执行顺序 / 优先级(2026-06-02 议定)

```text
1. web-backend Phase 0-3(骨架 + 统一错误 + Identity/ACL,~1 周)— 地基 + 关掉当前审计缺口(token 强制 project_id / org-team ACL / 入口级 ACL 集成测试)+ 消费 core/errors.py 错误码成果
2. pluginized 切薄片(统一 GraphNode/Edge + 1 个 AnalyzerPlugin + demo)— 证明“影响分析”差异化
3. web-backend Phase 4-6(org/user/project/job)与 pluginized graph 并行
4. agent-memory M0-M1(任务记忆)接在 web-backend 之上
```

理由: web-backend 是地基(#2/#4 都踩它),最有界可验证,直接关审计缺口,且 pluginized 的 Phase 0 安全部署底座与它重叠 → 先把地基铺好再切差异化薄片。

## 关联

- 上一轮服务器加固: `../roadmap-2026-05-31/`
- Token 启用 runbook: `../roadmap-2026-05-31/token-auth-enablement-2026-06-01.md`
- 当前代码审计结论: token 模式需强制 project_id、memory org/team ACL 需补齐、入口级 ACL 集成测试不足。
