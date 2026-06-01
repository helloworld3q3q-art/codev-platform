# Agent Memory 平台化落地 Plan

日期: 2026-06-01

目标: 把当前已有的会话记忆、分层记忆、召回、冲突消解、维护任务，升级成可被 Agent、MCP、企业应用和非开发人员入口共同使用的记忆基础设施。

## 1. 当前判断

图片里的 5 条能力不是一个小功能，而是一套企业级 Agent Memory 基础设施。当前项目已经具备基础雏形，但还没有完成完整平台化。

当前已有:

- 会话记忆: `agent/session.py`、`agent/session_pg.py`
- 分层记忆: `agent/memory_store.py`、`agent/memory_store_pg.py`
- 记忆召回: `agent/recall_service.py`
- 冲突消解: `agent/memory_recall.py`
- 生命周期维护: `agent/memory_maintenance.py`
- 记忆 HTTP 接口: `agent/routes/memory.py`
- RBAC/ACL 基础: `agent/rbac_store_pg.py`、`core/rbac.py`、`core/acl.py`
- Agent 注入链路: `agent/services/chat_service.py`、`agent/prompts.py`

当前不足:

- 没有海量数据压测和明确延迟指标。
- 没有高可用部署方案。
- 任务记忆还没有形成完整闭环。
- 记忆压缩、遗忘、摘要融合还偏基础。
- 多模态记忆几乎没有。
- 行为记忆没有。
- 与飞书、Wiki、Jira、Git、CI、工单系统的业务闭环还没有完成。
- 缺少评测集，无法量化“记忆是否真的提升 Agent 效果”。

## 2. 总目标

最终要形成四层能力:

```text
业务入口层
Feishu / Wiki / Jira / Web UI / CLI / MCP / Agent

记忆服务层
写入 / 召回 / 更新 / 压缩 / 遗忘 / 权限 / 审计

索引与治理层
结构化 PG / 向量检索 / BM25 / RRF / rerank / lifecycle job

业务上下文层
项目 / 人员 / 团队 / 需求 / 代码 / 接口 / 数据库 / 文档 / 行为事件
```

## 3. 分阶段计划

### M0: 稳定当前基础能力

目标: 把当前已写的 Memory 基础能力整理成稳定可验证状态。

范围:

- 固化 PostgreSQL memory schema。
- 确认 `memory_entries`、session、RBAC 表结构。
- 修齐 memory route 的错误码和权限错误返回。
- 给 `memory_store_pg.py`、`recall_service.py`、`memory_maintenance.py` 增加边界测试。
- 增加 `codev-platform memory doctor`，检查 DSN、表、连接池、基础写读召回。

验收:

- `python -m pytest -q tests/test_agent_memory_* tests/test_rbac_*`
- 能写入一条 project memory。
- 能召回当前 project + personal + org 可见记忆。
- 过期记忆不会被召回。
- 被 supersede 的旧记忆不会被召回。

优先级: P0

### M1: 任务记忆闭环

目标: 支持 Agent 围绕一个任务持续记住上下文，而不是只记用户偏好和事实。

新增概念:

- `task_id`: 一个需求、工单、会话任务或 Jira issue。
- `task_memory`: 与任务相关的目标、约束、决策、已做事项、阻塞项、验收标准。
- `task_state`: active、blocked、done、archived。

范围:

- 在 memory entry 中明确 `kind=task|decision|constraint|todo|result`。
- `/chat` 请求支持 `task_id`。
- recall 时优先召回当前 `task_id` 相关记忆。
- Agent 执行后能写入任务摘要。
- 支持“继续上次任务”。

验收:

- 同一个 `task_id` 下多轮问答能保留任务目标。
- 新会话带同一个 `task_id` 能恢复上下文。
- 不同 `task_id` 的任务记忆不串。

优先级: P0

### M2: Context Engineering 升级

目标: 不再把记忆简单塞进 prompt，而是按预算、优先级、冲突策略组织上下文。

范围:

