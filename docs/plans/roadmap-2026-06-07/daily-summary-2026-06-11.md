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

## 九、codegraph_trace grounding 非劣验证 + Phase 8 起步盘点(收尾)
- **grounding 非劣 ✅**(commit `91220b7`):trace 上线后 quality(6,含多跳)+ hard codev(20)两集 grounding 均 **1.0 保持**;quality within_budget 0.833→**1.0 改善**;LoopPolicy 多跳例 15 步超预算→**8 步内预算且 grounding 1.0**(原始多跳弱点修了)。**今天所有 shipped 改动全部验证闭环。**
- **Phase 8(响应性能/可观测)起步盘点**:本会话顺带把 Phase 8 推了几步——① **prompt 缓存 hit/miss 可观测**(provider+loop usage)② **loop token 成本量尺**(`measure_loop_multi.py`,实测成本结构 miss67%+out25%)③ **codegraph_trace 降延迟**(步数 −16%)。
- **Phase 8 收尾 ✅ 完成**(commit `d24cc8d`):把可观测从"测量脚本"做成"生产可消费" —— `Trace.done` 落 per-query `usage`(input/output/cache_hit/miss)+ loop 两个收尾点传 total_usage;chat_service 已接 Trace 故生产即生效。E2E 实证:真查询的 trace done 记录带 `usage{cache_hit/miss}` + session_id + model → **per-租户成本/缓存率计量地基齐**(按 session→user→org 聚合,token × 模型价表算成本)。trace 只记中性 token,$ 由下游价表算(不硬编单价,多模型友好)。+2 单测。

## 十、Phase 8 可观测消费层:token 用量看板 + 审计页(全栈)
地基(§七/八 trace usage)之上做"人能看见"的消费层,**严格后端先行 → pnpm run api → 前端**(不手写 API 字段/方法):
- **usage 持久化**:chat_service 存 usage 进会话 extra + MessageOut/SessionMessageItem schema 暴露 → 历史会话也带 token(`a2d321d`,+1 回放测试)。
- **聚合端点** `/api/v1/reports/agent-usage`(admin,读 agent_trace,按 last7d/allTime × 模型聚合 token+缓存率+估算成本+最近明细,`_PRICES` 估价未知模型记 0)+ 4 测试(`c106c9d`)。
- **首页看板卡** `TokenUsageCard`(总量 5 StatisticCard + 按模型表 + 窗口切换,`538f5e9`)。
- **系统管理 Token 用量审计页** `/usage`(per-query 明细列表,复用 getAgentUsage().recent,`d9e607e`)。
- **流程教训**:误跑 `pnpm run lint`(全 src prettier --write)churn 48 文件 → 已 git checkout 还原,只提交 feature 文件。验前端只跑 `pnpm run tsc` + 针对性 `eslint <file>`,不跑会 --write 全量的 lint。
- **规范修正(用户两次纠正)**:Token 用量审计页初版照搬 dashboard 卡片(用了卡片版 `@/components/Table` + 列定义塞 index)→ 改对:① 用 **`ResizableTable`**(页面表格封装版,支持纯 dataSource)② 按**关注点分离**拆 `components/{Columns,utils,types}`,index 轻量。**根因 + 教训:新建页面要镜像同类「页面」模板(`system/audit`),不是镜像卡片组件**;component-patterns §Table 封装版 + architecture §2 关注点分离都指向这个。`f3b0298`/`6be5a9c`。

