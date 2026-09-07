# Web Backend 框架化落地 Plan

日期: 2026-06-02

## 结论

codev-platform 需要新增一个正式的 Python Web Backend 服务。它不是 MCP 的简单 HTTP 包装，也不是只为了生成 Swagger，而是面向前端、管理台、外部系统和企业私有化部署的后端服务层。

最终定位:

```text
MCP 服务       给 Claude / Codex / Cursor 等 AI 工具调用
Web Backend   给 Web 管理台、前端页面、外部系统调用
CLI           给本地开发、运维、脚本化操作调用
Worker        给索引、扫描、报告、同步等长任务调用
```

Web Backend 的职责:

- 统一 API 契约和 OpenAPI 文档。
- 统一请求验证、鉴权、权限、审计和错误处理。
- 统一字段命名、响应包装、分页结构和错误码。
- 隔离前端和底层 MCP / codegraph / chroma / memory / reindex 实现。
- 承担读写分离、并发控制、幂等、长任务调度。
- 承载登录、退出、组织、用户、项目等后台基础主数据。
- 承载组织管理、用户管理、项目管理和权限管理。
- 为后续 Web 控制台和企业集成提供稳定接口。

## 零、现有基础设施复用映射 + 关键决策(2026-06-02 校准)

> 本节是全文最高优先级约束。本 plan 初稿按 greenfield 写,但代码审查发现 `codev_platform/` 下
> 约一半"地基"已存在且有单测(gateway / core.acl / core.rbac / core.errors / core.audit /
> core.ratelimit / core.obslog / agent FastAPI app)。**新 Web Backend 必须建在这些之上,
> 不得另造平行实现。** 下面的决策推翻初稿里若干"新建"假设。
>
> **本节是约束总闸**。§二 目录、§五 角色、§六 响应体、§十 错误码、§十二 存储、§十九 开放项**已据本节改写**。

### 0.1 复用映射(plan 组件 → 现有资产 → 动作)

| 本 plan 组件 | 仓内现有资产 | 动作 |
|---|---|---|
| §二 `web/app.py`+`main.py` FastAPI 入口 | `agent/service.py` —— `create_app()` 工厂 + router 挂载 + 中间件栈 | **扩展,不新建 app** |
| §九 RequestId/AccessLog/Error/Auth/RateLimit 中间件 | `gateway/` —— `AuthMiddleware`(纯 ASGI,SSE 安全)、`RateLimitMiddleware`、`build_authenticator`、passthrough/token 双模 | **复用**;仅 `RequestId` 是真新增 |
| §三 `deps/identity.py` IdentityDependency | `gateway.AuthMiddleware` 已把 Identity 写进 `request.state.identity`;`core/identity.py` 从 header 解析 user/org | dep **只读 `request.state`**,不再自解析头 |
| §五 权限模型(5 角色) | `core/acl.py` `can_access()` + `core/rbac.py` scoped RBAC(`Membership`、role×scope×action) + `agent/rbac_store_pg.py` 取数 | **映射到现有**,见 §五 |
| §六 响应体 + §十 错误码 | `core/errors.py` —— `ErrorCode`(8 类)、`PlatformError`、`http_status`、`to_http_payload`、`to_mcp_error` | **单一真值源**,见 §六/§十 |
| §九 写审计 | `core/audit.py` —— `audit_access()` jsonl | **复用** |
| §九 RateLimit(二阶段) | `core/ratelimit.py`(滑窗,纯逻辑)+ gateway 中间件(config 开关) | **复用** |
| §十二 存储 | `memory_store_pg.py` / `session_pg.py` / `rbac_store_pg.py` 已 PG | **PG 起步**,见 §十二 |
| AccessLog 脱敏 | `core/obslog.py` —— dev/prod redaction | **复用** |

### 0.2 关键决策(已定,后文据此)

- **D1 进程拓扑**:Web Backend 是**独立服务/独立进程**(独立端口、独立 `web/app.py`+`main.py`+`config.py`、
  独立 OpenAPI),与 agent 服务平级。**关键认知:复用 `gateway`/`core.*` 是"共享库"层面,不需要同进程** ——
  独立服务照样 `import` 这些库,单一真值源不丢。`web/` **不重造**中间件/identity:Auth/RateLimit/AccessLog
  复用 `gateway` 中间件类,identity 走 `gateway.AuthMiddleware` + `request.state.identity`。
  与 agent 的关系是 **BFF**:前端只跟 Web Backend 说话,`/agent/chat` 由 web 经
  `integrations/agent_client.py` 内部转调 agent 能力(对齐 §十五 意图)。
  理由:生命周期/爆炸半径隔离(改 org/user 不断 agent SSE)、负载隔离(admin 流量不被 LLM 慢请求饿死)、
  安全面分离(web 前端面权威 vs agent AI 工具面)、私有化可裁剪;运维上同现有 `serve-mcp` 多端点模型一致。
- **D2 错误码单一真值源**:统一用 `core/errors.py:ErrorCode`。§十 那串 `VALIDATION_*/AUTH_*/...`
  **不另立第二套 taxonomy**,而是收敛/additive 扩进现有 8 类(`invalid_params` / `access_denied` /
  `project_unknown` / `dependency_missing` / `index_missing` / `upstream_unavailable` / `rate_limited` / `internal`)。
- **D3 响应 envelope 收口(2026-06-02 终定:对齐 stock-admin-web `BaseApiResponse`)**:为让新 admin 前端
  **逐字复用** stock-admin-web 的 `utils/fetch`(`fetch.ts`/`types.ts`)+ `models/enum.ts` + ProTable 全套(零改请求层),
  envelope 终态对齐业务前端 `BaseApiResponse`,而非更早设想的扁平 `success` 版:
  - **成功**:`{result:0, message:"OK", data, errors:[], requestId}`(`result===0`=成功,前端 `responseCodeHandler` 据此判);
  - **分页**:`{result:0, data:[], currentPage, pageSize, total, totalPage, errors:[], requestId}`;
  - **错误**:HTTP 4xx/5xx + `{result:1, message, data:null, errors:[{errorCode, errorMessage, field?}], requestId}`。
  - `result` 是**业务码**(0=成功,与 HTTP status 正交)—— 这是成熟约定,**不是** 早先反对的 `result:200`(body 内镜像 HTTP 的双状态码);
    HTTP status 仍由 `ErrorCode` 8 类决定(`to_http_payload`),前端 401/403 拦截器照常工作。
  - `errors[].errorCode` 复用 `core.errors.ErrorCode`(8 类)或 sub-code(§十),`errorMessage` = 对外 message。
  适配在 `core/httpkit/envelope.py`(`ok`/`page`/`error_response`);路由用 `ok(data)`/`page(...)` helper,**不读 envelope 字段**,故对齐改动只动 envelope.py + 测试断言。
  agent 服务架构一致(同用 httpkit envelope);**MCP server 仍裸 `{error, code}`**(AI 工具面,`to_mcp_error`)。
  现状提醒:`agent/routes/chat.py` 现 `HTTPException(detail=str(e))` 泄漏 str(e),迁 envelope 时改抛 `PlatformError`。
- **D4 权限模型复用 scoped RBAC**:复用 `core.acl.can_access`(项目访问闸)+ `core.rbac`
  (`Membership` + `role_allows` + scope 链 org>team>project>personal)。plan 的 5 角色是 scope×rank
  组合,映射见 §五;`team` scope 代码已有、初稿漏了,补上。取数走已存在 `rbac_store_pg.fetch_membership`。
- **D5 存储 PG 起步**:对齐已落地的 `*_store_pg.py`,org/user/member/job 表走 PG,**不引 SQLite job 表**。§十九 删除该开放项。
- **D6 单一鉴权真值源(卖点)**:web 路由调 `core.acl.can_access`,使其从"3 MCP + agent + memory"
  扩为 **6 个 consumer 共用一套授权模型**。这是复用收益,不是重造。
