# Plan — web Agent 对话:会话管理 + @ant-design/x 重写(2026-06-04)

> 归属迭代:`roadmap-2026-06-03`(B web-backend 收尾 / B1 agent 面延伸)。
> 设计红线:**严格分层 · 低耦合高内聚 · 轻量聚合(route 只编排,逻辑下沉)· 可扩展(接口而非堆砌)**。
> 关联:`web/routes/agent.py`(现仅 `agentChat` 代理)、`agent/routes/chat.py`、`agent/session_pg.py`(`SqlSessionStore`)、`web/integrations/agent_client.py`。

---

## 一、目标

1. **后端贯通"列会话 / 取会话消息"接口** —— 会话数据已存服务端(`agent_sessions` / `agent_messages` 表),缺读取面。
2. **前端用 `@ant-design/x`** (`Bubble` / `BubbleProps` / `Sender`) + `@ant-design/x-markdown` (`XMarkdown`) 重写对话页,支持 **新建会话 + 历史会话**。

非目标(本轮不做,留扩展位):流式 `useXChat`、消息重生成、会话重命名/删除(范式预留,机械可加)。

---

## 二、现状

```
前端 pages/agent → web /api/v1/agent/chat (鉴权前门) → AgentClient 签 X-Identity 代理
   → codev-agent 服务 :8848
        ├ routes/chat.py     POST /chat        (已通)
        ├ routes/memory.py   POST/GET /memory
        ├ routes/meta.py     GET /health /providers
        └ session_pg.py      SqlSessionStore
             agent_sessions (org_id, user_id, session_id, created_at, updated_at)
             agent_messages (org_id, user_id, session_id, role, content, payload, created_at)
             已有: create / exists / list_messages / append_message
             缺:   list_sessions
```

`chat` 端到端通,且**每次对话已服务端持久**(append_message)。新建会话 = `chat` 不传 `sessionId`(已支持)。唯一缺口:**没有"列会话"+ 对外"取会话消息"的 HTTP 面**。

---

## 三、分层设计(每层单一职责,不跨层堆逻辑)

```
┌─ 前端 pages/agent/ (轻量聚合) ───────────────────────────────┐
│  index.tsx        仅编排 + 状态聚合, 不写业务逻辑             │
│  services.ts      适配层: 生成API → 剥 CommonResult → data    │
│  components/SessionSider.tsx  Conversations 侧栏 (自洽组件)    │
│  components/ChatPanel.tsx     Bubble.List + XMarkdown + Sender │
│  types.ts         视图类型 (ChatMessage / SessionMeta)        │
└──────────────────────────────────────────────────────────────┘
        ↓ pnpm run api (生成, 不手写)
┌─ web 鉴权前门 (薄代理) ──────────────────────────────────────┐
│  web/routes/agent.py     require_project_access → AgentClient │
│  web/schemas/agent.py    SessionItem / SessionMessageItem     │
│  web/integrations/agent_client.py  薄传输方法                 │
└──────────────────────────────────────────────────────────────┘
        ↓ X-Identity 签名代理
┌─ codev-agent 服务 :8848 ─────────────────────────────────────┐
│  agent/routes/sessions.py  新路由 (不塞 chat.py): 解身份+调store│
│  agent/schemas/sessions.py SessionOut / MessageOut            │
│  agent/session_pg.py       SessionStore 接口 + 两实现 加方法   │
│    └ list_sessions() / list_messages()  ← SQL 只在这一层      │
└──────────────────────────────────────────────────────────────┘
```

**低耦合**:每层只认下一层接口;session 是**新增模块**,不改 chat 现有代码;web/AgentClient 只加薄方法;SQL 只在 store 层。
**高内聚**:会话相关逻辑集中(store 方法 + sessions 路由 + SessionSider 组件)。
**轻量聚合**:route 只"解身份 + 调 store + 映射 schema";`index.tsx` 只"持状态 + 编排回调"。

---

## 四、逐层改动

### 后端 codev-agent 服务

**① `agent/session_pg.py`** —— `SessionStore` 接口 + `InMemorySessionStore` + `SqlSessionStore` 三处同步加:

```python
def list_sessions(self, org_id, user_id, *, limit=50, offset=0) -> list[SessionMeta]: ...
```

- 新增 `SessionMeta` dataclass `{session_id, title, created_at, updated_at, message_count}` —— **dataclass 不用裸 dict**,以后加字段(pinned / model)零破坏调用方(扩展性)。
- `title` 从首条 user message 派生(SQL 子查询取 `MIN(id)` 的 user content 截断),**不加 schema 列**;以后要持久标题再加列、接口形状不变。
- `list_messages` 已有 → 复用,不重写。
- SQL **只在此层**;路由/服务不碰 SQL。

**② `agent/routes/sessions.py`**(新文件,**不塞进 chat.py** —— 内聚到会话域):

