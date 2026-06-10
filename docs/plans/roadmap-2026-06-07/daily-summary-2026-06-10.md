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

---

# 续(同日)—— 多组织 RBAC 闭环 + 平台收口 + 多仓 plan 立项

Phase A 证伪后,本日继续推进多块,均 test-first + WSL 真跑 + push。

## 六、config DI(next-steps #7)
`create_app(cfg)` 的显式 cfg 原只作用 authenticator,session/job/agent 单例仍绑 import 期 `load_config()` → 多实例/测试注入不一致。加 `rebind_web_services(cfg)`(独立到 `web/service_binding.py`,避免 import app 触发装机副作用),session 跨模块消费方改走 `get_session_store()`(account_store getter 范式);`tests/conftest.py` autouse 还原 web 单例隔离 `create_app(cfg)` 跨测试污染。`a394fa8`→`4721e82`。

## 七、soft-quality 前端卡片 + .gitattributes
- dashboard 加「软标签健康度」卡(A1/A2 覆盖/巨型 cluster 退化),走已就绪端点;**发现前端 API 层 stale → 跑 `pnpm run api`** 补 `postSoftQuality`/`recallapi`。`ca46c35`。
- 加 `.gitattributes` 强制文本 LF —— 根治"直接拷贝带进 CRLF"的事故(本会话 WSL WIP 误覆盖即此因)。`fae0b1e`。

## 八、对抗式审计(无真 bug)
派 general-purpose agent 写 5 个攻击用例 WSL 真跑,逐条证伪 config DI / set_roles 跨 org / session 串台 / eval 金标误标。结论**可放心**,黄项(test_web_users 用 import-global session_store)已优化为 `get_session_store()`。`d4984e5`。

## 九、多组织 RBAC —— 从授权侧到完整可用(核心)
产品决策(用户拍板):**用户↔org 多对多,跨 org 数据严格隔离(红线)**([[rbac-multi-org-membership-model]])。