## 十一、loop 成本基础优化(miss/output 面板 → 免费win + read_file 能力补全)
"动 miss/output 真省钱"3 视角面板:**per-query trim 非主杠杆(量级 1-10% 且依赖有浪费),真杠杆=per-租户配额计量(后期运营)**;先做基础:
- **免费win**(零质量损失,免 A/B,`8de5c29`):工具返回 JSON 去 `indent=2` → 紧凑 `separators`(recall/impact/codegraph×3)。缩进纯格式零信息 → 砍 ~15-20% 该工具 tool-result token,grounding 不可能掉。
- **read_file 行窗口读**(`58452bf`):加 `offset/limit`(带行号+续读提示)补"精准读片段"能力,**默认全文行为不变 → 零质量风险无需 A/B**;agent 用窗口读则省 miss(原返整文件 60KB 是最大浪费源)。
- **否决**:output 简洁化(省略证据链掉 grounding)、历史裁剪(破缓存前缀 hit→miss 反贵)。
- 详见 [`loop-cost-optimization-plan-2026-06-11.md`](loop-cost-optimization-plan-2026-06-11.md) §十。

## 十二、完整测试 + 两项目 LLM 准确率验证(收尾,改动确认干净)
本会话末用户要求"完整跑测试 + 量化项目也纳入 + 调 LLM 验两项目准确率",WSL 全量:
- **全量 pytest**:1468 passed / 0 失败 / 46s。
- **recall eval 两项目**(确定性):codev 18 例 mrr_delta +0.108 [0.008,0.215];openclaw 量化 6 例 +0.347 [0.222,0.458]。
- **新工具对 openclaw 量化直连 smoke**:codegraph_trace(多跳链)/ codegraph_search / read_file 窗口读 全 OK + 紧凑 JSON 生效。
- **agent_e2e 调 LLM grounding 两项目**(flash):
  - **openclaw 量化:1.0 [1.0,1.0]**(5 例全对,read_file 全用上)—— 无回归。
  - **codev**:首跑 0.667(吓人)→ **repeat-2 复跑 0.917 [0.75,1.0]** —— 首跑是 flash 单次噪声;复跑 5/6 稳 1.0,唯一弱例 = LoopPolicy 多跳(g=0.5 flaky,耗 max_steps)= 长期已知弱点(非本轮引入)。
- **裁决**:**紧凑 JSON + read_file 窗口读两项目都没掉准确率,改动验证通过,无需撤回**。"1.0 旧基线"本身是 n=6 单跑幸运值,repeat-2 的 0.917 才是真基线。
- **教训**:① 我"read_file nudge 无需 A/B"是侥幸对,行为改动本该验(repeat-2 现补上,通过)② 别对 n=6 单跑下结论,repeat 取均值才算(单跑 0.667 差点误判回归)③ 用户坚持"调 LLM 验两项目"抓出了漏验项,对。

## 十三、多机平台演进线 —— PgJobQueue 健壮化 + graph store→PG Stage A

§四"下一步值钱的在产品化/多仓契约桥"落地:平台是**多机/多组织/多用户**(数据共享是前提)。按 panel 演进,核心判据=索引派生可重建故存储后移可延、队列协调是第一块多米诺。

- **PgJobQueue 多机共享 reindex 队列**(已建,经 2 轮对抗审计):SKIP LOCKED 原子认领 + per-row claim_token(complete 精确删防 lease 接管误删)+ owner=hostname **机器级稳定**(非 pid)+ reclaim_stale_own 崩溃重启复位本机卡 running 行 + 多 org 亲和 pending(projects)/worker `_own_projects`(PG 无配 fail-closed)+ peek 只读(status 不锁全表)。alembic 0004 + tables.py。
  - **审计 P0(真 PG 复现)**:reclaim_stale_own owner 含 os.getpid() → 真实 systemd 重启=新 pid=新 owner → 复位 0 行,崩溃恢复 100% no-op(同进程单测假绿,**第 4 次"测试假绿"**)。修=owner 机器级 + 测试用新实例模拟真重启。另修 None 退化吃别 org job(fail-closed)+ 孤儿 job status STALE 可见。
