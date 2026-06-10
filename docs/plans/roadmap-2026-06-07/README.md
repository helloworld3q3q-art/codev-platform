# roadmap-2026-06-07 迭代规划目录

> **主题**: codev-platform 从“多项目 AI 协作工具栈”升级为“多项目代码智能平台”。
>
> 本目录聚焦索引构建、代码解析引擎、图谱融合、多跳影响分析、社区检测、检索排序、查询规划与响应性能。

## 文件清单

| 文件 | 内容 | 状态 |
|---|---|---|
| [code-intelligence-platform-plan-2026-06-07.md](code-intelligence-platform-plan-2026-06-07.md) | 主计划: 评测基线、索引 DAG、跨语言解析引擎、统一图谱、社区检测、Graph+Vector 联合检索、查询规划、响应性能与治理 | 计划(11 Phase 蓝图, 不整体启动) |
| [next-steps-2026-06-09.md](next-steps-2026-06-09.md) | 接下来做什么: 审计剩余项(#10 ✅ 已修 / #7 config DI / #8 set_roles 多 org 延后)+ 三梯队整体剩余计划 + 新窗口开局动作 | 进行中(审计 10/11) |
| [agent-e2e-eval-design-2026-06-09.md](agent-e2e-eval-design-2026-06-09.md) | Phase 7 完整版剩余: agent 端到端 eval 设计(grounding 优先打分 + 数据集格式 + harness + 分期 E1-E4)| 设计(E1-E4 已落地) |
| [anti-false-premise-plan-2026-06-10.md](anti-false-premise-plan-2026-06-10.md) | 下一轮: agent 抗错误前提幻觉(validate-first 分期 A 验证→B 机制→C measure; 不重蹈 rule9)| 🔚 Phase A 证伪结案(停轮) |
| [tiered-domain-labeler-plan-2026-06-11.md](tiered-domain-labeler-plan-2026-06-11.md) | 按客户分档业务域标注(规则/hybrid/llm); 8 视角面板 + 真图谱实测 + 设计 + 触发条件 | 🧊 搁置待做(触发: 免费/离线档需无 LLM 域名) |

> 多仓 operationId 契约桥课题独立成轨, 见 [`../roadmap-2026-06-10/`](../roadmap-2026-06-10/)。

## 落地状态(2026-06-08, 只拎 2 高 ROI 项)

11 Phase 超大蓝图(自评 30-50 天)**不整体启动**(沿 roadmap-2026-06-08 §下一迭代 backlog 的分析结论:地基 Phase 1/2 重型、A1/A2 已用更轻范式绕过)。本轮只交付两个高 ROI 项,且互相咬合(Phase 7 质量用 Phase 0 框架回归):

| Phase | 交付 | 落点 | 验证 |
|---|---|---|---|
| **Phase 0 评测基线** | A1/A2 软标签准确率从人肉核对固化成可回归 golden set(`code_intelligence` suite, 读图谱软边算准确率, 不调 LLM, 软标签缺失优雅 skip) | `eval/datasets/code_intelligence.jsonl` + `eval/run_eval.py` + `tests/test_eval_code_intelligence.py` | 8 单测; 端到端实证抓出 mislabel(`platform_status` repository≠service) |
| **Phase 7 Query Planner 最小版** | 确定性查询分类(overview/impact/symbol/doc_rule/general)→ 工具预算 + 优先 lane + 停止条件; loop planner 每模型策略(`LoopPolicy.planner_enabled`, 按 `_STRONG/_MID/_WEAK` 档走)+ 超预算只读硬封顶 | `codev_platform/agent/planner.py` + loop/trace/registry/chat_service/deps + `eval` `planner` suite | 单测全绿; planner suite 1.0; 真机 A/B + 上线 deepseek |
| **Phase 1 IndexManifest(MVP 切片)** | 把分散新鲜度(chroma `.last_build`/graph `ingest_meta`/codegraph mtime)统一成一张表 `index_builds`(每 project×kind 最近构建 commit/耗时/状态); worker 构建完写一行(best-effort 不阻断); CLI `index status` 看每类索引是否对齐 HEAD | `codev_platform/index_manifest.py` + `core/paths.py` + `reindex/worker.py` 钩子 + `ops/index_status.py` | 7 单测; 真机端到端(worker 写真行 `codegraph 对齐 ✓`) |
| **Phase 3 图谱审计(MVP 切片)** | 让影响分析依据可审计: `graph audit` 在**现有数据**上做结构体检 —— errors(断链 dangling / 跨租户串台 cross-project)+ warnings(重复节点 / 低置信硬边); markdown/JSON; 有 error 非零退出(可作 CI gate) | `codev_platform/graph/audit.py` + `cli.py` graph audit action | 5 单测; 真机审计 codev-platform: 0 error(clean)+ 71 低置信 calls 边 + db_column 误报已修 |
| **Phase 3 provenance 字段(MVP 切片)** | 让影响分析每条边**可追溯来源**: `edge.meta[prov]` 盖 `src/parser`(零迁移)。**全硬边覆盖**: post-pass 三档(bridge/framework/calls 按 resolver 声明)+ **插件直产边**(sql/react/vue=regex、frontend_deps=ast)经 `executor` 按 plugin 声明的 `prov_source` 边界统一盖戳(声明式扩展点, 加插件零改核心)。impact 路径 brief 带 `src+confidence+certain`, report 出确定/候选拆分(展示侧); **Gate 操作侧**: impact 查询(find_impact/table_usage/api_callers/report + MCP)加 `certain_only` —— 高风险改动结论只走确定依赖, 滤候选边不参与遍历; audit 加 no-provenance 硬边计数 | `codev_platform/graph/{schema,ingest,impact,audit,mcp_server}.py` + `plugins/{base,executor}.py` + `call_resolvers/*` + sql/react/vue 插件 | 22 单测; graph+plugin 回归全绿; 纯确定性不调 LLM |
| **Phase 6 联合召回(2026-06-09 续)** | 跨 lane 代码融合召回: `weighted_rrf` 核心(加权 RRF + 可解释)→ service 融 graph + codegraph(免 daemon, 每 lane fail-soft)→ planner 自动调权 → 多词分词 + 相关性分级(精确>前缀>子串)+ codegraph OR 模式 + 测试文件降权。**三消费者**: MCP 工具 `recall_code`(IDE agent)+ web 端点 `/api/v1/recall/code` + agent 工具 `code_recall`(chat) | `codev_platform/recall/{fusion,service,weights}.py` + `graph/{impact,mcp_server}.py` + `web/{routes,schemas}/recall.py` + `agent/tools/recall.py` + `eval` `recall` suite | 真机 eval: **MRR 0.357→0.917 / nDCG 0.269→0.858**; planner 权重 A/B 验证 +0.167; 单测全绿。**剩余**: 接 vector/bm25/memory lane(需 daemon)+ reranker |
| **soft-quality 软标签诊断(2026-06-09)** | 与 audit(结构)/ eval(对 golden 准确率)正交的第三轴: A1/A2 软标签**健康度**(分布/覆盖/giant-cluster 退化), 无需 golden 任意项目可跑。CLI `graph soft-quality` + web `/api/v1/graph/soft-quality` + 平台 `health --all`。**首跑抓修双 bug**: A2 repository 64% = ①soft-quality membership 口径假阳性(Fix-A 按文件)②analyzer 给 db 节点赋代码角色(Fix-B 排除 db_table/db_column), 真实重建验证 | `codev_platform/graph/soft_quality.py` + `analyzers/architecture_layer.py` + `cli.py` + `web/routes/graph.py` + `platform_status.py` | 4 轮取证 + 3 专家面板定论; worker 重建后 `find_arch_role(列)`→空、plays_role 2263→1111、✅ healthy |

**未做(刻意, plan §六纪律)**: Phase 1 重型(DAG 编排/atomic handoff/dashboard/count 回填)/ Phase 3 重型剩余(`source_line`/`index_manifest_id` 落每边 + 同 endpoint 多 parser **冲突消解**)/ Phase 2 统一 IR 解析引擎 / Phase 4 社区检测 / Phase 5 path scoring / Phase 6 剩余 lane(vector/bm25/memory 需 daemon + reranker)/ Phase 8 响应性能 / Phase 9 多语言 adapter / Phase 10 治理。这些是**重型地基, 按真实业务需求触发再做**, 不为"完整平台"堆。Phase 7 完整版(LLM planner + agent 端到端 eval)移交下一迭代。

## 背景

当前平台已经有 Chroma 向量检索、BM25/RRF/reranker、统一图谱、影响面分析、MCP 工具编排和多模型 agent 适配，但这些能力还不是一套严格闭环的“代码智能系统”。

本轮计划的目标是补齐三个硬底座:

1. **构建侧**: 多语言、多框架、多项目的索引构建必须可追踪、可复现、可增量、可审计。
2. **图谱侧**: 代码解析、框架语义、数据库、接口、前端页面、调用链要进入统一 IR 和统一图谱。
3. **查询侧**: agent 不能靠堆工具调用碰运气，必须有 query planner、召回排序、路径解释、响应预算和评测指标。

