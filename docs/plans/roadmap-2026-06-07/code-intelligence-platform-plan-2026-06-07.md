# codev-platform 代码智能平台升级计划

> **一句话定位**: codev-platform 不是模型训练项目，而是多项目代码智能基础设施。它把多语言代码解析、索引构建、向量排序、统一图谱、多跳推理、社区检测、查询规划和 Web agent 响应统一成一个可评测、可扩展、可分发的平台。

---

## 一、定位

当前 codev-platform 已经具备几块底座:

- 多项目配置和 project_id 管理。
- Chroma 多租户向量索引。
- BM25、RRF、reranker 等检索排序能力。
- 统一 graph store、跨层影响分析、多跳 BFS。
- MCP server 编排和 Web agent 对话入口。
- 多模型 provider registry，以及 DeepSeek 等模型的提示词 / rule / skill pack 适配。

但这些能力目前更像“工具集合”，还没有完全变成“代码智能系统”。下一阶段要做的是把它升级为:

- **索引构建系统**: 每个项目、每种索引、每次构建都有 manifest、版本、来源、耗时、覆盖率和 freshness。
- **代码解析引擎**: 不同语言和框架通过 parser / adapter / emitter 输出统一 IR，避免靠 grep、正则、LLM 猜结构。
- **统一图谱系统**: 代码符号、API、页面、表、配置、任务、文档、记忆都能进入一套带 provenance / confidence 的图。
- **检索排序系统**: vector、BM25、graph neighborhood、memory、doc search 不是各查各的，而是统一召回、融合、重排、解释。
- **查询响应系统**: Web agent 按问题类型规划工具、控制预算、组合上下文、解释证据，并可用评测集回归。

边界也要说清楚:

- 不是训练大模型。
- 不是做真正模型内核级 sparse attention。
- 不是替代 IDE。
- 不是把所有业务规则塞进 codev-platform 分发源。

这里的“稀疏注意力”应落成工程能力: **graph-guided sparse context selection**，也就是从巨大代码库里用图谱和检索选择少量高价值上下文，而不是改 Transformer attention kernel。

## 二、决策前提

### 2.1 已有基础

- `codev_platform/chroma/`: 已有向量检索、BM25、RRF、reranker 相关能力。
- `codev_platform/agent/recall/`: 已有 agent memory 的 scorer / fusion / reranker 流水线雏形。
- `codev_platform/graph/`: 已有统一图谱 ingest、linker、impact、多跳 BFS、endpoint/function 桥接。
- `codev_platform/agent/`: 已有 Web agent、tool registry、provider registry、prompt profile、rule pack、skill pack。
- `web-ui/`: 已有 agent 对话页和工具调用展示。

### 2.2 主要缺口

- 缺少统一 index manifest，无法回答“这个项目当前索引是否新鲜、完整、可复现”。
- 缺少一等公民的多语言解析引擎，语言 / 框架能力分散在脚本、插件或图谱 ingest 里。
- 图谱边缺少统一 provenance、confidence、evidence，冲突来源难审计。
- 多跳影响分析能走，但路径评分、解释、剪枝和社区边界还不够强。
- 检索侧有 vector/BM25/RRF/reranker，但还缺 query classifier、weighted fusion、graph+vector 联合召回和评测闭环。
- Web agent 有工具调用，但还缺稳定的 query planner、工具预算、停止条件和响应质量指标。
- 多模型适配已经开始，但 prompt/rule/skill 的能力包还没有和项目类型、语言栈、agent 场景完全绑定。

### 2.3 总体原则

- **先评测再优化**: 没有指标的“优化”只能算改动。
- **先 IR 再图谱**: 不同语言先归一成统一 IR，再进入 graph store。
- **确定性优先**: AST、LSP、框架 adapter 优先；LLM 只做标签、摘要、候选解释，不能当主解析器。
- **来源可审计**: 每个节点、边、召回结果都要知道来自哪个 parser、哪个文件、哪一行、哪个索引版本。
- **可降级**: parser 缺失、reranker 不可用、graph 不新鲜时，系统仍能给出低置信度但不崩溃的答案。
- **多项目隔离**: project_id、org_id、tenant 边界不能被索引、图谱、记忆或缓存打穿。

