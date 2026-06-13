# Phase 4 结构社区检测 — 实现 plan(2026-06-13)

> 蓝图 Phase 4。继 Phase 5 路径评分(`d0ffa7f`/`e7b72a1`)留白的"社区因子"。
> 三兄弟对抗讨论定调(必要性 / 算法依赖存储 / Phase 5 集成正确性)后落地。

## 一、定位与消费方(为什么做)

**Phase 4 = 在统一图谱硬骨架上做结构性社区检测**,给**每个**节点派一个确定性社区号。

**真实消费方 = 编码 agent(web agent / IDE agent / Claude / Codex)**,经新增 MCP 工具调用:
- `find_node_community(node)` — "这节点结构上和谁抱团" + 同簇成员
- `list_communities(project)` — 整仓模块地图(onboarding / 结构概览)

**与 A1 业务域互补不重复**:

| | A1 business_domain | Phase 4 community |
|---|---|---|
| 来源 | LLM 命名(贵、config 默认关) | 算法(免 LLM、确定性、默认有) |
| 覆盖 | 只 endpoint→表 | **全节点**(前端组件/函数/模块/符号) |
| 答 | "什么业务"(订单/库存) | "结构上属哪个簇 / 仓的模块地图" |

次要消费方:Phase 5 `find_impact_paths` 的"同社区路径加成"(本轮接,见 §五)。

## 二、核心架构洞察:复用 Analyzer 协议,核心编排零改

平台已有"硬骨架上叠软理解层"成熟模式:A1 业务域(LLM 语义)、A2 架构分层(LLM 角色)。
社区检测 = **第三个软层(算法结构)**,与 A1/A2 同构(都在 `_analyzers_pass`、都产软节点/软边、
都走 `Analyzer` 协议)。**故复用 `Analyzer` 协议,不新写 `_community_pass`**。

一个决定满足全部质量要求:

| 要求 | 复用 Analyzer 怎么满足 |
|---|---|
| 不堆码 | `ingest.py` **零改**(注册即自动参与 `_analyzers_pass`);白嫖 referential-integrity 校验 + 软隔离 + worker 刷新 |
| 低耦合 | 算法纯核 ↔ 图谱适配器 ↔ 已有编排 三层解耦 |
| 可扩展 | 注册式策略(registry+Protocol,零 if-else);换 Leiden = 换 analyzer |
| 必要才用模式 | **复用**已有 Strategy/Protocol;算法纯函数(无状态不套类) |

## 三、模块分层与职责

```
graph/community.py            算法纯核(functional core)
  detect_communities(nodes, edges) -> dict[node_id, community_id]
  纯 stdlib 确定性 Louvain,无 IO/无 networkx/无 random,可脱 store 单测。唯一职责=算社区。

graph/analyzers/community.py  图谱接入适配器
  class CommunityAnalyzer(Analyzer 协议): applies/analyze
  过滤软产物 → detect_communities → 产 COMMUNITY 软节点 + IN_COMMUNITY 软边。无算法逻辑。

graph/analyzers/__init__.py   无条件注册(确定性+免费,默认开;区别 LLM analyzer 默认关)
graph/schema.py               NodeKind.COMMUNITY + EdgeKind.IN_COMMUNITY + 进 SOFT_*_KINDS
plugins/ownership.py          POST_PASS_OWNERS 加 community:{builtin.analyzers}
graph/impact.py               find_node_community / list_communities(薄封装,镜像 find_node_domain)
                              (可选)_edge_quality 加 ≤1 跨社区惩罚
graph/mcp_server.py           2 个新 MCP 工具暴露给 agent
```

**数据流**:`worker → ingest_project → _analyzers_pass(已有)→ CommunityAnalyzer.analyze →
builtin.community 软层落盘 → ① agent 调 MCP 工具 / ② Phase 5 读社区映射算惩罚`。

## 四、关键决策

**① 算法:纯 stdlib 确定性 Louvain,不引 networkx**(对齐 A1 先例 + 无新依赖偏好)
- 确定性:`sorted(node.id)` 遍历 + 增益 tie-break 取 min-id(复用 A1 `_UnionFind` 纪律)+ 固定 eps
  + 无 `random` import + `max_passes` 封顶 + 社区号 = 成员最小 id 派生(重跑同图同号)。
- 效率:几百~几千节点 ms 级;`_MAX_NODES` 护栏超量 fail-soft 跳过 + warning(同 A1 `max_clusters`)。
- 图口径:跑**硬骨架**(复用 `is_soft_*` 过滤,不另造 kind 集)、有向当无向、首版等权 1.0
  (置信加权留后续质量不足再引,不预过度设计)、孤立节点自成社区。

**② 存储:落 `builtin.community` 软节点/软边,不碰 node.meta、不动 store 契约**
- 复用 A1/A2 软层 → 自动软隔离(`include_soft=False` 查依赖不污染)+ referential-integrity。
- COMMUNITY 软节点 id=`<pid>:community:c<rank>`(rank 按成员 min-id 排序,确定性);
  meta 带 `size`/`dominant_layer`。IN_COMMUNITY 软边:硬节点 --in_community--> 社区软节点。