- **D7 共享 HTTP 骨架 `core/httpkit/`(支撑 D1+D3 的"独立但一致")**:新抽叶子子包,被 agent + web
  **都 import**,装"任意 HTTP 入口共用"的部分:`envelope.py`(CommonResult/PageResult + PlatformError→envelope
  适配)、`pagination.py`(分页 dep)、`permissions.py`(FastAPI 权限依赖工厂,内部调 `core.acl`+`core.rbac`)、
  `app_factory.py`(`build_app(title, routers, public_paths)`:挂 gateway 中间件栈 + 统一异常处理器 + `/health` 样板)、
  `openapi.py`(operationId 去重 + envelope 泛型 schema 命名稳定化 + BearerAuth)。
  **一致性来自"同 import 一套 core+gateway+httpkit",独立性来自"各自 create_app/路由/业务 service/进程"**。
  agent 现已 ~80% 同构,对齐成本约 **2.5 天**(抽 app_factory + 加统一异常处理器 + 路由抛 PlatformError + 采用 envelope + identity dep 上移),
  作为 Phase 1/2 的一部分顺带做(不单列阶段)。`core/httpkit` 不 import 任何业务模块,保持叶子可复用。
- **D8 资源隔离铁律(admin 不挤占平台/MCP 并发)**:Web Backend 负载**绝不**争用 AI 侧 MCP 的 GPU/并发资源(详 §12.1)。
  三条:① codegraph/cross_link 读走**只读 SQLite**不经 daemon → 零争用;② chroma 检索**走现有 daemon + 有界并发 + timeout**,
  **绝不本地起 embedding/reranker**(防 8GB GPU 第二消费者 OOM,GPU 恒单例);③ reindex/rebuild **job 化 + 复用平台既有写锁**,
  不另开并行写路径。语言已定 **Python FastAPI**(见 §十九),枚举统一返回在 Python 侧复刻 stock-admin-api 契约(§二十一)。

### 0.3 据此对实施阶段的影响(详见 §十六)

- Phase 1 骨架**收缩**:新建独立 `web/app.py`(自己的进程/端口),但中间件复用 gateway(`add_middleware`),不重造;Phase 1 = app + 挂 gateway 中间件 + envelope helper + health。
- Phase 2 **改向**:接 `core.errors` → web envelope adapter + 新增 RequestId 中间件。**不迁 agent 路由**(D3:前端走 BFF,agent 保持自有契约)。
- Phase 3 **大幅收缩**:复用 gateway identity,只新增 project permission 依赖(调 `core.acl`)+ 补 ACL 集成测试 —— 正好关审计缺口,不再造 Identity model。
- Phase 4 **上调估时**:Auth/Orgs/Users 是唯一真 greenfield 块(当前无 user 表、无密码库,`identity.py` 默认 `'local'`),2-3 天偏乐观,实为 3-4 天。

---

## 一、目标

第一阶段目标不是把所有现有能力都暴露成接口，而是先搭出一个可维护、可扩展、可测试的后端框架。

必须具备:

- FastAPI 应用入口。
- Swagger/OpenAPI: `/api/docs`、`/api/redoc`、`/api/openapi.json`。
- 模块化目录结构。
- routes / services / repositories / schemas / domain / integrations 分层。
- 统一请求上下文: request_id、org_id、project_id、user_id。
- 统一响应结构: `CommonResult`、`PageResult`(对齐前端 `BaseApiResponse`,见 §六:`result:0`+`errors[]`)。
- 统一异常和错误码。
- 统一字段命名规范。
- 统一鉴权和权限依赖。
- 写操作审计。
- 长任务 job 化。
- 项目级并发锁。
- route、service、repository 的基础测试样例。

暂不追求:

- 复杂微服务拆分。
- 一开始引入 Celery / Redis / Kafka。
- 一次性覆盖所有历史 MCP 能力。
- 直接替换现有 agent / chroma / codegraph 的 **MCP / 索引服务**(AI 面不动)。
  (例外:**确实要替代并退役 Java `codegraph-api`(:18082)的前端 HTTP 可视化面** —— 那是本 plan 的目标之一,见 §二十;退役的是它的 Web 前端面,不是 codegraph 的 AI MCP 面。)

## 二、推荐目录结构

> **据 D1+D7 修订**:envelope / openapi / 分页 / 权限依赖 / app 工厂样板 **不在 `web/`**,而在新共享层
> `core/httpkit/`(agent + web 都 import,见下)。`web/` 只有自己的 `app.py`/`main.py`/`config.py`
> (独立 uvicorn 进程 + 端口)+ 纯业务分层。`web/app.py` 与 `agent/service.py` 都调
> `core.httpkit.app_factory.build_app(...)`,各自 uvicorn 起。Auth/RateLimit 复用 `gateway` 中间件类,
> identity 走 `request.state.identity`,**不重造**。

```text
codev_platform/
  core/
    httpkit/          # D7 共享 HTTP 骨架(agent + web 都 import;叶子,不 import 业务)
      __init__.py
      envelope.py     # CommonResult[T]/PageResult[T] + PlatformError->envelope(复用 core.errors.to_http_payload)
      pagination.py   # 分页参数 dep(pageNumber/pageSize/nextToken)+ PageResult 组装
      permissions.py  # FastAPI 权限依赖工厂: require_project_access()/require_role(),内部调 core.acl+core.rbac
      app_factory.py  # build_app(title, routers, public_paths): 挂 gateway 中间件栈 + 统一异常处理器 + /health
      openapi.py      # operationId 去重 + envelope 泛型 schema 命名稳定化 + BearerAuth(见 §十三)
      enums.py        # 枚举元数据机制: BaseEnum 协议 + EnumRegistry + EnumItem(见 §二十一)

  web/
    __init__.py
    app.py            # create_app(): 调 httpkit.build_app + 注册本包 routes(独立进程)
    main.py           # uvicorn 入口 / CLI: codev-platform web serve
    config.py         # web 专属配置(端口、CORS、token TTL...);复用 core.config 加载

    schemas/
      __init__.py
      common.py
      fields.py
      auth.py
      health.py
      orgs.py
      users.py
      projects.py
      indexes.py
      jobs.py
      agent.py
      memory.py
      reports.py

    routes/
      __init__.py
      auth.py
      health.py
      orgs.py
      users.py
      projects.py
      indexes.py
      jobs.py
      agent.py
      memory.py
      reports.py
      audit.py

    services/
      __init__.py
      auth_service.py
      org_service.py
      user_service.py
      project_service.py
      index_service.py
      job_service.py
      agent_service.py
      memory_service.py
      report_service.py

    repositories/
      __init__.py
      org_read_repo.py
      org_write_repo.py
      user_read_repo.py
      user_write_repo.py
      project_read_repo.py
      project_write_repo.py
      job_read_repo.py
      job_write_repo.py
      memory_read_repo.py
      memory_write_repo.py
      audit_write_repo.py

    domain/
      __init__.py
      enums.py
      errors.py
      identity.py
      org.py
      user.py
      project.py
      job.py
      locks.py

    integrations/
      __init__.py
      codegraph_client.py
      cross_link_client.py   # 接 cross_link.server/query(替代 Java codegraph-api 的 cross-link 面,见 §二十)
      chroma_client.py
      agent_client.py
      reindex_client.py
```

测试目录:

```text
tests/
  web/
    test_health_routes.py
    test_auth_routes.py
    test_org_routes.py
    test_user_routes.py
    test_project_routes.py
    test_project_service.py
    test_index_jobs.py
    test_web_openapi.py
    test_web_auth_acl.py
    test_web_error_handler.py
```

## 三、分层规则

### routes

职责:

- 声明 HTTP method、path、tags、summary、operation_id。
- 接收 Pydantic request。
- 通过依赖获取 identity、pagination、permissions。
- 调 service。
- 返回 `CommonResult` 或 `PageResult`。

禁止:

