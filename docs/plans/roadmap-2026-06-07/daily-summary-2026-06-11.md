# daily-summary 2026-06-11 —— deepseek 迁 flash + planner 翻默认收口 + 多跳弱点诊断

> 承 [`daily-summary-2026-06-10.md`](daily-summary-2026-06-10.md) §十二~二十二(recall 金标扩集 + within_budget CI + 首轮 flash A/B)。本日把几条线**收口**,并诚实判定 eval 调优到平台期。
> 全程 git push `fuwuqi/dev` → WSL `origin` ff 真跑;commit 链 `b346e3c`→`7b86997`。

## 一、deepseek 模型选型 + 迁移(chat → v4-flash)
- 用户问平台用哪个 deepseek。查实:配 `deepseek-chat`,但 `/models` 现仅 `deepseek-v4-flash` / `deepseek-v4-pro`,官方 **2026/07/24 下线** chat/reasoner 旧名 —— 必须迁。
- 选型(官方规格 + 我们 eval 双驱动):两者 1M 上下文;flash $0.14/$0.28、pro $0.435/$0.87(输出约 3×);pro agentic/SimpleQA 更强,**flash 是 RAG/tool-calling 默认款**。因我们负载正是 RAG+工具调用 + grounding 已饱和(pro 推理红利无处发挥)→ **迁 `deepseek-v4-flash`**。pro 只按难度路由给多跳题([[agent-design-multi-model-first]])。
- 执行:WSL config `deepseek-chat → deepseek-v4-flash`(备份 `config.json.bak.predeepseekv4`),sudo 重启 `codev-agent` + `codev-web`(干净起,:8848)。
- **验证**:flash A/B grounding **1.0**(优于 chat 0.909,含的多跳 0.67 也补上)→ 迁移不仅守住质量、还更好,且便宜 ~3×。

## 二、planner LLM 翻默认 —— 双否证伪,收口
扩 agent_e2e 硬集 16→25(**codev 11→20**,+9 口语例 codegraph 核实,24/25 被 keyword 误判)后,flash 重跑 off/keyword/llm A/B:

| 变体 | grounding [CI95] | within_budget [CI95] |
|---|---|---|
| off | 0.9 [0.75, 1.0] | 0.7 [0.5, 0.9] |
| keyword | **1.0 [1.0, 1.0]** | 0.7 [0.5, 0.9] |
| llm | 0.925 [0.8, 1.0] | 0.7 [0.5, 0.9] |

- **n=11 的"效率红利"(llm within_budget 0.818 vs keyword 0.545)在 n=20 抹平成三者同 0.7 = 小样本噪声**。`within_budget_ci95`(本轮新增 harness)+ 扩 n 正是为挡这个,否则会据 5/11 vs 9/11 误翻默认。
- LLM planner 任何轴无优势、甚至略差(grounding keyword 1.0 ≥ llm 0.925)。
- **决策**:`planner_llm_enabled=False` 默认从"证据不足"升级为**"实测无益 + 略成本"强证据**;keyword planner ≥ llm 且零额外调用。**这条线收口**(同 [[recall-weight-ab-finding]] / [[anti-false-premise-phaseA-falsified]] 纪律:苗头放大到可信尺子后证伪)。

## 三、多跳 max_steps 弱点诊断 + planner lane 小修
- 先 scope 否了 Phase 4(社区检测核心已被 A1 95% 交付;naive 图聚类实测 58% 干不过 file 启发式 → 重做负 ROI)。
- quality 集 per-case 实证(flash):6 例 grounding 全 1.0,**仅 1 例**("LoopPolicy 加字段改哪几处")手爬 codegraph_callers 15 次超 max_steps、**从未用一次性多跳工具 `impact_paths`**(原不在 planner IMPACT lane)。**弱点轻:效率非正确性**。
- 修:`impact_paths` 补进 IMPACT lane(排 codegraph_callers 前)+ 单测(`4a28f03`)。
- **诚实边界**:`impact_paths` 走 graph store,只解**跨层多跳**(endpoint/table/page);**符号级多跳**(LoopPolicy 类不在 graph,search_nodes 0 命中)仍手爬 —— 该口子无一次性 codegraph 多跳工具,但轻(纯效率/答案已对),**不为其造重型**(同 Phase 4 纪律)。

## 四、阶段判断:eval/智能调优到平台期
本会话连续证伪 3+ 假设(recall _PREFER 调参 / planner LLM 质量+效率 / 抗错误前提)、grounding 在两模型两项目都饱和 → **agent 答案质量/检索智能层已成熟,继续在此层挖是边际递减**。下一步真正值钱的在别的轴(产品化 / 多仓契约桥),非 eval 微调。本轮"扩 golden 集让 recall/planner 有可信 CI 结论"目标达成且全部收口。

