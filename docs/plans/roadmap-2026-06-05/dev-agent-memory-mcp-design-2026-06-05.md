# 设计:开发端编程 agent 接平台 Memory(MCP 前门)— 四专家会诊

> **产物性质**: roadmap-2026-06-05 子设计文档。沉淀 2026-06-04 四专家(架构集成 / 身份安全RBAC /
> 记忆质量AI / 开发者体验产品)会诊,各自读真实代码后收敛。
> **一句话**: 平台 memory(org/team/project/personal + RBAC + redline + 冲突消解)目前**只服务
> 平台自己的 web 端 agent**;开发者天天用的 Claude Code / Codex 用各自本地文件记忆(单机单人、不分
> org、不共享)。本设计把这套已验证的 memory 底座**多接一个客户端(IDE 编程 agent via MCP)**。

---

## 一、问题与定位

| | 用哪套 memory | 分 org/项目 | 跨开发者共享 |
|---|---|---|---|
| 平台 web 端 agent | 平台 memory(RBAC+redline)✅ | 是 | 是 |
| **开发端 Claude Code / Codex** | 各自本地文件(MEMORY.md / AGENTS.md) | **否** | **否(单机单人)** |

本轮 plan 把 codev 定位锤为 **"AI-first: 让 agent + 团队在多项目间共享对自有系统的精确理解"**。
**"团队共享"这四个字, memory 跨开发者共享是它最直接的兑现** —— 而最高频的 agent 恰是开发者的
Claude Code / Codex, 它们现在还没接上。这是"建好了没交付到用户手里"的缺口, 不是新特性。

**用户判断(原话)**: "开发端也需要, 而且要求也高"。要求高体现在: 开发端无 web 登录态、多开发者
共用一套 WSL 服务、个人记忆隐私、多工具(Claude Code+Codex+web)一致 —— 身份与隐私门槛高于 web 端。

---

## 二、四专家收敛结论(一表)

| 议题 | 收敛结论 |
|---|---|
| **优先级** | 四专家一致: 开发端 memory 读侧 MVP **应先于 A1**(用已验底座的渠道扩展 > 未验底座的能力扩展);但 **C1(project_id 列化)仍最先**(共同前置)。新序: `C1 → M(读侧MVP) → B1 → A1 → C2/B2/D` |
| **服务形态** | 新建独立 SSE server `agent/memory_mcp.py`(对称 cross-link), 复用 `SqlMemoryStore`+`LocalRecallService`, 经 `mcp_serve` 注册第 4 端点(:18087 local / :19087 platform)。**~3-4 人日**(纯前门, store 全就绪) |
| **工具集** | `recall`(最高频, query-aware 去冲突 top-N)/ `remember` / `list_scope` / `forget` / `supersede`。`set_task_state` 不暴露(web agent 语义) |
| **身份(最难)** | 推荐 **(b) per-dev token + 服务侧 token→org 映射** —— 直接复用现有 `gateway/auth.py:TokenAuthenticator` + `gateway.tokens`(只存 sha256, client 永远拿不到 org_id)。开发端走 token 直认证, 等价 web 认证结果 |
| **红线不破** | `org_id` 绝不由 client 传; token→org 映射在**服务侧 config**。`_resolve_identity` 已实现"有 `request.state.identity` 就不看裸 header" |
| **读写矩阵** | personal(本人)+ project(allowlist 同源)放开; **org/team 默认只读**(IDE token 登记 `viewer`); **redline 写入禁 IDE 端**(仅 web 管理面 / 迁移脚本) |
| **隐私(生死线)** | 默认 **personal**, 显式才 project; personal 对 org **断言级不可见**; 可见性透明 + 可删可改。**默认值设成 project = 一次外泄全队弃用** |
| **与自带 memory 关系** | **分层共存 + 单向桥接(本地→平台)** —— 不替代、不双向。本地 MEMORY.md = 个人草稿/离线 fallback; 平台 = 团队真值层; `migrate_memory_md.py` 是已有的正确单向桥 |
| **召回策略** | MCP 端 agent **主动调 recall(带 query)**; **redline 走会话起手强注入 system**。不复制 web 端空 query 启动注入(`_score` 空 query 退化为纯 recency) |
| **顺序依赖** | **先修写入侧 `topic_key`, 再上 M3 向量召回** —— 否则向量把重复脏条目一起召回放大噪声 |

---

## 三、架构方案(集成专家)

### 3.1 服务形态: 独立 SSE server

不挂进现有 cross-link/codegraph server —— 它们是只读 sqlite/graph、无重依赖; memory 是 **PG 写库 + RBAC**,
职责与依赖面不同。新建 `codev_platform/agent/memory_mcp.py`, 照搬 cross-link `run_http` 骨架
(`SseServerTransport` + `AuthMiddleware` + rate-limit + `/sse` `/messages/` `/healthz` `/platform/status`),
dispatch 薄包装 `@server.call_tool()` + usage jsonl 埋点。**复用 `deps.get_memory_store()` + `LocalRecallService`,
零新建 store**。

