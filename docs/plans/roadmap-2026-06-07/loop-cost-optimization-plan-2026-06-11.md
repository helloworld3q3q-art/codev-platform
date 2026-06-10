# Plan — agent loop 成本优化(数据驱动,2026-06-11)

> **状态:可做(下一轮主线候选)。** 不同于 tiered-labeler(搁置),这是**云账单大头**且有实测数据 + 质量双赢支撑。
> 承 daily-summary-2026-06-11 §七(loop token 实测)+ 2 视角数据面板。**铁律:每刀都先 A/B + CI 验证再上,不拍脑袋。**

---

## 一、为什么是这里(钱在 loop)

实测(flash,5 类型代表查询,prompt 缓存可观测已落地 `e959630`):

| 类型 | 步数 | input | 成本 | 成本构成 |
|---|---|---|---|---|
| symbol/overview/doc_rule | 4-6 | 13-14K | $0.0007-0.001 | — |
| impact | 13 | 98K | $0.0025 | — |
| multihop | 18 | 170K | $0.0037 | — |
| **合计 5 例** | 45 | 309K | $0.0088 | **miss 67% + output 25% + hit 9%** |

- 一个 chat 查询($0.001-0.004)≈/> 整个 A1 标注重建 → **标注是小头,loop 是大头**(标注分档优化已据此搁置)。
- 缓存已自动命中 **86%**(loop 稳定前缀结构有效),**无需"修缓存"**。

## 二、成本结构(数据钉死,跨 5 类型稳定)

- `miss 65-73% + output 23-27% + hit 6-9%`。**miss 和 output 都随步数涨**;hit(system+tools+recall 稳定前缀)只占 ~9%。
- **成本随步数超线性**:4 步 ~3.3K/步 → 18 步 ~9.5K/步(每步重发累积上下文)。
- **成本集中在多步查询**:impact(13)+multihop(18)吃总成本 70%。

## 三、杠杆排序(按实测 $ 收益)

| 杠杆 | 动 | 省 | 质量风险 | 备注 |
|---|---|---|---|---|
| **① 符号级一次性多跳工具** | miss 主项 | **~60-70%**(18→2-3 步) | **无** | 双赢:顺带修多跳 max_steps 弱点 |
| **② per-档 max_steps 上限** | miss+out 封顶 | ~33% 上限 | 低(budget 已软停) | 接"按客户分档" |
| ③ 裁历史/旧 tool-result | miss | 中 | 中(破缓存连续性可能反伤 hit) | 谨慎 |
| ❌ 砍 recall_limit | hit | **≈0** | **反效果**(召回差→多绕步→净亏) | 陷阱,别做 |
| ❌ 压 system/tools | hit | ~5% | 白费 | — |

## 四、第一刀:符号级一次性多跳工具(最大单点 + 双赢)

**问题**:multihop/impact 题手爬 `codegraph_callers`/`callees` 逐跳,18 步烧 170K token。`impact_paths` 只覆盖跨层 graph(endpoint/table/page),**符号级(函数/类的多层调用链)无一次性工具**(见 [[call-resolver-blind-spot-principle]] / 多跳诊断,daily-summary-2026-06-10 §二十四)。

**做法**:加一个 codegraph 多跳工具(如 `codegraph_trace`:给符号一次返回 N 层 callers/callees 链),让 agent 一两跳拿到整条链,而非逐跳爬。
- 省钱:砍 cache-miss 主项 ~60-70%(18 步→2-3 步)。
- 提质量:多跳题不再耗尽 max_steps(grounding 那个 0.67 例)。
- 多模型:工具是中性能力,不为单模型硬编。

## 五、第二刀:per-档 max_steps(接"按客户分档")

走 `LoopPolicy`/`ProviderSpec` 字段 + `registry.loop_policy(provider, tier)` 加 tier 维度(**不在 loop 加分支**,[[code-quality-principles]] / [[agent-design-multi-model-first]]):
- 免费档:max_steps=6 + 紧 budget + `planner_hard_cap_readonly` 开 + 只读硬封顶。
- 付费档:max_steps=12 + 软停 + 强模型免 planner。
- **成本绑定档位** + per-租户用量计量(cache_hit/miss 已可观测,是计量地基)。

## 六、危险动作(别做)

- 不补多跳工具就砍 max_steps → 多跳题手爬到一半被切,grounding 崩。
- 收太狠 `retrieval_distinct_cap`/`no_progress_limit` → 多跳合法多次调 callers 被误拦。
- 缩 IMPACT budget(8→4)→ 跨层反向链覆盖不到。

## 七、验证(强制,A/B + CI,反拍脑袋)

量尺已就位:`cache_hit/miss_tokens`(provider+loop)+ agent_e2e(grounding/within_budget bootstrap CI)。

A(现状 max_steps=12)vs B(补多跳工具 / 降档),同金标集跑,bootstrap CI 比:
- **grounding 非劣**:B 的 CI 下界 ≥ A 下界(证伪条件:B 下界 < A → 拒绝/回退)。
- **cache_miss_tokens / steps 显著降** = 真省钱。
- **within_budget 不降**。
- **多跳例单列子集**(整体集稀释多跳信号)。
判定:grounding 重叠 + miss 显著降 → 采纳;grounding 掉 → 回退记录证伪([[recall-weight-ab-finding]] 纪律)。

## 八、最小改动方向(落地时细化)

- 新增 codegraph 多跳工具(`agent/tools/` + codegraph 集成;复用现有 codegraph_callers 的多层 BFS)。
- `registry.loop_policy` 加 tier 维度 + per-档 LoopPolicy 值;per-租户 tier 存 meta.json。
- eval:agent_e2e 加多跳子集标记 + 跑 A/B 脚本(`measure_loop_multi.py` 已是成本量尺雏形)。

## 关联

- 数据:daily-summary-2026-06-11 §七 + `measure_loop_usage.py` / `measure_loop_multi.py`(成本量尺)。
- 多跳弱点诊断:daily-summary-2026-06-10 §二十四(impact_paths 只覆盖跨层,符号级多跳无一次性工具)。
- 记忆:[[call-resolver-blind-spot-principle]]、[[agent-design-multi-model-first]]、[[code-quality-principles]]、[[recall-weight-ab-finding]]。
