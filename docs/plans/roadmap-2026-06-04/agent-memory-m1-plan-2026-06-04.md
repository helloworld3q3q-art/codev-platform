# Agent Memory M1 任务记忆 + 写侧闭环 实施 plan(2026-06-04)

> **✅ 6 步全实现(2026-06-04, commit 9fe5ab2 → c30ebe0)**:数据层(task_id/task_state)→
> remember 写侧闭环 → 召回 task 加权(redline>task>query)→ /chat 全链路透传 → task_state
> 状态机 API(owner 限定)→ 验收。每步走 实现→兄弟 review→修 nit→commit, 全套 868 passed +
> 真 PG 三处冒烟。下方为原始 plan 留档(决策点 2 "先查 agent 零使用"已核实=误判, agent 实际在用)。

> 关联 [`../roadmap-2026-06-03/`](../roadmap-2026-06-03/) **Track C(Memory M1 起步)**。
> 本 plan = roadmap-2026-06-04 完成度核实后, 对唯一剩余大焦点缺口 M1 的专轮调研产出。
> 设计真值源:`../roadmap-2026-06-01/agent-memory-platform-plan-2026-06-01.md` §M1。

---

## 一、现状(代码核实, 2026-06-04)

| 项 | 状态 |
|---|---|
| M1 设计意图 | task_id(需求/工单/会话任务/Jira issue)+ task_memory(目标/约束/决策/已做/阻塞/验收)+ task_state(active/blocked/done/archived) |
| **数据底座** | **部分已在**:`MemoryEntry.kind` 已支持 `"task"`,`extra: dict` 可放 task_id;store/recall/maintenance 成熟(M0/M2/M3 已交付) |
| **缺** | task_id **一等公民**(召回要按它过滤)+ 写入路径 + recall 按 task_id 加权 + `/chat` 接入。`grep task_id` 全仓**零命中** |
| ⚠️ **真缺口=写侧** | agent 服务健康且**实际在用**(2026-06-04 journalctl 见一次完整对话:deepseek LLM ×10+ + platform-docs MCP 工具,loop 全通)。`agent_sessions=0` 是 `session_backend=memory` 默认(inmemory 不落 PG + 重启丢),**非零使用**(此前据此判"没人用"系误判)。真缺口=**写侧闭环空**:agent loop 无 `remember` 工具,对话不沉淀 memory(唯一写入是人工 HTTP)。M1 要补这一环让对话产生 task memory |

---

## 二、两个耦合问题必须一起解

1. **写侧闭环**:agent 对话怎么产生 task memory(当前完全没有这条路径)。
2. **M1 任务记忆**:task_id 一等公民 + 召回加权 + `/chat` 接入。

只做 2 不做 1 = M1 有 schema 没数据;只做 1 不做 2 = 记忆不按任务组织。

---

## 三、设计

### 3.1 数据模型
- `memory_entries` 加 `task_id TEXT` + `task_state TEXT` 列 + `(org_id, task_id, status)` 索引
  (task_id 做**一等公民列**而非塞 extra:召回要 `WHERE task_id = ?`,JSONB 查询慢且难索引)。
- `MemoryEntry` 加 `task_id` / `task_state` 字段(向后兼容,默认 None)。
- task_state 状态机:`active → blocked → active → done → archived`。

### 3.2 写侧闭环(⚠️ 核心决策点,见 §五)
- **方案 A**:给 agent 加 `remember` 工具 —— 对话中模型判断"这是任务目标/约束/决策"就调
  `remember(task_id, content, kind)` 写 PG。AI 主动沉淀,与 Claude Code 文件记忆同构。
- **方案 B**:对话结束后自动 LLM 提炼任务记忆。无需模型主动,但每轮多一次 LLM 调用 + 易记噪声。
- **方案 C**:显式 API —— 外部任务系统(Jira/工单)或人工写 task memory(对接 M6 connector)。
- **推荐 A + C**:A 让 agent 对话自沉淀(闭环),C 给外部系统接入。B 不推荐(噪声 + 成本)。

### 3.3 召回加权
- `recall_service` 加 `task_id` 入参:当前 task_id 的记忆**优先**(单独 pool 置顶 / 加权),
  其余分层记忆按现有逻辑。`LocalRecallService.recall` 已是接缝,加一层 task 过滤即可。

### 3.4 `/chat` 接入
- agent `/chat` 请求 schema + `ChatService.ask` 加 `task_id` 透传(复用现有 project_id/org_id 范式)。
- recall 时把 task_id 传进 `_recall_memories`。

---

## 四、分步实施(每步配测试)

1. **数据层**:`memory_entries` 加 task_id/task_state 列 + 索引;`MemoryEntry` 加字段;
   `SqlMemoryStore.write/list_scope` 带 task_id。→ 测试:按 task_id 写入/读回。
2. **召回**:`recall_service` task_id 加权 + 测试(同 task_id 优先、不同 task_id 不串)。
3. **/chat 透传**:schema + ChatService.ask + recall 接入 task_id。
4. **写侧**(按 §五 选定方案):remember 工具(`agent/tools/`)/ 或自动提炼 / 或显式 API。
5. **task_state**:状态流转 API(`/memory/task/<id>/state`)+ 状态机校验。
6. **验收测试**(plan §M1 三条,见 §六)。

---

## 五、待你拍板的决策点

1. **写侧闭环方向**:推荐 **A(remember 工具)+ C(显式 API)**;或 B(自动提炼);或仅 C(先不碰 agent 自主)。
2. **agent 是否真推起来用**:M1 的数据源前提。若 agent 暂不主推,建议 M1 先做
   **数据 + 召回 + /chat 底座**(步骤 1-3,5),写侧只做 **C(显式 API 接外部)**,
   等 agent 起来再加 A(remember 工具)。这样 M1 底座不空转、也不赌 agent 使用量。

---

## 六、验收(plan §M1)

- 同一 `task_id` 多轮问答保留任务目标。
- 新会话带同一 `task_id` 能恢复上下文。
- 不同 `task_id` 的任务记忆不串。

---

## 七、ROI / 风险

- **底座(数据 + 召回 + /chat,步骤 1-3,5)** 是确定性工作,ROI 清晰、不依赖 agent 使用量。
- **写侧 A(remember 工具)** 依赖 agent 被用;若 agent 零使用持续,A 的价值受限 →
  决策点 2 要先想清(是否主推 agent,或先 C 接外部任务系统喂数据)。
- 建议执行序:**先底座(1-3,5)+ 写侧 C** → 验证 task 召回正确 → 再视 agent 使用情况加 A。