### 3.2 工具集(对齐 REST 语义)

| 工具 | 入参 | 后端 |
|---|---|---|
| `recall` | query, limit(≤50), task_id?, policy? | `LocalRecallService.recall()`(已含冲突消解+redline 优先) |
| `remember` | scope, scope_ref?, content, kind?, topic_key?, ttl? | `store.write()` 经 `_scope_decision`; personal 的 scope_ref 强制=session user_id |
| `list_scope` | scope, scope_ref, limit? | `store.list_scope()`(精确列单作用域, 诊断用) |
| `forget` | entry_id | `store.forget()`; **强制 owner 校验** |
| `supersede` | old_id + 新条字段 | `store.supersede()`(偏好演进留痕) |

### 3.3 路由与接线

- **project_id**: 沿用 cross-link contextvar(`?project_id=`)。差异: memory 不拿 pid 选库(同一 PG, 靠 `org_id` 列硬隔离)。
- **org_id/user_id**: SSE 建流时从 `request.state.identity`(AuthMiddleware 注入)取, 绑 contextvar。**唯一非对称于 cross-link 的安全面**(memory 有 personal+owner+redline, cross-link 只读无此维度)。
- **serve-mcp**: `mcp_serve.py` 加 `build_memory_cmd()` + `iter_endpoints()` 追加 `agent-memory` 端点; `_dep_ok`/`_db_present` 加 memory 分支(探 psycopg + pg_dsn); systemd unit 自动生成 `codev-mcp-agent-memory`。
- **.mcp.json**(本仓+业务仓): 加第 4 块 `"agent-memory": {"type":"sse","url":".../sse?project_id=<id>"}`。

---

## 四、身份 / 安全 / 隐私(安全专家)

### 4.1 身份: per-dev token + 服务侧映射(红线不破)

| 环节 | 机制 | 破不了的原因 |
|---|---|---|
| client | `.mcp.json` 只带 `Authorization: Bearer <token>` + `?project_id=` | token 不透明、不含 org_id, client 无字段声明 org |
| 服务侧映射 | `TokenAuthenticator` 遍历 `gateway.tokens`(只存 hash)→ 取**服务侧预登记**的 org_id/user_id/projects | org 由平台管理员写 config 决定, 不读 client 输入 |
| Identity | `Identity(org_id=..., via="token")` 写 `request.state`; `_resolve_identity` 优先取它、忽略裸 header | 杜绝持合法 token 者伪造他人 org/user |

**关键护栏要扩展**: `deploy_policy_error` 当前只对 prod/非 loopback 强制 token; **多开发者共用绑 127.0.0.1
的 WSL 服务时 host 是 loopback、mode 可能仍 dev → passthrough 放行 → `X-User-Id` 可伪造 → personal 串号**。
须加"多 dev"判据(config `multi_user=true` 或检测 >1 登记 token)强制 `auth_mode=token`。

### 4.2 开发端读写矩阵(token 模式)

| scope | 读 | 写 | 判定 |
|---|---|---|---|
| personal | 仅本人 | 仅本人(scope_ref 强制=user_id) | acl/rbac 双路校验 ==self |
| project | allowlist+org | 同读(不叠 RBAC project_role) | `can_access` |
| org | RbacStore org_role≥viewer; 无 store 兜底 deny | **默认禁(IDE token 登记 viewer)** | `memory_scope_decision` |
| team | 成员 role≥viewer | **默认禁** | `memory_scope_decision` |
| redline | 随 org 读 | **禁 IDE 端**(仅 web 管理面) | 需新增独立写闸 |

### 4.3 隐私四道闸(已在代码闭环, 前提是 user_id 可信)

1. 写侧归一: personal 的 scope_ref 强制=写入者 user_id, client 传的被丢弃。
2. 读侧对称: personal 读也要求 scope_ref==self。
3. org 隔离: `list_scope(org_id=)` 带 org 过滤, 跨 org 物理不可见。
4. owner 限定: forget/supersede/set_task_state 只能改本人条目。

---

## 五、记忆质量(AI 专家)

- **写入**: 显式("记住X")优先, 自动仅限任务级(decision/blocker/验收), **默认 personal/project, 永不自动写 org/redline**。
- **召回**: agent 主动 `recall(query)`; redline 会话起手强注入 system(never-cut)。
- **分层共存**: 本地 MEMORY.md(草稿/离线)+ 平台 PG(团队真值)+ `migrate_memory_md.py` 单向上行桥。
- **topic_key 统一**: 三写入端(remember 工具 / web route / 迁移脚本)调同一 slug 生成函数, 否则同偏好被判两条不冲突、both 注入自相矛盾。

---

## 六、产品 / 迁移 / MVP(产品专家)

