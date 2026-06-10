# Plan — 多组织 server:IDE agent 身份 / 认证 / 数据模型对齐(2026-06-10)

> **本文件是 plan, 不是实现。** 承 2026-06-10 4 位高级开发面板(安全 · 部署拓扑 · 身份集成 · ROI)
> + 数据模型讨论收敛。**核心节奏:架构定下来不再吵 + validate-first/ROI 分段(现在最小、子系统冻结到真有多 dev)。**

---

## 一、背景 / 目标

让 **claude/codex IDE agent 经 MCP 接入**时,身份/RBAC 用 **web 管理面配的 user/org**(本会话刚做完 web 多组织 RBAC)。
现状:单机 WSL loopback + passthrough,实际**就 1 个 dev、所有项目 `org_id=default`**;认证中间件已建,但 IDE token 的 user/org 来自 **config 手填**(`gateway.tokens`),与 web 管的 PG 用户/成员**脱节**。

**目标**:定下"做好"的目标架构 + 分段交付,让"web 配一次、IDE agent 经 token 认得多组织 RBAC"成立,且不在 0 真实多 dev 时过度建设。

## 二、专家面板收敛

**强共识(4 人一致)**:
- **选 A(IDE 各带 token 直连 MCP-SSE),否决 B(web 前置代理)**:
  - 安全:X-Identity 是 web→agent 进程间短令牌,SSE 长连不可续签;`internal_secret` 泄露 = 全租户沦陷(`acl.py:30` 对 `via=internal` 无条件放行)。
  - 拓扑:四套 MCP **已各自挂 `AuthMiddleware`+`build_authenticator`**(token+ACL 双闸就绪);B 重造轮子 + BaseHTTPMiddleware 破坏 SSE + 单点。
  - ROI:B 是杀鸡用牛刀。
- **认证用 token,授权用共享 PG RBAC**(`fetch_membership` 实时查,不用 token 快照)。

**关键分歧 = 现在建 vs 冻结**:安全/身份 2 人画了完整 PG token 绑定设计;ROI(采纳)指出**0 个第 2 dev/org**,建 token 表/签发/吊销/多 org 一整套 = 过度设计。

## 三、目标架构("做好"的终态,技术 3 人趋同)

| 维度 | 做法 |
|---|---|
| **拓扑** | A:IDE `.mcp.json` `type:sse` + `Authorization: Bearer` 直连 MCP-SSE;多机用**反代终结 TLS**,uvicorn 仍 bind loopback(最干净) |
| **认证** | token(sha256 hash 存、常量时间比对、`token_expired` fail-closed)—— `TokenAuthenticator` 已实现 |
| **授权** | token→user → **实时 `fetch_membership(org,user,project)` 查 PG**(非 token 快照)→ web 改权/禁用下一请求即生效 |
| **token 后端** | **复用 SessionStore 范式**(已 hash+revoke+`revoke_user`)新建 `agent_tokens` 表;web 管理面签发/吊销(类 GitHub PAT,明文只显一次)|
| **一 token 一 org** | 多 org 的 dev 发多个 token、配多个 MCP entry;**不让 client 传活动 org**(撞身份红线)|
| **隔离护栏** | 非 loopback bind 硬拒 passthrough(现在只 WARN);`multi_user_policy_error` fail-fast 已在 |

## 四、数据模型对齐(org / 项目 / token / memory 同一 PG 真值源)

现状三条绑定(`acl.py` 实测):**org=租户根 → 项目挂 org(config `projects.<id>.org_id` 手填)→ token 带 org → 访问闸1 项目org==身份org → memory 按 scope 归属**。
| 绑定 | 现在 | 目标(统一 PG) |
|---|---|---|
| token→org | config `gateway.tokens.<hash>.org_id` | token→user → 实时 `fetch_membership` 得活动 org |
| **项目→org** | **config `projects.<id>.org_id` 手填**(与 PG 两张皮)| **项目登记时挂 PG 的 org**(web 管理面管,meta.json/PG 带 org_id)|
| memory→org | org-scope→org / project-scope→跟项目org / **personal→跟人(跨org)** / team→团队 | 不变(scope 模型已对);org 来自实时身份 |