- 定义 Context Budget: system、rules、memory、docs、codegraph、cross-link 各占多少 token。
- 记忆召回结果分组: redline、task、project、personal、history。
- RRF 融合 BM25 和向量召回。
- reranker 对 memory 结果重排。
- 冲突消解支持 `personal_first`、`org_first`。
- 输出 prompt 前生成 `context_plan`，便于调试为什么选了这些记忆。

验收:

- prompt 中 memory 不超过预算。
- redline 永远保留。
- 同 topic 冲突时只注入胜出项。
- trace 中能看到每条记忆的来源、分数、作用域和淘汰原因。

优先级: P0

### M3: 生命周期治理

目标: 让记忆能长期运行，不无限膨胀，也不悄悄污染 Agent。

范围:

- TTL 到期归档。
- supersede 更新留痕。
- forget 显式遗忘。
- topic 级压缩摘要。
- 低质量记忆标记。
- 重复记忆合并。
- 定期维护 job。
- 维护报告: 压缩多少、归档多少、遗忘多少、失败多少。

验收:

- 1000 条同类记忆能压缩成可控数量。
- 原始记忆可追溯。
- redline 不参与压缩。
- 忘记的记忆不能再被 recall。

优先级: P1

### M4: 权限、审计和多租户

目标: 支持企业内部多人、多团队、多项目使用。

范围:

- org / team / project / personal 四层可见性。
- `X-Org-Id`、`X-User-Id`、`X-Project-Id` 统一解析。
- token/API key 到可信身份。
- `read`、`write`、`recall`、`admin` 分权。
- personal memory 只允许本人直接读。
- recall 允许按权限使用，但不等于直接读取内容。
- 审计日志记录谁写入、谁召回、谁删除、谁修改。

验收:

- A 用户不能读取 B 的 personal memory。
- A 用户不能召回无权限项目记忆。
- team memory 只对团队成员可见。
- org redline 所有人都能 recall。
- 审计日志可追溯。

优先级: P1

### M5: 海量数据和性能

目标: 给 Memory 设定明确工程指标，而不是只说“支持海量”。

第一阶段指标:

- 10 万条 memory entry。
- 单次 recall P95 小于 800ms。
- 写入 P95 小于 300ms。
- 维护 job 可在 30 分钟内处理 10 万条。
- memory store 支持连接池配置。

范围:

- 构造压测数据生成器。
- 增加 `memory benchmark` 命令。
- 分别测 PG 查询、BM25、向量召回、rerank、prompt 组装。
- 慢查询日志。
- 必要索引优化。

验收:

- 输出 benchmark 报告。
- P95 延迟有基线。
- 发现慢查询能定位到 SQL 或索引。

优先级: P1

### M6: 业务系统接入

目标: 让记忆不是 Agent 自己玩，而是接入真实业务流。

接入顺序:

1. Git / Gitea / GitLab webhook: commit、PR、review 形成项目事件记忆。
2. Jira: issue、需求、状态、验收条件形成 task memory。
3. 飞书: 群消息、审批、文档、会议纪要形成业务记忆。
4. Wiki: 规则、流程、系统说明形成 org/project memory。
5. CI/CD: 构建失败、发布记录、回滚记录形成运维记忆。

范围:

- 统一 connector 协议。
- 每个 connector 输出标准 memory event。
- 事件进入 memory queue。
- 写入前做脱敏、权限、topic_key 生成。
- Agent 查询时能按 task/project/user 召回。

验收:

- Jira issue 能生成 task memory。
- 飞书会议纪要能生成 project memory。
- Git push 能生成 code change memory。
- Agent 能回答“这个需求之前讨论过什么、谁改过、现在卡在哪里”。

优先级: P2

### M7: 多模态和行为记忆

目标: 支持文本以外的信息，但不提前大投入。

文本优先，后续扩展:

- 图片: 截图、设计稿、报错截图。
- 音频: 会议录音转写后进入文本记忆。
- 行为: 用户点击、搜索、查看页面、常用项目。

落地原则:

- 第一阶段不直接存大文件进 memory。
- 大文件进入 object storage 或文件系统。
- memory 只保存引用、摘要、权限、向量索引。

验收:

- 上传一张报错截图，OCR/描述后形成 memory。
- 一段会议录音转写后形成 task memory。
- 用户常用项目能影响 Agent 默认召回。