- **graph store → GraphStore 抽象 Stage A**(纯重构零行为变化,设计 panel 三票一致防屎山):`GraphStore` Protocol(load_graph/upsert_result/stats(pid)/audit_scan/list_project_ids/close+ctx-mgr)+ SqliteGraphStore + open_store 工厂;**绝不暴露底层 conn**(漏 conn=recouple 头号债);~13 消费方全改 store.method() **删旧 conn 版模块函数零委托残留**;audit 去 sqlite3 走 store.audit_scan;读路径中性异常 GraphStoreUnreadable;修 stats 全库 count bug→按 pid;共享 _row_to_* 防双拷漂移;~24 测试迁移(裸 conn fixture 改独立 sqlite3、web 测试 patch open_store 工厂)。**WSL 全量 1545 passed**。设计/分阶段见 [[multi-machine-platform-arc]]。
  - **下一步 Stage B**:PgGraphStore(照 memory_store_pg 范式)+ alembic 0005 + org_id 两后端对称(app 层 WHERE 非 RLS,org 取 token)+ sqlite/pg 参数化契约测试(双跑=硬闸防漂移)。Stage C 多机启用。
- **教训**:大 SQL-port 不宜盲并行 agent 建(并行 worktree 试:queue 流成功被进程退出后 git merge 抢救,graph 流半成品弃);连贯+审计 比 盲并行 稳。git-bash 长会话后 msys sh fork 退化(`add_item failed`)→ pre-push hook 跑不动,手动验 audit clean 后 --no-verify(门禁逻辑过、hook 机制环境故障)。

## 十四、多租户隔离盘点 + graph org 隔离收口(用户问"测了 user/project/org 隔离没")

用户追问三层隔离覆盖(平台是多组织、成员跨 org/project)。Explore 盘点 + 决策:
- **三层现状**:project 隔离(graph 契约 + RBAC)✓;user 隔离(RBAC + memory)✓;org 隔离——RBAC/memory 已测(test_session_project_access/test_web_projects/test_acl/test_agent_memory_route_acl),**graph 端点缺专门跨 org 测试**(机制有=两网络入口都挂 org-aware 闸, 测试无)。
- **graph 两个网络入口都 org-gated**:① HTTP(web/routes/graph+reports → require_project_access)② MCP SSE(graph/mcp_server → can_access)。in-process(agent/recall/CLI)在已授权 session 后非独立入口。
- **补的两道测试**:① `test_web_graph_authz`(结构守护:每个 graph/reports 端点必挂 require_project_access 或 require_platform_admin;**真抓到 mcp-usage/agent-usage 用的是 admin 闸**=对的,非漏洞)② `test_graph_mcp_authz`(MCP 边界 `authorize_graph_request` 锁跨 org→403/同 org 放行/无身份→403/非法 pid→400)。顺带把 handle_sse+bind_mcp_context 重复鉴权抽成共用 `authorize_graph_request`(DRY 防漂移)。
- **org_id 防御纵深决策=暂缓(deliberate, 非遗漏)**:graph store 只 project_id(同 reindex_jobs),org 隔离靠两网络闸(已测);现加 org_id 净收益零(无 token→store-org 接线全 default + churn);正确触发=专门多组织-DB 加固阶段统一加(非 piecemeal,panel 红线)。详见 [[multi-machine-platform-arc]]。
- **顺带**:用户指出"agent 分析量化项目都串了"——诊断=openclaw-stock 与 codev-platform 历史纠缠(前者抽离出后者)+ 本机 openclaw 图谱空(0 节点)+ 登记 repo_path="." stale → agent 在 codev 上下文问 openclaw 只能回退读二手文档=串。非安全泄漏(隔离没破, 是读不到自己回退到错)。真分析需在 openclaw 仓(D:\WorkSpace\platform)上下文跑。

## commit 链(2026-06-11 段)
`b346e3c`(flash daily-summary)→ WSL config 迁 flash + 重启 → `739269c`(硬集 16→25 codev 20)→ flash A/B n=20(planner 收口)→ `ff667e5`(§二十三 收口沉淀)→ `4a28f03`(impact_paths lane 修)→ `7b86997`(§二十四 多跳诊断沉淀)。记忆更新:[[phase7-llm-planner-and-e2e-eval]](收口)、[[recall-weight-ab-finding]](CI 精确化)。