## 三、Phase 划分

### Phase 0: 评测基线与验收指标

**目标**: 先建立一套能判断“系统有没有变好”的最小评测框架。

**范围**:

- 建立 `eval/code_intelligence/` 评测集目录。
- 覆盖 4 类问题:
  - 项目概览类: “这个项目有什么用？”
  - 修改面分析类: “聊天记录新增字段前后端要改哪里？”
  - 符号 / 调用链类: “某个函数谁调用、它又调用谁？”
  - 规则 / 文档类: “这个操作应该遵守什么规则？”
- 每条 case 保存:
  - query
  - expected_files
  - expected_symbols
  - expected_tables / endpoints / pages
  - forbidden_behaviors
  - min_answer_requirements
- 指标:
  - Recall@K: 关键文件 / 节点是否召回。
  - MRR / nDCG: 关键证据排序是否靠前。
  - Tool Count: 工具调用是否失控。
  - Latency P50/P95。
  - Grounding Rate: 答案中可追溯证据比例。
  - Wrong Architecture Hint Rate: 是否把 Java/Flyway/stock 规则误套到 Python/FastAPI 平台。

**Estimate**: 1-2 天。

**Gate**:

- 至少 20 条 golden case。
- 有 CLI 能跑全量评测并输出 JSON + Markdown summary。
- 当前主干跑出 baseline，不要求分数高，但分数可重复。

### Phase 1: 索引构建 Manifest / DAG / Freshness

**目标**: 让索引构建从“跑脚本产物”变成“可追踪的数据管线”。

**范围**:

- 定义统一 `IndexManifest`:
  - project_id
  - repo_path
  - git_commit
  - source_hash
  - index_kind: docs / chroma / codegraph / graph / memory / parser_ir
  - builder_version
  - started_at / finished_at
  - status
  - file_count / node_count / edge_count / chunk_count
  - warnings / errors
  - output_location
- 定义构建 DAG:
  - file scan
  - language parse
  - framework adapter
  - IR emit
  - codegraph index
  - graph ingest
  - doc chunk
  - vector embed
  - BM25 build
  - rerank metadata build
- 支持全量构建和增量构建。
- 支持 atomic handoff: 新索引构建成功后再切换 current pointer。
- Web dashboard 暴露 freshness:
  - 当前 commit
  - 最后构建时间
  - 索引是否落后工作区
  - 失败阶段
  - 最近错误。

**Estimate**: 2-3 天。

**Gate**:

- 每次索引构建都写 manifest。
- `validate` 或新增 `index status` 能看到每类索引 freshness。
- 构建失败不会污染当前可用索引。

### Phase 2: 代码解析引擎一等公民化

**目标**: 建立跨语言、跨框架的解析内核，不再把解析逻辑散落在具体 ingest 代码里。

**核心抽象**:

```text
LanguageParser
  输入: 文件内容、路径、语言
  输出: 原始 AST / tokens / symbol facts

FrameworkAdapter
  输入: language facts + 项目配置
  输出: 框架语义 facts，如 route、api_call、component、orm_entity、sql_ref

UnifiedIREmitter
  输入: parser facts + framework facts
  输出: 统一 IR 节点和边

GraphEmitter
  输入: 统一 IR
  输出: graph store nodes / edges，带 provenance + confidence
```

**统一 IR 初版**:

- Symbol:
  - function
  - class
  - method
  - variable
  - interface
  - type_alias
- Web:
  - route
  - endpoint
  - api_call
  - page
  - component
  - hook
  - store
- Data:
  - db_table
  - db_column
  - migration
  - orm_model
  - sql_statement
- Dependency:
  - imports
  - calls
  - renders
  - reads
  - writes
  - exposes
  - consumes
  - configured_by