## 五、Phase 4 实测复核 + 按客户分档标注(8 视角面板 → 搁置 plan)
用户问"标注能否省钱/提效/可不用 deepseek,按客户需求"。
- **Phase 4 实测复核(纠前一日 §二十四 overstatement)**:真图谱跑社区检测——**连通分量退化成巨型簇**(codev 97%/openclaw 90% 一坨,确认 naive 没用),但 **Louvain 结构可行**(22/52 个合理社区,无巨型簇)。所以"Louvain 跑不动"是错的;否 Phase 4 的真实理由=A1 已 95% 覆盖业务域 + 无需求驱动。
- **8 视角面板裁决**:标注是云账单**小头**(loop 大 2-3 个数量级);hybrid 实测真省 ~46% 标注调用但**正确率不涨**(纯规则无置信门实测 **27% 静默错**);→ **现在不做**,唯一有产品意义的是免费/离线档纯规则器,留触发条件。沉淀 [`tiered-domain-labeler-plan-2026-06-11.md`](tiered-domain-labeler-plan-2026-06-11.md)(设计+置信门+golden 门+触发条件,commit `1a1e43a`)。
- **真省钱杠杆 = agent loop token**(prompt-cache 命中/recall·context 体量/per-档 caps),非标注。

## 六、flash 迁移收尾验证(无回归)
今天 chat→flash 后,验当前 codev 图谱标签(53min 前 @4a28f03 重建,flash 重标):**A2 arch_role 0.933**(14/15)+ A1 域名两仓干净(代码召回/审计/智能体… ‖ 选股推荐/回测持仓/龙虎榜…)。**flash 标注无质量回归。**

## 七、agent loop token 实测 + prompt 缓存可观测(真省钱入口,commit `e959630`)
面板共识"钱在 loop 不在标注",今天去量证实:
- **gap**:`OpenAICompatProvider` 只抓 prompt/completion tokens,**没抓 deepseek 的 prompt_cache_hit/miss** → 对缓存率瞎。修:抽 `_extract_usage`(deepseek 直给 / OpenAI 走 prompt_tokens_details.cached_tokens 两种都接)+ loop 汇总 cache_hit/miss + 4 单测。
- **真查询实测**(flash,多跳题,12 步):input **104,602** tokens / output 3,122 / **缓存命中 80.2%**(hit 83,840)→ 本次 $0.004,无缓存会 $0.0155,**缓存已省 74%**。
- **结论**:① loop 的稳定前缀结构让 deepseek 自动缓存吃满(80%),**无需"修缓存"**;② 真成本驱动是**单查询 10 万 input(12 步 × 重发增长上下文)**,下一杠杆=收紧 recall top-N(现 8)/按档限 max_steps/裁历史,但是**成本↔质量权衡,须 eval 量后再砍**;③ 一个 chat 查询($0.004)≈/> 整个 A1 标注重建 → **实证"钱在 loop 不在标注"**,标注分档优化是小头(印证 §五搁置)。

## 八、codegraph_trace 实现 + "省钱"假设被自己实测证伪(commit `5f2273d`)
§七数据 + 面板指向"省钱第一刀=符号级多跳工具",于是建了 `codegraph_trace`(沿 codegraph 边表 BFS,一次返回 N 层调用链,默认 3/上限 6/防环)+ 进 planner SYMBOL/IMPACT lane + 7 单测(32 passed)。
- **实测前后**(flash 5 类型):步数 45→38(−16%,impact 13→9/multihop 18→15)、input 309K→182K(−41%),**但总成本持平 $0.0088→$0.0089**。
- **证伪**:cost = miss+output,**信息量绑定非步数绑定**。input 降的是免费的 hit(缓存重发,占成本 4%);miss(真成本)= 调用链新信息量,只是从"18 小块"变"几大块",总量守恒 → 钱不降。
- **codegraph_trace 定位修正**:留用,价值=**延迟/吞吐(少往返,利 Phase 8)+ 多跳质量(不耗尽 max_steps)**,**非省钱**。详见 [`loop-cost-optimization-plan-2026-06-11.md`](loop-cost-optimization-plan-2026-06-11.md) §九。
- **元教训**:连数据面板都错在"把 input 当成本"(成本在 miss+output);真降成本只能动信息量(全是质量权衡)。先验证后定论再次兑现。

## commit 链(2026-06-11 段)
`b346e3c`(flash daily-summary)→ WSL config 迁 flash + 重启 → `739269c`(硬集 16→25 codev 20)→ flash A/B n=20(planner 收口)→ `ff667e5`(§二十三 收口沉淀)→ `4a28f03`(impact_paths lane 修)→ `7b86997`(§二十四 多跳诊断沉淀)。记忆更新:[[phase7-llm-planner-and-e2e-eval]](收口)、[[recall-weight-ab-finding]](CI 精确化)。