- 直接访问数据库。
- 直接调用底层 MCP 工具。
- 写复杂业务判断。
- 拼接异常响应。

### services

职责:

- 编排业务流程。
- 做业务级校验。
- 做权限边界补充判断。
- 控制读写仓储调用顺序。
- 创建 job。
- 控制事务、锁、幂等。

禁止:

- 直接依赖 FastAPI Request。
- 返回裸 dict 给 routes。
- 绕过 repository 写文件或数据库。

### repositories

职责:

- 负责数据读写。
- 隐藏 SQLite / 文件 / 内存存储细节。
- 提供 read repo 和 write repo。
- 不写业务流程。

规则:

```text
ReadRepository  只查询
WriteRepository 只创建、更新、删除
Service         决定何时读写、如何组合
```

### domain

职责:

- 统一枚举。
- 领域对象。
- 错误码。
- 业务规则函数。
- 并发锁、版本号、状态流转规则。

### integrations

职责:

- 封装对 codegraph、chroma、agent、reindex、MCP 能力的调用。
- 把外部异常转换为领域错误。
- 控制超时、重试、降级。

## 四、统一请求链路

所有 Web API 请求必须经过固定链路:

```text
HTTP Request
  -> RequestIdMiddleware
  -> AccessLogMiddleware
  -> ErrorHandlerMiddleware
  -> Auth / Identity Dependency
  -> Permission Dependency
  -> Pydantic Request Validation
  -> Route
  -> Service
  -> Repository / Integration
  -> CommonResult / PageResult
  -> Audit for write operations
```

请求上下文统一字段:

```text
request_id
org_id
project_id
user_id
username
roles
is_admin
```

HTTP Header 建议:

```text
Authorization: Bearer <token>
X-Org-Id: <org id>
X-Project-Id: <project id>
X-Request-Id: <optional client request id>
Idempotency-Key: <optional write request key>
```

登录、退出和刷新 token 是例外入口:

- `login` 不要求已有 identity，但必须做账号密码校验、失败限流和审计。
- `refreshToken` 不要求 access token，但必须校验 refresh token。
- `logout` 要使 refresh token 失效；如果是多端登录，后续再扩展 session 级退出。
- 创建第一个组织可以走安装初始化模式；正常运行后创建组织必须要求 platform_admin。

## 五、权限模型

> **据 D4 修订**:复用现有 `core/rbac.py` 的 **scope × rank** 模型,**不另立 5 个平铺角色枚举**。
> 经分析,plan 初稿那 5 个"角色"其实是 `(scope, rank)` 的笛卡尔子集 —— `org_admin` 与 `project_owner`
> 都是 rank=`admin`,区别只在 scope;`viewer` 是 rank=`viewer` 跨任意 scope。映射如下,与
> `core.rbac.role_allows(role, action)` 零改动对齐。判定取数走已存在的 `agent/rbac_store_pg.fetch_membership`。

现有模型(`core/rbac.py`):`ROLE_RANK = {viewer:1, member:2, admin:3}`,`ACTION_MIN = {read:1, recall:1, write:2, admin:3}`,
scope 链 `org > team > project > personal`,判定恒等式 `role_allows(role, action) = ROLE_RANK[role] >= ACTION_MIN[action]`。

### 5.1 plan 角色 → (scope, rank) 映射

| plan 角色 | (scope, rank) | action 门槛 | 数据落点 |
|---|---|---|---|
| `org_admin` | org + admin(3) | org 域 admin:管用户/角色/项目 | `org_members.org_role='admin'` |
| `project_owner` | project + admin(3) | project 域 admin:管成员/配置/索引任务 | `project_access.role='admin'` |
| `project_member` | project + member(2) | project 域 write:用能力/触发分析 | `project_access.role='member'` |
| `viewer` | 任意 scope + viewer(1) | 仅 read/recall | 对应 scope 表 `role='viewer'` |
| `team_*`(初稿漏列)| team + member/admin | team 域,scope 链已有 | `team_members.role` |
| `platform_admin` | **现模型无此维度**,见 5.2 | 跨 org 全权 | `platform_admins(user_id)` 白名单 |

> 关键:**不新增角色枚举**。要"加角色"= 在数据表填对应 `(scope, role)`,不改代码。

### 5.2 platform_admin(org-less 跨组织超管)—— 方案 C:identity 级 bypass 标志(已定)

**关键事实**:`core/acl.py:can_access`(项目访问闸)**不读 `Membership`**,只看 `identity`(org_id / all_projects / projects 白名单);
只有 `core/rbac.py:role_allows`(动作闸)读 `Membership`。两闸分离 → platform_admin 必须同时过两闸。
因为 `can_access` 是身份驱动,**无论哪种方案都要在 acl.py 加一处 platform 分支**,所以方案 A "靠 fetch_membership 单点注入" 的省事优势其实不成立。

**采用方案 C**:在 identity 上加布尔 `is_platform_admin`,作**显式 bypass**,与 acl.py 现有 `all_projects` 标志同一范式:
- `can_access` 顶部:`is_platform_admin → AccessDecision(True, "platform admin", advisory=False)`(非 advisory → 审计必记)。
- `httpkit.permissions.require_role`(动作闸):同一标志短路放行。
- `compute_visible_scopes`:`is_platform_admin → 可见全部 org`(列举面)。

**真值源(两阶段)**:① 装机初始化期由 **config/env 白名单**播种第一个(`config.platform_admins=[uid]` / `CODEV_PLATFORM_ADMINS`),
**启动不依赖 DB**,破 "建组织需超管、设超管需登录" 的鸡生蛋(与 §四 安装初始化模式对齐);② 稳定后迁 **PG `platform_admins(user_id)` 表** + Web 管理接口,
增删超管不再改配置重启。判定逻辑不变,只换白名单来源:`identity.is_platform_admin = (user_id ∈ 白名单 ∪ PG 表)`。

> 为什么不选 A/B:A(`org_role` 哨兵 + `ROLE_RANK` 加 `platform:4`)把全局身份塞进 "scope 内角色" 语义,且 `ROLE_RANK`
> 在 `core/rbac.py` 与 `rbac_store_pg._ROLE_RANK` 有**两份副本**,加 rank 引入 DRY 同步隐患;又因 `can_access` 不读 Membership,A 仍要改 acl.py,并未省。
> B(新 `platform` scope)动 `Membership` dataclass + 多处分支,且 platform 是无 ref 单例 scope,与 `(scope, scope_ref)` 二元范式不贴。
> C 保持 rank 阶梯纯净(viewer/member/admin = scope 内授权),特权建模成它本来的样子(身份级标志),改动点都在 core 的两个汇聚闸。

### 5.3 web / agent 共用同一 rbac(解耦)

两服务共用**完全相同**三段栈,不复制:① 取数 `rbac_store_pg.fetch_membership → Membership`(唯一 PG 入口);
② 判定 `core.rbac.role_allows` + `compute_visible_scopes`(纯函数无 IO,直接 import);
③ 身份 `core.identity.resolve_*_from_request`(web 走 HTTP header,agent 同函数)。
`can_access`(能否进 project)与 `role_allows`(进了能做啥 action)两闸串联,两服务同序调用。
解耦点:`rbac.py`/`acl.py` 不依赖任何 web/agent 框架对象,只吃 `Membership` + duck-typed `identity`。

### 5.4 权限规则(不变)

- 用户必须属于组织,才能进入组织上下文。
- 用户必须是项目成员或组织管理员,才能访问项目上下文。
- `platform_admin` 可跨组织管理,但所有跨组织操作必须审计。
- `org_admin` 只能管理本组织用户、组织配置和项目。
- `project_owner` 不能创建组织、不能管理组织级用户,只能管理项目成员和项目配置。
- 禁用组织会阻断该组织下所有项目访问;禁用用户会阻断其所有组织和项目访问。
- 角色变更、成员变更、组织状态变更必须写审计。

## 六、统一响应结构

