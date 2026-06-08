# codev-platform 后端 Bug / Edge Bug 审计报告

日期：2026-06-05  
范围：本地 Python 后端代码 + MCP/Codegraph 上下文；前端 UI 不作为重点。  
编码：UTF-8。

## 结论

本轮重点不是“扫了多少文件”，而是找可落地问题。当前确认 7 个问题，其中 4 个属于对象级访问控制或跨项目隔离风险，2 个属于边界异常路径，1 个属于运行态一致性缺口。

## P1：Agent 会话消息可跨项目读取

位置：
- `codev_platform/web/routes/agent.py:92-99`
- `codev_platform/agent/routes/sessions.py:60-67`
- `codev_platform/agent/session.py:96-97`
- `codev_platform/agent/session_pg.py:127-135`

证据：
- web 的 `/api/v1/agent/sessions` 会把鉴权后的 `project_id` 传给 agent list。
- web 的 `/api/v1/agent/sessions/messages` 丢弃 `_project_id`，只传 `session_id`。
- agent 的 `/sessions/messages` 只按 `org_id + user_id + session_id` 读消息，不校验会话所属 `project_id`。
- PG 版同样只查 `agent_messages`，没有 join `agent_sessions.project_id`。

复现摘要：

```text
list_proj_a= ['<proj-a-session-id>']
messages_by_bare_id= ['secret from proj-b']
```

影响：
同一 user/org 下，只要知道或保留了另一个项目的 `session_id`，就能在当前项目上下文读取另一个项目的历史对话。

建议：
- web 层把鉴权后的 `project_id` 传给 `session_messages`。
- agent route 增加 `project_id` query。
- session store 的 `get()` 增加 project 校验；PG 版 join `agent_sessions` 并加 `s.project_id = %s`。
- 增加测试：`list_sessions(project_a)` 不包含 project_b，且 `messages(project_b_session, header project_a)` 必须 403/空。

## P1：Web Job detail/cancel 可跨项目访问和取消

位置：
- `codev_platform/web/routes/jobs.py:40-61`
- `codev_platform/web/services/job_service.py:83-103`

证据：
- `list_jobs()` 按 `project_id` 过滤。
- `get_job_detail()` 和 `cancel_job()` 只依赖 `require_project_access` 验证 header 项目可访问，但不校验 `job.project_id == header project_id`。
- `JobService.get_detail(job_id)` 和 `cancel(job_id)` 都只有裸 `job_id` 参数。

复现摘要：

```text
list_proj_a= ['proj-a']
detail_by_bare_id= proj-b
cancel_by_bare_id= proj-b
```

影响：
用户对 project A 有权限时，只要知道 project B 的 jobId，就能查看甚至取消 project B 的任务。

建议：
- `get_detail(job_id, project_id=...)` 查询后强制比对 `job.project_id`。
- `cancel(job_id, project_id=...)` 同样校验。
- 错误不要暴露“job 存在但不是本项目”，统一返回 not found/access denied。
- 增加跨项目 detail/cancel 回归测试。

## P1：Web Memory 代理允许 header project 与 scopeRef project 错位

位置：
- `codev_platform/web/routes/memory.py:39-56`
- `codev_platform/web/routes/memory.py:67-82`
- `codev_platform/core/acl.py:23-31`
- `codev_platform/gateway/auth.py:77-90`

证据：
- web memory route 只用 `require_project_access` 校验 `X-Project-Id`。
- 对 `scope == "project"` 时，`scopeRef` 来自 body/query，未强制等于已鉴权的 `project_id`。
- web 将身份签成 `X-Identity`，agent 侧验签后 `via="internal"`。
- `core.acl.can_access()` 对 `via="internal"` 直接 allow，不再复核 scopeRef 项目。

影响：
如果用户对 project A 有权限，但传 `scope=project&scopeRef=project-b`，web 会以“已鉴权身份”代理给 agent；agent 因 internal identity 全信任，可能读/写 project B memory。

建议：
- web 层：`scope == "project"` 时强制 `scopeRef == project_id`，或直接忽略客户端 scopeRef 使用 header project。
- agent 层：`via="internal"` 不应全局放行任意 project_id，至少应继续检查 `identity.projects/all_projects`；或者 X-Identity claims 增加 `authorized_project_id` 并按它校验。
- 增加测试：header project A + scopeRef project B 必须 403。

## P2：Web session 身份与 `require_project_access` 模型不一致

位置：
- `codev_platform/web/security/session_authenticator.py:38-44`
- `codev_platform/core/httpkit/permissions.py:27-46`
- `codev_platform/core/acl.py:32-50`

证据：
- `SessionAwareAuthenticator` 对 web 登录态返回 `Identity(..., via="session")`。
- 注释说“逐项目授权由 current_session + service 闸判”。
- 但 graph/jobs/indexes/reports/agent/memory 等路由实际依赖 `require_project_access()`。
- `require_project_access()` 直接调用 `acl.can_access()`；token 模式下 `can_access()` 只接受 `via="token"`，`via="session"` 会被拒。

影响：
生产 token 模式下，web 登录 session 可能通过中间件，但访问这些项目路由时被 `require_project_access` 拒绝，导致控制台项目功能不可用或只能靠 gateway 静态 token 调用。

建议：
- web 项目路由统一使用 web session/RBAC 项目授权依赖，或让 `require_project_access` 支持 `via="session"` 并调用 web membership。
- 增加测试：token 模式 + web session token + 有项目角色，访问 `/api/v1/jobs/list`、`/api/v1/agent/chat` 应成功；无角色应拒绝。

## P2：Reindex spool 中未知 kind 会让 worker 崩溃

位置：
- `codev_platform/reindex/queue.py:100-119`
- `codev_platform/reindex/worker.py:76-82`