**核心**:把"项目→org"从 config 手填收进 **PG/web 管理面**,让 token org ↔ 项目 org ↔ memory org **同一真值源**,而非 config vs PG 两张皮。

## 五、分期(validate-first + ROI 红线)

### Phase 1 —— 最小可用(现在做,无新子系统)
让"**第 2 个 dev 用自己身份接入、看不到别人 personal 记忆**"今天成立,不锁死未来:
- **P1.1** 加 `mcp.bind_host` config(默认 loopback,多机才改);四套 `run_http` + `MCPEndpoint` 读它;把**真实 bind host** 传给 `warn_if_insecure`/`deploy_policy_error`(现传死值,护栏对 MCP 漏判)。
- **P1.2** 非 loopback bind + passthrough → **启动硬拒**(现只 WARN)。
- **P1.3** 确认/补 MCP-SSE 入口 token 认证端到端(中间件已挂,验证 token 模式 + 越权 project 403)。
- **P1.4** 写 runbook:"第 2 dev 接入 = `auth_mode=token`+`multi_user=true`+config 填 token hash"(分钟级,零新代码;`multi_user_policy_error` 兜底)。
- 验证:`serve-mcp status` OK + 拿别人 token 访越权项目 403 + personal 按身份隔离。

### Phase 2 —— PG token 绑定(🔒 Gate 触发才做)
**Gate(ROI 红线)**:**≥2 个真实 org,或 ≥3 个需独立动态签发/吊销 token 的 dev。** 未到只立项不开工。
届时(身份 agent 蓝图):`agent_tokens` PG 表(复刻 `PgSessionStore` hash/revoke/`revoke_user`)+ `TokenAuthenticator` 接 PG lookup(`Authenticator` Protocol 不变,config 路径降 dev fallback)+ 实时 `fetch_membership` 组装 `Identity`(30s TTL 缓存)+ web 签发/吊销端点+UI + **项目→org 纳入 PG**(§四)+ 活动 org 经 `X-Active-Org` 经成员校验(非成员 deny)。

## 六、必须先堵的已知洞(记下,别忘)
**config token 与 PG 用户表脱节** → "web 禁用用户但其 config token 仍有效 + 项目越权绕过 PG"。
**单 dev 时无害;上"多 dev + web 管理账号"前必须先做 Phase 2 的 PG token + 实时 RBAC**,否则禁用不生效是真漏洞。

## 七、纪律红线
1. **否决 B**(web 前置代理 MCP);X-Identity 仅留 web→agent 进程内,绝不外延 MCP。
2. **ROI 红线**:≥2 真实 org / ≥3 dev 才建完整 token 子系统;在那之前只 Phase 1(config token + bind 解锁)。
3. **身份红线**:`org_id`/`user_id` 只取认证 token 身份,**绝不由 client 传**;活动 org 经成员校验过滤。
4. **冻结清单**:OAuth/SSO、token 自动轮换、web 前置全代理、多 org claim 状态机 —— 等真实规模触发。
5. 授权用 PG 实时查,不用 token 快照(避免越权窗口);token 后端复用 SessionStore 不另造加密路径。

## 八、工作量
- Phase 1:**0.5–1 天**(bind_host + 护栏硬拒 + token 端到端验证 + runbook;纯小补丁)。
- Phase 2:**3–5 天**(PG token 表 + 认证接 PG + 实时 RBAC + 项目→org 入 PG + web 签发/吊销 UI),**冻结待 Gate**。

## 九、本 plan 不含
实现代码、`agent_tokens` schema 定稿、web 签发端点细节、项目→org 迁移脚本 —— 留 Phase 1 实现轮 / Gate 触发轮。