> **据 D3 终定(对齐 stock-admin-web `BaseApiResponse`,2026-06-02)**:envelope 形状与业务前端
> `utils/fetch` 的 `BaseApiResponse` 一致,使新 admin 前端**逐字复用** `fetch.ts`/`types.ts`/`enum.ts`/ProTable 全套。
> 模型 + 适配在 `core/httpkit/envelope.py`(D7 共享层),已落地。`result` 是业务码(0=成功,与 HTTP status 正交)。

成功响应(HTTP 200):

```json
{ "result": 0, "message": "OK", "data": {}, "errors": [], "requestId": "..." }
```

分页响应(HTTP 200):

```json
{ "result": 0, "message": "OK", "data": [], "currentPage": 1, "pageSize": 20, "total": 0, "totalPage": 0, "errors": [], "requestId": "..." }
```

错误响应(HTTP status 由 `ErrorCode` 8 类决定,如 400/403/404/429/503):

```json
{ "result": 1, "message": "unknown enumType: NopeEnum", "data": null,
  "errors": [{ "errorCode": "invalid_params", "errorMessage": "unknown enumType: NopeEnum", "field": null }],
  "requestId": "..." }
```

- `result`:业务码,**0=成功 / 非0=失败**(前端 `responseCodeHandler` 判 `result===0`);与 HTTP status 正交。
- `message`:对外文案。`data`:业务数据。`errors[]`:`{errorCode, errorMessage, field?}`。
- `errors[].errorCode`:`core.errors.ErrorCode`(8 类)或 sub-code(§十);`errorMessage` = 对外 message。
- HTTP status 仍由 `ErrorCode` 8 类决定(`to_http_payload`),前端 401/403 拦截器照常工作。
- `requestId`:透传 `RequestIdMiddleware`(`BaseApiResponse` 额外字段,前端忽略不影响兼容)。

Pydantic 模型 + 适配(`core/httpkit/envelope.py`,已实现):

```python
class ErrorItem(BaseModel):
    errorCode: str; errorMessage: str; field: str | None = None

class CommonResult(BaseModel, Generic[T]):
    result: int = 0; message: str = "OK"; data: T | None = None
    errors: list[ErrorItem] = Field(default_factory=list); requestId: str | None = None

class PageResult(BaseModel, Generic[T]):
    result: int = 0; message: str = "OK"; data: list[T] = Field(default_factory=list)
    currentPage: int = 1; pageSize: int = 20; total: int = 0; totalPage: int = 0
    errors: list[ErrorItem] = Field(default_factory=list); requestId: str | None = None

def ok(data=None, *, request_id=None): return CommonResult(result=0, data=data, requestId=request_id)
def error_response(err, *, request_id=None, error_code=None):   # result=1 + errors[]; HTTP status 复用 to_http_payload
    body, status = to_http_payload(err)
    item = ErrorItem(errorCode=error_code or body["code"], errorMessage=body["error"])
    return JSONResponse(CommonResult(result=1, message=body["error"], errors=[item],
                                     requestId=request_id).model_dump(), status_code=status)
```

统一异常处理器(`app_factory` 注册):非 `PlatformError` → `PlatformError(INTERNAL)`(不泄漏 str(e));
`RequestValidationError` → `PlatformError(INVALID_PARAMS)`。均经 `error_response` 转 envelope。

## 七、统一字段规范

新 Web API 使用统一字段，不再沿用多个历史命名混用。

通用字段:

| 字段 | 含义 |
|---|---|
| id | 主键 |
| code | 业务编码 |
| name | 名称 |
| description | 描述 |
| status | 状态 |
| createdBy | 创建人 |
| createdAt | 创建时间 |
| updatedBy | 更新人 |
| updatedAt | 更新时间 |
| deleted | 逻辑删除 |
| version | 乐观锁版本 |

分页字段:

| 字段 | 含义 |
|---|---|
| pageNumber | 页码 |
| pageSize | 每页数量 |
| nextToken | 游标，后续扩展 |

时间字段:

| 推荐字段 | 禁止新增混用 |
|---|---|
| createdAt | createTime / createdOn / dataCreatedTime |
| updatedAt | updateTime / updatedOn / dataLastModifiedTime |
| startedAt | startTime / startedOn |
| completedAt | completeTime / completedOn |

兼容策略:

- 旧接口可以保留旧字段。
- 新 Web API 默认用统一字段。
- 如果必须兼容外部系统字段，在 schema 层做 alias，不把历史字段扩散到 service 和 domain。

## 八、统一验证

验证分三层:

```text
Schema      字段类型、长度、必填、格式
Service     业务规则、状态流转、权限边界、重复提交
Repository  唯一约束、外键约束、版本约束
```

示例:

```python
class ProjectRegisterRequest(BaseModel):
    code: str = Field(..., min_length=1, max_length=100, description="项目编码")
    name: str = Field(..., min_length=1, max_length=200, description="项目名称")
    path: str = Field(..., min_length=1, description="项目路径")
```

规则:

- 所有 request body 必须有 schema。
- 所有 response 必须有 schema。
- 不允许 routes 返回裸 dict。
- 不允许 service 依赖前端已经校验。
- 路径、文件系统、项目 ID、org ID 必须服务端再次校验。

## 九、统一拦截和中间件

> **据 §零 修订**:大部分已存在,只 RequestId 是真新增。下表"来源"列标明复用点。

第一阶段:

| 中间件 / 依赖 | 职责 | 来源 |
|---|---|---|
| RequestIdMiddleware | 生成或透传 request_id | **新增**(放 `core/httpkit`,两服务共挂) |
| AccessLog | 记录 method、path、status、duration | 复用 `core/obslog.py`(dev/prod 脱敏) |
| 统一异常处理器 | 把异常转换为统一 envelope | **新增** `core/httpkit/app_factory` 的 handler,复用 `core.errors` |
| Identity | 解析 token、org、project、user | 复用 `gateway.AuthMiddleware` + `request.state.identity`,**不重造** |
| 权限依赖 | 检查角色和项目权限 | 复用 `core.acl.can_access` + `core.rbac`(封装在 `httpkit.permissions`) |
| 写审计 | 记录写操作审计 | 复用 `core.audit.audit_access()` |
| RateLimit | 限流 | 复用 `gateway.RateLimitMiddleware`(config 开关,已落地) |

第二阶段再考虑:

- BodySizeLimitMiddleware。
- CORS 白名单。
- API key for external systems。
- SSE / WebSocket 连接管理。

## 十、错误码规范

> **据 D2 修订**:**不另立第二套 taxonomy**。机器可读的**主码 `code` 收敛到 `core.errors.ErrorCode` 的 8 类**
> (它决定 HTTP status + 程序分支);初稿那串 `VALIDATION_*/AUTH_*/ORG_*/...` 降格为 envelope 的**可选
> `errorCode` sub-code**(纯展示/前端精细分支,不影响 status)。两层职责分离 → 既保单一真值源,又不丢业务粒度。

### 10.1 主码:core.errors.ErrorCode(8 类,唯一真值源)

`invalid_params`(400) · `access_denied`(403) · `project_unknown`(404,语义=resource_unknown) ·
`dependency_missing`(503) · `index_missing`(503) · `upstream_unavailable`(503) · `rate_limited`(429) · `internal`(500)。

### 10.2 初稿 sub-code → 8 类主码 映射

