# 模块化核心 + 插件化扩展决策

日期: 2026-06-02

## 结论

codev-platform 后续不只叫“插件化平台”，而是统一表述为:

> 模块化核心 + 插件化扩展

原因:

- “模块化”解决平台内部复杂度、测试边界和长期维护问题。
- “插件化”解决客户技术栈差异、外部系统差异和商业授权组合问题。
- 如果只叫插件化，容易误解为平台核心也可以随意替换，导致核心边界不稳。
- 如果只叫模块化，又不能体现客户按需扩展能力。

## 1. 什么是模块化核心

模块化核心是平台自己的稳定能力，不优先下放给客户替换。

核心模块:

| 模块 | 职责 | 是否客户插件 |
|---|---|---|
| auth | org/user/project/token/ACL/RBAC | 否 |
| audit | 访问审计、召回审计、插件执行审计 | 否 |
| scheduler | reindex、增量触发、失败重试、队列 | 否 |
| graph | 统一 Node/Edge/Evidence/Finding 存储和查询 | 否 |
| retrieval | 文档 RAG、BM25、RRF、rerank、权限过滤 | 否 |
| memory | Agent Memory、任务记忆、生命周期、遗忘、压缩 | 否 |
| agent | Agent 编排、工具调用、上下文组装 | 否 |
| report | 影响分析报告、证据聚合、风险输出 | 否 |
| ops | health、logs、backup、deploy、MCP/HTTP 服务 | 否 |

这些模块可以内部拆包、拆文件、拆服务，但不应该变成客户随意替换的插件。

## 2. 什么是插件化扩展

插件化扩展是客户差异能力。不同客户用不同技术栈、不同协同工具、不同代码规范，所以这部分必须按需安装、配置、启用。

插件类型:

| 插件类型 | 示例 | 输出 |
|---|---|---|
| FrontendPlugin | Vue、React、qiankun | 页面、路由、组件、API 调用 |
| BackendPlugin | Java Spring、Node Express、Python FastAPI、.NET | endpoint、service、function、ORM、SQL |
| DatabasePlugin | MySQL、PostgreSQL、迁移脚本 | 表、字段、读写关系 |
| ConnectorPlugin | 飞书、Jira、Wiki、Confluence、GitLab、Gitea、CI | 需求、文档、评论、提交、构建事件 |
| CodeGraphAdapter | Java/Python/Node/.NET parser | 函数、类、调用、引用 |
| CrossLinkAdapter | Vue/React/Spring/Express/ORM 规则 | 页面 -> API -> 后端 -> DB 关系 |

插件必须输出统一模型:

```text
Node      发现了什么对象
Edge      对象之间有什么关系
Evidence 结论来自哪里
Finding  插件发现的风险、影响或异常
```

## 3. 特殊能力归类

### codegraph

归类: 核心能力 + 语言插件。

- 核心: 图谱存储、查询、统一输出协议、MCP/HTTP 暴露。
- 插件: Java、Python、Node、.NET 等 parser/indexer。

### cross-link

归类: 核心能力 + 框架插件。

- 核心: 跨层关系模型、影响分析查询、证据聚合。
- 插件: Vue、React、Spring、Express、ORM、SQL 的识别规则。

### Agent Memory

归类: 核心模块 + 来源插件。

- 核心: 记忆存储、权限、召回、冲突消解、生命周期、遗忘、压缩。
- 插件: 飞书会议、Jira issue、Git commit、CI 失败、Wiki 文档等记忆来源。

### 影响分析报告

归类: 核心模块 + 插件证据贡献。

- 核心: 报告结构、证据排序、风险聚合、置信度、输出格式。
- 插件: 提供页面、接口、表、文档、历史任务、测试风险等证据。

## 4. 架构形态

```text
codev-platform core
  ├─ auth
  ├─ audit
  ├─ scheduler
  ├─ graph
  ├─ retrieval
  ├─ memory
  ├─ agent
  ├─ report
  └─ ops

plugin runtime
  ├─ manifest
  ├─ registry
  ├─ config
  ├─ executor
  ├─ sandbox / timeout
  └─ result validator

plugins
  ├─ frontend-vue
  ├─ frontend-react
  ├─ backend-java-spring
  ├─ backend-node-express
  ├─ backend-python-fastapi
  ├─ backend-dotnet
  ├─ database-sql
  ├─ connector-feishu
  ├─ connector-jira
  ├─ connector-wiki
  ├─ connector-git
  └─ connector-ci
```

## 5. 改造原则

1. 不推倒重来。
2. 先模块化核心，再插件化客户差异能力。
3. 现有 chroma、codegraph、cross-link、memory、agent 能力保留。
4. 先定义插件协议和统一图谱模型，再迁移现有能力。
5. 第一批只做内置插件，不急着开放第三方插件。
6. 插件失败不能影响核心服务可用性。
7. 所有插件输出必须经过 schema 校验。
8. 插件执行必须可观测: 日志、耗时、输入、输出摘要、错误码。

## 6. 第一阶段落地顺序

### Step 1: 核心模块边界整理

目标: 先把平台核心拆清楚。

输出:

- `core/auth`
- `core/audit`
- `core/graph`
- `core/retrieval`
- `core/scheduler`
- `core/report`
- `agent/memory`

注意: 这是代码组织和边界收敛，不是功能重写。

### Step 2: 插件协议文档和 schema

目标: 先有合同，再写插件。

输出:

- plugin manifest
- plugin input schema
- plugin output schema
- Node / Edge / Evidence / Finding schema
- 插件错误码
- 插件生命周期

### Step 3: demo 静态插件

目标: 用假项目跑通插件输入输出，不碰复杂真实扫描。

输出:

- `builtin.demo_static`
- 生成 page/api/table/doc/task 的节点和关系。

### Step 4: cross-link 包成内置插件

目标: 把当前 cross-link 输出适配统一图谱模型。

输出:

- `builtin.cross_link`
- 当前查询能力不回退。

### Step 5: codegraph 包成内置插件

目标: 把当前 codegraph 输出适配统一图谱模型。

输出:

- `builtin.codegraph`
- 当前 MCP/HTTP 能力不回退。

## 7. 不做事项

短期不要做:

- 不让客户直接替换 auth/audit/memory/report 核心模块。
- 不一开始做第三方插件市场。
- 不一次性支持所有语言和框架。
- 不为了插件化重写 chroma、codegraph、cross-link。
- 不把插件执行做成复杂分布式平台。

## 8. 对外表述

推荐:

> codev-platform 是一个私有化部署的全链路 AI 平台，采用“模块化核心 + 插件化扩展”架构。核心平台负责权限、审计、图谱、检索、Memory、Agent 编排和影响分析；客户差异能力通过 Vue、React、Java、Node、Python、.NET、飞书、Jira、Wiki 等插件按需接入。

不推荐:

> 所有能力都是插件。

也不推荐:

> 只是模块化重构。

准确表达:

> 核心平台模块化，客户差异能力插件化。