**优先语言 / 框架顺序**:

1. Python + FastAPI + Pydantic + SQLAlchemy/psycopg。
2. TypeScript/TSX + React + Umi/Ant Design。
3. Java + Spring MVC + MyBatis/Flyway。
4. Node.js + Express/NestJS。
5. Vue 3 + Vite。
6. .NET + ASP.NET Core + EF Core。
7. Angular。

**解析策略**:

- Python: AST + import graph + FastAPI decorator adapter。
- TS/TSX: Tree-sitter 或 TypeScript compiler API / ts-morph，识别组件、hooks、service API 调用。
- Vue: SFC parser + script setup + template component refs。
- Angular: TypeScript AST + decorator adapter + template parser。
- Java: JavaParser / tree-sitter-java + Spring/MyBatis adapter。
- .NET: Roslyn 优先；没有 Roslyn 环境时降级 tree-sitter-c-sharp。
- SQL: sqlglot / tree-sitter-sql，识别表、列、读写。

**Confidence 规则**:

- AST / compiler API: 1.0
- Framework adapter: 0.85-0.95
- Config / route manifest: 0.8-0.95
- Existing codegraph bridge: 0.7-0.85
- Regex fallback: 0.4-0.6
- LLM annotation: 0.3-0.6，默认不得作为硬依赖边。

**Estimate**: 3-5 天建立框架，后续每个 adapter 1-3 天。

**Gate**:

- 有 `ParserPlugin` / `FrameworkAdapter` / `UnifiedIREmitter` 接口。
- Python FastAPI 和 React TSX 两套 golden fixtures 通过。
- 每个项目能输出 capability matrix，明确哪些语言 / 框架是 full / partial / unsupported。

### Phase 3: 图谱融合、Provenance 与冲突审计

**目标**: 图谱不只是“有节点有边”，而是每个事实可解释、可审计、可冲突处理。

**范围**:

- 图谱节点 / 边统一增加:
  - source_kind: ast / framework_adapter / config / doc / memory / llm / manual
  - source_file
  - source_line
  - parser_name
  - parser_version
  - index_manifest_id
  - confidence
  - evidence
- 冲突处理:
  - 同一 endpoint 被多个 parser 识别。
  - 同一 api_call 被多个 adapter 给出不同目标。
  - 同一表名跨 schema / project 冲突。
- 增加 graph audit:
  - dangling edges
  - duplicate nodes
  - low-confidence hot paths
  - no-source facts
  - cross-tenant leakage check。

**Estimate**: 2-3 天。

**Gate**:

- impact_analysis 返回路径时能展示关键边来源和 confidence。
- graph audit 能输出 Markdown 报告。
- 低置信度边默认不参与高风险改动结论，只作为候选提示。

### Phase 4: 社区检测与业务域图谱

**目标**: 让系统知道“哪些文件 / 表 / 页面 / 接口属于同一个功能域”，减少多跳遍历噪声，提升问答聚焦能力。

**范围**:

- 构建 project-level graph projection:
  - symbol graph
  - file graph
  - module graph
  - endpoint-table-page graph
- 社区检测算法:
  - MVP: Louvain / Leiden 任选一个可维护实现。
  - fallback: connected components + path density。
- 社区命名:
  - 规则命名: 路径、endpoint prefix、表名前缀、模块名。
  - LLM 只做 label suggestion，不做社区归属真值。
- 社区产物:
  - community_id
  - members
  - top_files
  - top_symbols
  - representative_endpoints
  - representative_tables
  - label
  - confidence
- 用途:
  - impact_analysis 剪枝。
  - search_docs / codegraph_search rerank。
  - Web agent 回答“某功能在哪些模块”。
  - Dashboard 展示项目功能域地图。

**Estimate**: 2-4 天。

**Gate**:

- 每个项目构建后能产出 communities。
- 查询“聊天记录功能在哪里”时，能优先返回 agent chat/session 相关社区，而不是全仓泛化结果。
- impact_analysis 可按 community 扩展 / 收缩影响面。