| 初稿 sub-code(填 `errorCode`)| 主码 `code` | HTTP |
|---|---|---|
| `VALIDATION_INVALID_FIELD` | `invalid_params` | 400 |
| `AUTH_TOKEN_MISSING/INVALID` · `AUTH_LOGIN_FAILED` · `AUTH_REFRESH_TOKEN_INVALID` | `access_denied` | 403 |
| `PERMISSION_PROJECT_FORBIDDEN` | `access_denied` | 403 |
| `ORG_NOT_FOUND` · `USER_NOT_FOUND` · `JOB_NOT_FOUND` · `PROJECT_NOT_FOUND` | `project_unknown` | 404 |
| `ORG_ALREADY_EXISTS` · `USER_ALREADY_EXISTS` · `PROJECT_ALREADY_REGISTERED` | `invalid_params`(冲突就近归;无 409)| 400 |
| `INDEX_JOB_ALREADY_RUNNING` · `CONCURRENCY_VERSION_CONFLICT` | `rate_limited`(可退避重试语义)| 429 |
| `INTEGRATION_CODEGRAPH_TIMEOUT` · `INTEGRATION_*` | `upstream_unavailable` | 503 |
| `MEMORY_* / AGENT_* / REPORT_*` 依赖缺失 | `dependency_missing` | 503 |
| `INTERNAL_UNEXPECTED_ERROR` / 未归类 | `internal` | 500 |

> 8 类无 409:`*_ALREADY_EXISTS` / `VERSION_CONFLICT` 就近归 400/429,**精确性由 `errorCode` sub-code 承担**,
> 不为此扩第 9 类(否则破 `_HTTP_STATUS` 单一真值源的简洁)。新增 sub-code = 加个常量字符串,零成本、不碰枚举。

### 10.3 异常分层(全部 raise `core.errors.PlatformError`)

业务/入参/权限/外部调用/并发冲突一律抛 `PlatformError(code, message, detail=...)`,由 `app_factory` 统一异常处理器转 envelope。
**不再自定义 `DomainError/ValidationError/...` 一组异常类**(那是又一套平行 taxonomy);需要细分时用 `code`(8 类)+ `errorCode`(sub-code)表达。

## 十一、读写分离方案

第一阶段采用轻量 CQRS，不引入复杂基础设施。

读接口:

```text
OrgReadRepository.list_orgs()
OrgReadRepository.get_org_detail()
UserReadRepository.list_users()
UserReadRepository.get_user_detail()
ProjectReadRepository.list_projects()
ProjectReadRepository.get_project_detail()
ProjectReadRepository.get_project_status()
JobReadRepository.get_job_detail()
```

写接口:

```text
OrgWriteRepository.create_org()
OrgWriteRepository.update_org()
UserWriteRepository.create_user()
UserWriteRepository.update_user_status()
ProjectWriteRepository.register_project()
ProjectWriteRepository.update_project_status()
ProjectWriteRepository.delete_project()
JobWriteRepository.create_job()
JobWriteRepository.update_job_status()
```

Service 示例:

```text
ProjectService.register_project()
  -> validate path
  -> check permission
  -> check duplicate via read_repo
  -> write via write_repo
  -> create audit event
```

收益:

- 查询和写入边界清楚。
- 后续接只读库、缓存、搜索索引更容易。
- 写操作统一处理事务、审计、锁和幂等。
- 降低并发覆盖风险。

## 十二、并发设计

必须提前处理的并发问题:

| 场景 | 风险 | 方案 |
|---|---|---|
| 同项目重复重建索引 | codegraph/chroma 状态互相覆盖 | 项目级互斥锁 |
| 用户重复点击写接口 | 重复创建、重复调度 | Idempotency-Key |
| 多人同时更新配置 | 后写覆盖先写 | version 乐观锁 |
| 长任务占用 HTTP 请求 | 超时、前端卡死 | job 化，异步执行 |
| 底层模块阻塞 | FastAPI worker 被占满 | 长任务进 Worker，短请求限时 |
| Memory 跨用户写入 | 权限泄漏 | org/project/user 边界强校验 |

并发原则:

- HTTP 请求只处理短事务。
- 索引、扫描、报告、同步必须走 job。
- 同一项目同一类型 job 默认互斥。
- 写接口必须支持幂等或有唯一约束。
- 更新类接口必须带 `version` 或等价并发控制。
- 所有 integration 调用必须有 timeout。

第一阶段可采用(**据 D5:存储 PG 起步**,对齐已落地的 `memory_store_pg`/`session_pg`/`rbac_store_pg`,不引 SQLite):

```text
Postgres job table        # 与现有 *_store_pg 同库,多租户存储不分叉
asyncio task runner       # 进程内,短期够用
in-process project lock registry  # 单进程互斥;跨进程升级见二阶段
```

第二阶段可升级:

```text
Redis lock                # 跨进程/多副本互斥
Redis Queue / Dramatiq / Celery
独立 Worker 进程
```

长任务 API:

```text
POST /api/v1/indexes/rebuild   提交重建，返回 jobId
GET  /api/v1/jobs/detail       查询 job 状态
POST /api/v1/jobs/cancel       取消 job
```

Job 状态:

```text
Pending
Running
Succeeded
Failed
Cancelled
```

### 12.1 资源隔离铁律 —— admin 服务不得影响平台 / MCP 并发资源

> **硬约束**:Web Backend(admin 面)的负载**绝不能**挤占 AI 侧 MCP 的并发与 GPU 资源 —— 用户 coding 会话的 MCP 召回是延迟敏感主路径,admin 是次要路径,**admin 让路**。

| 风险路径 | 隔离方案 |
|---|---|
| **进程级争用**(事件循环 / CPU) | D1 已独立进程独立端口 → admin HTTP 负载不进 MCP daemon / agent 进程的事件循环。**这是隔离的根基**。 |
| **GPU 单例争用**(致命:8GB GPU 上多一个 embedding/reranker 消费者即 OOM) | **Web Backend 绝不自载 embedding/reranker 模型**。所有 chroma 检索一律走现有 chroma daemon(:18083),复用其 **GPU 信号量**串行,**不旁路、不起第二个 GPU 进程**(对齐 workflow.md §6.1 "重资源单例")。 |
| **图谱查询争用**(§十五 Graph 组 / §二十) | codegraph / cross_link 是**只读 SQLite**:用独立只读连接(`open_mode=READONLY` / WAL),天然并发,**完全不碰 MCP daemon**,零 GPU、零写锁争用 —— 与 codegraph-api 旧做法同(只读),但加多租户 project 隔离。 |
| **daemon 请求洪峰**(admin 流量灌满 chroma daemon 队列拖垮 AI 召回) | `integrations/chroma_client.py` 调 daemon 走**有界并发信号量**(小上限,如 2-4)+ **timeout**,确保 daemon 余量始终留给 MCP;入口再叠 gateway `RateLimitMiddleware`(已有,config 开)。 |
| **写侧争用**(reindex/rebuild 并发写坏索引) | reindex/rebuild **job 化**(§十二)且**复用平台既有写锁**(chroma `.reindex.lock`、`.daemon.spawn.lock`、codegraph/cross-link rebuild 串行),**不另开并行写路径**;同项目同类型 job 互斥(对齐 workflow.md §6.1 "写侧串行")。Web 只提交 job + 查状态,真正的重建仍走平台单实例串行通道。 |

落地原则(三条,写进 integration 层):
1. **读走只读 SQLite,不经 daemon**(codegraph/cross_link)→ 零争用。
2. **chroma 走 daemon + 有界并发 + timeout**,绝不本地起模型 → GPU 恒定单例。
3. **重活提交给平台既有 job/锁通道**,admin 不持有也不绕过写锁 → 写侧串行不破。

> 验收(并入 §十七):压测 admin Graph/enums/CRUD 高并发时,MCP 召回 P95 延迟无明显抬升;GPU 进程数不随 admin 负载增长;无第二个 embedding/reranker 进程;reindex 仍单实例串行。

## 十三、OpenAPI 规范

FastAPI 自动生成 OpenAPI，但必须统一约束。

要求:

- 中文 tags。
- 中文 summary。
- 英文 operationId。
- 所有 request / response 都引用 schema。
- 统一 `BearerAuth` security。
- 统一 servers: `/api`。
- 保持 schema 名称稳定，方便前端生成类型。

示例接口声明:

```python
@router.post(
    "/list",
    tags=["ProjectAPI-项目管理"],
    summary="项目管理-项目列表",
    operation_id="listProjects",
    response_model=PageResult[ProjectListItemDTO],
)
async def list_projects(...):
    ...
```

