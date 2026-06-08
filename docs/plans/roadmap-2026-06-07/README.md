# roadmap-2026-06-07 迭代规划目录

> **主题**: codev-platform 从“多项目 AI 协作工具栈”升级为“多项目代码智能平台”。
>
> 本目录聚焦索引构建、代码解析引擎、图谱融合、多跳影响分析、社区检测、检索排序、查询规划与响应性能。

## 文件清单

| 文件 | 内容 | 状态 |
|---|---|---|
| [code-intelligence-platform-plan-2026-06-07.md](code-intelligence-platform-plan-2026-06-07.md) | 主计划: 评测基线、索引 DAG、跨语言解析引擎、统一图谱、社区检测、Graph+Vector 联合检索、查询规划、响应性能与治理 | 计划(11 Phase 蓝图, 不整体启动) |

## 落地状态(2026-06-08, 只拎 2 高 ROI 项)

11 Phase 超大蓝图(自评 30-50 天)**不整体启动**(沿 roadmap-2026-06-08 §下一迭代 backlog 的分析结论:地基 Phase 1/2 重型、A1/A2 已用更轻范式绕过)。本轮只交付两个高 ROI 项,且互相咬合(Phase 7 质量用 Phase 0 框架回归):

| Phase | 交付 | 落点 | 验证 |
|---|---|---|---|
| **Phase 0 评测基线** | A1/A2 软标签准确率从人肉核对固化成可回归 golden set(`code_intelligence` suite, 读图谱软边算准确率, 不调 LLM, 软标签缺失优雅 skip) | `eval/datasets/code_intelligence.jsonl` + `eval/run_eval.py` + `tests/test_eval_code_intelligence.py` | 8 单测; 端到端实证抓出 mislabel(`platform_status` repository≠service) |
| **Phase 7 Query Planner 最小版** | 确定性查询分类(overview/impact/symbol/doc_rule/general)→ 工具预算 + 优先 lane + 停止条件; loop `planner_enabled` 软预算(默认关, 行为逐字节不变); config 门 `agent.planner.enabled` | `codev_platform/agent/planner.py` + loop/trace/chat_service/deps + `eval` `planner` suite + `tests/test_agent_planner.py` | 11 单测; planner suite classification_accuracy 1.0 |

**未做(刻意, plan §六纪律)**: Phase 1 IndexManifest / Phase 2 统一 IR 解析引擎 / Phase 4 社区检测 / Phase 5 path scoring / Phase 6 联合召回 / Phase 8 响应性能 / Phase 9 多语言 adapter / Phase 10 治理。这些是**重型地基, 按真实业务需求触发再做**, 不为"完整平台"堆。Phase 7 完整版(LLM planner + agent 端到端 eval + tool budget 真机统计)移交下一迭代。

## 背景

当前平台已经有 Chroma 向量检索、BM25/RRF/reranker、统一图谱、影响面分析、MCP 工具编排和多模型 agent 适配，但这些能力还不是一套严格闭环的“代码智能系统”。

本轮计划的目标是补齐三个硬底座:

1. **构建侧**: 多语言、多框架、多项目的索引构建必须可追踪、可复现、可增量、可审计。
2. **图谱侧**: 代码解析、框架语义、数据库、接口、前端页面、调用链要进入统一 IR 和统一图谱。
3. **查询侧**: agent 不能靠堆工具调用碰运气，必须有 query planner、召回排序、路径解释、响应预算和评测指标。