### Phase 5: 多跳路径评分与解释

**目标**: 多跳不是 BFS 全吐，而是按路径质量排序并解释为什么相关。

**范围**:

- Edge weight:
  - calls / imports / exposes / consumes / reads / writes / renders 各自默认权重。
  - AST 边权重大于 regex / LLM 边。
  - 同社区边权重加成。
  - 跨层关键边，如 page -> api -> route -> function -> table，加成。
- Path scoring:
  - depth decay
  - confidence product / min-confidence
  - community coherence
  - edge type priority
  - freshness penalty
- Path explanation:
  - 每条路径给出 `file:line` 证据。
  - 区分“确定依赖”和“候选依赖”。
  - 输出 top-N paths，不输出不可读大图。

**Estimate**: 2-3 天。

**Gate**:

- impact_analysis 输出 top paths + score + evidence。
- 对同一 query，路径排序稳定可测。
- 多跳展开默认有预算，不会因为高出度节点爆炸。

### Phase 6: 检索排序升级: Graph + Vector + BM25 联合召回

**目标**: 把现在分散的文档检索、代码符号检索、图谱邻域、记忆召回合成一个可配置检索系统。

**范围**:

- Query classifier:
  - overview
  - how-to
  - code symbol
  - impact analysis
  - doc/rule
  - bug investigation
  - frontend/backend chain
  - data/table chain
- Retrieval lanes:
  - vector docs
  - BM25 docs
  - codegraph symbols
  - graph neighborhood
  - memory
  - recent files / manifest freshness
- Fusion:
  - weighted RRF
  - lane-specific top_k
  - community boost
  - freshness boost
  - exact symbol boost
- Rerank:
  - reranker 只吃压缩候选，不吃全量。
  - reranker 不可用时降级 weighted RRF。
- Evaluation:
  - Phase 0 的 MRR/nDCG 作为 gate。

**Estimate**: 3-5 天。

**Gate**:

- 相比 baseline，关键任务 MRR/nDCG 有可量化提升。
- 召回结果能解释来自哪个 lane、为什么排前。
- reranker 关闭时系统仍能稳定回答。

### Phase 7: Query Planner / Tool Budget / Stop Conditions

**目标**: Web agent 不再把工具调用当自由发挥，而是根据问题类型生成短计划、受预算执行、满足停止条件后回答。

**范围**:

- Planner 输入:
  - user query
  - project_id
  - provider profile
  - rule pack
  - skill pack
  - index freshness
  - available tools
- Planner 输出:
  - query_type
  - required lanes
  - tool budget
  - stop conditions
  - answer contract
- 工具预算示例:
  - 项目概览: list_dir + search_docs + README/read_file，不超过 3-5 次工具。
  - 修改面分析: impact/table/api/page/codegraph 优先，不超过 8-12 次工具。
  - 文档规则: search_docs -> read_file，不超过 4 次工具。
  - 符号链路: codegraph_search -> callers/callees -> read_file，不超过 6 次工具。
- Stop conditions:
  - 关键文件已覆盖。
  - 正反向链路都验证。
  - 已有足够证据回答。
  - 超预算时必须说明残余风险。

**Estimate**: 2-3 天。

**Gate**:

- Web agent 每次回答可输出 internal trace / debug trace。
- 常见问题工具调用数下降，答案证据率上升。
- 不再出现“跳过链路和文档直接猜目录”的默认行为。

### Phase 8: 查询响应性能与可观测性

**目标**: 让查询响应不只是“能答”，还要快、稳定、可定位慢点。

**范围**:

- Latency budget:
  - overview: P50 < 3s，P95 < 8s。
  - impact: P50 < 8s，P95 < 20s。
  - deep investigation: 可超过 20s，但必须流式展示进度。
- Cache:
  - query classification cache
  - graph neighborhood cache
  - rerank result cache
  - doc chunk embedding cache
  - parser IR cache
