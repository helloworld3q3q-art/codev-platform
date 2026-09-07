# B1 实施方案 — web-backend Phase 7:agent / memory / audit 三路由

> 属 `next-plan-2026-06-03.md` Track B / B1。本文定"web 对外这三路由各暴露什么 + 怎么落"。
> **核心结论:优化复用现有 agent 子系统,不重建。**

---

## 一、现状调研结论(决定"替换 or 优化")

| 已有资产 | 状态 | 结论 |
|---|---|---|
| `codev_platform/agent/`(~2960 行):ChatService / AgentLoop / memory_store(PG)/ recall / tools / providers | 成熟,独立进程 **codev-agent :8848(绑 internal.example.invalid)** | **复用,不重写** |
| agent 现有路由:`POST /chat`、`POST/GET /memory` + 完整 scope ACL | 已实现,鉴权走 `core.acl` + `core.rbac`(与 web **同一真值源**) | **复用** |
| `core/acl.py` + `core/rbac.py` | 3 MCP + agent + memory + **web 已共用** | 零重复风险 |
| web app(:18088):auth/orgs/users/projects/graph/jobs/reports… | 走 `core/httpkit.build_app`(统一 envelope+RequestId);**无 agent/memory/chat 路由**,`integrations/` 无 agent_client | 需新增**薄路由 + 代理 client** |
| `core/audit.py:audit_access` | 只写 jsonl(`data_root/audit/access.jsonl`),**无查询函数** | audit 读侧**新建**(小) |

**判定**:
- **chat / memory → 优化复用**:web 当"鉴权前门",经新 `agent_client` 代理到 codev-agent,**不把 LLM 重依赖、记忆存储逻辑搬进 web**。契合 `agent-memory-platform-plan` 的四层架构(Web UI = 记忆服务层**消费方**)。
- **audit → 新建只读**:无现成查询,新写一个读 repo。不替换任何东西。

---

## 二、为什么"代理"而非"同进程挂载"

| 维度 | 代理(agent_client)✅推荐 | 同进程 include router |
|---|---|---|
| 依赖面 | web 保持轻(不引 LLM/anthropic) | chat 把 `[agent]` 重依赖拉进 web 进程 |
| 部署 | 顺现状(两 unit 已分离,codev-agent:8848) | 要合并进程或双挂 |
| 身份体系 | web 鉴权后转发 identity,agent 复核 | agent 的 Membership 来自 memory.pg_dsn,与 web account PG 要打通 |
| 隔离/降级 | agent 挂了 → web 返 503,不拖垮 | 同进程崩一起崩 |

代价:新写 `agent_client` + 解决 **服务间身份信任**(见 §五)。可接受。

---

## 三、三路由详细设计

### 3.1 `web/routes/agent.py` — Agent 对话(代理)

| 端点 | 暴露 | 代理到 |
|---|---|---|
| `POST /api/v1/agent/chat` | question, session_id?, max_steps? → answer, steps, usage, stop_reason, session_id | codev-agent `POST /chat` |
| `GET /api/v1/agent/providers`(可选) | 可用 LLM provider 列表 | codev-agent `GET /providers` |

- **鉴权**:`require_project_access`(强制 project_id);identity 由 web 的 `SessionAwareAuthenticator` 产出。
- **schema**:web/schemas/agent.py 对齐 agent `ChatRequest/ChatResponse`(字段照搬,camelCase 对齐 web 约定)。
- **降级**:agent 不可达 → `upstream_unavailable(503)` envelope,不 500。

### 3.2 `web/routes/memory.py` — 分层记忆(代理)

| 端点 | 暴露 | 代理到 |
|---|---|---|
| `POST /api/v1/memory` | 写:scope(org/team/project/personal), scope_ref, content, kind?, topic_key?, ttl? | codev-agent `POST /memory` |
| `GET /api/v1/memory` | 列:scope, scope_ref, limit | codev-agent `GET /memory` |
| `POST /api/v1/memory/forget`(P2) | 遗忘单条 | 待 agent 补端点(plan 已规划) |
| `POST /api/v1/memory/supersede`(P2) | 覆盖留痕 | 待 agent 补端点 |