- `GET /sessions` → 解 X-Identity(复用 chat.py 同款身份解析;若 chat.py 内联了就顺手抽公共 `_identity_from_request` 到 `agent/routes/_deps.py`,DRY)→ `store.list_sessions` → `SessionListOut`。
- `GET /sessions/messages?session_id=` → `store.list_messages` → `MessagesOut`。
- 路由**只编排**:解身份 + 调 store + 映射 schema,零 SQL、零业务分支。
- 在 agent app 装配处(chat/memory router 注册旁)注册本 router。

**③ `agent/schemas/sessions.py`**:`SessionOut` / `MessageOut`(与 chat schema 分文件,内聚)。

### 后端 web 前门(薄代理,镜像现有 `agentChat`)

**④ `web/integrations/agent_client.py`** 加 2 个一行方法(复用 `_request`):
```python
def list_sessions(self, ident, params): return self._request("GET", "/sessions", ident, params=params)
def session_messages(self, ident, params): return self._request("GET", "/sessions/messages", ident, params=params)
```

**⑤ `web/routes/agent.py`** 加 2 路由,镜像 `agentChat` 范式:
- `GET /api/v1/agent/sessions`(`operation_id="agentSessions"`)→ `require_project_access` → `AgentClient.list_sessions` → envelope。
- `GET /api/v1/agent/sessions/messages`(`operation_id="agentSessionMessages"`)→ `AgentClient.session_messages` → envelope。

**⑥ `web/schemas/agent.py`** 加 `SessionItem` / `SessionMessageItem`(response_model → openapi → 前端类型)。

### 前端(轻量聚合)

**⑦ `pages/agent/` 重写**:
- `index.tsx`:**只**持 `{ sessions, activeSessionId, messages, loading }` + 编排回调(`onNewSession` / `onSelectSession` / `onSend`),`useState + useCallback + useEffect` 三件套(禁 useRequest)。
- `services.ts`:加 `fetchSessions` / `fetchSessionMessages`(剥 CommonResult,沿现有 `fetchAgentChat` 模式)。
- `components/SessionSider.tsx`:`@ant-design/x` 的 `Conversations`(顶部"新建会话"按钮 + 历史 items);props `{ items, activeKey, onSelect, onNew }` —— 自洽、可复用。
- `components/ChatPanel.tsx`:`Bubble.List`(`BubbleProps[]`;助手气泡 `messageRender={(c) => <XMarkdown>{c}</XMarkdown>}`)+ `Sender`;props `{ messages, loading, onSend }`。
- 删 `MessageList.tsx` / `Composer.tsx`(被 ChatPanel 取代)。
- 规则遵循:不碰 `src/services`(直调生成 API 经 services.ts 剥壳)、antd6、UnoCSS `baseFontSize=4`、组件 PascalCase、页面目录小写。

---

## 五、分阶段执行

| 阶段 | 谁 | 内容 | 验证 |
|---|---|---|---|
| **A 后端全链** | 实施方 | store.list_sessions(含 SessionMeta) → agent `routes/sessions.py` + schemas → AgentClient 2 方法 → web 代理 2 路由 + schemas | agent service session 路由单测(list/messages 形状 + org+user 隔离)+ web 代理测试(鉴权 + envelope)+ 全量不降;重启 agent/web 服务实测 |
| **B 依赖+生成** | 用户 | `pnpm install @ant-design/x @ant-design/x-markdown` + `pnpm run api` | agentapi.ts 含 `getAgentSessions` / `getAgentSessionMessages` |
| **C 前端重写** | 实施方 | SessionSider + ChatPanel + index 重写 | `pnpm run tsc` = 0 / `eslint` = 0;用户 `pnpm dev` 看效果 |

---

## 六、扩展性预留

- `SessionMeta` dataclass → 加 `pinned` / `rename` / `model` 字段零破坏调用方。
- 会话路由按现有 route/proxy 范式 → `DELETE /sessions` / 重命名 机械可加(各层加一薄方法)。
- 前端 `@ant-design/x` 标准件 → 后续接 `useXChat` 流式、消息重生成自然扩展(`Bubble` 支持 `loading` / `typing`)。
- 会话按 **org+user 服务端持久**(跨设备),非 localStorage —— 多端一致。
- `agent/routes/sessions.py` 与 chat/memory 路由并列 → agent 服务"会话域"独立演进,不与 chat 业务耦合。

---

## 七、风险 / 边界

- agent 服务与 web 是**两个进程**,改完都要重启(systemd kill MainPID + Restart=always)。
- `@ant-design/x` 未在 package.json → 阶段 B 装好前,前端 tsc 不过(预期);阶段 A/B 完成后再做 C。
- `title` 派生用子查询,大会话量时注意索引(`agent_messages (org_id, user_id, session_id, id)` 已有联合索引,够用)。
- 身份隔离红线:sessions 接口必须按 X-Identity 的 org+user 过滤,**绝不跨用户列会话**(store 层 WHERE 强制)。