- Streaming:
  - tool trace streaming
  - partial evidence streaming
  - final answer synthesis。
- Observability:
  - 每次查询记录 query_id。
  - 记录每个 lane 耗时、候选数、top result。
  - 记录 reranker 耗时和是否降级。
  - Dashboard 展示 P50/P95、失败率、工具调用数、无证据回答比例。

**Estimate**: 2-4 天。

**Gate**:

- Web agent debug 面板能看到 query trace。
- eval 输出 latency 分布。
- reranker / graph / chroma 任一组件慢时能定位。

### Phase 9: 多语言 / 多框架 Adapter 扩展

**目标**: 让平台真正适配不同业务系统，而不是把某一个业务仓的架构写死。

**Adapter 交付模板**:

- 支持语言 / 框架版本。
- 可识别节点类型。
- 可识别边类型。
- fixtures。
- false positive / false negative 已知边界。
- fallback 策略。
- capability matrix。

**优先级建议**:

1. codev-platform 自己: Python FastAPI + React TSX。
2. 量化业务仓: Java Spring/MyBatis/Flyway + React/Vue 按真实仓库确认。
3. Node/NestJS。
4. .NET ASP.NET Core。
5. Angular。

**Estimate**: 每个 adapter 1-3 天，复杂框架 3-5 天。

**Gate**:

- 每个 adapter 至少 10 个 fixture。
- 能进入统一 IR。
- 能生成 graph nodes / edges。
- 能被 impact_analysis 和 query planner 使用。

### Phase 10: 治理、分发与产品化

**目标**: 把这些能力变成其他业务项目能接入、能验证、能维护的底座。

**范围**:

- 配置:
  - project language stack
  - enabled parsers
  - enabled adapters
  - indexing schedule
  - retrieval profile
  - provider prompt/rule/skill pack
- CLI:
  - `codev-platform index build`
  - `codev-platform index status`
  - `codev-platform graph audit`
  - `codev-platform eval code-intelligence`
  - `codev-platform parser capabilities`
- Dashboard:
  - index freshness
  - graph quality
  - parser coverage
  - query metrics
  - community map
- 文档:
  - 业务仓接入指南。
  - adapter 开发指南。
  - eval case 编写指南。
  - provider prompt/rule/skill pack 配置指南。

**Estimate**: 2-4 天。

**Gate**:

- 一个新业务仓能按文档接入，跑出 index status、graph audit、eval baseline。
- 平台自身和至少一个业务仓通过端到端验证。

## 四、风险

1. **解析器范围失控**: 语言和框架太多，容易每个都做半截。
   - 控制: 先做接口和 capability matrix，再按项目优先级补 adapter。

2. **图谱噪声污染影响分析**: 低置信度边进入核心路径会导致误报。
   - 控制: confidence + provenance 强制落库，高风险结论默认只用高置信度边。

3. **多跳爆炸**: 高出度节点导致 BFS 结果不可读、响应变慢。
   - 控制: path scoring、depth decay、community pruning、tool budget。

4. **检索优化无评测**: 排序改动看起来更聪明，但实际可能更差。
   - 控制: Phase 0 先建 baseline，后续每次优化必须跑 MRR/nDCG/latency。

5. **Web agent 变成 prompt 堆叠**: 不同模型、不同业务项目、不同工具靠提示词硬撑。
   - 控制: prompt/rule/skill pack 只负责行为约束，真正能力放在 planner、retrieval、graph、parser。

6. **业务架构误套**: 平台把某个业务仓的 Java/Flyway/React 规则分发给所有项目。
   - 控制: codev-platform 分发源只放通用规则；Web agent 指令走 `codev_platform/agent/instructions/`；项目特定能力由 config 选择。

7. **索引陈旧导致错误答案**: 代码变了但 graph/vector 旧。
   - 控制: manifest freshness、dashboard 告警、回答时暴露索引 commit。