优先级: P3

## 4. 数据模型建议

记忆主表继续以 `memory_entries` 为核心，但需要补齐字段:

```sql
org_id
scope
scope_ref
owner_user_id
project_id
task_id
topic_key
kind
content
summary
source_type
source_ref
confidence
importance
status
ttl_at
supersedes
created_at
updated_at
```

关键原则:

- PG 是真值存储。
- 向量库只做召回索引。
- 所有写入必须保留 source。
- 所有召回必须经过权限过滤。
- 删除默认逻辑删除，除非合规要求物理删除。

## 5. API 规划

内部 API:

```text
POST   /memory/write
GET    /memory/recall
PATCH  /memory/{id}
DELETE /memory/{id}
POST   /memory/compress
POST   /memory/forget
GET    /memory/scopes
GET    /memory/audit
GET    /memory/benchmark
```

Agent API 集成:

```text
POST /chat
body:
  project_id
  task_id
  user_id
  org_id
  question
  memory_policy
```

## 6. 评测体系

没有评测，Memory 做得再复杂也无法证明有价值。

必须建立 5 类评测:

- 长期事实召回: 多天前写入的事实能否找回。
- 偏好召回: 用户偏好能否影响回答风格和默认选择。
- 任务连续性: 跨会话继续任务是否准确。
- 冲突消解: 新规则覆盖旧规则是否正确。
- 遗忘正确性: 已忘记内容是否不再出现。

指标:

- recall hit rate
- top1 accuracy
- conflict resolution accuracy
- forgotten leakage rate
- context token cost
- answer success rate
- P50/P95 latency

## 7. 不做事项

短期不要做:

- 不做多模态大平台。
- 不做分布式记忆集群。
- 不做复杂推荐系统。
- 不做 SaaS 级组织管理。
- 不做和 supermemory / mem0 的全量功能竞争。

原因: 当前项目的差异化不是“通用记忆产品”，而是“私有化代码知识图谱 + 业务全链路 Agent”。Memory 是底座，不是唯一产品。

## 8. 排期建议

### 第 1 周: M0 + M1

- 整理当前 memory 表和测试。
- 增加 task_id。
- 支持 task memory 写入、召回、继续任务。
- 输出 `memory doctor`。

交付:

- 任务记忆 demo。
- memory doctor 全绿。
- 单测补齐。

### 第 2 周: M2

- Context Budget。
- memory recall 排序和分组。
- trace 中记录记忆入选原因。
- 冲突策略参数化。

交付:

- 一份 context trace 示例。
- Agent 能解释“为什么用了这些记忆”。

### 第 3 周: M3 + M4 基础

- TTL、forget、supersede 强化。
- 压缩 job。
- personal/team/project/org 权限测试。
- 审计日志初版。

交付:

- 生命周期维护报告。
- 权限隔离测试。

### 第 4 周: M5 评测和压测

- memory benchmark。
- 10 万条测试数据。
- P95 延迟报告。
- 慢查询定位。

交付:

- 性能基线报告。
- 下一轮优化清单。

### 第 5-6 周: M6 业务接入

- Jira 或飞书二选一先接。
- Git webhook 形成 task/code memory。
- Web/API playground 展示。

交付:

- 一个完整 demo: 需求 -> 讨论 -> 代码变更 -> Agent 召回 -> 影响分析。

## 9. 对外表述

不能说:

> 已完成新一代 Agent Memory 基础设施。

可以说:

> 已实现 Agent Memory 的基础底座，包括会话记忆、分层长期记忆、基础召回、冲突消解、生命周期维护和 Agent 注入链路。下一阶段将围绕任务记忆、Context Engineering、权限审计、性能评测和业务系统接入进行平台化升级。

## 10. 最终目标

做到下面这个闭环，才算真正完成图片里的方向:

```text
用户在飞书/Jira 提出需求
-> 系统生成 task memory
-> 关联 Wiki 规则、历史讨论、代码图谱、接口链路
-> Agent 生成影响分析和执行计划
-> 开发/测试/发布过程持续写入记忆
-> 下一次同类任务自动召回历史经验
-> 权限、审计、遗忘、压缩全程可控
```