- **鉴权**:scope 决策由 agent 侧 `_scope_decision`(core.acl + core.rbac)做(单一真值源,web 不复制);web 只保证 identity 可信透传。personal scope_ref 强制=写入者(agent 已做)。
- **forget/supersede/archive**:agent 当前只有 write/list → P2 时先在 agent 补这些端点,web 再代理(保持"agent 是记忆服务唯一实现")。本期 B1 先上 write + list。

### 3.3 `web/routes/audit.py` — 审计查询(新建只读)

| 端点 | 暴露 | 实现 |
|---|---|---|
| `POST /api/v1/audit/list` | 查访问审计:过滤 service / user_id / project_id / allowed / 时间范围 + 分页 | **新建** `web/repos/audit_read.py`:读 `data_root/audit/access.jsonl`,tail+filter(行数上限防爆) |
| `GET /api/v1/audit/stats`(可选) | 近 N 天 deny 次数 / 按 service 聚合 | 同 repo 聚合 |

- **鉴权**:`require_org_role("admin")` 查本 org;`platform_admin` 跨 org。**普通用户禁查**。
- **数据源**:audit jsonl 在 `data_root`,web 可直接读(文件,不经 agent)。第一版读 jsonl;量大再落 PG。
- **不动写侧**:`audit_access` 横切函数保持不变,chat/memory(agent 侧)已在调它记账。

---

## 四、共用基建:`web/integrations/agent_client.py`

```
class AgentClient:
    base_url = config.agent.base_url 或 https://example.invalid/reference
    def chat(payload, identity) -> dict        # POST /chat
    def memory_write(payload, identity) -> dict # POST /memory
    def memory_list(params, identity) -> dict   # GET  /memory
```
- **身份透传 + 服务间信任(关键设计点)**:web 已鉴权,把 identity 转成 header(`X-User-Id`/`X-Org-Id`/`X-Project-Id`)发给 agent。agent 默认信任 `request.state.identity`(gateway 写),或裸 header(dev)。**生产需一个"web→agent 信任"机制**:
  - 方案(推荐):**共享服务密钥** —— web 带 `X-Internal-Token`(config.agent.internal_token),agent 校验通过则信任转发的 identity。codev-agent 仅绑 internal.example.invalid,单机风险低。
- **超时/降级**:client 设 timeout,异常 → `PlatformError(UPSTREAM_UNAVAILABLE)`(对齐 codegraph_client 范式)。
- 复用 `core/httpkit` envelope 解包(agent 若仍裸 FastAPI,client 做响应适配;见 §八)。

---

## 四b、身份与权限传播(org / project_id / role)—— 越权红线,必须严守

复用代理最大的坑:**身份被绕过 / project_id 被冒充 / 跨 org 读写**。规则如下,逐条不可破:

### (1) 身份只在 web 解析一次,不信浏览器
- `user_id / org_id` **只来自 web 的已认证 session/token**(`SessionAwareAuthenticator` 解析),**绝不取前端裸 header**。前端无法直接塞 `X-User-Id` 给 web —— web 自己算。
- 前端能传的只有"意图参数"(要访问哪个 project_id、写哪个 scope),**是否允许由 web 判定**。

### (2) project_id:web 先校验"该用户能否访问",再转发
- `require_project_access(project_id)` 在 web 路由层**先闸**:校验 authenticated 用户对该 project 有权(`core.acl.can_access`,看 identity.all_projects/projects/org)。
- 通过后才把 project_id 转发给 agent。**未校验的 project_id 绝不转发**。
- agent 侧 `can_access` **再闸一次**(纵深防御)。