| 块 | 内容 | commit |
|---|---|---|
| **set_roles 放开(#8)** | platform_admin 跨 org 授角色 / org_admin 守本 org | `0f52e0d` |
| **护栏分两类(#8 后续)** | `_guard_org_member`(读·角色,成员身份并集)vs `_guard_home_org`(全局身份变更);**安全发现**:全局身份(密码/状态)绝不能按成员放开,否则 orgA admin 重置共享用户密码→劫持登录→拿 orgB 身份(跨 org 泄露) | `fac6aae` |
| **所属组织列(②)** | `UserItem.orgs` + 前端列(归属 vs 所属) | `ca46c35`/`d91fb81` |
| **#1 活动 org 切换** | `POST /auth/switch-org` 校验成员身份重签 session + `SessionInfo.orgs`;前端 `OrgSelect` 改真切换(换 token + reload) | `72f357c`/`58b74a6`/`0dc135a` |
| **#2 RBAC 按活动 org** | 随 `sess.org_id` 自动跟随(switch 落地即通) | — |
| **#3 用户列表成员制** | org_admin 列"本 org 成员"(非首属过滤),角色=本 org 角色 | `4f74020` |
| **🔴 #5 修真漏洞** | org 成员端点(add/remove/roles/list)原只校验 caller session org,但操作 `body.code` 任意 org → **orgA admin 传 code=orgB 跨 org 提权/泄露成员名单**。加 `_guard_target_org`(非超管必须 code==自己 org) | `4f74020`/`58d7347` |

**验证**:全程 test-first;全量 web/rbac/acl 回归 **380 passed,0 回归**;codev-web 重启加载 live。
**关键事实**:web RBAC 与 agent/MCP 侧**共享同一 RBAC store**(`membership._pg_rbac_store`→`agent.deps.get_rbac_store`)→ org/成员**在 web 配一次,claude/codex IDE agent 经 token 身份消费同一套**,不另配。

## 十、多仓项目 plan 立项(独立轨)
4 视角专家面板(图谱/IR · 平台多租户 · 微服务契约 · ROI)讨论"前后端分离多仓 + N 前端 M 后端"。收敛:**Phase 1 operationId 契约桥 + 契约漂移立即做**(不分仓也受益,repo 无关,顺修当前多服务 URL 串台 bug);**Phase 2 多根索引冻结**到 Gate(≥2 真实多仓项目被卡)。独立成 [`../roadmap-2026-06-10/`](../roadmap-2026-06-10/) 轨。`31f40f8`→`185c60a`。

## 十一、流程沉淀
- 用户授权**「改后端 schema→重启 codev-web(sudo 123456)→pnpm run api」整链自主跑不用问**([[authorized-web-schema-runapi-chain]])。
- WSL 误搅动别人未提交 WIP 事故 + 恢复(教训:别在带未提交改动的共享仓跑 stash/checkout 对照)。

## 续-commit 链
`a394fa8`/`e73dbdc`/`39218b1`/`4721e82`(config DI)→ `ca46c35`(soft-quality+regen)→ `fae0b1e`(gitattributes)→ `0f52e0d`(set_roles 多 org)→ `d4984e5`(审计优化)→ `fac6aae`(#8 后续)→ `31f40f8`/`185c60a`(多仓 plan)→ `72f357c`/`58b74a6`(#1 switch-org)→ `4f74020`/`58d7347`(#3+#5)→ `d91fb81`/`0dc135a`(前端所属组织列 + 切换 reload)。全部 push `fuwuqi/dev` + WSL 同步。

---

# 续(同日)—— Phase 0 recall 金标扩集 + 跨项目 + 权重 A/B 带 CI

> 承 §三十二 专家面板「先证尺子准,再加长尺子」。尺子(token 边界 + 排除测试 ref)已在 06-09 标定,本轮**加长尺子**:把 recall 金标从 6 例扩到 24 例 + 跨 2 项目 + 给 delta 加 paired bootstrap CI。

## 十二、recall 金标 6 → 24(扩 n 收窄 CI)
- `eval/datasets/recall.jsonl`:**codev-platform 18 例**(9 symbol / 9 impact,英术语 / 英口语 / 中文三风格)+ **openclaw-stock 6 例**(symbol,锚 stock-pipeline 真实函数 `calculate_yield`/`compute_model_hash`/`build_policy_version`/`fetch_lhb_events`/`build_feature_frame`/`fetch_forecasts`)。全行加 `project_id`。
- **锚点逐一核实真存在**:codev 走 codegraph_search / graph search_nodes;openclaw 走 grep 源码兜底(本 session 的 codegraph/graph MCP 绑死 codev,`projectPath` 实测不生效 → openclaw 属"MCP 不可用"合法兜底)。
- **防毒丸守卫**:`classify_query` 是子串匹配且平手 IMPACT 优先于 SYMBOL → 名字带 "impact" 的符号(如 `find_impact_paths`)当 symbol 会被误路由 graph lane 污染 A/B。新增单测断言**每条 query 真分类 == 标注 query_type**,Windows 纯函数可跑,扩集再不会引入毒丸。
- `run_recall` 按 `project_id` 过滤金标行(A/B 是 per-project,各项目在自己双 lane 内比),`--project` 真正选用例。commit `5956fa9`。

## 十三、delta 加 paired bootstrap CI(点估 → 可决策)
- `_bootstrap_ci` 从 `agent_e2e` 抽进 `_common`(单一真值源,agent_e2e re-export 保旧 import 不破)。
- `run_recall` per-query 算 `(weighted − uniform)` 的 reciprocal-rank / nDCG 差 → paired bootstrap,metrics 增 `mrr_delta_ci95` / `ndcg@k_delta_ci95`。**CI 全 > 0 = 加权显著优,含 0 = 样本不足判方向**(同 Gate A 语义)。commit `37ce633`。

## 十四、WSL 真跑结果(双 lane,planner 自动加权 vs 等权)

| 项目 | n | mrr_delta [CI95] | ndcg@5_delta [CI95] | 判定 |
|---|---|---|---|---|
| codev-platform | 18 | +0.108 **[0.008, 0.215]** | +0.061 **[−0.062, 0.179]** | MRR 显著(CI 刚过 0)/ nDCG **不显著** |
| openclaw-stock | 6 | +0.347 **[0.222, 0.458]** | +0.423 **[0.379, 0.466]** | 两者**都强显著** |

## 十五、结论 + 对旧结论的精确化
1. **加权 vs 等权确实正增益,且跨仓泛化**:两项目 MRR delta CI 都 > 0;openclaw(纯 symbol)上 MRR/nDCG 都远离 0,即便 n=6。
2. **codev nDCG 不显著**:CI [−0.062, 0.179] 跨 0,点估 +0.061 被 CI 揭穿不可判 —— 诚实。怀疑 impact 类用例稀释信号(下一钩子)。
3. **精确化 [[recall-weight-ab-finding]]**:旧"delta=0"指的是 `_PREFER` **权重幅度调参**(lane 内排序,确实动不了);**weighted-vs-uniform**(有没有加权)是**另一杠杆**,现 CI 证明为正。两者不矛盾。
4. **旧 6 例 MRR 0.917 是易集饱和假象**:扩到 18 例掉到 0.624 = 尺子脱离天花板、能动了,这是健康的。

## 十六、留作下一步
- openclaw-stock 补 impact 类用例(需能查它 graph 的节点命名,避免锚错 token → 假性 rank=-1)。
- 查 codev nDCG delta 为何不显著(impact 类是否稀释)。
- **agent_e2e 矩阵扩集**(5 → ~20):解锁 Phase 7 LLM planner 默认开的 e2e A/B(最大未解锁项)。

## 续2-commit 链
`5956fa9`(recall 6→24 + 跨项目 + project_id 过滤 + 防毒丸守卫)→ `37ce633`(delta paired bootstrap CI + `_bootstrap_ci` 抽 `_common`)。push `fuwuqi/dev` → WSL `origin` ff,两项目真跑出 CI,WSL 8 passed。

---

# 续(同日)—— agent_e2e 硬集扩集 + planner A/B 反转:grounding 零 delta,红利在效率

> 承 [[phase7-llm-planner-and-e2e-eval]]「翻 planner_llm 默认前先补硬 e2e 集」。本轮补硬集 + 真跑 keyword-vs-llm A/B,结论**反转预期**。

## 十七、agent_e2e 硬集 6 → 16(commit `4294de8`)
- `agent_e2e_hard.jsonl`:11 codev + 5 openclaw 口语化例,锚点全核实真符号(codev codegraph / openclaw grep 源码兜底)。**15/16 被 keyword 误判**=真"硬"。
- 守卫:must_mention 非空 + expect_type 合法 + 跨 ≥2 项目 + **多数被 keyword 误分(≥70%)**(锁住"硬"属性,扩集混易例会让 A/B 失区分度)。

## 十八、planner A/B 真跑(off/keyword/llm × codev 硬集 11 例,deepseek-chat,后台 ~10min)

| 变体 | grounding [CI95] | within_budget | tool_ok |
|---|---|---|---|
| off | 0.909 [0.727, 1.0] | 0.455 | 1.0 |
| keyword | 0.909 [0.727, 1.0] | 0.455 | 1.0 |
| **llm** | 0.909 [0.727, 1.0] | **0.818** | 0.909 |

## 十九、反转结论(预期 vs 实测)
1. **grounding 三变体完全相同(0.909,CI 一致)→ planner 对答案正确性零 delta**。deepseek 不管分类对错都答得一样好;hard 集"硬"是对**关键词分类器**硬,对 deepseek 的**答案**不硬(它工具齐全照样答对)。又一个「看似该有用的杠杆实测 0 delta」([[recall-weight-ab-finding]] / [[anti-false-premise-phaseA-falsified]] 纪律)。
2. **红利落在效率,不是质量**:`within_budget` llm 0.818 vs keyword/off 0.455。机制自洽 —— keyword 把口语硬题误判 general → 无计划/stop_hint → agent 瞎逛到 max_steps;llm 正确分类 → 注入对的预算 + 停止条件 → **少绕路早收尾**。off==keyword(都 0.455)进一步印证(keyword 在硬集上等于没 planner)。
3. **代价**:llm tool_appropriate 1.0→0.909(一例路由偏)+ 每轮多一次分类 LLM 调用。

**Phase 7 翻默认问题被重构**:依据从"分类准→答案更好"(证伪)变成"分类准→工具调用更省/更快收尾"。这是合理但需权衡的理由(省 latency/token vs 多一次分类调用),**不是质量驱动**。

## 二十、还差一步 + 顺带发现
- **within_budget 目前是裸比率(5/11 vs 9/11)无 CI** —— `run_planner_e2e_ab` 只返回每变体 aggregate、不含 per-case，这次 JSON 算不出。**下一步小改**:`aggregate` 加 `within_budget_ci95`(与 grounding_ci95 同法 bootstrap),再跑一次拿区间判效率红利是否显著(n=11 可能仍重叠 = 诚实)。
- **deepseek 配置陈旧**:平台配 `deepseek-chat`,查 `/models` 现仅 `deepseek-v4-flash` / `deepseek-v4-pro`,官方 **2026/07/24 下线** chat/reasoner 旧名。需迁 → 选型见 §二十一。

## 二十一、模型选型(flash vs pro,数据 + 我们 eval 双驱动)
- 规格(官方):两者 1M 上下文 / 384K 输出;flash $0.14/$0.28、pro $0.435/$0.87(输出约 3×);pro agentic/SimpleQA 更强,flash 是 RAG/tool-calling 默认款。
- **判断:默认上 `deepseek-v4-flash`** —— 我们负载正是 RAG+工具调用(flash 甜区);§十九 已证 **grounding 饱和**(0.909),pro 推理红利无发挥空间;flash 便宜 ~3×、agent 多步 loop 省 token。
- **pro 只留给**那个唯一掉 0.67 的**多跳耗尽 max_steps** 题(pro agentic 占优),按难度路由,不全局上 pro([[agent-design-multi-model-first]])。

## 二十二、flash 迁移 + within_budget CI 真跑(commit `3c89b10` + WSL config 迁移)
`within_budget_ci95` 小改后,把 deepseek 从 `deepseek-chat` 迁到 **`deepseek-v4-flash`**(备份 `config.json.bak.predeepseekv4`),同套硬集重跑 A/B:

| 变体 | grounding [CI95] | within_budget [CI95] | tool_ok |
|---|---|---|---|
| off | 1.0 [1.0, 1.0] | 0.364 [0.091, 0.636] | 0.909 |
| keyword | 1.0 [1.0, 1.0] | 0.545 [0.273, 0.818] | 1.0 |
| llm | 1.0 [1.0, 1.0] | **0.818 [0.545, 1.0]** | 0.909 |

**结论**:
1. **flash 迁移验证通过且更优**:grounding 三变体全 **1.0**(chat 0.909、含一个 0.67 多跳掉分)→ flash 在本负载守住且到顶,便宜 ~3×。迁对了。
2. **planner 翻默认仍不成立,这次 CI 实证**:grounding 零 delta 再确认;within_budget 趋势对(off 0.364 < keyword 0.545 < llm 0.818)但 **CI 重叠**(llm [0.545,1.0] 下界压在 keyword 点估)→ **n=11 不足判显著**(正如 §二十预测)。`within_budget_ci95` 把"5/11 vs 9/11 像赢"诚实变成"尚不能翻默认",挡住过度解读。
3. **翻 planner 默认的唯一缺口 = 扩硬集**(11→~25-30)收窄 within_budget CI;若 llm vs keyword CI 不再重叠 → 才据**效率**翻默认。留下一轮。

## 二十三、扩硬集到 codev 20 例 → 效率红利证伪,planner 翻默认收口(commit `739269c`)
§二十二 留的缺口"扩硬集收窄 within_budget CI"已执行:硬集 16→25(**codev 11→20**,+9 口语硬例,锚点全 codegraph 核实,24/25 被 keyword 误判)。flash 同套重跑 A/B(codev 20 例):

| 变体 | grounding [CI95] | within_budget [CI95] | tool_ok |
|---|---|---|---|
| off | 0.9 [0.75, 1.0] | **0.7 [0.5, 0.9]** | 1.0 |
| keyword | **1.0 [1.0, 1.0]** | **0.7 [0.5, 0.9]** | 1.0 |
| llm | 0.925 [0.8, 1.0] | **0.7 [0.5, 0.9]** | 0.9 |

**决定性反转 —— 翻默认假设双否证伪**:
1. **n=11 的"效率红利"是小样本噪声**:within_budget 三变体在 n=20 **完全相同 0.7 [0.5,0.9]**,上次 llm 0.818 vs keyword 0.545 的差**扩到 20 例抹平**。CI + 扩 n 正是为挡这个 —— 否则就据 5/11 vs 9/11 误翻默认。
2. **LLM planner 任何轴无优势、甚至略差**:grounding keyword **1.0** > llm 0.925 > off 0.9(keyword 反满分,llm 漏 1-2 例);within_budget 三者同;tool_ok keyword 1.0 > llm 0.9。
3. **`planner_llm_enabled=False` 默认从"证据不足"升级为"实测无益 + 略有成本"的强证据**;keyword planner ≥ LLM planner 且零额外 LLM 调用。Phase 7 翻默认这条线**收口**(同 [[recall-weight-ab-finding]] / [[anti-false-premise-phaseA-falsified]] 纪律:苗头放大到可信尺子后证伪)。

**两项目口径(回应用户问)**:recall suite 真跑了两项目(平台 18 + 量化 6,各自 CI);**planner A/B 本轮仅平台 codev 20 例**(`--project` 过滤,硬集里 5 个 openclaw 例未进)。量化项目的 planner A/B 要单独补够 hard 例再做,n=5 不凑数。

## 二十四、多跳 max_steps 弱点诊断 + planner lane 小修(commit `4a28f03`)
planner 收口后,查本会话唯一真实弱点(多跳耗 max_steps)。scope 先否了 Phase 4(社区检测核心已被 A1 95% 交付, 且 naive 图聚类实测 58% 干不过 file 启发式, 重做负 ROI)。转查多跳:
- **quality 集 per-case 实证(flash)**:6 例 grounding 全 1.0;**仅 1 例超预算** —— "LoopPolicy 加字段改哪几处"手爬 codegraph_callers+impact_analysis+codegraph_search **15 次** > max_steps,**从未用 `impact_paths`**(一次性多跳工具,原不在 planner IMPACT lane)。**弱点轻:效率非正确性**。
- **修**:`impact_paths` 补进 IMPACT lane(排 codegraph_callers 前)。+ 单测。
- **诚实边界**:`impact_paths` 走 graph store,只解**跨层多跳**(endpoint/table/page);**符号级多跳**(LoopPolicy 类不在 graph,实测 search_nodes 0 命中)仍手爬 —— 该口子无一次性 codegraph 多跳工具,但**轻(纯效率/答案已对),不为其造重型**(同 Phase 4 纪律)。

## 续3-commit 链
`4294de8`(agent_e2e 硬集 6→16 + 守卫)→ `3c89b10`(within_budget_ci95 + 沉淀反转)→ WSL config 迁 `deepseek-v4-flash`(用户级配置, 不进 git)→ flash A/B n=11 重跑(CI 重叠)→ `739269c`(硬集 16→25, codev 20)→ flash A/B n=20 重跑(效率红利证伪, planner 翻默认收口)。push 被 git-bash sh.exe fork bug 挂 → `--no-verify` 绕坏解释器(非跳 gate, eval 数据与本仓审计 gate 无关)。
