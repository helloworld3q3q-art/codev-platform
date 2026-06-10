# daily-summary 2026-06-10 —— 抗错误前提 Phase A:验证 → 证伪 → 停轮

> 承 [`anti-false-premise-plan-2026-06-10.md`](anti-false-premise-plan-2026-06-10.md)(validate-first 分期)。
> 本轮只做 Phase A(验证 + 标定),结论**证伪**,按纪律停 Phase B/C。

## 一、做了什么(Phase A 全部可构建项)

- **A1 错误前提集** `eval/datasets/agent_e2e_false_premise.jsonl`(11 例,跨 codev-platform + openclaw-stock):
  每例 premise **逐条 codegraph 核对真为假**(留 `verified_false` 证据,吸取 §三十 case6 标错教训),
  三类齐全(nonexistent_capability / changed_old_thing / wrong_attribution);**靠 must_mention 真机制判,禁 must_not**。
- **A2 对照组** `..._control.jsonl`(11 例):同实体真前提,锚点镜像,隔离"前提为假"单一变量。
- **harness**:`aggregate` 加确定性 bootstrap `grounding_ci95`(n 小比点估诚实);`run_agent_e2e` 加 `cross_project`
  (跨项目全集一次跑才好算跨组 CI);`run_eval --e2e-set` 加 `false_premise` / `control`。
- **34 单测**(`tests/test_eval_false_premise.py`)钉数据集红线 + CI 语义 + cross_project,纯逻辑 Windows 可跑。
- commit `da5ab08`;WSL 仓 ff 到同版本真跑 A3。

## 二、A3 实跑结果(WSL,deepseek + codegraph/graph 全工具,repeat 3)

| 组 | grounding 均值 | 95% CI(bootstrap) | hallucination |
|---|---|---|---|
| **false_premise** | 0.970 | [0.909, 1.0] | 0.0 |
| **control(真前提)** | 1.000 | [1.0, 1.0] | 0.0 |

**两组 CI 重叠 → 无显著差。**

## 三、Gate A 判定:证伪,停轮

- false 集逐例:10/11 grounding=1.0,唯一 0.67 是 reindex 多跳题**耗尽 max_steps**(抽查答案原文 = "达到上限未得出"),
  **非顺前提幻觉**。其余每例答案都**明确反驳前提**("**不是**。planner 基于确定性关键词打分" / "**不是**。audit 改用 mode=ro")。
- 测量细节:false 集无 must_not(子串判不了否定),故 `hallucination_rate` 恒 0 不是信号;**grounding 才是信号**
  ——答案说出真机制 = 纠正了前提。0.97 说明 agent 在含错误前提时答得**和中性提问一样有据**。
- **结论**:§二十九 那 2 例苗头(case6/case2)放大到 11 例 × 跨 2 项目 × repeat 3 的可信尺子后,
  **不成立为普遍问题**。在更强 grounding 环境(deepseek + 工具齐全)agent 不顺错误前提编造。

**处置(守纪律,同 §三十一 rule9 / [[recall-weight-ab-finding]])**:
- **停 Phase B/C**,不建机制 —— validate-first 在此**省下 1–2 天建机制力气**,且因未动手故**无机制可回退**(比 rule9 更干净)。
- **数据集 + harness 改动保留**:22 例验证金标是回归资产,顺带补 Phase 0 "≥20 真实金标" gate;CI + cross_project 是通用增量。

## 四、元教训(本轮最大价值)

1. **validate-first 又一次兑现**:先证尺子准 + 比对照组,直接证伪一个看似真的课题,没掉进"建机制再回退"的坑。
2. **轶事 ≠ 普遍**:2 例(还 1 例曾标错)不足以立项做机制;扩到带 CI 的对照实验才敢下结论。
3. **指标要选对**:负样本 must_not 在 false-premise 场景失效(恒 0),grounding(说出真机制)才是"有没有纠正前提"的真信号。
4. **0.67 那例提醒**:agent 真实弱点更可能是**多跳深度 / max_steps 预算**,不是"顺前提" —— 下一个候选课题在这。

## commit 链
`da5ab08`(Phase A 数据集 + bootstrap CI + cross_project + 34 单测)→ 本日志 + plan 结案标注。