### (3) org_id / scope:取 session 的 org,personal 强制本人
- memory 写 `org/team` scope → 需角色(web `require_org_role` 或 agent rbac `memory_scope_decision`);`org redline` 仅 platform/org admin。
- `personal` scope_ref **强制 = 已认证 user_id**(agent 已做,memory.py:98 不信 client),web 转发前也按 session user 填,不取 body。
- org_id 一律取 session,**不接受 client 声明的 org_id**(防跨 org)。

### (4) audit 读:org_admin 限本 org,platform_admin 跨 org
- `require_org_role("admin")` → 查询**强制注入 session 的 org_id 作过滤**,不让传别的 org;`platform_admin` 才放开跨 org。普通用户 403。

### (5) 服务间信任(web→agent)—— 防冒充,推荐 HMAC 签名身份
裸 `X-User-Id` + 静态 `internal_token` 的弱点:token 一旦泄露,持有者可冒充任意用户/org。强化:
- **web 用共享密钥对 identity 签名**:`X-Identity = base64({user_id,org_id,projects,exp})` + `X-Identity-Sig = HMAC(密钥, 上述)`。agent 验签通过才采信 —— **身份与密钥绑定,不可伪造单个字段**。
- 配合:codev-agent **仅绑 internal.example.invalid:8848 不对外**;密钥 `agent.internal_secret` 走 config/env 不进 git。
- agent 侧加身份解析分支:`验签通过 → 用签名里的 identity`(优先于裸 header)。
- (长期更干净:web 直接转发用户 session token,agent 用同一套校验自取 identity —— 待两边 store 收敛后做。)

### (6) 纵深防御铁律
web 闸**不替代** agent 闸:agent 的 `can_access` / `_scope_decision` / `audit_access` **照常全跑**。web 闸是"提前拒 + 注入可信 identity",agent 闸是"最终真值"。两层都在。

---

## 五、实施步骤(分小步,每步可验证)

1. **agent_client + 签名身份信任**(§四b-5):`web/integrations/agent_client.py`(转发 HMAC 签名的 identity)+ config `agent.base_url`/`agent.internal_secret`;agent 侧加"验签通过则采信签名 identity"分支。验证:单测 mock + 本地 curl;**伪造/无签名请求被 agent 拒**。
2. **agent 路由**:`web/schemas/agent.py` + `web/routes/agent.py`(chat),挂进 `web/app.py`。验证:登录后 `POST /api/v1/agent/chat` 返回对话结果(集成测试 + 实测 18088)。
3. **memory 路由**:`web/schemas/memory.py` + `web/routes/memory.py`(write/list)。验证:跨 org/project 越权被拒(集成测试)。
4. **audit 路由**:`web/repos/audit_read.py` + `web/schemas/audit.py` + `web/routes/audit.py`。验证:org_admin 只看本 org,普通用户 403。
5. **契约**:`operation_id` 唯一(并入 B3 OpenAPI 快照测试)。

---

## 六、测试

- 单测:agent_client(mock agent)、audit_read(临时 jsonl filter/分页)。
- 集成:`test_web_agent.py` / `test_web_memory.py` / `test_web_audit.py`(passthrough cfg,验路由 + 鉴权闸 + 降级 503)。
- **越权专项(必加)**:他 org 用户查本 project chat/memory → 拒;client 篡改 org_id/project_id/personal scope_ref → 不生效(以 session 为准);无签名/伪造签名 web→agent → agent 拒;普通用户查 audit → 403;org_admin 查他 org audit → 空/拒。
- 实测:18088 登录 → chat/memory/audit 走通;agent 停掉验降级。
- 基线不降(≥ 当前 passed)。

---

## 七、风险 / 红线

- ❌ **不重写** ChatService / AgentLoop / memory store / scope 决策 —— 一律复用 agent。
- ❌ **不在 web 复制 scope ACL** —— 决策留 agent(core.acl/core.rbac 单一真值源)。
- ⚠️ **服务间信任必须做**(internal_token),否则 web→agent 转发 identity 不可信 = 越权漏洞。
- ⚠️ agent 仅 internal.example.invalid 暴露,不可对外开 8848。
- ⚠️ memory forget/supersede 本期不在 web 暴露(等 agent 先补端点),避免 web 直写绕过 agent 记忆服务。

