# 模块化核心 + 插件化扩展全链路 AI 平台落地 Plan(2026-06-01)

> 定位: 把 codev-platform 从固定工具集合升级为私有化部署的“模块化核心 + 插件化扩展”全链路 AI 平台。
>
> 核心判断: 商业价值不在“又一个 Agent”，而在私有环境中稳定回答“改一个接口、字段、页面、需求会影响哪里”。

> 2026-06-02 补充决策: 不把所有东西都叫插件化。平台内部能力先模块化，客户差异能力再插件化。详见 `modular-core-plugin-extension-decision-2026-06-02.md`。

## 一、目标

第一版不追求支持所有语言和框架，先打穿一条可演示、可部署、可 POC 的链路:

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

平台核心先做模块化:

- auth: 项目 / 租户 / 用户 / token / 权限。
- audit: 访问审计、召回审计、插件执行审计。
- scheduler: 索引调度、增量触发、失败重试。
- graph: 统一图谱存储、节点、边、证据。
- retrieval: 文档 RAG、BM25、RRF、rerank、权限过滤。
- memory: Agent Memory、生命周期、遗忘、压缩、任务记忆。
- agent: Agent 编排、工具调用、上下文组装。
- report: 影响分析报告、证据聚合、置信度和风险输出。
- ops: MCP / HTTP 查询接口、部署、健康检查、日志。

客户差异能力插件化:

- FrontendPlugin: Vue / React / qiankun / 路由 / 组件 / 页面 API 调用。
- BackendPlugin: Java / Node / Python / .NET 的 endpoint、service、function、ORM。
- DatabasePlugin: SQL、表、字段、读写关系、迁移脚本。
- ConnectorPlugin: Git / Wiki / 飞书 / Jira / Confluence / CI。
- CodeGraphAdapter: 语言内部结构扫描器，函数、类、组件、调用、引用。
- CrossLinkAdapter: 框架级跨层规则，页面 -> API -> 后端 -> DB。

边界原则:

- 平台核心模块不轻易让客户替换，重点是稳定、可测、可维护。
- 插件只负责客户差异能力，必须输出统一 `Node / Edge / Evidence / Finding`。
- 插件失败不能拖垮核心服务，必须有错误码、日志和降级结果。
- 插件可以按客户购买、启用、禁用、升级。

## 二、决策前提

- 私有化部署优先，真实客户代码不进公开 SaaS。
- 公开 demo 只使用假项目和假业务数据。
- 第一阶段先做 Vue/React + Java/Spring + Python/FastAPI，Node/.NET/Django/Flask 后续以插件扩展。
- 先保留 SQLite 图谱存储，不急于引入 Neo4j/Postgres 图数据库。
- codegraph 与 cross-link 采用“核心能力 + 插件适配”模式，二者不是替代关系:
  - codegraph 核心 = 符号图谱存储、查询和统一输出协议。
  - codegraph 插件 = Java / Python / Node / .NET 等语言 parser/indexer。
  - cross-link 核心 = 跨层关系模型、证据聚合、影响分析查询。
  - cross-link 插件 = Vue / React / Spring / Express / ORM / SQL 识别规则。

## 三、Phase 划分

### Phase 0: 安全和部署底座

Estimate: 1 周。

任务:

- token 模式下 `/chat` 强制 `project_id`，禁止服务器模式隐式 cwd fallback。
- `/memory` 补 org/team/project 权限边界。
- MCP SSE、`/chat`、`/memory` 增加入口级 ACL 集成测试。
- 日志生产模式默认脱敏。
- webhook 增加 body size 限制。
- 补 Docker Compose / systemd / Windows WSL 部署说明。
- 增加统一 health check 命令。

Gate:

- `pytest` 全绿。
- 非授权 token 不能访问其他项目。
- 默认服务只监听 `internal.example.invalid`。
- 远程部署必须走反代、TLS、token。
- 新用户能按文档在本机启动。