### 6.1 最高 ROI 场景

① **跨机一致**(personal+project recall)—— 当前真实在痛(MEMORY 里多条"换机器/重启后…"事故)。
② **团队 redline/约定共享**(project 只读)—— `.claude/rules` 2200 行不 autoload, 新人 agent 看不到; project memory = "会自己冒出来的 redline"。
③ 跨开发者踩坑共享(②的外溢)。④ onboarding(②③攒够量后的兑现, 不单独做)。

### 6.2 MVP 边界

**先交: 只读 recall + personal/project 双 scope, 经 MCP SSE。** 读优先于写(纯增益、零风险);
personal+project 一起(跨机+团队两条腿); org/team 缓。写侧留 `remember` 但**默认进 personal**, 经
ownership 提升才升 project(与 A1 纠错回路同源, 可共用)。

### 6.3 迁移

`codev-platform memory import-md <path>` CLI: MEMORY.md + feedback_*.md 拆成 entry, **全落 personal**;
人工提升团队级条目到 project(量小 10 条, 人工比 LLM 自动可靠)。保留 MEMORY.md 作离线 fallback(双写过渡)。

---

## 七、前置必修(安全+质量专家独立抓到的 3 个真缺口)

> 这 3 个不修, MCP 一开放写就放大成事故。属本轨道 P0。

1. **`remember` 工具不写 `topic_key`/`is_redline`**(`tools/remember.py`)→ agent 记忆永走 passthrough、
   永不去重、永不参与冲突消解, 数月后 project scope 被同主题重复条灌满。**最高优先**。
2. **`RememberTool.run` 绕过 `audit_access` + 绕过 `_scope_decision` 鉴权** → dev 写记忆既无授权校验
   也无留痕。修: remember/recall 复用 `_scope_decision`+`audit_access`, "工具写"与"路由写"同一道闸。
3. **`is_redline` 由 client 直接传、无独立写闸** → 持 org member token 可冒造 org 硬约束污染冲突消解。
   修: redline 写入单独要求 `org_role==admin` 或仅 web 管理面。

---

## 八、分阶段交付

| 阶段 | 内容 | 验证 |
|---|---|---|
| **P0 前置修复** | §七 三缺口(topic_key / 审计旁路 / redline 写闸) | 单测: agent 写记忆带 topic_key 参与去重; remember 经 audit; 非 admin 写 redline 被拒 |
| **P1 MCP 读侧 MVP** | `memory_mcp.py` 暴露 `recall`+`list_scope`(只读)+ token 身份 + .mcp.json 接入 | e2e: Claude Code recall 到本人 personal + 本项目 project; 跨机一致 |
| **P2 写侧 + 迁移** | 开放 `remember`(默认 personal)/`forget`/`supersede` + `memory import-md` CLI | personal 默认 + 显式提升; MEMORY.md 导入幂等 |
| **P3 多 dev 护栏** | `multi_user` 强制 token; 隐私可见性 CLI(`memory list --scope`) | 多 dev 共用 WSL 0 串号; personal 对 org 不可见断言 |

> org/team 写、redline IDE 写、双向同步 —— **本轮不做**(治理复杂, 留后)。

---

## 九、优先级裁决

**修订序**: `C1 → 开发端 memory 读侧 MVP(M) → B1 → A1 → C2/B2/D`

依据(四专家共识): 底座成熟度(M 全已建已验 vs A1 全新代码)、价值确定性(M 跨机一致是当前事故源
vs A1 ≥70% 才成立)、用户信号(M 是直接需求)、失败代价(M 读侧纯增益接不通也不破坏现状 vs A1 投一周
可能证伪)、复用治理回路(M 的 ownership 提升反哺 A1 纠错回路)。**先把建好的东西交付到用户手里, 再造新的。**

---

## 十、三大风险(跨专家)

1. **identity 映射做歪 = 一致体验+隐私双崩**(产品+安全): dev 无登录态, user_id 映射错→召回到别人记忆/personal 串 project。MVP 核心交付物, 非附属。
2. **隐私默认值错 → 一次外泄全队弃用**(产品): 写侧默认必须 personal + 显式提升 + RBAC 断言级保证。
3. **多 dev 共用 WSL 仍跑 passthrough**(安全): `deploy_policy_error` 护栏须扩展覆盖"多开发者"判据, 否则裸 header 可伪造身份。

---

## 关联

- 主 plan: [next-plan-2026-06-05.md](next-plan-2026-06-05.md)(本设计对应新增 Track M)
- 相关代码: `agent/memory_store.py` `memory_recall.py` `recall_service.py` `tools/remember.py` `core/acl.py`
  `core/rbac.py` `gateway/auth.py` `agent/routes/memory.py` `cross_link/server.py`(MCP 范式)
- 相关 memory: `agent-model-config-footgun` `codev-platform-ops-via-wsl`
