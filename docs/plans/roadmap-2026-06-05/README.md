# roadmap-2026-06-05 迭代规划目录(🗄️ 已归档 / 综合理解层 A1 交付归档)

> **🗄️ 归档说明(2026-06-08 起)**: 本轮代码需求**全部闭环**(M/B1/A1 + 收尾 C1/C2/B2 + 2 子设计 +
> loop-guard, 见下「落地状态核实」)。**当前主目录 → [`../roadmap-2026-06-08/`](../roadmap-2026-06-08/)**
> (A2 架构分层映射)。未完结项(M e2e 部署 / D1-D2 业务仓)已继承到新目录。
> 承上一迭代 [`../roadmap-2026-06-03/`](../roadmap-2026-06-03/)(主线已闭环)。
> **主题**: 综合理解层 —— 在技术血缘之上加 LLM 业务域映射,**但收尾先行、新特性狠切 MVP**。

---

## 文件清单

> **落地状态核实(2026-06-08 代码级)**:本轮代码需求**全部落地** —— M/B1/A1 + 收尾 C1/C2/B2 + 2 个
> 子设计 + loop-guard 都有代码证据(逐个查实, 不信文档标注)。详见本目录 §「落地状态核实」。

| 文件 | 内容 | 状态 |
|---|---|---|
| [next-plan-2026-06-05.md](next-plan-2026-06-05.md) | 主 plan **v3**:ROI 重排 `C1→M→B1→A1`(收尾先于新特性)+ **新增 Track M 开发端 memory** + A 砍只读 MVP(≥70% 验收)+ 软/硬节点隔离 + grounding-first | ✅ 本轮代码需求全落地(C1/C2/B2 状态 2026-06-08 补标) |
| [dev-agent-memory-mcp-design-2026-06-05.md](dev-agent-memory-mcp-design-2026-06-05.md) | **子设计(四专家会诊)**:开发端 Claude Code/Codex 经 MCP 接平台 memory —— 独立 SSE server + per-dev token 身份(org_id 不可由 client 传)+ personal 默认隐私 + P0 三缺口前置 + 读侧 MVP 先行 | ✅ 代码 P0-P3 全落地;⏳ 换机同步 e2e 待部署验收 |
| [mcp-port-config-unification-2026-06-05.md](mcp-port-config-unification-2026-06-05.md) | **子设计**:4 套 MCP 端点端口/配置键统一 —— 单一 `_bind_port` resolver + `_SERVICE_PORTS` 服务键注册表 + canonical 键 `mcp.<service>_sse_port` + 本机口从 bind 派生,全 back-compat | ✅ 已落地(`mcp_serve._SERVICE_PORTS`/`_bind_port`) |
| [memory-recall-pluggable-pipeline-2026-06-05.md](memory-recall-pluggable-pipeline-2026-06-05.md) | **子设计**:memory 召回解构为可插拔 —— 不变量(ACL/redline/去重)+ Scorer/Fusion/Reranker 三段可插拔 + 模型 registry,对齐 agent-provider-architecture(config 驱动零 if-else) | ✅ 已落地(`agent/recall/` 7 文件 + `agent/embed/` 模型层) |
| [agent-loop-guard-redesign-2026-06-05.md](agent-loop-guard-redesign-2026-06-05.md) | loop guard 重构(5 专家两轮会诊):工具三分类护栏(只读近乎不限 / 检索类输出侧零增量 / 无效调用单独防线)+ 收尾禁脑补 + 读取充分性门。修 `per_tool_cap=3` 误杀 read_file 的主瓶颈 | ✅ 已落地(`e3c6594`+`9246664`,两轮审计+端到端验证过) |
| [daily-summary-2026-06-05.md](daily-summary-2026-06-05.md) | W3 loop-guard 全程日报:三分类护栏 + P0 module 枚举放开 + 两轮审计 + WSL 端到端验证(11/11 工具 + P0 live 确认)+ 踩坑(多窗口 git add -A / 改 schema 必重启 daemon) | 📓 日报 |

---

## 继承自 roadmap-2026-06-03(2026-06-04 代码级核实)

