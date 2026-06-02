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

## 一、目标

第一阶段目标不是把所有现有能力都暴露成接口，而是先搭出一个可维护、可扩展、可测试的后端框架。

必须具备:

- FastAPI 应用入口。
- Swagger/OpenAPI: `/api/docs`、`/api/redoc`、`/api/openapi.json`。
- 模块化目录结构。
- routes / services / repositories / schemas / domain / integrations 分层。
- 统一请求上下文: request_id、org_id、project_id、user_id。
- 统一响应结构: `CommonResult`、`PageResult`、`ErrorItem`。
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
- 直接替换现有 agent / chroma / codegraph 服务。

## 二、推荐目录结构

```text
codev_platform/
  web/
    __init__.py
    app.py
    main.py
    config.py
    openapi.py

    middleware/
      __init__.py
      request_id.py
      access_log.py
      error_handler.py
      audit.py

    deps/
      __init__.py
      identity.py
      permissions.py
      pagination.py

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

第一版采用平台级、组织级、项目级三层权限。

角色:

| 角色 | 范围 | 权限 |
|---|---|---|
| platform_admin | 平台 | 创建组织、管理所有组织、查看平台级审计 |
| org_admin | 组织 | 管理本组织用户、角色、项目 |
| project_owner | 项目 | 管理项目配置、成员、索引任务 |
| project_member | 项目 | 使用项目能力、触发允许范围内的分析 |
| viewer | 组织/项目 | 只读访问 |

组织和用户关系:

```text
User
  -> OrgMember
    -> OrgRole
    -> ProjectMember
      -> ProjectRole
```

权限规则:

- 用户必须属于组织，才能进入组织上下文。
- 用户必须是项目成员或组织管理员，才能访问项目上下文。
- `platform_admin` 可以跨组织管理，但所有跨组织操作必须审计。
- `org_admin` 只能管理本组织用户、组织配置和项目。
- `project_owner` 不能创建组织，不能管理组织级用户，只能管理项目成员和项目配置。
- 禁用组织会阻断该组织下所有项目访问。
- 禁用用户会阻断其所有组织和项目访问。
- 角色变更、成员变更、组织状态变更必须写审计。

## 六、统一响应结构

成功响应:

```json
{
  "result": 200,
  "message": "OK",
  "data": {},
  "errors": []
}
```

分页响应:

```json
{
  "result": 200,
  "message": "OK",
  "data": [],
  "currentPage": 1,
  "pageSize": 20,
  "totalPage": 0,
  "total": 0,
  "errors": []
}
```

错误响应:

```json
{
  "result": 403,
  "message": "Permission denied",
  "data": null,
  "errors": [
    {
      "errorCode": "AUTH_FORBIDDEN",
      "errorMessage": "Current user cannot access this project.",
      "referenceData": "requestId=..."
    }
  ]
}
```

Pydantic 模型建议:

```python
class ErrorItem(BaseModel):
    errorCode: str
    errorMessage: str
    referenceData: str | None = None


class CommonResult[T](BaseModel):
    result: int = 200
    message: str = "OK"
    data: T | None = None
    errors: list[ErrorItem] = Field(default_factory=list)


class PageResult[T](BaseModel):
    result: int = 200
    message: str = "OK"
    data: list[T] = Field(default_factory=list)
    currentPage: int = 1
    pageSize: int = 20
    totalPage: int = 0
    total: int = 0
    errors: list[ErrorItem] = Field(default_factory=list)
```

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

第一阶段必须实现:

| 中间件 / 依赖 | 职责 |
|---|---|
| RequestIdMiddleware | 生成或透传 request_id |
| AccessLogMiddleware | 记录 method、path、status、duration |
| ErrorHandlerMiddleware | 把异常转换为统一错误响应 |
| IdentityDependency | 解析 token、org、project、user |
| PermissionDependency | 检查角色和项目权限 |
| AuditMiddleware / AuditService | 记录写操作审计 |

第二阶段再考虑:

- RateLimitMiddleware。
- BodySizeLimitMiddleware。
- CORS 白名单。
- API key for external systems。
- SSE / WebSocket 连接管理。

## 十、错误码规范

错误码必须机器可读，不能只靠 message。

基础分类:

```text
VALIDATION_*
AUTH_*
PERMISSION_*
ORG_*
USER_*
PROJECT_*
INDEX_*
JOB_*
MEMORY_*
AGENT_*
REPORT_*
INTEGRATION_*
CONCURRENCY_*
INTERNAL_*
```

示例:

```text
VALIDATION_INVALID_FIELD
AUTH_TOKEN_MISSING
AUTH_TOKEN_INVALID
AUTH_LOGIN_FAILED
AUTH_REFRESH_TOKEN_INVALID
PERMISSION_PROJECT_FORBIDDEN
ORG_NOT_FOUND
ORG_ALREADY_EXISTS
USER_NOT_FOUND
USER_ALREADY_EXISTS
PROJECT_NOT_FOUND
PROJECT_ALREADY_REGISTERED
INDEX_JOB_ALREADY_RUNNING
JOB_NOT_FOUND
CONCURRENCY_VERSION_CONFLICT
INTEGRATION_CODEGRAPH_TIMEOUT
INTERNAL_UNEXPECTED_ERROR
```

异常分层:

```text
DomainError       可预期业务错误
ValidationError   入参错误
PermissionError   权限错误
IntegrationError  外部或内部模块调用错误
ConcurrencyError  锁、版本、幂等冲突
```

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

第一阶段可采用:

```text
SQLite job table
asyncio task runner
in-process project lock registry
```

第二阶段可升级:

```text
Redis lock
Redis Queue / Dramatiq / Celery
Postgres job table
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

### Phase 1: Web Backend 骨架

Estimate: 1 天。

任务:

- 新增 `codev_platform/web` 包。
- 新增 `app.py`、`main.py`、`config.py`、`openapi.py`。
- 新增 `schemas/common.py`。
- 新增 `middleware/request_id.py`、`middleware/error_handler.py`。
- 新增 `routes/health.py`。
- 接入 `/api/docs`、`/api/redoc`、`/api/openapi.json`。

Gate:

- `GET /api/v1/health/check` 可用。
- OpenAPI JSON 可生成。
- `pytest tests/web/test_health_routes.py` 通过。

### Phase 2: 统一拦截和错误

Estimate: 1 天。

任务:

- 接入 request_id。
- 接入 access log。
- 接入统一异常处理。
- 定义 `ErrorItem`、`DomainError`、错误码。
- 处理 Pydantic validation error。

Gate:

- 404、422、业务错误都返回统一结构。
- 错误响应包含 request_id。
- 不暴露内部 traceback。

### Phase 3: Identity 和权限边界

Estimate: 1-2 天。

任务:

- 新增 `Identity` domain model。
- 新增 `IdentityDependency`。
- 支持开发模式 header identity。
- 支持 token 模式 identity。
- 新增项目级权限依赖。
- 补 ACL 测试。

Gate:

- 未授权请求不能访问项目接口。
- token 模式不能 fallback 到隐式 cwd 项目。
- project_id / org_id / user_id 边界测试通过。

### Phase 4: Auth / Orgs / Users 管理

Estimate: 2-3 天。

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

- Web Backend 是否和现有 Agent FastAPI 合并进程，还是独立进程。
- Job runner 第一版使用 in-process，还是直接引入独立 Worker。
- 存储第一版继续 SQLite，还是引入 Postgres。
- 前端 API client 使用 openapi-typescript、orval 还是 swagger-typescript-api。
- 桌面 widget 后续是调用 Web Backend，还是只保留本地启动器能力。