8. **多租户泄漏**: 多项目共享 chroma/graph/cache 时串数据。
   - 控制: project_id/org_id 强制参与索引 key、查询过滤、缓存 key 和 audit。

9. **LLM 幻觉参与结构事实**: LLM 生成不存在的节点或边。
   - 控制: LLM 只做标签、摘要、候选解释；无文件行号证据不得进入硬图谱。

## 五、替代方案

### 5.1 只优化 prompt

不够。prompt 能改善工具选择，但不能解决索引陈旧、解析缺失、图谱噪声、排序无指标、响应慢这些工程问题。

### 5.2 LLM-only 解析代码

不作为主路线。LLM 适合摘要、命名、解释和少量候选补全，不适合作为跨语言结构事实的真值源。

### 5.3 Regex-only 解析

只能做 fallback。它能快速补洞，但复杂框架、泛型、装饰器、模板、动态 import 都容易误判。

### 5.4 直接接第三方代码搜索平台

可以作为参考或补充，但不能替代 codev-platform 的多项目隔离、业务规则、agent 工具、graph store 和本地部署约束。

### 5.5 真做模型级 sparse attention

暂不做。当前收益最大的不是改模型结构，而是用图谱和检索做 sparse context selection，把有限上下文喂给现有模型。

## 六、启动条件

启动前必须满足:

1. 选定首批 2 个验证仓:
   - codev-platform 自身。
   - 一个真实业务仓。
2. 冻结 Unified IR v0.1:
   - 节点类型。
   - 边类型。
   - provenance 字段。
   - confidence 规则。
3. 建立 Phase 0 golden cases:
   - 至少 20 条。
   - 覆盖项目概览、修改面、符号链路、规则文档。
4. 当前服务状态可用:
   - Web agent health OK。
   - graph / chroma / codegraph 当前能力可跑 baseline。
5. 明确短期非目标:
   - 不训练模型。
   - 不做真实 sparse attention kernel。
   - 不一次性支持所有语言。
   - 不把业务仓专属规则塞回平台通用分发源。

## 七、建议执行顺序

第一轮不要贪多，建议按这个顺序:

1. Phase 0: 评测基线。
2. Phase 1: IndexManifest + freshness。
3. Phase 2: Parser engine 接口 + Python/FastAPI + React/TSX fixtures。
4. Phase 3: Graph provenance/confidence。
5. Phase 7: Query planner 最小版。

原因很简单: 没有评测，后面的优化不可证明；没有 manifest，查询不知道索引是否可信；没有 parser IR，图谱继续靠零散逻辑；没有 provenance，影响分析不可审计；没有 planner，Web agent 仍然会把工具乱序调用。

第二轮再做:

1. Phase 4: 社区检测。
2. Phase 5: 多跳路径评分。
3. Phase 6: 联合召回排序。
4. Phase 8: 响应性能。
5. Phase 9/10: 多语言扩展和治理产品化。

## 八、最终系统形态

这些都做完后，codev-platform 的形态会从“AI 工具底座”升级为:

```text
Multi-project Code Intelligence Platform

Project Registry
  -> Index Build DAG
  -> Multi-language Parser Engine
  -> Unified IR
  -> Graph Store + Vector Store + BM25
  -> Community Detection
  -> Query Planner
  -> Retrieval Fusion + Reranker
  -> Web Agent / MCP Tools / Dashboard
  -> Eval + Audit + Freshness Governance
```

到那时，业务开发问“这个功能怎么改”，系统应该能做到:

- 先判断问题类型。
- 检查索引是否新鲜。
- 定位相关社区。
- 用 graph 找前端、接口、后端、表、配置和文档链路。
- 用 vector/BM25 找规则、设计和历史决策。
- 用 reranker 精排证据。
- 给出需要修改的文件、风险、验证项和残余不确定性。
- 全程可追溯到文件、行号、索引版本和图谱路径。

这才是平台真正有价值的地方: 不是“模型更会说”，而是“系统知道它依据什么说”。

