# B1 实施方案 — web-backend Phase 7:agent / memory / audit 三路由

> 属 `next-plan-2026-06-03.md` Track B / B1。本文定"web 对外这三路由各暴露什么 + 怎么落"。
> **核心结论:优化复用现有 agent 子系统,不重建。**

---

## 一、现状调研结论(决定"替换 or 优化")

| 已有资产 | 状态 | 结论 |
|---|---|---|
| `codev_platform/agent/`(~2960 行):ChatService / AgentLoop / memory_store(PG)/ recall / tools / providers | 成熟,独立进程 **codev-agent :8848(绑 127.0.0.1)** | **复用,不重写** |
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
    base_url = config.agent.base_url 或 http://127.0.0.1:8848
    def chat(payload, identity) -> dict        # POST /chat
    def memory_write(payload, identity) -> dict # POST /memory
    def memory_list(params, identity) -> dict   # GET  /memory
```
- **身份透传 + 服务间信任(关键设计点)**:web 已鉴权,把 identity 转成 header(`X-User-Id`/`X-Org-Id`/`X-Project-Id`)发给 agent。agent 默认信任 `request.state.identity`(gateway 写),或裸 header(dev)。**生产需一个"web→agent 信任"机制**:
  - 方案(推荐):**共享服务密钥** —— web 带 `X-Internal-Token`(config.agent.internal_token),agent 校验通过则信任转发的 identity。codev-agent 仅绑 127.0.0.1,单机风险低。
- **超时/降级**:client 设 timeout,异常 → `PlatformError(UPSTREAM_UNAVAILABLE)`(对齐 codegraph_client 范式)。
- 复用 `core/httpkit` envelope 解包(agent 若仍裸 FastAPI,client 做响应适配;见 §八)。

---

## 五、实施步骤(分小步,每步可验证)

1. **agent_client + 信任机制**:`web/integrations/agent_client.py` + config `agent.base_url`/`agent.internal_token`;agent 侧加内部 token 校验(信任 web 转发的 identity)。验证:单测 mock + 本地 curl web→agent 通。
2. **agent 路由**:`web/schemas/agent.py` + `web/routes/agent.py`(chat),挂进 `web/app.py`。验证:登录后 `POST /api/v1/agent/chat` 返回对话结果(集成测试 + 实测 18088)。
3. **memory 路由**:`web/schemas/memory.py` + `web/routes/memory.py`(write/list)。验证:跨 org/project 越权被拒(集成测试)。
4. **audit 路由**:`web/repos/audit_read.py` + `web/schemas/audit.py` + `web/routes/audit.py`。验证:org_admin 只看本 org,普通用户 403。
5. **契约**:`operation_id` 唯一(并入 B3 OpenAPI 快照测试)。

---

## 六、测试

- 单测:agent_client(mock agent)、audit_read(临时 jsonl filter/分页)。
- 集成:`test_web_agent.py` / `test_web_memory.py` / `test_web_audit.py`(passthrough cfg,验路由 + 鉴权闸 + 降级 503)。
- 实测:18088 登录 → chat/memory/audit 走通;agent 停掉验降级。
- 基线不降(≥ 当前 passed)。

---

## 七、风险 / 红线

- ❌ **不重写** ChatService / AgentLoop / memory store / scope 决策 —— 一律复用 agent。
- ❌ **不在 web 复制 scope ACL** —— 决策留 agent(core.acl/core.rbac 单一真值源)。
- ⚠️ **服务间信任必须做**(internal_token),否则 web→agent 转发 identity 不可信 = 越权漏洞。
- ⚠️ agent 仅 127.0.0.1 暴露,不可对外开 8848。
- ⚠️ memory forget/supersede 本期不在 web 暴露(等 agent 先补端点),避免 web 直写绕过 agent 记忆服务。

---

## 八、可选优化(非阻塞,建议排 B 收尾)

agent `service.py` 现用裸 `FastAPI()`,缺 httpkit 的统一 envelope + RequestId + 异常处理器。**建议迁到 `core/httpkit.build_app`**,与 web 一致 → agent_client 解包逻辑归一(不用做响应适配),错误码契约统一。属架构对齐,非 B1 阻塞项。
