# Plan — agent loop 成本优化(数据驱动,2026-06-11)

> **状态:部分实施 + 关键假设被证伪(2026-06-11 当日)。** codegraph_trace 已建并实测(commit `5f2273d`)。
> **⚠️ 重大更正**:实测推翻"多跳工具省 60-70% 成本"——**成本是信息量(miss+output)绑定,非步数绑定**;
> codegraph_trace 砍步数 16% / input 41%,但**总成本持平**($0.0088→$0.0089),因省的是免费的缓存重发(hit),
> miss(真成本)信息量守恒未降。**codegraph_trace 定位改为"延迟+多跳质量",非省钱工具。** 详见 §九 实测复核。
> 承 daily-summary-2026-06-11 §七~八。**铁律:每刀都先 A/B + CI 验证再上,不拍脑袋——本 plan 自身的 #1 假设即被自己的实测证伪。**

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
| **① 符号级一次性多跳工具** | ~~miss 主项~~ **证伪**(见 §九) | ~~~60-70%~~ **成本持平** | **无** | 已建(`5f2273d`);真收益=延迟+多跳质量,**非省钱** |
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

## 九、实测复核(2026-06-11,codegraph_trace 上线 A/B-lite,证伪 #1 省钱假设)

codegraph_trace 建好后同套量尺前后对比(flash,5 类型各 1 例):

| 指标 | 前 | 后 | 变化 |
|---|---|---|---|
| 总步数 | 45 | 38 | −16%(impact 13→9 / multihop 18→15) |
| 总 input | 309K | 182K | **−41%** |
| 总 hit | 267K | 137K | −49%(缓存重发少了) |
| 总 miss | 42K | 45K | **持平/略升** |
| 总 output | 7.7K | 7.8K | 持平 |
| **总成本** | **$0.0088** | **$0.0089** | **持平** |

**结论(证伪)**:步数/input 真降,但**成本没降**。根因:cost = miss + output,是**信息量绑定**非步数绑定。
- input 降的 41% 主要是 hit(被缓存的前缀重发,占成本仅 4%)→ 省它省不到钱。
- miss(真成本)= 调用链的新信息量,codegraph_trace 只是把它从"18 小块"变"几大块",**总量守恒** → 钱不降。
- 故"砍步数=省钱"错;**真降成本只能动信息量**(terser 工具输出 / 小 recall / 压 context),全是质量权衡。

**codegraph_trace 定位修正**:留用,价值在 **① 延迟/吞吐(少 LLM 往返,利 Phase 8 响应性能)② 多跳质量(不耗尽 max_steps)**,**不是省钱**。**grounding 非劣 ✅ 已验证**(quality + hard codev 两集 trace 上线后 grounding 均 1.0 保持,quality within_budget 0.833→1.0 改善;LoopPolicy 多跳例 15 步超预算→8 步内预算且 grounding 1.0)。

**元教训**:本 plan 自己的 #1 假设("多跳工具省 60-70%")被自己的实测证伪——再次印证"先验证后下定论",连数据面板的推理都可能错在"把 input 当成本"(实际成本在 miss+output)。

## 十、miss/output 真降成本(3 视角面板,2026-06-11)

§九 证伪"砍步数省钱"后,面板分析"真省钱只能砍 miss/output 信息量,但是质量权衡"。结论分三档:

**① 免费 win(零质量损失,免 A/B,已做 `8de5c29`)**:工具返回 JSON 去 `indent=2` → 紧凑 `separators`。缩进/换行是**纯格式零信息**,模型证据一字不差 → grounding 不可能掉。砍 recall/impact/codegraph 这类 tool-result 的 ~15-20% token。5 处(recall/impact/codegraph×3)。

**② 真杠杆但需 A/B**:`read_file` 实测 `_MAX_BYTES=60000`≈15K token/次、**返整文件前 60KB 且无 offset/分窗**(读 600 行只用 30 行=最大浪费 + 产品缺口)。修=加 offset/窗口 + 收紧 cap。**风险**:窗口切坏→agent 续取→反增 miss/步数。A/B:grounding 非劣下界 + 监控 read_file 调用数/步数,多跳/读码例单列。**中等工程,留触发**。

**③ 否决**:output 简洁化(prompt 促简短→省略证据链,grounding 掉,比啰嗦危险)、历史裁剪(改写缓存前缀→hit 暴跌成 miss 反贵 + 丢上下文,双输)。

**战略裁决(ROI 专家)**:**per-query trim 不是主云成本杠杆**——量级仅 1-10% 且依赖"有浪费"。真杠杆 = **per-租户计量+配额**(限用量不碰质量,风险趋零 + 解决计费归属)。**但这是后期运营开发**(用户定),先做基础(免费 win 已做,read_file 留触发)。

## 关联

- 数据:daily-summary-2026-06-11 §七 + `measure_loop_usage.py` / `measure_loop_multi.py`(成本量尺)。
- 多跳弱点诊断:daily-summary-2026-06-10 §二十四(impact_paths 只覆盖跨层,符号级多跳无一次性工具)。
- 记忆:[[call-resolver-blind-spot-principle]]、[[agent-design-multi-model-first]]、[[code-quality-principles]]、[[recall-weight-ab-finding]]。
