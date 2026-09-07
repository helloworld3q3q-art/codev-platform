# daily-summary 2026-06-09 —— Phase 1/3 落地 + 前端孤岛修复 + 可观测性 + 门禁

> 承 [`daily-summary-2026-06-08.md`](daily-summary-2026-06-08.md)(Phase 7 planner 每模型策略 + A/B + impact 调参)。
> 本日把 roadmap-2026-06-07 蓝图又往前推 1/3/10 三块, 并修了一个前端跨层断裂真 bug。

## 一、Phase 1 IndexManifest(端到端闭环)

- `index_manifest.py`: 统一 `index_builds` 表(每 project×kind 最近构建 commit/耗时/状态), reindex worker 终态 best-effort 写一行(不阻断索引)。
- CLI `index status` + freshness(记录 commit vs 仓库 HEAD → 对齐/落后/未知)。
- **Web 端点** `POST /api/v1/indexes/status` + **dashboard 索引新鲜度卡片** —— Gate "Web 暴露 freshness" 补齐。
- 真机:worker 写真行 `codegraph 对齐 ✓`;端点 live 注册;7 单测。

## 二、Phase 3 图谱审计 + pre-push 门禁

- `graph/audit.py`: 结构审计 errors(断链/跨租户串台/**孤儿 plugin**)+ warnings(重复/低置信)。
- **孤儿 plugin 检测**抓出真 bug: A2 软节点曾被 `arch_layer` 自名 plugin 直 upsert 残留(reindex 只清规范 `builtin.analyzers` → 永不清), 致 11 个角色节点重复。**已 purge + 加检测防复发**。
- `graph audit --all` + `tools/dev/pre-push-audit.ps1`: **接进 git pre-push**, 结构 error 非零退出挡 push(无 store 优雅跳过, 只读 sqlite 内存友好)。多次真机自跑 `OK clean`。
- **Web 端点** `POST /api/v1/graph/audit` + **dashboard 图谱结构健康卡片**。8+ 单测。

## 三、前端孤岛 bridge linker(修真 bug, 通用)

- 两个前端插件(frontend_deps 建 module / react 建 api_call/route)为同批文件建节点但 id 不相交、无边相连 → `frontend_module` 成孤岛, impact 滤软边后**到不了后端**, 前端页→接口→表 跨层链断。
- web 上 codev-platform "看着连"是 **A1/A2 软边经角色 hub 糊的视觉假象**(连通块: 硬骨架 32 块 vs 含软边 1 块);量化仓无软层直接裸露孤岛。
- 修: ingest `_frontend_bridge_pass` 按文件 `module --contains--> 同文件 api_call/route`(硬边)。**通用对所有项目生效**。真机: openclaw-stock `find_page_dependencies(alerts页) → backend 51 / database 194`, 跨层链贯通。

## 四、web 软硬边区分

`Graph3DCanvas` 加 `linkIsSoftFn`: 软边(plays_role/belongs_to_domain)淡色细线退背景, 硬依赖实线 —— 让"经角色 hub 连通"不被误读成功能依赖。按 kind 判定, 无需 pnpm run api。

## 五、可观测性收口

dashboard 现有 **索引新鲜度(Phase 1)+ 图谱结构健康(Phase 3)** 两张卡, "索引新不新鲜 / 图谱结构干不干净"一眼可见。

## 六、教训: 内存危机(记一笔)

32G 机器跑爆: WSL2 默认吃一半(16G)+ 浏览器 3D 统一图谱占核显共享内存(11G, 从系统 RAM 挖)+ 超长会话终端 scrollback(37G commit)。处置: `wsl --shutdown` + **`.wslconfig` 封顶 WSL 12G + autoMemoryReclaim** + 关 3D 图谱页 + 重启。**结论: 3D 大图谱用完即关; 超长会话适时开新终端。**

## 七、roadmap 进度

落地 ~30-35%: Phase 0/7 实质推进, Phase 1/3 本轮做成 MVP+Web+dashboard, Phase 4/6/10 轻量覆盖。重型 Phase 2(统一 IR)/5(path scoring)/8(性能)/9(多语言 adapter)按 §六纪律待真实需求触发。

## 八、统一图谱完整重建验证(走 worker, 非手动)

对两仓**经 reindex worker 正经重建 + 审计**, 验证本轮修复经住干净重建:

| 仓 | 重建后关键产物 | 审计 |
|---|---|---|
| codev-platform | 498 节点; `contains` 76(bridge)/ arch_layer 11 + plays_role 471(A2)/ business_domain 12 + belongs_to_domain 121(A1) | **clean, 0 error** |
| **openclaw-stock(量化)** | `contains` **214**(bridge, 前端孤岛已连)/ arch_layer 9 + plays_role 2263(A2)/ **business_domain 27 + belongs_to_domain 219(A1; 之前 0 软层, 本次产出)** | **clean, 0 error** |

两仓均: 断链/串台/孤儿 plugin/重复 **全 0**。三修复(frontend_bridge / arch_layer 孤儿不复发 / A1·A2 软层)全部经住干净重建; **量化仓顺带补齐了之前缺的综合理解层**(A1 业务域 + A2 架构层, deepseek 真跑)。

**教训(已记)**: 手动 `graph ingest` 在裸 shell 跑会撞 **npx dependency-cruiser 冷启动** → fail-soft 返 0 退化 + upsert 覆盖好数据(frontend_deps/calls/analyzers 全 0)。**重建一律走 reindex worker**(`reindex-queue enqueue <pid> --kind ingest`)—— 它在 systemd service 环境有正确的 node/codegraph, 不会冷启动失败。

## commit 链(本日上午)
`6d07da3`(Phase1 manifest)→ `e85d582`/`4928dca`(Phase3 audit+孤儿检测)→ `faacb0b`(前端 bridge)→ `745d3e4`(web 软硬边)→ `1f492d3`(pre-push 门禁)→ `faef6cd`/`e8087aa`(index status web+卡片)→ `01af953`/`0ea0c5b`(graph audit web+卡片)。

---

# 续(同日新窗口会话)—— Phase 3 provenance + soft-quality 双 bug + Phase 6 联合召回 + eval

> 32 commit, 全 pushed(`2359ffe`…`9d30694`, 与远端 0/0), 几乎都带单测, 关键节点 WSL 真实数据验证。

## 九、Phase 3 Provenance(影响分析可追溯)— 三件套

让统一图谱每条边可追溯来源, 高风险结论只采信确定依赖。
- **盖戳**: `schema.stamp_provenance` 约定走 `edge.meta`(零迁移)→ ingest 三 pass(bridge/framework/calls 按 resolver 声明)+ **插件直产边经 executor 按 plugin `prov_source` 统一盖**(声明式扩展点, 加插件零改核心)→ **全硬边覆盖**。
- **展示**: impact 路径 brief 带 `src + confidence`, report 出**确定依赖 vs 候选**拆分(由 confidence 判, 与 src 正交)。
- **操作**: impact 查询加 `certain_only`(滤候选边, 落地 Gate)+ MCP 透传; audit 加 `no-provenance` 硬边计数。
- 发现 sql/react/vue 实为**正则解析**→ 修正 `ProvSource.regex` 语义(与置信解耦)+ 删未用 `CERTAIN_PROV_SOURCES`。

## 十、soft-quality 软标签健康诊断 + 抓修双 bug(真实重建验证)

建 A1/A2 软标签健康体检(`graph soft-quality` CLI + `/api/v1/graph/soft-quality` web + 平台 `health --all`)。**首跑就抓到真问题**: openclaw A2 `repository` 占 64%。
- **4 轮取证 + 3 专家兄弟面板**(架构师/诊断/怀疑论者)→ 定论: **非标注质量**, 是**双 bug**叠加。
- **Fix-A**(soft-quality): giant 按 **distinct 文件**口径(原 membership 口径被 Mapper 方法密度 + db_column/db_table 撑大成假阳性)。
- **Fix-B**(analyzer): `architecture_layer` 只对**代码节点**产 plays_role, 排除 db_table/db_column(数据层不演代码架构角色)。
- **真实验证**(worker `--kind ingest` 重建): `find_arch_role(列)` 从误返 repository → 正确空; plays_role 2263→1111; soft-quality ✅ healthy。
- 顺带 `fix(cli)`: CLI 输出切 UTF-8, 防 Windows GBK 控制台 emoji 崩(audit/soft-quality 同受益)。

## 十一、Phase 6 联合召回(大头, 数据驱动)

从零建跨 lane 代码融合召回子系统, 三类消费者全打通:
```
fusion 核心(加权 RRF + 可解释) → service(graph + codegraph, 免 daemon, 每 lane fail-soft)
  → planner 自动调权(symbol 偏 codegraph / impact 偏 graph) → 多词分词 → 相关性分级(精确>前缀>子串)
  → codegraph OR 模式(verbose 多词不再 AND 全灭) → 测试文件降权
消费: MCP 工具 recall_code(IDE agent) + web 端点 /api/v1/recall/code + agent 工具 code_recall(chat)
```
**eval 量化验证**: weighted **MRR 0.357→0.917(+157%) / nDCG 0.269→0.858(+219%)**; planner 权重 A/B 从"看似 0 delta"(被 symbol 检索全废污染)→ 排除混淆(重建 codegraph + OR 模式)后验证 **+0.167 真有用**。教训: 测出意外结果先查混淆因子(陈旧索引/检索太严), 别急下"X 没用"结论。

## 十二、eval 框架打磨

- `metrics` 加 `nDCG@k` / `aggregate_ndcg`(比 recall@k 更惩罚相关项排靠后)。
- 新增 **recall A/B suite**(加权 vs 等权, 防 overfit 的真值尺)。
- `run_eval.py` **拆成 `eval/suites/` 包**(一 suite 一模块, 699→114 行 CLI), 新增 suite = 加一模块 + dispatch 一行。

## 十三、commit 链(本会话, 32 个)

provenance(`2359ffe`→`866ecc8`)→ CLI UTF-8(`ef04670`)→ soft-quality(`2c73c1c`/`3ca92bb`/`a4e4817`)+ web/health(`fe8a704`/`765dec3`)→ Phase 6 recall(`e83bac8`→`aa623a1`)+ web/MCP(`40cf3fe`/`87d7ab6`)→ A2 双 bug(`bcabe4c`/`36b8a39`)→ recall 多词/相关性/OR/测试降权(`8bbfbc0`/`c019e77`/`40537a3`/`e915fb4`)→ eval suite+拆分(`15fe71e`/`cdcd59a`)→ agent 工具(`9d30694`)。

## 十四、待上线 + 下一步

- **待重启 live**: 最后 2 改(测试降权 / code_recall agent 工具)需 `codev-mcp-graph`+`codev-web`+`codev-agent` 重启(攒批, 免 churn MCP 连接)。其余已 live 验证。
- **roadmap 位置**: Phase 0/3/6/7 实质推进; Phase 6 从"未启动"做到端到端可用 + 三消费者 + eval 验证。
- **下一步候选**: ① 让 code_recall 成 agent 主检索(planner 接 `preferred_lanes` + instructions)② 精准化 recall eval golden(只标真实现为 relevant)③ 换方向。

## 十五、本会话沉淀的记忆

`code-quality-principles`(写码准则)/ `soft-quality-first-run-findings`(A2 双 bug 定论)/ `recall-weight-ab-finding`(权重 A/B 翻转 + lane 内是杠杆)/ `locate-via-codegraph-not-grep` 等。

## 十六、续二 —— code_recall 成主检索 + Phase 3 冲突消解 + Phase 5 路径评分

> 日志后 4 commit(`9d30694`→`d0ffa7f`), 全 pushed + WSL 真实验证 + 三服务重启上线 + IDE-agent 实调。

- **code_recall 接进 agent 工具集**(`9d30694`): 把 Phase 6 融合召回封成 agent 工具 `code_recall`(注册进 build_default_registry), agent 一次拿融合 graph+codegraph 可解释排名。
- **code_recall 成主检索**(`dba963c`): planner `_LANES` 把 code_recall 放进 symbol/overview/general 首位、impact 末位; `code-understanding` skill 同步"找代码优先 code_recall"。Phase 6↔7 真正咬合 —— 不再是旁路工具而是默认首选。
- **Phase 3 冲突消解完成**(`910ea21`): 实测 openclaw 155 组同 (source,target,kind) 重复(退役 `builtin.codegraph_bridge` 残留 vs call_resolvers, 一盖戳一没盖)。`build_impact_graph` 查询时 `_resolve_duplicate_edges` 保 provenance 更全/高置信者(非破坏); audit 加 `duplicate_edges` warning; **purge openclaw 155 条残留(独有 0 纯冗余)→ audit clean**。
- **Phase 5 多跳路径评分**(`d0ffa7f`): `find_impact_paths` —— Dijkstra 最大乘积每节点保最优单路径(有界 O(节点)+ max_depth/fanout 封顶), 评分 `Π(confidence × src 权重)`(ast/framework/bridge 满权, regex 0.7/llm 0.5), 每跳给 file+src+confidence 证据 + 确定/候选, 降序稳定。暴露成 graph MCP 第 13 工具。IDE-agent 实调 `find_impact_paths(graphAudit)` → 14 可达 top 10 路径, 完整跨层依赖链 + 每跳证据。**社区/新鲜度加成依赖未做的 Phase 4 待后续**。

**完成度更新**: Phase 3 ~85%→**完成**(冲突消解补齐); Phase 5 未启动→**MVP 落地**。**纯未启动重型 Phase 只剩 2(统一 IR)和 8(性能)**。本会话累计 **36 commit**。三服务(codev-mcp-graph 13 工具 / codev-web / codev-agent)已重启上线。

---

# 续三(同日第三窗口)—— 审计收尾 #10 只读 audit + #1 recall eval golden + 运维上线

> 承 §十六。本窗口做综合审计剩余项的收尾 + 把前两窗口已提交的改动重启上线。单 commit `4cdf204`(6 文件 +216/-7, 无 AI 痕迹), Windows 89 tests 绿。

## 十七、#10 graph audit 只读化(审计 9/11 → 10/11)

`audit_all_stores` 之前用 `open_store()` 打开每个 store → 会 mkdir + 设 WAL + 跑迁移 + 建表 DDL(**名义只读的 pre-push 门禁却在改本地 sqlite**)。

- 改 **read-only 连接** `sqlite3.connect(file:?mode=ro)`: 不建目录 / 不设 WAL / 不迁移 / 不建表 —— 审计纯读, schema 迁移是写侧 ingest 的职责。
- 旧 schema(edges 缺 `project_id` 列)read-only 读不动 → `_unreadable_report` 记一条 `audit_error`(计 1 error 不崩门禁, 让操作者知道需 reindex 迁移); `render_markdown` 渲染该原因; 删去不再用的 `open_store` 导入。
- 测: `test_graph_audit` **11/11**(新增"只读审计不改 db 字节 hash" + "旧 schema 优雅记错" + "空目录不创建")。真机 CLI `graph audit --all` 跑通 codev-platform store(**457 节点 / 607 边, read-only, exit 0 clean**)。

## 十八、#1 recall eval golden 精准化(只标真实现 ref)

eval `_recall_per_query` 之前按 name/file 子串判相关 → `test_weighted_rrf` 这类**同名测试也算相关**, 虚高 nDCG。

- 现**排除测试代码**(`test_` 函数 / `tests/` 目录 / `*_test.py` / `*.spec.ts`)出相关集, 判据**复用 service `_is_test_hit`**(单一真值源, 不在 eval 侧重写启发式 —— 与 lane 内降权用的同一定义)。
- 测: `test_eval_recall` 新增 test-hit 排除用例; recall/eval/graph 合并回归 **89 passed**。
- **WSL 真实复跑(push `3219c24` + WSL pull 后)**: recall suite 在 Windows skip(需 codegraph.db, 在平台/WSL 侧 junction), 故在 WSL 跑 `--suite recall --project codev-platform` → **weighted MRR 0.917 / nDCG@5 0.858, uniform 0.750 / 0.724, planner delta +0.167 / +0.134**。排除测试 ref 后数字**稳定保持**(与日志记录一致), 说明 golden 现只认真实现、且这些查询本就未被测试文件虚高 —— 修正生效且无回退。

## 十九、运维上线 + 全栈验证

1. **重启 `codev-agent` + `codev-web`**(WSL systemd)→ 让**前两窗口已提交**(≤`b2c03a1`, WSL clone 已有)的 #1/#2 安全修复 + code_recall agent 工具 + planner 主检索 **live**(代码已 pull、服务持旧内存码)。
2. **`wsl --shutdown` 全栈干净重起**(顺带清前述内存危机残留): systemd `running`, 6 服务自启 `active`; 4 MCP 端点全 **OK**(platform-docs 19083 冷启动模型加载完成 / codegraph 19091 / agent-memory 19087 / graph 19092); `codev-web` :18088 `/docs` HTTP 200。
3. **Claude 重启后端到端绿**: codegraph MCP(653 文件 / 8528 节点)+ graph MCP 均正常响应。

## 二十、审计状态 + 文档

- 综合审计(`docs/audits/codev-platform-consolidated-audit-2026-06-09.md`): **9/11 → 10/11**(#10 收掉)。
- 延后 2 项(真实需求触发再做): **#7** config DI `rebind_web_services`(改动大、单实例价值低)/ **#8 残留** `set_roles` 多 org 角色管理(RBAC 安全敏感, 改错会跨 org 越权, 要先补越权测试)。
- 新增 [`next-steps-2026-06-09.md`](next-steps-2026-06-09.md): 审计剩余 3 项细节 + 三梯队整体剩余计划 + 新窗口开局动作(供下个窗口直接 Read)。

**纯未启动重型 Phase 仍只剩 2(统一 IR)和 8(性能)**。

## 二十一、全量回归 + 修两类既有失败(用户"多测试下")

WSL 全量 pytest(完整环境: PG/模型/codegraph)首跑 **1361 passed / 10 failed** —— 10 个**全不在本会话 #10/#1 改动区**(那几块 100% 绿), 是既有债, 一并修掉:

**① impact.py 超行预算**(1 个): `graph/impact.py` 608>600(上一会话 Phase 3 冲突消解 + Phase 5 path-scoring 加进去的)。修: 抽 `_resolve_duplicate_edges` → 新模块 `graph/edge_resolve.py`(public `resolve_duplicate_edges`), impact 导入复用; `test_graph_impact` 3 处导入改指新模块。impact.py 回 ≤600(budget 测试绿)。

**② web RBAC 9 测试环境耦合失败**: `test_web_auth`/`test_web_projects`/`test_web_filtering` 在配了 `memory.pg_dsn` 的环境(WSL)报 403 / roles 空。根因: `web/security/membership.resolve_membership` **优先**查真 PG RBAC store(`_pg_rbac_store()→get_rbac_store()`), 而这些 dev/passthrough 测试把角色写进**内存 member_store** → PG 空 → 越过内存路径 → deny。修: autouse fixture 强制 `_pg_rbac_store→None` 走内存路径(PG RBAC 路径由 `test_web_db_stores_sqlite`/`test_rbac_wire` 专测)。**纯测试隔离, 不动生产 RBAC**; 与 audit #7(import 期单例/DI 绑定)同源。

**结果**: WSL 全量 **1371 passed / 0 failed**(`7a89bb3`)。

**本窗口 commit 链**: `4cdf204`(#10 只读 audit + #1 eval golden)→ `3219c24`(日志续三)→ `b1fc1f7`(#1 WSL 复跑回填)→ `7a89bb3`(全量修两类既有失败)。

## 二十二、Phase 7 完整版起步 —— LLM planner(数据驱动 + 多模型 + determinism-first)

"继续开发": 给 Query Planner 加 LLM 分类增强(完整版第一块), 全程**先量再建**:

- **先量 headroom**(关键): 新增 eval planner **硬集** `planner_hard.jsonl`(对抗/口语化/无关键词命中问法)。关键词分类标准集 **1.0**, 硬集**仅 0.267** —— 口语化问法易掉 `general`(丢 preferred_lanes + 预算引导), 证明 LLM 增强有真实空间(非凭感觉上)。`test_eval_planner` 把这条 headroom 钉成回归。
- **LLM planner**: `classify_query_llm(question, provider)` —— 只依赖中性 `LLMProvider`(brain/base, 不耦合厂商 = 多模型) + `classify_query_smart`(LLM 优先 + **关键词永久兜底**)。provider 任何故障/输出非法 → 吞成兜底, planner 不可靠也绝不拖垮 loop。
- **策略门**: `LoopPolicy.planner_llm_enabled`(**默认全档关**, registry 解析 + config 逐 provider 开); loop 按 policy 把同一 provider 注入 `plan_query`。determinism-first: 多一次分类 LLM 调用的取舍未经 A/B 不默认开。
- 测: +10(fake provider 测分类解析/兜底/precedence/plan_query 集成, 不连真模型)。WSL 全量 **1381 passed / 0 failed**。commit `39b7f3b`。
- **待 WSL A/B**(后续): enable `planner_llm_enabled` 后跑硬集 keyword vs LLM, 量 LLM 实际提升 → 决定是否某档默认开;**agent 端到端 eval**(完整回答质量, 非仅分类准确率)是 Phase 7 完整版剩余大块。

## 二十三、planner LLM A/B 实测(deepseek 真跑)—— LLM planner 验证有效

加 A/B 管线(`run_planner(provider=)` + `run_eval --llm`, oracle 单测验管线), WSL 真 deepseek 跑 `--suite planner --llm`:

| 数据集 | 关键词 | LLM(smart) | delta |
|---|---|---|---|
| 标准集(关键词调过) | 1.0 | 0.938 | −0.062(1 例) |
| **硬集(口语化/无关键词)** | **0.267** | **1.0** | **+0.733** |

**结论**: LLM planner 在真实口语化问法上 **+0.733**(0.267→1.0), 正是关键词盲区被补上; 代价是标准合成集偶有 LLM-first 覆盖关键词已对的 1 例。**净判定: LLM planner 实证有效。**

**不做(纪律)**: 不据这 31 例 overfit "关键词置信优先 + LLM 兜底" 混合策略 —— 标准 −0.062 是单例/合成集噪声, 不足以推翻 LLM-first 的简单设计([[recall-weight-ab-finding]]: 小集别上窄杠杆)。LLM planner 保持**默认关 + config 逐 provider 可开**, 收益已实证。要默认开某档需更大评测集 + 权衡每查询多一次 LLM 调用的延迟/成本。commit `7a76bfb`。

**Phase 7 完整版剩余**: ① 是否给某档默认开 `planner_llm_enabled`(需更大集 + 延迟权衡)② **agent 端到端 eval**(完整回答质量, 最后大块)。

## 二十四、agent 端到端 eval E1+E2 落地 + 首个基线

承 ② —— 先写[设计](agent-e2e-eval-design-2026-06-09.md)(grounding 优先打分 + 数据集格式 + harness + 分期 E1-E4), 再按期落地:

- **E1(Windows 可验)**: `score_case` 纯打分器(grounding_coverage / hallucination / tool_appropriate / within_budget, 脱 AgentLoop 可单测)+ aggregate + 种子集 `agent_e2e.jsonl`(codev-platform 5 case 真实锚点)+ suite(provider 缺则优雅 skip)+ run_eval 注册。**9 单测**。确定性 grounding 为主, LLM-judge 留 E3。
- **E2(WSL 真跑)**: deepseek + 真 AgentLoop + MCP 后端, **首个基线**:

| 指标 | 值 | 读法 |
|---|---|---|
| grounding_coverage | **1.0** | 5/5 答案都落到真实文件/符号锚点 |
| hallucination_rate | 0.0 | 无 must_not 违规 |
| tool_appropriate_rate | 0.8 | 4/5 用到期望工具类(1 例走别的路径但仍 grounding 满分)|
| within_budget_rate | 1.0 | 全在 planner/max_steps 预算内 |

**结论**: agent 在种子集上**全程有据、零幻觉、不超预算**, 回答质量基线优。`expect_tools` 是"至少一类"非强制, 故 0.8 不算缺陷。

**剩余**: E3 LLM-judge(主观质量, 默认关)/ **E4 planner 端到端 A/B**(planner 开关 + keyword vs LLM planner 下答案质量差 —— 验 §二的"分类更准→答案更好"独立假设)。种子集小 = 趋势工具非绝对分([[recall-weight-ab-finding]] 纪律)。WSL 全量 pytest **1392 passed / 0 failed**。commit `1f437bb`。

## 二十五、Phase 7 完整版收尾 —— E3 judge + E4 A/B + `planner_llm_enabled` 默认决策

用户"1、2、3 挨着做"。harness 全建成 + 14 单测(judge 解析/兜底 + A/B 三变体), WSL 真跑(`8d8d8d5`):

**E4 planner 端到端 A/B**(3 变体 × 种子集):

| 变体 | grounding | tool_appropriate | within_budget |
|---|---|---|---|
| planner off | 0.9 | **0.6** | 1.0 |
| planner keyword | 0.9 | **1.0** | 1.0 |
| planner llm | 0.9 | **1.0** | 1.0 |

- **planner ON(任一分类器)把 tool_appropriate 0.6→1.0(+0.4)** —— planner 的 lane 引导让 agent 用对工具类, 端到端真有价值(planner 这个 Phase 7 特性被实证)。
- grounding 三者同 0.9(种子集易 → **天花板效应**, 测不出差); **keyword vs llm 此集无差**(5 例分类够清晰, 两分类器路由一致)—— LLM planner 的优势在口语化问法(分类 A/B +0.733), 本易集不触发。

**E3 LLM-judge**: deepseek 自评 5 例 **judge_score_avg = 5.0**(满分)。judge 层通了, 但自评偏宽 + 易集饱和 → 此集不 differentiate。基础设施就位, **默认关**(噪声 + 成本)。

**#3 决策 → `planner_llm_enabled` 保持默认关(config 逐 provider 可开)**:
- LLM planner 分类增益已证(硬集 +0.733), 但**端到端答案质量相对 keyword 的优势尚未在 eval 证出**(种子集太易, 不触发 keyword 弱点)+ 每查询多一次 LLM 调用(延迟/成本)。
- 守纪律不据小易集翻默认([[recall-weight-ab-finding]])。**翻默认的条件**: 补口语化硬 e2e 集, 证出 keyword vs LLM 端到端答案质量差, 再考虑某档默认开。

**Phase 7 完整版 = 主体完成**: 最小版 + LLM planner(验证有效)+ e2e eval(设计→E1→E2→E3→E4 全落地)。剩纯精修(硬 e2e 集 / judge 取舍 / 默认开条件), 按真实需求触发。commit `8d8d8d5`。

## 二十六、Phase 7 最后精修 —— 口语化硬 e2e 集拉开 keyword vs LLM(端到端差证出)

补口语化硬 e2e 集 `agent_e2e_hard.jsonl`(6 例真实锚点; 实测 keyword 分类 **5/6 误判 general**)→ E4 A/B(`79406df`):

| 变体 | grounding | tool_appropriate | within_budget |
|---|---|---|---|
| off | 1.0 | 0.833 | 0.5 |
| keyword | 1.0 | 0.833 | 0.5 |
| **llm** | 0.833 | **1.0** | **0.667** |

**关键发现**:
- **keyword == off**(逐项相同)—— 硬集上 keyword 误判 5/6 为 general → 无 lane 引导 → **planner 形同未开**。坐实 keyword 脆性会传导到端到端(分类失败 = planner 不给力)。
- **LLM planner: tool_appropriate 0.833→1.0(+0.167) + within_budget 0.5→0.667(+0.167)** —— 正确分类 → 正确 lane 引导 → 用对工具 + 更守预算。**keyword vs LLM 的端到端差: 易集测不到(三变体趋同), 硬集证出。**
- grounding 三者近天花板(0.83-1.0; llm -0.167 = 1 例噪声, 非确定)—— agent 取证够鲁棒, 不论路由对错都能 ground; LLM planner 改善的是**过程**(工具选择/预算), 非 grounding。

**#3 决策更新**: 证据更强(LLM planner 硬集改善路由 + 预算), 但 **grounding(答案质量主代理)未提升**(都近满)+ 6 例小集非确定。→ `planner_llm_enabled` **仍默认关**, 但**口语化重的工作负载可按 config 开**(已有路由/预算实证收益)。默认开某档待更大集证出 grounding/质量的稳定提升。

**Phase 7 完整版 = 完成**(主体 + 最后精修 + keyword-vs-LLM 端到端差实证)。剩纯按真实需求触发。

## 二十七、对抗式审计今日全会话(6/9-6/10)+ follow-up

派 general-purpose 对抗式审计兄弟审 `b2c03a1..a41ac07`(17 commit, 跨 6/9-6/10), 默认怀疑 + 自跑测试 + 写攻击用例([[post-change-adversarial-audit]] SOP):

- **🔴 真 bug = 0**。6 高风险点全证伪未中: audit 只读(**hostile live-WAL 场景实证纯读、不破坏 committed 数据、不崩门禁**)、edge_resolve 无损拆分、LLM planner 任何故障退关键词且 `provider=None` 字节等价、planner_llm 默认全档关不破 8 处构造、eval harness 接线正确。
- **web RBAC 测试隔离经 WSL 实证 = 修测试环境泄漏**(WSL 配 pg_dsn, 空 PG 遮蔽内存 fixture), **非掩盖生产 bug** —— 生产 resolve_membership PG-优先→空回退→deny 逻辑正确。
- 21 攻击用例全过 + Windows 100 / WSL 62 回归。

**收两个 🟡 follow-up**(`00c9f0f`, 均非今日回归):
- #2 `_parse_score` 取**首个完整数字 token** + 1-5 范围校验("10"/"2024" 不再被截成 1/2; judge 诊断鲁棒)。
- #1 `test_web_membership`(新): 直接钉 `resolve_membership` 三路径(PG-优先注入 fake store / 内存回退 / 无身份空)—— **PG 分支此前无直接单测**(被 web 测试的 `_pg_rbac_store→None` monkeypatch 遮蔽)的盲区补上。WSL 3 passed。

WSL 全量 **1401 passed / 0 failed**。**今日全会话改动经对抗式审计 + follow-up, 可放心。**

## 二十八、专家面板定方向 + eval 打分器信度标定

用户要"先来几个专家分析再继续"。派 4 视角面板(架构 / 检索-AI / 务实-ROI / 质量-测量)分析下一步候选(扩 eval 集 A / #7 config DI / Phase 6 lane / 重型):

- **共识否掉 C 原样**(架构 + AI): `search_docs`/memory **早是独立 agent 工具**, 异构语料(doc chunk vs 代码符号)塞进 `code_recall` fusion 会污染以代码实体为 ground truth 的金标; 当前"planner 路由到对的通道"才是正解, 要扩也是 bm25-over-代码(同构)。
- **B(#7)暂缓**(务实/AI/质量): 与检索真杠杆无关 + RBAC 敏感 + band-aid 零真实风险([[post-change-adversarial-audit]] 的 `test_web_membership` 已补盲区)。架构师想还债但承认不紧急。
- **务实视角力主收摊**(已到干净完成点); 但用户要继续。
- **检索 + 质量专家一致洞见**: 所有数字撞**易集天花板**(planner 饱和 / recall MRR 0.917 见顶 / grounding 0.9 / judge 自评 5.0)→ **测量已盲**; 且打分器有真漏洞: `must_mention` 裸子串假阳("sql"命中"sqlite"、"service"命中"services")→ grounding 虚高。质量专家定调: **"先证明尺子准, 再加长尺子。"**

**执行(A 的正确第一步 = 标定尺子, 非堆例子)** `4d3e50b`:
- `_mentions` 改 **token 边界匹配**(前后非 `[A-Za-z0-9]`; `_`/`.` 算边界, 故 `x.classify_query(` 仍命中)→ 灭裸子串假阳。+3 单测(假阳灭除 + 空/无关答案对照)。
- 锚点加固: 去通用单词("AI"→"Generated")、补特异锚("sql"+"plugins")、加 `must_not` 诱饵(open_store)让恒 0 的 hallucination_rate 能动。
- **标定结果(WSL 真跑)**: grounding **1.0 → 0.9** —— 证实旧 1.0 含 ~0.1 子串假阳虚高, 现更诚实; `hallucination_rate 0.0` 现**有意义**(诱饵在场未误触)。WSL 全量 **1404 passed**。

**下一步(面板共识路线, 留新一轮)**: 尺子准了 → 扩集才有意义。扩法(质量 + 检索专家): 覆盖矩阵(4 类 × 3 风格 × ≥2 项目, 30-40 例)+ 多跳/易错/**负样本**难题(让 grounding 脱天花板)+ judge 换**非自评**模型 + 多次取均值 + 报置信区间。这是把 Phase 7 "趋势"变"定论"、并暴露下一个真杠杆(召不全?读码不深?)的路 —— 属认真一轮, 不在本窗口仓促铺开。

## 二十九、诊断难集首跑 —— eval 开始"问倒" agent(找到真缺口)

建诊断难集 `agent_e2e_quality.jsonl`(6 例: 多跳 / 近义误导 / 负样本陷阱)+ `--e2e-set {default,hard,quality}`。WSL 真跑(`dcee8a5`):

| 指标 | 易集 | **诊断难集** |
|---|---|---|
| grounding_coverage | 0.9-1.0 | **0.833**(脱天花板)|
| hallucination_rate | 0.0(无诱饵)| **0.33~0.5**(诱饵触发)|
| judge_score_avg(自评)| 5.0 | **5.0**(给幻觉答案也打满分!)|

**逐 case 暴露的真缺口**:
- **🔴 case 6 向量库陷阱(grounding 0.0 + 全幻觉)**: 问"recall 用哪个向量数据库", agent 确信地答 **chroma / embedding / 向量数据库** —— 但 `code_recall` **只融 graph+codegraph、无向量 lane**。**agent 顺着错误前提编造, 而非纠正前提。** 最干净的真 bug 类。
- case 2/3 部分幻觉: 答案 grounding 1.0(答对了)但**夹带过时/错误项**(audit 还提 `open_store` 旧法 / planner fallback 说"抛异常")—— 新旧混淆 + 加错细节。
- **self-judge 彻底失效**: deepseek 自评给上述幻觉答案**全 5.0** → 坐实"judge 必须换非自评模型, grounding 才是真信号"(面板预言命中)。
- 多跳题(case 1/4: recall 融合链 / config 链)agent 答得好(grounding 1.0)→ 多跳综合**不是**瓶颈。

**意义**: 易集 saturated 测不出东西; 难集**一跑就找到 agent 真弱点(顺错误前提幻觉)+ 证伪 self-judge** —— 正是面板说的"扩集价值在暴露下一个真杠杆"。**下一个真活**: ① agent 抗错误前提(system prompt / loop 加"先验证问题前提, 假则纠正而非顺答")② judge 换非自评 + 多跳取均值压方差(本次 halluc 0.33↔0.5 抖动印证小集非确定)。

## 三十、① 抗错误前提 prompt + ② 多跑/非自评 judge —— 兼揪出金标自身缺陷

用户"都要"。落地 ①②, 但诚实复盘(`135f868`/`c589496`):

**②(交付 + 机制验证)**: `run_agent_e2e --repeat N` 每 case 跑 N 次, grounding 取均值 + 报 `grounding_min/max` 跨度 + `hallucination_runs`(压小集非确定方差)。`_summarize_runs` 纯函数 + 多数票 tool/budget。`get_provider(name=)` + `run_eval --judge-provider` = 非自评 judge。4 单测。

**①(prompt 规则9)+ 真发现: 金标自身有缺陷**:
- 加 `CODE_UNDERSTANDING_SYSTEM` 规则9: 先验证问题预设, 前提为假明确纠正而非迎合编造。
- **但诊断难集首跑的 hallucination 0.5 大半是金标缺陷, 非 agent bug**(质量面板预警命中):
  - `must_not` 子串**分不清肯定/否定**: `"抛异常"` 会假阳命中"**不**抛异常"的正确答案 → case3 假幻觉。
  - case6 把 `chroma/embedding` 标 `must_not` 是**误标** —— 平台**文档**检索真用 chroma + Qwen embedding; agent 答它是把文档检索混进 `code_recall`, 非纯幻觉。
- 修金标: 删 case3 假阳 must_not、reframe case6 靠 `must_mention`(graph+codegraph)判真机制(不用 must_not, 子串无法判肯定/否定)。
- **修后重测(repeat=2)**: grounding **0.833→0.917**, hallucination **0.5→0.167**。
- **诚实归因**: 改善**大半来自修金标**(去假阳), **规则9 净效果仍未单独证出**(被金标修复混淆)。干净验证需在修好金标上做 rule9 on/off A/B —— 留 follow-up。

**最大价值(元层面)**: eval 第一次**反过来查出金标自身缺陷**(must_not 脆性 + 误标陷阱)。坐实质量面板核心论点: **测量信度比堆例子重要; 负样本必须真为假, must_not 子串不适合判否定语境**。这条比"agent 有没有幻觉"更值钱 —— 它防止后续用错尺子做错决策。

## 三十一、rule9 on/off A/B 闭合 ① —— 零净效, 回退

在**修好金标**的 quality 难集上做干净的 rule9 on/off 对照(repeat=2, 隔离规则9):

| 变体 | grounding | hallucination |
|---|---|---|
| rule9 ON | 0.833 | 0.167 |
| rule9 OFF | 0.917 | 0.167 |
| **delta(on−off)** | **−0.084** | **0.0** |

**结论: 规则9 零净效** —— hallucination 一模一样(0.167), grounding 反而略低(−0.084 = 1 case/12 跑的噪声)。前一轮 0.5→0.167 的改善**全部来自修金标**(去假阳), 与规则9 无关。

**处置(守纪律)**: 按 [[recall-weight-ab-finding]]"零 delta 的杠杆不留", **回退规则9**(production prompt 不背无效文案)+ 移除 A/B 脚手架(`rule9_ab.py` / `system` 透传 / 测试), 保留 ②(`--repeat` 方差 + 非自评 judge + `get_provider(name=)`, 已验证)。方法留在 git 历史(`09b4165`)。

**① 闭合真结论**: agent 顺错误前提幻觉(case6 把平台**文档** chroma 混进 `code_recall`、case2 夹带旧 `open_store`)**不是一行 prompt 能修的** —— 是更深的 grounding/读码消歧问题(agent 读了代码仍混淆)。真修是独立课题(更强 grounding / 工具结果去歧义), 不在本轮。**本轮 ① 的净产出 = 证伪了一个 naive 修法 + 一套可复现的 prompt-rule A/B 方法。** 这正是"先证尺子准再下结论"的纪律闭环。

## 三十二、专家面板"怎么做" → 否决 bm25 lane + 标定 recall 金标

候选下一步是给 code_recall 加 bm25 lane。实测 recall MRR 0.917(6 查 5 个 rank-1)→ 近饱和。派 4 视角面板(IR/ROI/测量/产品)分析:

**一致否决 bm25 lane**:
- **IR**: bm25-over-符号名 **和** over-docstring **都**和 codegraph FTS5 冗余(它已索引 name+qualified_name+**docstring**+signature); 真盲区(body-text/语义改写)是**向量**的活非 bm25; ref 空间(chunk_id vs node_id)**fuse 不起来**(只增候选不叠分)。
- **测量**(最尖锐): 0.917 是**指标挪用** —— recall suite 本职是 weighted-vs-uniform **权重 A/B**, 非饱和度测量; 且 `recall.py:31` 仍是裸子串 `expect in name`(agent_e2e 已修 recall 漏修)。**6 查询既不能证饱和也不能证 gap。**
- **ROI**: 饱和指标追 ≤0.083 = 负 ROI 过度工程, 搁置进 §六。
- **产品**: 对开发者不可感(答案早在 top-3)。

**收敛执行(眼前便宜必做)**: recall 金标 substring→**token 边界匹配** —— 抽 `token_match` 进 `_common`(单一真值源, recall + agent_e2e 共用, 灭裸子串假阳)。诚实重测: **MRR 0.917 不变**(首命中稳)、**nDCG 0.858→0.897**(裸子串确实污染过 relevant 集)。`eae770c`。

**锁定的真课题(留新一轮, 非 recall 调优)**: **agent 顺错误前提幻觉**(产品+测量共识: 开发者会**弃用**的点, §31 已证非一行 prompt)。机制 = **工具结果带"能力边界"元信息**(让 `code_recall` 自报"只覆盖 graph+codegraph 符号、不含向量/文档检索"), 让 agent 从证据知前提为假而非脑补; 配 IR 提的 weighted_rrf **未用的 `boosts` 参数**(精确符号 boost, 最便宜的 recall 真增益)。**前置**: 先用已交付的非自评 judge + `--repeat` 钉 case6/case2 回归基线(没可信尺子改了也判不准 —— rule9 的教训)。

**bm25 lane 正式搁置。本轮净产出 = 否掉一个过度工程 + 标定 recall 金标一致化 + 锁定真课题。**

## 三十三、roadmap 盘点 + 下一轮立项(收尾)

**11-Phase 蓝图盘点**(对照 `code-intelligence-platform-plan-2026-06-07.md`):
- 🟢 完成/近完成(4): Phase 0(评测框架, 金标偏薄)、3(provenance/冲突/审计 ~90%)、5(多跳路径评分 MVP)、7(Query Planner 完整版)。
- 🟡 MVP/部分(4): Phase 1(IndexManifest MVP)、4(A1 业务域有 / Louvain 社区检测未, ~35%)、6(graph+codegraph 2 lane + code_recall MRR 0.917 / doc·vector·memory lane + reranker 未, ~55%)、10(部分 CLI+dashboard, ~25%)。
- 🔴 纯未启动重型(3): **Phase 2(统一 IR 解析引擎, 最大一块)、8(响应性能)、9(多语言 adapter, 依赖 2)**。
- 字面进度 ~55-60%; 但蓝图本就标"不整体启动、重型真实需求触发再做"。高 ROI 切片已交付, 实质到**平台期**。下一步真正值钱的不是补 Phase 2/8/9, 而是 agent **答案质量**(Phase 7 延伸)。

**下一轮立项**: [`anti-false-premise-plan-2026-06-10.md`](anti-false-premise-plan-2026-06-10.md)(`08b8bf3`)—— agent 抗错误前提幻觉, **validate-first** 分期(A 验证→B 机制→C measure), 红线"不重蹈 rule9"。**两个起步前提**: ① judge 非自评需 WSL 配非 deepseek provider key ② Phase A 的 false-premise 集 + 对照组顺带补 Phase 0 "≥20 真实金标" gate。新窗口 Read 该 plan 即可开工 Phase A。

---

**本会话(2026-06-09 22:22 → 06-10, ~38 commit, `b2c03a1`→`08b8bf3`)总览**: #10 只读 audit + #1 recall eval golden → impact.py 拆分 + web RBAC 测试隔离 → Phase 7 完整版(LLM planner 验证 + e2e eval E1-E4 + keyword-vs-LLM 端到端实证)→ 对抗审计 0 真 bug → eval 信度加固(token 边界灭假阳、自查金标缺陷、rule9 证伪回退)→ 专家面板否决 bm25 lane + 标定 recall 金标 → roadmap 盘点 + 抗错误前提 plan 立项。WSL 全量 1407 passed。全程纪律: 数据驱动 / 多模型 / determinism-first / 先证尺子准 / 零 delta 不留 / commit 无 AI 痕迹。
