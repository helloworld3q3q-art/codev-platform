# Plan — agent 抗错误前提幻觉(2026-06-10)

> **本文件是 plan, 不是实现。** 承 [`daily-summary-2026-06-09.md`](daily-summary-2026-06-09.md) §二十九~三十二
> (诊断难集发现 agent 顺错误前提幻觉 + 专家面板锁定为下一真课题)。
> **核心节奏: 先验证后建造。** 不重蹈 §三十一 rule9 "改善全来自修金标、机制零净效" 的混淆。

---

## 一、目标 / 非目标

**目标**: 让 agent 被问**含错误前提**的问题时, **不顺着编**, 而是从证据识别"前提为假"并纠正
("X 并不用 Y, 它实际是 Z")。把 §二十九 的苗头从**轶事**做成**可证伪、可测、可决策**。

**非目标**:
- 不靠 prompt 劝导(§三十一 rule9 "先验证前提" on/off A/B 已证**零净效**, 回退)。
- **不一上来建机制** —— 先证明这是不是真问题。
- 不追绝对幻觉率, 追**可比 + 带置信区间的可决策**。

## 二、诚实前提(证据评估, 必读)

当前支持"agent 顺前提幻觉"的证据 = **2 例, 其中 1 例金标曾标错**:
- case2(audit 夹带已删 `open_store`): 真, 但 1 例。
- case6(问"recall 用哪个向量库"→ 答 chroma): **平台文档检索真用 chroma**, agent 是把文档检索混进 `code_recall`, 非纯幻觉 —— **金标 premise 当时标错了**。

→ **2 例(1 例翻车)不足以证明普遍性。** 第一步必须是**验证**, 不是建造。守今天元教训: 小集 + 标错金标会把噪声当系统缺陷。

## 三、分期(validate-first)

### Phase A —— 验证 + 标定(必做; 可能 A 完即停)

**目标**: 用**可信尺子**证明 / 证伪"agent 顺错误前提幻觉"是真问题。

- **A1 造真·错误前提集** `eval/datasets/agent_e2e_false_premise.jsonl`(10–15 例), 铁律:
  - premise **必须真为假** —— 每例人工核对"问题的预设在代码里确实不成立"(不像 case6 自己标错)。
  - 三类: ① 不存在的能力("code_recall 用哪个向量库")② 已删/改的旧物("audit 还用 open_store 吗")③ 错误归因("planner 失败会抛异常吗")。
  - 靠 **must_mention 真机制**判(agent 有没有说出真相); **禁用 must_not 子串**(§三十 已证子串分不清肯定/否定)。
  - 跨 **≥2 项目**(codev-platform + openclaw-stock)防单仓过拟合。
- **A2 对照组**: 同实体但**前提为真**的正常问法(如"code_recall 内部怎么融合的"), 确认 agent 正常时答得对 —— 隔离"前提为假"这单一变量。
- **A3 跑基线**: 用**已交付**的 `--repeat N` + `--judge-provider`(非自评 judge)跑 false-premise 集 vs 对照组, 报均值 + min/max + **置信区间**(n 小, CI 比点估诚实)。

**Gate A**(决定走不走 Phase B):
- false-premise 集 grounding **显著低于**对照组(CI 不重叠)→ 确证真问题 → 进 Phase B。
- 没差 / CI 重叠 → **停, 放掉这一轮**(agent 其实不顺前提编), 记录证伪。**省 Phase B/C 的力气。**

### Phase B —— 机制(仅 Gate A 通过才做)

**目标**: 让 agent 从**证据**就知前提为假, 而非脑补。**代码护栏 / 工具结果, 非 prompt**(agent-provider §4)。

- **B1 工具结果带"能力边界"元信息**: `code_recall`(及 codegraph/graph 工具)返回时声明覆盖范围
  (如 code_recall: "融 graph+codegraph 符号; **不含**向量 / 文档语义检索")。agent 据此能识别
  "问的东西不在我的证据范围" → 答"本工具不覆盖, 据现有证据 X 并不使用 Y"。
- **B2 新旧混淆护栏**(治 case2): 回答前对"问题预设的关键实体"强制**回读真实文件确认现存**
  (workflow §4 对人类 agent 已立此条; 落进 loop 硬护栏, 非靠 agent 自觉)。
- **B3(可选)** weighted_rrf 未用的 `boosts`: 精确符号 boost(IR 面板提的最便宜 recall 真增益), 顺带做。
- **多模型**: 机制走工具结果 / loop 护栏, 不为单模型 prompt 硬编。

### Phase C —— measure + 决策

- 在 Phase A 的集上做 **B 机制 on/off A/B**(repeat + 非自评 judge, 带 CI)。
- **Gate C**: grounding 提升 + hallucination 下降 + **CI 不重叠** → 上线(机制默认开); 否则**回退**(像 rule9)+ 记录证伪。

## 四、纪律红线(不重蹈 rule9)

1. **先证尺子准再下结论**: 金标 premise 真为假 + 非自评 judge + 置信区间。
2. **零 delta / CI 重叠的机制不留**: 回退 + 记录证伪([[recall-weight-ab-finding]])。
3. **机制用代码护栏 / 工具结果, 不用 prompt 劝导**(§三十一 已证 prompt 无效)。
4. **小集只当趋势**; 默认开某档需更大集 + CI。

## 五、工作量

| Phase | 估时 | 备注 |
|---|---|---|
| A 验证 + 标定 | 1–1.5 天 | **最有价值**; 可能直接证伪 → 省后续 |
| B 机制 | 1–2 天 | 仅 Gate A 通过 |
| C measure + 决策 | 0.5 天 | A/B + 上线/回退 |

合计 2.5–4 天, 但 **A 完若证伪即停**。

## 六、验收

- 全程**可证伪、带 CI**; 每步 commit + 日志 + WSL 真跑。
- 终态二选一: 机制**实证有效 → 上线**, 或**诚实证伪 → 放掉**(像 rule9, 不留无效文案 / 机制)。
- 顺带补齐 Phase 0 gate("≥20 真实金标")的一块: false-premise 集 + 对照组本身就是更扎实的 e2e 金标。

## 七、本 plan 不含

实现代码、judge 模型选型(需确认 WSL 有非 deepseek 的 provider key)、具体案例内容 —— 留 Phase A 实现轮。
