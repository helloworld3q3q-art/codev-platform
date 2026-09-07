# roadmap-2026-06-07 迭代规划目录

> **主题**: codev-platform 从“多项目 AI 协作工具栈”升级为“多项目代码智能平台”。
>
> 本目录聚焦索引构建、代码解析引擎、图谱融合、多跳影响分析、社区检测、检索排序、查询规划与响应性能。

## 文件清单

| 文件 | 内容 | 状态 |
|---|---|---|
| [code-intelligence-platform-plan-2026-06-07.md](code-intelligence-platform-plan-2026-06-07.md) | 主计划: 评测基线、索引 DAG、跨语言解析引擎、统一图谱、社区检测、Graph+Vector 联合检索、查询规划、响应性能与治理 | 计划(11 Phase 蓝图, 不整体启动) |
| [next-steps-2026-06-09.md](next-steps-2026-06-09.md) | 接下来做什么: 审计剩余项(#10 ✅ 已修 / #7 ✅ config DI 已实现+测 / #8 ✅ set_roles 多 org 已实现+测)+ 三梯队整体剩余计划 + 新窗口开局动作 | ✅ 全部完成(审计 #7/#8/#10 均已做且测; 三梯队真活 2026-06-12 已清) |
| [agent-e2e-eval-design-2026-06-09.md](agent-e2e-eval-design-2026-06-09.md) | Phase 7 完整版剩余: agent 端到端 eval 设计(grounding 优先打分 + 数据集格式 + harness + 分期 E1-E4)| 设计(E1-E4 已落地) |
| [anti-false-premise-plan-2026-06-10.md](anti-false-premise-plan-2026-06-10.md) | 下一轮: agent 抗错误前提幻觉(validate-first 分期 A 验证→B 机制→C measure; 不重蹈 rule9)| 🔚 Phase A 证伪结案(停轮) |
| [tiered-domain-labeler-plan-2026-06-11.md](tiered-domain-labeler-plan-2026-06-11.md) | 按客户分档业务域标注(规则/hybrid/llm); 8 视角面板 + 真图谱实测 + 设计 + 触发条件 | 🧊 搁置待做(触发: 免费/离线档需无 LLM 域名) |
| [loop-cost-optimization-plan-2026-06-11.md](loop-cost-optimization-plan-2026-06-11.md) | agent loop 成本优化(云账单大头); 实测成本结构(miss 67%+out 25%, 随步数超线性)+ 符号级多跳工具(省 60-70% 且修多跳质量弱点)+ per-档 max_steps + A/B CI 验证 | ✅ 残留已清(2026-06-12: codegraph_trace/紧凑JSON/read_file窗口/per-租户计量/max_steps cap 机制); 激进 cap 值待 A/B |
| [phase4-community-detection-plan-2026-06-13.md](phase4-community-detection-plan-2026-06-13.md) | Phase 4 结构社区检测实现 plan: 确定性 Louvain(纯 stdlib, 复用 Analyzer 协议)+ COMMUNITY 软层 + `find_node_community`/`list_communities` 2 MCP 工具 + Phase 5 社区因子 gate 默认关 | ✅ 已落地(2026-06-13 `907088a`→`f5020ec`: `graph/community.py` + `CommunityAnalyzer`; 实测 codev 34 / openclaw 60 / ideas 84 社区, L1/L3/L4 全链路验过) |

> 多仓 operationId 契约桥课题独立成轨, 见 [`../roadmap-2026-06-10/`](../roadmap-2026-06-10/)。
>
> 日报: [06-08](daily-summary-2026-06-08.md) / [06-09](daily-summary-2026-06-09.md) / [06-10](daily-summary-2026-06-10.md) / [06-11](daily-summary-2026-06-11.md) / [06-12](daily-summary-2026-06-12.md) / [06-13](daily-summary-2026-06-13.md)(PDA 链路收尾: code_vec 大项目建成 + find_api_callers URL 解析 + 前端调用方精确归因 uses_api + 跨仓 config 版本化 + agent 文件工具多仓 + 部署根因 web agent=codev-agent)/ [**06-14**](daily-summary-2026-06-14.md)(/agent 端到端 SSE 流式 + 原生打字: 根治 30s 超时 + 逐字打字; 挖出 contextvar 跨线程 / contentRender 关闭 Bubble typing 两个真 bug, 都不在用户怀疑的 proxy)。

## 落地状态(2026-06-08, 只拎 2 高 ROI 项)

11 Phase 超大蓝图(自评 30-50 天)**不整体启动**(沿 roadmap-2026-06-08 §下一迭代 backlog 的分析结论:地基 Phase 1/2 重型、A1/A2 已用更轻范式绕过)。本轮只交付两个高 ROI 项,且互相咬合(Phase 7 质量用 Phase 0 框架回归):

| Phase | 交付 | 落点 | 验证 |
|---|---|---|---|
| **Phase 0 评测基线** | A1/A2 软标签准确率从人肉核对固化成可回归 golden set(`code_intelligence` suite, 读图谱软边算准确率, 不调 LLM, 软标签缺失优雅 skip) | `eval/datasets/code_intelligence.jsonl` + `eval/run_eval.py` + `tests/test_eval_code_intelligence.py` | 8 单测; 端到端实证抓出 mislabel(`platform_status` repository≠service) |
| **Phase 7 Query Planner 最小版** | 确定性查询分类(overview/impact/symbol/doc_rule/general)→ 工具预算 + 优先 lane + 停止条件; loop planner 每模型策略(`LoopPolicy.planner_enabled`, 按 `_STRONG/_MID/_WEAK` 档走)+ 超预算只读硬封顶 | `codev_platform/agent/planner.py` + loop/trace/registry/chat_service/deps + `eval` `planner` suite | 单测全绿; planner suite 1.0; 真机 A/B + 上线 deepseek |
| **Phase 1 IndexManifest(MVP 切片)** | 把分散新鲜度(chroma `.last_build`/graph `ingest_meta`/codegraph mtime)统一成一张表 `index_builds`(每 project×kind 最近构建 commit/耗时/状态); worker 构建完写一行(best-effort 不阻断); CLI `index status` 看每类索引是否对齐 HEAD | `codev_platform/index_manifest.py` + `core/paths.py` + `reindex/worker.py` 钩子 + `ops/index_status.py` | 7 单测; 真机端到端(worker 写真行 `codegraph 对齐 ✓`) |
| **Phase 3 图谱审计(MVP 切片)** | 让影响分析依据可审计: `graph audit` 在**现有数据**上做结构体检 —— errors(断链 dangling / 跨租户串台 cross-project)+ warnings(重复节点 / 低置信硬边); markdown/JSON; 有 error 非零退出(可作 CI gate) | `codev_platform/graph/audit.py` + `cli.py` graph audit action | 5 单测; 真机审计 codev-platform: 0 error(clean)+ 71 低置信 calls 边 + db_column 误报已修 |
| **Phase 3 provenance 字段(MVP 切片)** | 让影响分析每条边**可追溯来源**: `edge.meta[prov]` 盖 `src/parser`(零迁移)。**全硬边覆盖**: post-pass 三档(bridge/framework/calls 按 resolver 声明)+ **插件直产边**(sql/react/vue=regex、frontend_deps=ast)经 `executor` 按 plugin 声明的 `prov_source` 边界统一盖戳(声明式扩展点, 加插件零改核心)。impact 路径 brief 带 `src+confidence+certain`, report 出确定/候选拆分(展示侧); **Gate 操作侧**: impact 查询(find_impact/table_usage/api_callers/report + MCP)加 `certain_only` —— 高风险改动结论只走确定依赖, 滤候选边不参与遍历; audit 加 no-provenance 硬边计数 | `codev_platform/graph/{schema,ingest,impact,audit,mcp_server}.py` + `plugins/{base,executor}.py` + `call_resolvers/*` + sql/react/vue 插件 | 22 单测; graph+plugin 回归全绿; 纯确定性不调 LLM |
| **Phase 6 联合召回(2026-06-09 续)** | 跨 lane 代码融合召回: `weighted_rrf` 核心(加权 RRF + 可解释)→ service 融 graph + codegraph(免 daemon, 每 lane fail-soft)→ planner 自动调权 → 多词分词 + 相关性分级(精确>前缀>子串)+ codegraph OR 模式 + 测试文件降权。**三消费者**: MCP 工具 `recall_code`(IDE agent)+ web 端点 `/api/v1/recall/code` + agent 工具 `code_recall`(chat) | `codev_platform/recall/{fusion,service,weights}.py` + `graph/{impact,mcp_server}.py` + `web/{routes,schemas}/recall.py` + `agent/tools/recall.py` + `eval` `recall` suite | 真机 eval: **MRR 0.357→0.917 / nDCG 0.269→0.858**; planner 权重 A/B 验证 +0.167; 单测全绿。**剩余(已清, 2026-06-11)**: vector lane ✅上线+打磨+worker 自动刷新 / reranker ✅建+默认关 / bm25 ✅drop(被 codegraph-FTS+vector 夹冗余) / memory ✅不进核心(跨 org 红线) |
| **soft-quality 软标签诊断(2026-06-09)** | 与 audit(结构)/ eval(对 golden 准确率)正交的第三轴: A1/A2 软标签**健康度**(分布/覆盖/giant-cluster 退化), 无需 golden 任意项目可跑。CLI `graph soft-quality` + web `/api/v1/graph/soft-quality` + 平台 `health --all`。**首跑抓修双 bug**: A2 repository 64% = ①soft-quality membership 口径假阳性(Fix-A 按文件)②analyzer 给 db 节点赋代码角色(Fix-B 排除 db_table/db_column), 真实重建验证 | `codev_platform/graph/soft_quality.py` + `analyzers/architecture_layer.py` + `cli.py` + `web/routes/graph.py` + `platform_status.py` | 4 轮取证 + 3 专家面板定论; worker 重建后 `find_arch_role(列)`→空、plays_role 2263→1111、✅ healthy |

> ⚠️ 此表是 2026-06-08 快照。06-09~13 又交付了 Phase 3(冲突消解)/4(社区)/5(路径评分)/8(观测层)/9(adapter)—— **最新逐 Phase 状态以下方「状态对账(2026-06-14)」为准**。

**未做(刻意, plan §六纪律, trigger-gated 非欠债)**: Phase 2 统一 IR 解析引擎(完全未启动, 最重)/ Phase 1 重型(DAG 编排/atomic handoff/parser_ir/count 回填; MVP 已做)/ Phase 3 重型剩余(`source_line`/`index_manifest_id` 落每边; 判低 ROI 冗余)/ Phase 8 广义延迟优化(观测层已做, latency budget/cache 未)/ Phase 10 治理产品化(CLI/audit 已有, 业务仓接入指南/adapter 开发指南/治理看板未, ~25%)。按真实业务需求触发再做。
**已收口/证伪(不再做)**: Phase 6 vector lane/reranker ✅交付(bm25 drop / memory 不进核心);Phase 7 完整版 ✅收口(keyword planner 上线 / LLM planner 双否证伪 / agent e2e eval 已落);anti-false-premise Phase A 证伪结案;Phase 5 社区因子 gate 默认关(待 find_impact_paths 真被实战用 + A/B)。

## 收尾状态(2026-06-12)

**本 roadmap 基本"做完或刻意不做"**:代码智能层(eval/recall/planner/图谱)判定到**平台期**(连续证伪 3+ 假设, grounding 两模型两项目饱和)。真正"可做但未做"的 4 残项 2026-06-12 由 2 兄弟并行清完(前端 services.ts 清理 + 后端 loop-cost 残留;soft-quality 卡/impact-paths 可视化经核查早已存在=stale TODO)。剩余 trigger-gated 重型地基按真需求触发(**06-13 又交付了 Phase 4/5/9, 见下方对账**)。
**主线已转**:多机/多组织**服务器 arc**(部署服务器供多人多机连用)—— PgJobQueue + graph→PG(Stage A/B 已审已验)+ 多人 auth(token/?token=/onboarding CLI/双 store 厘清)。该 arc 见 [`multi-user-server-deploy-runbook-2026-06-12.md`](multi-user-server-deploy-runbook-2026-06-12.md) + daily-summary-2026-06-11/12 + 记忆 multi-machine-platform-arc。

## 状态对账(2026-06-14 代码核实)

> 上方两块状态停在 06-08/06-12, 把 06-13 才建的 Phase 4/5/8观测/9 仍列为"未做/否"。本块按**代码符号核实**(codegraph 查 `detect_communities`/`_KIND_WEIGHT`/`recall_latency_report`/`describe_capabilities` 等真实存在 + graph 实时 34 社区)逐 Phase 订正。

| Phase | 真实状态(代码核实) | 证据 |
|---|---|---|
| 0 评测基线 | ✅ 做完 | `eval/suites/` 多 suite + golden set |
| 1 IndexManifest + atomic handoff | 🟡 MVP + handoff 做完 | `index_manifest.py` MVP + **atomic handoff(blue-green 双缓冲)2026-06-14 交付上线**(`core/index_handoff.py` + docs/code_vec 两库 writer/reader, WSL 真机 SIGKILL 验过, 见 [`phase1-atomic-handoff-plan-2026-06-14.md`](phase1-atomic-handoff-plan-2026-06-14.md))。**DAG 砍(YAGNI)/ df 预检·keep 调优实测无压力不做(磁盘 930G free + 多机未部署)/ parser_ir 未做** |
| **2 统一 IR 解析引擎** | ❌ **完全未启动** | codegraph 查无 IR 归一层(只命中前端 `unifiedgraph` 可视化页)。被 Phase 9 插件协议族替代, 刻意不建 |
| 3 provenance/审计/冲突 | ✅ 主体做完 | `stamp_provenance` + `graph/audit.py` + `edge_resolve.py` 冲突消解(`910ea21`)。`source_line`/`index_manifest_id` 落每边判低 ROI 冗余跳过 |
| **4 社区检测** | ✅ **做完**(README 旧写"否") | `graph/community.py:detect_communities` + `CommunityAnalyzer`(已注册)+ 2 MCP 工具; 实时图谱 34 社区 |
| **5 路径评分** | ✅ 做完(README 旧写"未") | `find_impact_paths` + `impact.py:_KIND_WEIGHT` + 深度衰减。社区因子 gate 默认关 / 新鲜度因子未做(数据不现成) |
| 6 联合召回 | ✅ 做完 | `recall/{fusion,service,weights}` + vector lane + reranker(默认关); bm25/memory 刻意 drop |
| 7 Query Planner | ✅ 做完 | keyword planner 上线 + LLM planner 默认关(双否证伪)+ e2e eval E1-E4 |
| 8 响应性能/可观测 | ✅ 主体做完(measure-first 收口) | 观测层 + SSE streaming + token 看板 + `codegraph_trace` + **latency budget gate(`check_latency_budget` + `recall-stats --budget-p95-ms`, 2026-06-14 真机验过)**。**cache 层实测 P95 4s<8s 判 YAGNI; vector 长尾诊断=sample-project-beta 大库 8GB GPU 争用硬约束(上云大卡解, 非代码问题)** |
| **9 多语言 adapter** | ✅ 框架做完(README 旧写"未") | `plugins/capabilities.py:describe_capabilities` + `ownership.py:kind_owners`(produces 派生)。新栈(Node/Angular/.NET full)按需补 |
| 10 治理产品化 | 🟡 ~25% | CLI(`index status`/`graph audit`/`plugins list`)+ dashboard 卡有; **业务仓接入指南/adapter 开发指南/治理看板未做** |

**真正"还没做"的口径(2026-06-14 收口后)**:① 完全没建 = **Phase 2 统一 IR**(刻意, 插件范式替代);② 唯一有真实价值潜力 = **Phase 10 治理产品化**(~25%, 需"第二个业务仓接入"真实触发);③ 实测/数据驱动判不做 = Phase 1 DAG(YAGNI)·df 预检·keep 调优(磁盘 930G free + 多机未部署)/ Phase 8 cache(P95 4s<8s 达标)/ Phase 3 source_line(低 ROI)/ vector 长尾(8GB GPU 硬约束, 上云解)。**无"想做却拖着"的活债**。

> **2026-06-14 收口**:代码智能平台线把所有 trigger-gated 残项**逐个用真实数据证实"现在不该做"**(磁盘探测/延迟实测/多机未部署),唯一真需求 **atomic handoff 已交付上线**, Phase 8 latency gate 补上。这条线判定**整体到平台期**。主线已转多机/多组织服务器 arc。详见 [`daily-summary-2026-06-14.md`](daily-summary-2026-06-14.md)。

## 背景

当前平台已经有 Chroma 向量检索、BM25/RRF/reranker、统一图谱、影响面分析、MCP 工具编排和多模型 agent 适配，但这些能力还不是一套严格闭环的“代码智能系统”。

本轮计划的目标是补齐三个硬底座:

1. **构建侧**: 多语言、多框架、多项目的索引构建必须可追踪、可复现、可增量、可审计。
2. **图谱侧**: 代码解析、框架语义、数据库、接口、前端页面、调用链要进入统一 IR 和统一图谱。
3. **查询侧**: agent 不能靠堆工具调用碰运气，必须有 query planner、召回排序、路径解释、响应预算和评测指标。