证据：
- `pending()` 把任意 `<pid>__<kind>` 文件转为 `Job`，不验证 kind。
- worker 遇到未知 kind 设计上要“丢弃”。
- 但 `complete(job)` 内部会调用 `_path()`，后者又校验 kind 必须在 runner 白名单里，导致丢弃路径抛 `ValueError`。

复现摘要：

```text
[2026-06-05 11:28:51] 未知 kind 'oldkind' (demo-proj__oldkind) — 丢弃
ValueError: 未知 reindex kind: 'oldkind' (允许: ['chroma', 'codegraph', 'ingest'])
```

影响：
升级后旧 spool 文件、手工坏文件、半写入标记都可能让 worker drain 中断。

建议：
- `pending()` 阶段过滤/隔离非法 kind。
- 或 `complete()` 增加 raw path 删除路径，不再对已从 spool 读出的 job 重做白名单校验。
- 增加测试：`pid__oldkind` 应被移除且 worker 不抛异常。

## P2：Codegraph SSE 正常结束缺少 `Response()`

位置：
- `codev_platform/codegraph/server.py:353-386`
- 对照：`codev_platform/graph/mcp_server.py:247-279`
- 对照：`codev_platform/chroma/server.py:249-305`
- 对照：`codev_platform/agent/memory_mcp.py:378-416`

证据：
- codegraph `handle_sse()` 在 `connect_sse()` 正常结束后没有返回 Response。
- graph/chroma/memory MCP 三个同类 SSE handler 都显式 `return Response()`。
- graph 代码里还写了注释：SDK 的 SSE helper 结束后需要 Response，否则 Starlette 可能出现 `await None(...)` 类噪声。

影响：
SSE 客户端断开或正常结束时，codegraph 服务可能出现异常日志/500 噪声，影响连接稳定性和排障。

建议：
- 在 `finally` 之后补 `return Response()`。
- 加一致性测试：四个 SSE handler 结束路径都返回 Response。

## P2：Web IndexService 允许 kind 与 reindex runner 注册集合不一致

位置：
- `codev_platform/web/services/index_service.py:17-19`
- `codev_platform/web/services/index_service.py:61-67`

证据：
- 注释说 `_ALLOWED_KINDS` 对齐 `reindex.runners.kinds() + all`。
- 实际只允许 `("all", "chroma", "codegraph")`。
- reindex runner 当前注册集合包含 `ingest`。
- `all` 展开时会 enqueue 所有 runner kind，包括 `ingest`，但 API 不能单独请求 `indexKind=ingest`。

影响：
API 合约与后端 runner 真值源不一致；当需要单独重建统一图谱 ingest 阶段时只能触发 all，增加不必要的重建和锁冲突。

建议：
- `_ALLOWED_KINDS` 动态读取 `reindex.runners.kinds()`，再加 `all`。
- 或明确将 `ingest` 设为内部 runner 并改注释/文档/测试。

## P3：Project loaded 状态是进程内集合，重启/多 worker 不一致

位置：
- `codev_platform/web/repositories/project_write_repo.py:18-19`
- `codev_platform/web/repositories/project_write_repo.py:64-75`

证据：
- `_LOADED: set[str] = set()` 是模块级内存状态。
- 文件注释也写了 TODO：PG 后续持久化。

影响：
进程重启后 loaded 状态丢失；多 worker/多实例下各进程看到的 loaded 状态不同。

建议：
- 如果 loaded 是生产控制面状态，应落 PG/runtime 表。
- 如果只是单机 UI 标记，需要在接口/文档中明确“不持久、不跨实例”。

## 测试现状

已执行过：
- `python -m compileall codev_platform`：通过。
- `python -m pytest -q`：8 个失败，均由缺少可选依赖触发（`sentence-transformers`、`rank_bm25`、`jieba`、`torch`）。
- 排除上述可选依赖相关测试后：`1102 passed, 5 skipped, 41 deselected, 1 warning`。
- `pip check`：通过。
- `ruff check codev_platform`：5 个静态问题，主要为 unused import/variable 和 `zip(strict=...)`。

本报告的高优先级问题主要是当前测试没有覆盖的“对象级 ID + 项目上下文错位”路径。

## 修复状态(2026-06-08)

本报告 7 个核心问题, 已核实 + 修复 6 个(每条先读代码核实再改, 附回归测试):

| 问题 | 状态 | commit |
|---|---|---|
| P1 Agent 会话消息跨项目读 | ✅ 修(get 加 project_id 校验, web/agent route + in-memory/PG store 四层) | `111fac9` |
| P1 Job detail/cancel 跨项目 | ✅ 修(get_detail/cancel 加 project_id, 跨项目当 not found) | `3649543` |
| P1 Memory scopeRef 错位 | ✅ 修(project scope 强制用鉴权 project_id, 忽略 client scopeRef) | `a300e3b` |
| P2 Reindex 未知 kind 崩 drain | ✅ 修(complete 不重做 runner 白名单校验, 仍挡路径穿越) | `add339c` |
| P2 Codegraph SSE 缺 Response | ✅ 修(import Response + finally 后 return) | `10d2b61` |
| P2 IndexService kind 不一致 | ✅ 修(_allowed_kinds 动态读 runners.kinds()+all) | `d1c0d75` |
| P2 session vs require_project_access | ⏳ 待(架构层: via=session 需 web membership RBAC; **只 prod token 模式触发**, dev passthrough 不触发; 专门一轮) | — |
| P3 Project loaded 进程内状态 | ⏳ 待(同 web 控制面 PG 化决策, 见 full-local-mcp 报告) | — |

3 个 P1 跨项目隔离漏洞全修 + 回归测试; 全套 `1154 passed, 0 failed`。