---

## 八、前端页面方案(web-ui,3 页)

> 依赖:后端三路由上线后跑一次 `pnpm --dir web-ui run api` 生成 `typings.d.ts` + service,再接前端
> (禁假数据/硬编码,见 `frontend-backend-handoff.md`)。统一遵循 web-ui 规约:**禁 useRequest**(useState+useCallback+useEffect)、JSX 禁内联函数、antd6、`@/components/Drawer|Modal|ResizableTable`、`fetch.ts` 自动注入 `X-Project-Id/X-Org-Id`。

### 路由 + 菜单(`web-ui/config/routes.ts` + `src/menus.tsx`)
```
{ name:'AI 助手', path:'/agent',  icon:'RobotOutlined',     component:'./agent' }
{ name:'记忆库',  path:'/memory', icon:'BulbOutlined',      component:'./memory' }
{ name:'审计日志', path:'/audit',  icon:'AuditOutlined',     component:'./system/audit' }  // 归"系统管理"组
```

### 9.1 `pages/agent/` — AI 对话页(会话式,非表格)
- 布局:左会话列表(可选,v1 可省)+ 右消息流(user/assistant 气泡)+ 底部输入框 + 发送。
- 状态:`messages[]` / `loading` / `sessionId`(useState);发送 → `fetchAgentChat({question, sessionId, maxSteps})` → 追加 assistant 回复 + 存 sessionId 复用。
- 展示:回复正文 + 可折叠"步骤/工具调用 trace"(steps)+ token usage 小字;切项目(currentProjectId)→ 清会话重开。
- 组件:`index.tsx`(编排)/ `components/MessageList.tsx` / `components/Composer.tsx` / `services.ts` / `types.ts`。

### 9.2 `pages/memory/` — 分层记忆页(表格 + 写入)
- 顶部 scope 切换(org/team/project/personal,Segmented)+ `ResizableTable` 列(content/kind/scope_ref/status/updatedAt/操作)。
- 操作列:遗忘/覆盖(P2,等 agent 端点);新增 → `@/components/Drawer` 表单(scope/content/kind/topic_key/ttl)。
- 数据:`fetchMemoryList({scope, scopeRef, limit})`;写 `postMemory(...)`。**scope=org/team 的写入按钮按角色禁用**(useModel('user') 看角色;后端仍兜底)。
- 红线:personal scope_ref 不让用户填(默认本人,后端强制)。

### 9.3 `pages/system/audit/` — 审计日志页(只读表格,管理员)
- `ResizableTable` + 搜索项(service/user/project/allowed/时间范围)→ `POST /api/v1/audit/list` 分页。
- 列:时间 / service / 用户 / org / project / allowed(Tag 红绿)/ reason / via。
- **入口按角色显隐**:仅 org_admin / platform_admin 可见菜单(useModel('user') 判);后端 `require_org_role` 兜底。org_admin 只看本 org(后端注入,前端不传 org)。

### 前端联调红线
- 三页都**等后端 + `pnpm run api`** 再接,不用假数据 / 不硬编码 `Record<string,string>` 业务枚举(scope/kind 若是后端枚举,走 `useModel('enum')`)。
- 后端 commit 与前端 commit 拆开;后端改 DTO → 通知用户跑 `pnpm run api`/`enums`。

---

## 九、可选优化(非阻塞,建议排 B 收尾)

agent `service.py` 现用裸 `FastAPI()`,缺 httpkit 的统一 envelope + RequestId + 异常处理器。**建议迁到 `core/httpkit.build_app`**,与 web 一致 → agent_client 解包逻辑归一(不用做响应适配),错误码契约统一。属架构对齐,非 B1 阻塞项。