### Phase 1: 统一图谱模型

Estimate: 1 周。

统一节点:

```text
project
file
frontend_route
frontend_component
frontend_api_call
backend_endpoint
backend_function
db_table
db_column
wiki_page
jira_issue
feishu_doc
git_commit
pull_request
```

统一边:

```text
contains
imports
calls
renders
defines_api
calls_api
implements
reads_table
writes_table
updates_table
mentions
relates_to
changed_by
```

任务:

- 定义 `GraphNode`。
- 定义 `GraphEdge`。
- 定义 `AnalyzerResult`。
- 将现有 cross-link 输出适配到统一 nodes/edges。
- 保持现有 cross-link 查询能力不回退。

Gate:

- 任意插件只要输出 nodes/edges，平台就能存储、查询、给 Agent 使用。
- 现有 `find_table_refs`、API caller、endpoint 查询能力不丢。

### Phase 2: 插件协议

Estimate: 1 周。

建议接口:

```python
class AnalyzerPlugin:
    name: str
    version: str

    def detect(self, repo_path: Path) -> bool:
        ...

    def analyze(self, repo_path: Path, project_id: str) -> AnalyzerResult:
        ...
```

任务:

- 新增 `codev_platform/plugins/`。
- 新增插件注册表。
- 新增插件发现机制。
- 新增 CLI: `codev-platform plugins list`。
- 新增 CLI: `codev-platform analyze --project <project_id>`。
- 插件失败要记录错误，但不能拖垮整个平台。

Gate:

- 新增一个插件不需要改 Agent、MCP、CLI 主流程。
- 插件分析结果能进入统一图谱。
- 插件可被 reindex worker 调度。

### Phase 3: 前端插件 Vue + React

Estimate: 2 周。

Vue 插件第一版支持:

- Vue Router。
- `.vue` SFC。
- `axios`。
- `fetch`。
- 常见自定义 `request.get/post`。
- Pinia/Vuex action。

React 插件第一版支持:

- React Router。
- `.tsx/.jsx`。
- `axios`。
- `fetch`。
- Redux/Zustand action。

输出关系:

```text
frontend_route -> frontend_component
frontend_component -> frontend_api_call
frontend_store_action -> frontend_api_call
frontend_api_call -> backend_endpoint
```

Gate:

- 能回答“这个接口被哪些页面调用”。
- 能回答“这个页面依赖哪些 API”。
- 能回答“改 `/api/customer/list` 影响哪些组件”。

第一版可以先用静态规则和轻量 AST，不追求覆盖所有写法。

### Phase 4: 后端插件 Java/Spring + Python/FastAPI

Estimate: 2-3 周。

Java/Spring 插件:

- `@RestController`。
- `@RequestMapping`。
- `@GetMapping` / `@PostMapping`。
- Service 调用。
- MyBatis Mapper XML / 注解 SQL。
- JPA Repository。
- 表名 / 字段名识别。

Python/FastAPI 插件:

- `APIRouter`。
- `@router.get/post`。
- Pydantic schema。
- service function。
- SQLAlchemy。
- raw SQL。

输出关系:

```text
backend_endpoint -> backend_function
backend_function -> backend_function
backend_function -> db_table
backend_function -> db_column
```

Gate:

- 能回答“这个后端接口读写哪些表”。
- 能回答“这个表被哪些接口使用”。
- 能回答“改 `customer.phone` 字段影响哪些接口”。

### Phase 5: 链路合并和影响分析

Estimate: 1-2 周。

核心查询:

- API 影响页面。
- DB 表影响接口。
- DB 字段影响页面。
- 函数影响接口。
- Jira/Wiki 影响代码。
- 文件变更影响业务模块。

Agent 工具:

```text
find_impact(target)
find_api_callers(api)
find_table_usage(table)
find_page_dependencies(page)
generate_impact_report(target)
```

Gate:

- 输入一个表名，能输出页面、接口、函数、SQL、文档。
- 输入一个接口，能输出前端调用方和后端实现。
- Agent 能生成一份影响分析报告。

### Phase 6: Wiki / Jira / 飞书接入

Estimate: 2 周。

第一版只读，不做复杂双向同步。

Connector:

- Wiki 文档拉取。
- Jira issue 拉取。
- 飞书文档拉取。
- Git commit / PR 信息读取。

统一节点:

```text
wiki_page
jira_issue
feishu_doc
git_commit
pull_request
```

关系:

```text
jira_issue -> wiki_page
wiki_page -> backend_endpoint
jira_issue -> changed_file
feishu_doc -> business_module
```

Gate:

- 能回答“这个需求涉及哪些接口”。
- 能回答“这个 Jira 关联了哪些代码”。
- 能回答“这个业务模块有哪些文档和代码”。

### Phase 7: Demo 项目

Estimate: 1-2 周。

准备一套 fake-but-real demo:

```text
demo-crm-vue
demo-crm-react
demo-crm-java-spring
demo-crm-fastapi
demo-db-schema
demo-wiki
demo-jira-json
```

演示问题:

- 客户列表页调用哪些接口？
- 修改 customer 表 phone 字段影响哪里？
- 这个 Jira 需求涉及哪些页面和接口？
- 生成变更影响分析报告。

Gate:

- 有公开 demo 数据。
- 有截图。
- 有演示视频。
- 有一键启动脚本。
- 有 POC 说明文档。

### Phase 8: 私有化 POC 包

Estimate: 1 周。

交付物:

- Docker Compose。
- 初始化脚本。
- 项目注册命令。
- token 创建命令。
- reindex 命令。
- health check 命令。
- POC 操作手册。
- 10 个验证问题模板。
- 影响分析报告模板。

POC 范围:

- 1 个客户项目。
- 1 个前端仓。
- 1 个后端仓。
- 1 个数据库 schema。
- 1 套 Wiki/Jira 数据。
- 1 周验证。

Gate:

- 客户能在私有环境跑起来。
- 10 个真实业务问题中至少 7 个能给出可用答案。
- 能输出一份客户可读的影响分析报告。

## 四、风险

- 技术栈过多导致每个插件都很浅，失去差异化。
- 前端 API 调用封装多样，第一版规则识别会漏。
- ORM/SQL 分析天然不完美，需要 evidence/confidence 标注。
- Jira/Wiki/飞书权限复杂，第一版只能做只读和最小权限。
- Demo 如果不够真实，客户无法理解价值。

## 五、替代方案

如果插件化一次性改造成本过高，可以先走过渡方案:

- 先定义统一 `nodes/edges` schema。
- 保留现有 scanner。
- 用 adapter 把现有 cross-link/codegraph 数据转成统一图谱。
- 新技术栈从插件协议开始接。

这样不会阻塞现有功能，也能逐步迁移。

## 六、启动条件

开始 Phase 0 前需要确认:

- 当前定位是“私有化部署 + POC 优先”，不是公开 SaaS。
- 第一版深度支持范围锁定为 Vue/React + Java/Spring + Python/FastAPI。
- 允许先用 SQLite 统一图谱，不引入新数据库。
- 接入飞书/Jira/Wiki 的第一版只读。

## 七、近期执行顺序

1. 修 token 模式强制 `project_id`。
2. 补 `/chat`、`/memory`、MCP SSE ACL 集成测试。
3. 定义统一 GraphNode / GraphEdge / AnalyzerResult。
4. 定义 AnalyzerPlugin 协议和 registry。
5. 做 Vue/React API 调用识别插件雏形。
6. 做 demo CRM 项目。

不在第一版做:

- 全语言全框架深度支持。
- 大而全管理后台。
- 企业 SSO。
- 复杂图数据库迁移。
- 公开 SaaS 托管真实客户代码。