OpenAPI 验证:

- `/api/openapi.json` 可以生成。
- schema 中不能出现匿名裸 dict。
- operationId 不重复。
- 前端 openapi-typescript 可成功生成类型。

## 十四、代码注释规范

注释目标是降低维护成本，不是逐行解释代码。

必须有:

- 每个 service 模块顶部说明职责和边界。
- 复杂并发逻辑前说明为什么要加锁。
- 权限边界处说明安全原因。
- integration 降级处说明失败策略。
- 兼容旧字段时说明兼容来源和淘汰计划。

示例:

```python
"""
Project service.

负责项目注册、加载、索引状态聚合。
HTTP routes 不直接访问 repository，必须通过本 service 保持权限和审计边界。
"""
```

合理注释:

```python
# 重建索引是项目级互斥任务，避免同一项目并发写入 codegraph/chroma。
```

不需要的注释:

```python
# 调用函数
# 返回结果
# 设置变量
```

## 十五、第一批 API

### Health

```text
GET /api/v1/health/check
```

用途:

- Web Backend 存活检查。
- 可选检查 codegraph、chroma、agent、job runner 状态。

### Auth

```text
POST /api/v1/auth/login
POST /api/v1/auth/logout
POST /api/v1/auth/token/refresh
GET  /api/v1/auth/session
```

用途:

- 用户登录。
- 用户退出。
- refresh token 换 access token。
- 查询当前登录 session。

规则:

- 登录失败必须审计。
- 登录失败次数需要预留限流策略。
- logout 必须使 refresh token 或 session 失效。
- token payload 至少包含 user_id、org_id、roles、过期时间。

### Orgs

```text
POST /api/v1/orgs/list
POST /api/v1/orgs/create
GET  /api/v1/orgs/detail
POST /api/v1/orgs/update
POST /api/v1/orgs/status
POST /api/v1/orgs/members/list
POST /api/v1/orgs/members/add
POST /api/v1/orgs/members/remove
POST /api/v1/orgs/members/roles
GET  /api/v1/orgs/selections
```

用途:

- 组织列表、创建、详情、更新、启用/禁用。
- 组织成员管理。
- 组织成员角色授权。
- 组织选择器。

规则:

- 初始化模式允许创建第一个组织和第一个管理员。
- 正常运行后，创建组织必须要求 `platform_admin`。
- 组织禁用后，该组织下用户不能进入组织上下文。
- 删除组织第一版不做物理删除，只做禁用或归档。

### Users

```text
GET /api/v1/users/profile
POST /api/v1/users/list
POST /api/v1/users/create
GET  /api/v1/users/detail
POST /api/v1/users/update
POST /api/v1/users/status
POST /api/v1/users/password/reset
POST /api/v1/users/roles
GET /api/v1/users/selections
```

用途:

- 当前用户信息。
- 用户列表、创建、详情、更新、启用/禁用。
- 重置密码或生成初始密码。
- 用户角色授权。
- 用户选择器。

规则:

- 用户唯一键建议使用 `username` 或 `email`，后续可接企业 SSO。
- 密码不落明文，第一版至少使用安全哈希。
- 禁用用户后必须使其 token/session 失效。
- 用户角色变更必须审计。
- 普通 org_admin 只能管理本组织用户，不能越权到其他组织。

### Projects

```text
POST /api/v1/projects/list
POST /api/v1/projects/register
GET  /api/v1/projects/detail
POST /api/v1/projects/load
POST /api/v1/projects/unload
```

用途:

- 项目注册、加载、卸载、查询。
- 替代桌面 widget 对项目状态的直接本地耦合。

### Indexes

```text
POST /api/v1/indexes/rebuild
GET  /api/v1/indexes/status
```

用途:

- 提交索引重建任务。
- 查询索引状态。

### Jobs

```text
GET  /api/v1/jobs/detail
POST /api/v1/jobs/cancel
```

用途:

- 查询长任务状态。
- 取消可取消任务。

### Agent

```text
POST /api/v1/agent/chat
```

用途:

- Web 前端统一聊天入口。
- 由 Web Backend 负责身份、项目和上下文边界。

### Reports

```text
POST /api/v1/reports/impact
GET  /api/v1/reports/detail
```

用途:

- 影响分析报告生成。
- 报告详情查询。

### Graph(codegraph + cross-link 查询/可视化)

> **吸收并替代 Java `codegraph-api`(:18082)的 12 个只读 HTTP 接口**(详见 §二十)。经
> `integrations/codegraph_client.py` + `integrations/cross_link_client.py` 调 `codev_platform` 现有
> Python 实现(不再用 Java MyBatis 重写 SQLite 查询)。**多租户**:全部带 `X-Project-Id`,经 `core.acl` 鉴权。

```text
POST /api/v1/graph/codegraph/stats
POST /api/v1/graph/codegraph/search
POST /api/v1/graph/codegraph/node
POST /api/v1/graph/codegraph/neighbors
POST /api/v1/graph/codegraph/file-tree
POST /api/v1/graph/codegraph/graph
POST /api/v1/graph/cross-link/stats
POST /api/v1/graph/cross-link/tables
POST /api/v1/graph/cross-link/table-refs
POST /api/v1/graph/cross-link/endpoint-link
POST /api/v1/graph/cross-link/search-nodes
POST /api/v1/graph/cross-link/graph
```

用途:

- 前端 force-graph 可视化 + 图谱查询(原 codegraph-api 的全部能力)。
- 与 AI 面 MCP(SSE)区分:MCP 给 AI 工具,本组给 Web 控制台前端(带身份/项目/审计)。

### Enums(枚举元数据,统一返回)

> 对齐 stock-admin-api 的 `EnumMetadataService` 契约(详见 §二十一),前端 `useModel('enum')` 无感复用。

```text
POST /api/v1/enums/list   # body 可选 {enumType};不传返回全部
```

用途:

- 统一提供前端下拉/筛选所需的全部业务枚举(org/user/project/job 状态、角色、JobType 等)。
- 前端按 `enumType` 取 options,禁止前端本地硬编码业务枚举(对齐 `cross-layer-enum-consistency` 规则)。

## 十六、实施阶段

### Phase 0: 设计固化

Estimate: 0.5 天。

任务:

- 本文档评审。
- 确认 Web Backend 和 MCP 的边界。
- 确认第一批 API。
- 确认字段规范和响应包装。

Gate:

- 本计划进入 roadmap。
- 后续新增 Web API 必须遵守本文约束。

### Phase 1: 共享 httpkit + Web Backend 骨架

Estimate: 1-1.5 天(含抽 `core/httpkit` 并让 agent 复用)。

任务:

