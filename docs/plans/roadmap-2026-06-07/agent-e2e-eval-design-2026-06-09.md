# Agent 端到端 Eval — 设计(2026-06-09)

> Phase 7 完整版剩余最后大块。**本文件是设计, 不是实现**: 定目标 / 评分模型 / 数据集格式 /
> harness 结构 / 分期。承 [`daily-summary-2026-06-09.md`](daily-summary-2026-06-09.md) §二十二~二十三
> (planner LLM A/B 已证有效)。

---

## 一、目标 / 非目标

**目标**: 量 agent **完整回答质量** —— 给一个问题, agent 用工具产出的答案是否**正确 / 完整 /
有据(引真实代码)/ 不幻觉**, 以及**用对了工具、没空转**。这是 planner 之上的真正业务指标。

**非目标**(刻意不做):
- 不替代 planner 分类准确率 suite(那测路由对不对; 这测答案好不好)。
- 不做 CI 硬门禁(agent 答案非确定, 见 §六)—— 是**测量 + 趋势**, 不是 pass/fail 闸。
- 不追求绝对分, 追求**可比**(planner 开/关、模型 A vs B、改 prompt 前后的相对差)。

## 二、为什么不能只靠 planner 分类准确率

planner 分类对 ≠ 答案对。分类只是路由起点; 答案还取决于工具调得对不对、证据够不够、收尾合不合理、
有没有编造。§二十三证明了 LLM planner 分类 +0.733, 但"分类更准 → 答案更好"是**独立假设, 待端到端验证**。

## 三、评分模型(grounding 优先, judge 可选)

非确定的 LLM 答案要稳健评分 → **确定性 grounding 检查为主, LLM-judge 为辅**(对齐本仓 determinism-first):

| 维度 | 怎么算 | 性质 |
|---|---|---|
| **grounding_coverage** | 答案命中 `must_mention`(必提的真实文件/符号/表)比例 | 确定, 主指标 |
| **hallucination** | 答案出现 `must_not`(不存在的符号 / 已退役物)→ 扣分 | 确定 |
| **tool_appropriateness** | trace 是否调了 `expect_tools` 类别(如 code_recall/impact)| 确定(读 trace)|
| **within_budget** | 工具调用数 ≤ planner 预算 / max_steps; 无 thrash 拦截 | 确定(读 trace)|
| **judge_score**(可选) | LLM-judge 按 rubric 给 1-5(完整性/连贯/正确)| 非确定, 辅, `--judge` 开 |

**为什么 grounding 为主**: 对答案措辞鲁棒(换说法不影响"有没有提到 audit.py")、零额外 LLM 成本、
可复现。judge 解决 grounding 测不到的"答得顺不顺/对不对", 但有噪声 + 成本, 故默认关、单独开。

## 四、数据集格式 `eval/datasets/agent_e2e.jsonl`

每行一个 case:

```json
{
  "query": "改 graph audit 的只读连接会影响哪些调用方?",
  "project_id": "codev-platform",
  "expect_type": "impact",
  "must_mention": ["audit_all_stores", "graph/audit.py", "cli.py"],
  "must_not": ["open_store"],
  "expect_tools": ["code_recall", "impact_analysis"],
  "rubric": "应指出 audit_all_stores 是 CLI graph audit 与 pre-push 门禁的调用点, 且只读化不改调用方签名。",
  "note": "种子集人工标注; must_mention 是答案必须落到的真实锚点"
}
```

- `must_mention` / `must_not`: 大小写不敏感子串匹配(锚点是真实文件/符号名, 措辞无关)。
- `expect_tools`: 工具**类别**(非精确次数), 校"路子对不对"。
- `rubric`: 仅 `--judge` 时喂给 judge; 不开则忽略。
- 种子集小而精(每 project 5-8 个, 覆盖 overview/impact/symbol/doc_rule), 人工标注锚点。

## 五、Harness 结构

新 suite `eval/suites/agent_e2e.py`: `run_agent_e2e(project_id, provider=None, judge=False)`。

```
load agent_e2e.jsonl(按 project 过滤)
  → 缺 provider / 缺后端(codegraph/graph/chroma daemon)→ status=skipped(像 recall suite, 不阻断)
  → 每 case: 建 AgentLoop(配置 provider + build_default_registry(project_id)) → loop.run(query)
      → AgentResult(answer + trace)
      → 确定性打分: grounding_coverage / hallucination / tool_appropriateness / within_budget(读 trace)
      → judge=True 再调一次 judge LLM 出 judge_score
  → 汇总: 各维度均值 + 逐 case details(可定位差答案)
```

- 复用现成件: `AgentLoop` / `build_default_registry` / `registry.get_provider` / planner / trace。
- `run_eval` 加 `--suite agent_e2e [--judge]`; provider 复用 §二十三 的 `--llm` 构造路径。
- **是 WSL 步**(需 daemon + LLM); Windows 只能跑 §七 E1 的 mock 单测。

## 六、确定性 / 可复现(关键取舍)

agent 答案随 LLM 波动 → 同 case 多跑分数会变。对策:
- 主指标用 **grounding_coverage**(对措辞鲁棒, 波动小); judge 仅辅。
- 报告作**基线band + 趋势**, 不设硬 pass 阈值; 不进 pre-push / CI。
- 需要时同 case 跑 N 次取均值(`--repeat N`)压方差 —— 但默认 1 次(成本)。
- 固定 `temperature` 低值(provider 配), 减随机。

## 七、分期(每期可独立验, 不憋大招)

| 期 | 交付 | 验证 |
|---|---|---|
| **E1** 骨架 | 数据集 schema + 确定性打分器(grounding/halluc/tool/budget, 纯函数)+ suite skip 逻辑 | **Windows 单测**: 喂 fake AgentResult/trace 断言打分正确 |
| **E2** 接真 loop | wire `AgentLoop` + 种子集(codev-platform 5-8 case)+ `run_eval --suite agent_e2e` | WSL 真跑出基线(grounding 均值) |
| **E3** judge | LLM-judge 层(`--judge`, rubric → 1-5)+ judge 与 grounding 一致性抽查 | WSL; 判 judge 是否值得 |
| **E4** planner end-to-end A/B | planner 开/关 + 关键词 vs LLM planner 下, 答案质量差(验"分类更准→答案更好")| WSL; 回答 §二的独立假设 |

E1 纯逻辑、Windows 可验、零后端 —— 先做它(把打分器钉死), 再上 WSL 的 E2+。

## 八、与现有 eval 的关系

- `planner` suite(分类准确率)= 路由层; 本 suite = 答案层。正交, 不重叠。
- `recall` suite(MRR/nDCG)= 检索召回质量; 本 suite 测 agent **用**召回产出答案的质量。
- 同构: 都 `eval/suites/<name>.py` 一模块 + `run_eval` dispatch 一行 + 缺后端优雅 skip。

## 九、风险 / 取舍

- **judge 噪声**: 故默认关, grounding 为主; judge 只在需要"答得对不对"时开, 且抽查与 grounding 一致性。
- **种子集小 → 过拟合**: 明确是趋势工具非绝对分; 扩集再谈默认开/调参([[recall-weight-ab-finding]] 教训)。
- **成本**: 每 case 一次 agent loop(多次工具 + LLM)+ judge 再一次 → 默认小集 + 默认不 judge。
- **多模型**: harness 用配置 provider, 换 `agent.provider` 即换模型评测; 不为单模型硬编(agent-provider §4)。

## 十、本设计**不含**

实现代码、judge prompt 定稿、种子集内容 —— 留给 E1 起的实现轮。本文件只定契约与结构。