**③ 消费方 = agent MCP 工具**:`find_node_community`/`list_communities` 是 `find_node_domain`
同构薄封装,复用 `build_impact_graph(include_soft=True)`/`_resolve`/`_node_brief`,几乎不加码。

**④ Phase 5 接线:≤1 跨社区惩罚,但 measure-first **gate 默认关**(对抗审计后修正)**
- `_best_paths` Dijkstra 要求每乘子 ≤1。**禁**"同社区 >1 加成"(破单调性→排序错)。
  做成同社区=1.0、跨社区=`_CROSS_COMMUNITY_PENALTY`(<1),`_best_paths` 单点乘 `_community_factor`。
- **默认关**(config `analyzers.community.path_penalty_enabled`=false):0.8 是拍的常数、未经 A/B
  证实增益,比照 planner_llm/rerank/A1 默认关纪律,**不静默改活工具(find_impact_paths)排序**。
  gate 在 `build_impact_graph(with_community=)` 数据注入侧:关→`g.community` 空→`_community_factor`
  恒 1.0→严格等于未接因子 baseline。`find_impact_paths` 读 config flag 决定开关。
- **社区软层 + 2 个 agent 工具不受此 gate**(加性安全,始终可用)—— 真消费方在这,Phase 5 因子是次要。
- 新鲜度因子:数据不现成(无 last_modified,要 git 扫描重活)→ **本轮不做**,形态(≤1)先记录。

## 五、设计模式(必要才用)

| 用 | 为何必要 | 刻意不做 |
|---|---|---|
| Strategy/Protocol(**复用** Analyzer)| 算法可换、核心零 if-else | 不新造"算法策略层+单实现"(YAGNI) |
| Functional core(纯函数)| 脱 IO 单测、确定性 | 不把无状态算法套类 |
| Adapter(CommunityAnalyzer)| 隔离算法与 schema | 算法层不碰落盘/查询 |

## 六、改动文件清单(集中且小,ingest.py 零改)

| 文件 | 改动 |
|---|---|
| `graph/schema.py` | +COMMUNITY/IN_COMMUNITY 枚举 + 进 SOFT_*_KINDS |
| `plugins/ownership.py` | POST_PASS_OWNERS +1 行 |
| `graph/community.py` | 新建,算法纯核 ~130 行 |
| `graph/analyzers/community.py` | 新建,适配器 ~50 行 |
| `graph/analyzers/__init__.py` | 无条件注册 CommunityAnalyzer |
| `graph/impact.py` | +2 查询函数;_edge_quality +社区因子 |
| `graph/mcp_server.py` | +2 MCP 工具 |
| `tests/` | 算法 + Phase 5 + agent 工具单测 |

## 七、验证

- 算法单测:确定性(打乱输入序同划分)+ 退化(孤立/空/超规模)+ 已知两团划分正确。
- **质量度量**:`community.modularity()` 纯函数 + 不变量单测(取值域 / 两团 Q>0 / 全合并 Q≈0 / 无边=0);
  `soft_quality` 加**结构社区轴**(复用 `_assess_axis`,coverage/giant(阈值 0.7)/singleton + modularity 诊断)。
- Phase 5 单测:同社区路径分>跨社区(注入)+ 默认关零影响 + 单调性回归。
- agent 工具单测:find_node_community / list_communities 命中 + 软边不污染查依赖(默认图 g.community 空)。
- **A1+Community 共存单测**:两 analyzer 同跑 `_analyzers_pass`(共享 builtin.analyzers 一次 upsert),
  两套软层各自正确、互不丢失、幂等重跑不翻倍(A1 用 FakeLabeler,注册表隔离)。
- 既有测试:社区每次 ingest 都产 → owner 测试(community→builtin.analyzers)+ schema/mcp spec 镜像同步。
- 真实仓:codev-platform 32 社区/93%/最大 8%、openclaw 64/98%/最大 9%(临时 store)—— 无巨型簇,优于 2026-06-11。

## 八、不做(防过度设计)

- 不做新鲜度因子(数据不现成,本轮不为它引 git 扫描)。
- 不做算法策略层(只 Louvain 一种)。
- 不做置信加权图(首版等权,质量不足再引)。
- 不碰 node.meta / store 契约。
- **不做 A1↔社区对齐率指标**(A1 默认关不可算 + 二者正交无理论对齐意义,做成指标会诱导错误结论)。
- **不现在做 Phase 5 排序 A/B**:find_impact_paths 实战几乎没用(code_recall 抢主检索),为它造 MRR/CI
  eval = 为没人用的因子造 eval(YAGNI)。故先把社区因子 gate 默认关,待该工具真被实战用 + 能量出
  增益再做 A/B 翻默认(measure-first 触发条件,记录在此)。
