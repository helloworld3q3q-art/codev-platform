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

## commit 链(2026-06-11 段)
`b346e3c`(flash daily-summary)→ WSL config 迁 flash + 重启 → `739269c`(硬集 16→25 codev 20)→ flash A/B n=20(planner 收口)→ `ff667e5`(§二十三 收口沉淀)→ `4a28f03`(impact_paths lane 修)→ `7b86997`(§二十四 多跳诊断沉淀)。记忆更新:[[phase7-llm-planner-and-e2e-eval]](收口)、[[recall-weight-ab-finding]](CI 精确化)。