- **新增 `core/httpkit/`**(D7):`envelope.py`、`pagination.py`、`permissions.py`、`app_factory.py`、`openapi.py`。
- 新增 `codev_platform/web` 包:`app.py`(调 `httpkit.build_app`)、`main.py`、`config.py`。
- **不新建 middleware/** —— Auth/RateLimit 复用 `gateway`,RequestId 在 httpkit。
- 新增 `schemas/common.py`、`routes/health.py`。
- 让 `agent/service.py` 也改调 `httpkit.build_app`(消除中间件挂载拷贝,验证共享层两边可用)。
- 接入 `/api/docs`、`/api/redoc`、`/api/openapi.json`。

Gate:

- `GET /api/v1/health/check` 可用;OpenAPI JSON 可生成。
- agent 服务改用 `httpkit.build_app` 后行为不变(回归 `tests/` agent 相关用例)。
- `pytest tests/web/test_health_routes.py` 通过。

### Phase 2: 统一拦截和错误(envelope + RequestId)

Estimate: 1 天。

任务:

- 新增 `RequestIdMiddleware`(httpkit,两服务共挂)。
- AccessLog 复用 `core/obslog.py`。
- `app_factory` 装统一异常处理器:`PlatformError` / `RequestValidationError` → envelope(§六)。
- **错误码复用 `core.errors.ErrorCode` 8 类 + `errorCode` sub-code(§十)**,不新定义 `ErrorItem/DomainError`。
- agent 路由(`chat.py` 等)裸 `HTTPException(detail=str(e))` 迁为抛 `PlatformError`(关 str(e) 泄漏)。

Gate:

- 404、422、业务错误都返回统一 envelope;响应含 `requestId`;不暴露 traceback / `str(e)`。
- agent 与 web 错误体同构(同 `code`/`error` 字段)。

### Phase 3: Identity 和权限边界(复用 gateway + core.acl,补测试)

Estimate: 0.5-1 天(大幅收缩:identity/acl 模型已存在,本阶段=接线 + ACL 集成测试,正好关审计缺口)。

任务:

- **不新建 Identity model / IdentityDependency** —— 复用 `gateway.AuthMiddleware` + `request.state.identity`。
- `httpkit.permissions` 暴露 `require_project_access()` / `require_role()`,内部调 `core.acl.can_access` + `core.rbac`。
- 补 `platform_admin` 哨兵(§5.2:`ROLE_RANK` 加 `platform:4` + `fetch_membership` 白名单,两份 `_ROLE_RANK` 同步)。
- **补入口级 ACL 集成测试**(审计缺口:token 模式强制 project_id、org/team 边界)。

Gate:

- 未授权请求不能访问项目接口;token 模式不能 fallback 到隐式 cwd 项目。
- project_id / org_id / user_id 边界测试通过;`platform_admin` 跨 org 放行且留审计。

### Phase 4: Auth / Orgs / Users 管理

Estimate: 3-4 天(唯一真 greenfield 块:当前无 user 表 / 无密码库,`identity.py` 默认 `'local'`)。

任务:

- 新增 auth schemas、routes、service。
- 实现 login、logout、refresh token、session。
- 新增 org schemas、routes、service。
- 新增 org read/write repositories。
- 实现组织 list/create/detail/update/status/selections。
- 实现组织成员 list/add/remove/roles。
- 新增 user schemas、routes、service。
- 新增 user read/write repositories。
- 实现用户 profile/list/create/detail/update/status/selections。
- 实现 reset password 或初始密码生成接口。
- 角色变更、组织成员变更、用户禁用必须审计。

Gate:

- 登录成功返回 access token 和 refresh token。
- 登录失败返回统一错误码 `AUTH_LOGIN_FAILED`。
- logout 后 refresh token/session 失效。
- 初始化模式只允许创建第一个组织和第一个管理员。
- 正常模式创建组织需要 `platform_admin`。
- org_admin 不能管理其他组织用户。
- 用户禁用后不能继续访问需要身份的接口。
- 组织、用户写操作有审计记录。

### Phase 5: Projects 模块

Estimate: 1-2 天。

任务:

- 新增 project schemas。
- 新增 project service。
- 新增 read/write repositories。
- 实现 list/register/detail/load/unload。
- 写操作审计。

Gate:

- 项目注册幂等或唯一约束生效。
- 写操作有审计记录。
- route/service/repository 测试通过。

### Phase 6: Jobs 和 Indexes 模块

Estimate: 2 天。

任务:

- 新增 job domain model。
- 新增 job read/write repositories。
- 新增 in-process job runner。
- 新增项目级 lock registry。
- 实现 index rebuild job。
- 实现 job detail/cancel。

Gate:

- 同项目不能并发提交两个相同 rebuild job。
- 长任务返回 jobId，不阻塞 HTTP。
- job 状态可查询。
- 失败 job 有错误码和错误信息。

### Phase 7: Agent / Memory / Reports 接入

Estimate: 2-4 天。

任务:

- Web Agent route 调用现有 agent 能力。
- Memory route 复用并强化已有 ACL。
- Reports route 聚合 codegraph、cross-link、memory、retrieval。
- 所有 integration 加 timeout 和错误转换。

Gate:

- Agent chat 必须携带 project_id。
- Memory 写入不能跨 org/project/user。
- Report 生成失败不会拖垮 Web API。

### Phase 8: 前端类型生成和契约测试

Estimate: 1 天。

任务:

- 固定 `/api/openapi.json`。
- 使用 openapi-typescript 或 orval 生成前端类型。
- 增加 operationId 重复检测。
- 增加 OpenAPI schema 快照测试。

Gate:

- 前端可基于 OpenAPI 生成 client。
- 新增接口缺少 schema 时测试失败。
- operationId 重复时测试失败。

## 十七、验收标准

工程验收:

- 新增 Web Backend 包结构清晰。
- API 文档可打开。
- OpenAPI 可生成。
- `pytest` 通过。
- 统一错误处理覆盖 404 / 422 / 业务错误 / 未授权。
- 至少 3 个模块具备 route/service/repository 分层。

安全验收:

- token 模式强制 identity。
- 项目接口强制 project permission。
- 写接口审计。
- 错误响应不泄漏内部路径、traceback、secret。

并发验收:

- 长任务返回 jobId。
- 同项目 rebuild 互斥。
- 写接口支持幂等或唯一约束。
- 更新接口预留 version 机制。

可维护性验收:

- 字段命名统一。
- 注释说明边界和复杂逻辑。
- route 不写业务逻辑。
- service 不直接依赖 FastAPI Request。
- repository 不写业务编排。

## 十八、风险和控制

| 风险 | 控制 |
|---|---|
| Web Backend 变成 MCP 透传层 | 明确 service 层负责权限、审计、聚合 |
| Python 后端被写成脚本集合 | 强制目录分层和测试 |
| 接口字段持续混乱 | 新 API 使用统一字段，旧字段仅 schema alias |
| 长任务阻塞请求 | job 化，HTTP 只提交任务 |
| 并发写入破坏索引 | 项目级锁和 job 状态机 |
| 权限边界遗漏 | IdentityDependency + PermissionDependency + ACL 测试 |
| OpenAPI 不稳定 | operationId 检测和 schema 生成测试 |

## 十九、后续决策点

后续需要单独决策:

- ~~Web Backend 是否和现有 Agent FastAPI 合并进程~~ → **已定(D1):独立进程,BFF 内部转调 agent**。
- Job runner 第一版使用 in-process，还是直接引入独立 Worker。
- ~~存储第一版继续 SQLite~~ → **已定(D5):PG 起步,对齐现有 `*_store_pg.py`**。
- ~~管理后端用 Python 还是 Java~~ → **已定:Python FastAPI**(留 codev-platform,复用 gateway/core.acl/rbac/errors + 图谱/agent Python 实现;单一鉴权源,零跨语言重写;枚举统一返回在 Python 侧复刻,见 §二十一)。
- 前端 API client 使用 openapi-typescript、orval 还是 swagger-typescript-api。
- 桌面 widget 后续是调用 Web Backend，还是只保留本地启动器能力。
- `platform_admin`(org-less 超管)在现有 `core.rbac` scoped 模型里如何表达 —— 待补(见 §五)。

## 二十、替代并退役 Java codegraph-api(:18082)

> **本 Web Backend 的目标之一是替代旧的 Java `codegraph-api`,落地后退役它**(`C:\workspace\project`)。

### 20.1 codegraph-api 现状(待替代对象)

| 项 | 现状 |
|---|---|
| 技术栈 | Spring Boot 3.3.5 + MyBatis-Plus + sqlite-jdbc(只读)+ springdoc Swagger,端口 **18082** |
| 职责 | **只读** HTTP/Swagger 面,供前端 force-graph 可视化:`/v1/codegraph/*` 6 个 + `/v1/cross-link/*` 6 个 |
| 数据源 | 直读 `.codegraph/codegraph.db` 与 `cross_layer.sqlite`(env `CODEGRAPH_DB_URL` 单路径)|
| 短板 | **单租户**(一个 SQLite 路径,无 project_id 隔离)· **无 auth/org/RBAC/审计**(CORS 全开)· **用 Java MyBatis 重写了 `codev_platform` 已有的 Python 查询逻辑**(cross_link/codegraph 双实现,维护两套)|
| envelope | `common/CommonResult<T> = {result, message, data}`,**`result==0` 表成功**(注释"与 stock-admin-api 一致")|

> **关键发现**:本 plan §六 的响应体就是抄自这个 `CommonResult`,但初稿误写成 `result:200`(实际源是 `result:0`=成功)。
> 这反证 **D3 把数字 `result` 砍掉是对的** —— 它在不同仓语义不一(0 vs 200),留着必漂移;改用 HTTP status + `success` 布尔。

### 20.2 接口吸收映射(12 → Web Backend Graph 组)

| codegraph-api(Java :18082)| Web Backend(§十五 Graph 组)| 实现 |
|---|---|---|
| `POST /v1/codegraph/{stats,search,node,neighbors,file-tree,graph}` | `POST /api/v1/graph/codegraph/{...}` | `integrations/codegraph_client.py` → 现有 Python codegraph |
| `POST /v1/cross-link/{stats,tables,table-refs,endpoint-link,search-nodes,graph}` | `POST /api/v1/graph/cross-link/{...}` | `integrations/cross_link_client.py` → 现有 `cross_link.server`/`query` |

升级点:多租户(`X-Project-Id` + `core.acl` 鉴权)· 复用 Python 真实现(消除 Java 重写)· 统一 envelope/错误/审计/RequestId。

### 20.3 退役条件(Gate)与步骤

退役**前置**(全满足才停 18082):
- Web Backend Graph 组 12 接口全部可用,响应字段能满足前端 force-graph 渲染(逐接口对齐字段)。
- 前端可视化已从 18082 切到 `/api/v1/graph/*`(含多租户 project 选择)。
- 平台 status/统计若仍有消费方读 18082,改读 Web Backend 或 codegraph-api(:18082)REST status 面(见 `ai-tools-mcp.md` §一 b)。

步骤:① Graph 组上线 + 前端切流 → ② 观察期(确认无残留 18082 调用)→ ③ 停 18082 进程、从 `serve-mcp`/启动脚本摘除 → ④ 归档 `apps/codegraph-api`(Java 模块入 `archive/`,保留 git 历史)。

> 注:codegraph-api 与本仓 codegraph **MCP-SSE**(给 AI 工具)是两条线 —— 退役的是它的 **HTTP/前端可视化面**,
> AI 侧 codegraph 查询仍走 MCP,不受影响。本组接口接管的是"给 Web 前端的图谱查询",不是 AI 的 MCP。

## 二十一、枚举元数据(统一返回)

> **已定语言 Python(见 §十九)**,故在 Python 侧**复刻** stock-admin-api 的 `EnumMetadataService` 契约,
> 让前端 `useModel('enum')` / `pnpm run enums` 模式无感复用,且**禁止前端硬编码业务枚举**(对齐 `cross-layer-enum-consistency` 规则)。

### 21.1 参照源(stock-admin-api,Java)

`AdminEnumController` `POST /v1/admin/enums/list` → `CommonResult<Map<enumType, List<EnumItemDTO>>>`;
`EnumMetadataService` 持一张 `registry`(所有 `BaseEnum` 实现),`@Cacheable`;`EnumItemDTO` 字段:
`enumType / enumValue / localLanguage(中文) / enumOrder / displayName / description`。

### 21.2 Python 等价实现

**机制放 `core/httpkit/enums.py`(共享层,agent/web 都可用),枚举定义放各服务 `domain/enums.py`。**

```python
# core/httpkit/enums.py —— 机制(协议 + 注册表 + 端点 helper)
class BaseEnum(Protocol):
    enum_type: str          # 如 "ProjectStatusEnum"
    enum_value: str         # 如 "ACTIVE"
    local_language: str     # 中文展示
    enum_order: int
    display_name: str
    description: str

class EnumItem(BaseModel):                # ← 字段名保持 camelCase, 与 Java EnumItemDTO 对齐(前端无感)
    enumType: str; enumValue: str; localLanguage: str
    enumOrder: int; displayName: str; description: str = ""

class EnumRegistry:
    def register(self, enum_cls): ...     # 注册一个枚举类
    def list(self, enum_type: str | None) -> dict[str, list[EnumItem]]: ...  # None=全部, 否则单类; 内部缓存
```

```python
# web/domain/enums.py —— 平台后端自己的业务枚举(注册进 registry)
class ProjectStatusEnum(StrEnum): ACTIVE=...; ARCHIVED=...   # + 中文/order/desc 元数据
# OrgStatusEnum / UserStatusEnum / MemberRoleEnum / JobTypeEnum / JobStatusEnum ...
registry.register(ProjectStatusEnum); ...
```

路由 `POST /api/v1/enums/list`(body 可选 `{enumType}`)→ `ok(registry.list(enumType))`,走统一 envelope。

### 21.3 前端一致性

- 前端从 FastAPI `openapi.json` 用 openapi-typescript 生成类型(替代 Java `pnpm run api`)。
- 枚举消费保持 `useModel('enum').getEnumOptions/getFormattedEnums('XxxEnum')` 范式,值字面量四层一致(对齐 `cross-layer-enum-consistency`)。
- 新增/改业务枚举 → 改 `web/domain/enums.py` 真值源 → 前端重生成,**不在前端 `utils.ts`/`Columns.tsx` 本地造 options**。

## 二十二、依赖与降级矩阵(平台服务挂了 admin 还能干啥)

> **设计目标**:admin 的**系统 of record 是 PG**,与 AI 侧 MCP/agent/chroma daemon **解耦**。平台 AI 那套全挂,
> admin 的管理读写照常;AI 能力前台按端点优雅降级,不拖垮整服务。唯一硬依赖是 PG 与 web 进程自身。

| 挂掉的组件 | admin 自身数据(org/user/project/job/auth/RBAC/审计/enums) | 图谱查询(codegraph/cross_link **只读 SQLite 直连**) | chroma 搜索 | agent chat(BFF) | reindex/rebuild |
|---|---|---|---|---|---|
| chroma daemon(:18083) | ✅ 正常 | ✅ 正常(不经它) | ❌ 该端点 503 | ✅ | ⚠️ 重建 chroma 类失败 |
| codegraph / cross-link MCP daemon | ✅ | ✅ **正常**(直连 SQLite,不经 daemon) | ✅ | ✅ | ⚠️ 对应重建失败 |
| agent 服务 | ✅ | ✅ | ✅ | ❌ `/agent/chat` 503 | ✅ |
| reindex 通道 | ✅ | ✅(读旧索引) | ✅ | ✅ | ❌ job 失败/排队 |
| **PG**(硬依赖) | ❌ CRUD/登录/RBAC 不可用 | ✅(SQLite 不在 PG) | — | — | ❌ |
| Web 进程自身 | ❌ | ❌ | ❌ | ❌ | ❌ |

要点:
- **平台 AI 服务(MCP/agent/chroma daemon)全挂 → admin 仍能管 org/user/project**(只需 PG + web 进程)。
- **图谱只读查询不随 daemon 挂掉**(§12.1 走直连只读 SQLite)—— 隔离决策的健壮性副产物。
- **AI 能力前台是隔离 integration**:单个平台件挂 = 单端点 503(`IntegrationError → upstream_unavailable` + timeout),**不 crash admin**。
- **让 admin 自身读写瘫痪的只有 PG 或 web 进程本身**。

降级原则:① 所有 integration **必带 timeout + 错误转 `upstream_unavailable`**,绝不让下游挂掉冒泡成 500/拖垮进程;
② `GET /api/v1/health/check` **分别探测**各依赖(PG / chroma daemon / agent / 各只读 SQLite),返回 per-dependency `degraded` 状态,可观测;
③ PG 高可用(主从/副本只读)留二阶段,v1 PG 单点是已知硬依赖。