Memory **M0–M4 全闭环**、影响分析三层全在、18082 退役、agent-chat-sessions A/B/C 做完。真未做仅:
M5 压测(C2 起步)/ M3 向量(B1)/ M4 cron(B2)/ Store project_id(C1)/ 前端 backlog(D)。

## 本轮核心(四专家会诊后修订)

对比 [Understand-Anything](https://example.invalid/reference) 定位 —— borrow 其
domain/tour **高层分析**,用 codev 独有 **Agent+Memory 底座**实现(❌ 不抄 tree-sitter, 和 codegraph 重叠)。

**经架构/产品/AI/实施四专家会诊,原 v1 修订四点**:
1. **ROI 重排**: `C1(多租户安全)→ B1(向量召回)→ A1-MVP → C2/B2/D`——收尾确定 > 新特性未验;且 C1 是 A 的硬依赖。
2. **A 狠切 MVP**: 本轮只交 A1 业务域映射只读文本(**≥70% 准确率验收**, 可证伪止损);A2/A3/A4(架构/tour/web-ui/persona)推下轮专轮。
3. **软/硬节点隔离**: LLM 软节点独立 kind + confidence<1.0 + impact 默认过滤软边(保护"查依赖"护城河)。
4. **grounding-first**: LLM 当标注者不当发现者, 越界节点代码 reject(硬约束>prompt), 比 UA 纯 LLM 更抗幻觉。

**定位**: UA = 教人读懂任意代码(广浅, human-first);codev = 让 agent+团队在多项目间共享对自有系统的
精确理解(深专, **AI-first**)。胜负手在业务域映射的**准确 + 可纠错 + 可被 agent 复用**, 不在 tour/persona 视觉。

graphs that **query** → graphs that **explain on a deterministic skeleton**。

## 落地状态核实(2026-06-08 代码级)

逐个查代码证据(不信文档标注), 本轮**所有代码需求已落地**:

| Track / 需求 | 状态 | 代码证据 |
|---|---|---|
| M 开发端 memory MCP | ✅ | `agent/memory_mcp.py` + token 身份 + P0-P3(`f656c6a`..`2e0d6b9`) |
| B1 向量召回 | ✅ | `5cef5b0`/`a124179`, WSL 实测语义>关键词 |
| A1 业务域映射 | ✅ 95% | `graph/analyzers/` + 统一图谱 MCP(`807b65a`) + cross-link 退役 |
| C1 Store project_id 列化 | ✅ | graph store project_id 列 + `test_graph_store_project_isolation` |
| C2 压测摸底 | ✅ | `scripts/bench_memory.py` + `test_bench_memory.py` |
| B2 M4 cron | ✅ | `memory_maintenance` + `render_memory_maintenance_units`(systemd timer) + `run_memory_maintenance.py` |
| 子设计① MCP 端口统一 | ✅ | `mcp_serve._SERVICE_PORTS`/`_bind_port` |
| 子设计② recall 可插拔重构 | ✅ | `agent/recall/`(scorer/fusion/reranker/registry) + `agent/embed/`(模型 registry+remote) |
| loop-guard 重构 | ✅ | `e3c6594`+`9246664` |

**剩余(非漏做 —— 都是 plan 主动排到本仓外 / 下轮 / 部署的)**:
- **D1/D2/D3** 前端依赖图 backlog(is_page Next 验证 / vue 仓 scl-www-10 登记 / endpoint→表 DI 评估)—— 业务仓任务, 不在本仓; plan §四排"收尾末段"、无截止。
- **M 换机同步 e2e 验收** —— 部署运维动作(WSL 起 agent-memory 端点跑 A机→B机断言), 代码就绪、非代码缺口。
- **A2/A3/A4**(架构分层 / tour / web-ui / persona)—— plan §六明确"整体推下轮专轮", 等 A1 ≥70% 验证(已实测 95%)。

## 关联

- 上一主目录: [`../roadmap-2026-06-03/`](../roadmap-2026-06-03/)
- 周期生命周期 SOP: `.claude/rules/weekly-iteration-cadence.md`
