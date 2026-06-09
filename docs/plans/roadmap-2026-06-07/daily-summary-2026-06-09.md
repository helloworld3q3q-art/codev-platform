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
- ⚠️ **真实 MRR/nDCG 复跑是 WSL 步**: recall suite 在 Windows **skip**(需 codegraph.db, 在平台/WSL 侧 junction)。要拿排除测试 ref 后的真实数字, 需 push `4cdf204` + WSL pull 后跑 `python -m eval.run_eval --suite recall --project codev-platform`。

## 十九、运维上线 + 全栈验证

1. **重启 `codev-agent` + `codev-web`**(WSL systemd)→ 让**前两窗口已提交**(≤`b2c03a1`, WSL clone 已有)的 #1/#2 安全修复 + code_recall agent 工具 + planner 主检索 **live**(代码已 pull、服务持旧内存码)。
2. **`wsl --shutdown` 全栈干净重起**(顺带清前述内存危机残留): systemd `running`, 6 服务自启 `active`; 4 MCP 端点全 **OK**(platform-docs 19083 冷启动模型加载完成 / codegraph 19091 / agent-memory 19087 / graph 19092); `codev-web` :18088 `/docs` HTTP 200。
3. **Claude 重启后端到端绿**: codegraph MCP(653 文件 / 8528 节点)+ graph MCP 均正常响应。

## 二十、审计状态 + 文档

- 综合审计(`docs/audits/codev-platform-consolidated-audit-2026-06-09.md`): **9/11 → 10/11**(#10 收掉)。
- 延后 2 项(真实需求触发再做): **#7** config DI `rebind_web_services`(改动大、单实例价值低)/ **#8 残留** `set_roles` 多 org 角色管理(RBAC 安全敏感, 改错会跨 org 越权, 要先补越权测试)。
- 新增 [`next-steps-2026-06-09.md`](next-steps-2026-06-09.md): 审计剩余 3 项细节 + 三梯队整体剩余计划 + 新窗口开局动作(供下个窗口直接 Read)。

**纯未启动重型 Phase 仍只剩 2(统一 IR)和 8(性能)**。本窗口 1 commit(`4cdf204`)。
