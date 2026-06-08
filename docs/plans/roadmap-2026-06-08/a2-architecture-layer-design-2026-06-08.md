# 设计:A2 架构分层映射(综合理解层第二个 analyzer · 三兄弟会诊收敛)

> **产物性质**:roadmap-2026-06-08 子设计。承 roadmap-2026-06-05 的 A1(业务域映射, 实测 95%),
> 把综合理解层从 endpoint 链路**扩到架构分层**。经三视角会诊(产品/ROI · 架构复用 · AI 抗幻觉)收敛。
> **一句话**:A2 = A1 的"第二个 analyzer", 复用全套护栏范式给 file 归"架构角色"软标签 + 确定性违规检测,
> 近零增量成本、grounding 比 A1 更强;A3 降级、A4 砍(会诊用 grounding 判据证明 plan §六怀疑正确)。

---

## 一、会诊收敛(2026-06-08, 三兄弟一致)

统一判据:**LLM 输出能否解析回 closed-world ref 集 + 成本是否 O(域数) 而非 O(路径数/节点数)**。

| 特性 | 判定 | 理由 |
|---|---|---|
| **A2 架构分层映射** | ✅ 做(最高 ROI) | A1 第二个 analyzer, 复用全套范式, 离散标签可 reject + O(域数), 近零增量; grounding 比 A1 强(枚举闭集) |
| **A3 代码导览 tour** | 🟡 降级或砍 | 自由叙事 grounding 不住(解释层无真值)+ 成本 O(路径数)爆; 最多"真实 impact 路径 + 逐节点旁白 + on-demand" |
| **A4 web-ui + persona** | ❌ 砍 | persona grounding 完全不成立(主观投射无闭集真值)+ plan §六已判过度设计 + web-ui 是 human-first 花架子; 最多留 concise/verbose 确定性开关 |

**判据落点**:A2 双过、A3 半过(降级后过)、A4 双不过。本轮**只做 A2**(对齐 A1 狠切 MVP), A3 降 backlog, A4 砍。

---

## 二、A2 具体产出(给 agent 用, 不是给人看的图)

1. **分层角色软节点 `ARCH_LAYER`**(少量固定词表: controller / service / repository / domain-model / util / config / adapter / gateway)+ 软边 `PLAYS_ROLE`(file → layer)。与 A1 正交可叠加: 一个 file 既属"订单"域(A1)又是"service"层(A2)。
2. **分层违规 finding(确定性, 不靠 LLM)**: 有了 layer 标签后, 用已落库的 calls/imports 硬边 + role 偏序规则(controller→service→repository→model 单向)产 `Finding(kind="arch_violation")`。例: repository 逆向 calls controller、controller 跳 service 直 reads_table、跨边界 import 内部实现。**LLM 只提供 layer 标签这一个软输入, 违规判定全确定性。**

**对 agent 的用途**:
- `find_arch_role(file)` — agent 改代码前知道"这是 repository 层, 不该塞 HTTP 解析"。
- `find_arch_violations(project_id)` — agent PR/重构前自检"有没有制造逆向依赖/跨边界", 作为 grounding 喂回。
- 与 A1 组合: `understand(domain=订单)` 同时回"订单域涉及哪些层 + 哪些文件越层", agent 拿到"业务×架构"二维定位。

**MVP 不做**(会诊砍): 模块依赖图(codegraph 已有, 纯结构无需 LLM)、限界上下文识别(= A1 域 + 包边界, A1 已覆盖)。

---

## 三、复用 A1 范式(净新增极小)

| A1 机制 | A2 | 说明 |
|---|---|---|
| `Analyzer` 协议 + `register_analyzer` | 直接复用 | 新写 `ArchLayerAnalyzer` 实现协议 + `__init__` 一行注册 + config gate `analyzers.arch_layer.enabled`(默认关)。`_analyzers_pass` 已迭代所有 analyzer, **ingest 零改** |
| `validate_soft_result`(referential-integrity + conf 钳) | 直接复用 | 前提: `ARCH_LAYER`/`PLAYS_ROLE` 加进 `SOFT_NODE_KINDS`/`SOFT_EDGE_KINDS`。复用后悬空软边自动丢、confidence<1.0 自动钳 |
| 软/硬隔离(独立 kind + impact 默认过滤软边) | 直接复用 + 新增 2 枚举 | `impact.build_impact_graph(include_soft=False)` **自动**挡新软边在"查依赖"之外, 无需改 impact(护城河不破) |
| grounding-first(LLM labeler 喂 closed-world) | 复用范式, 换喂料 | A1 喂 impact report; **A2 喂每个 file 的确定性事实包**(import 出/入邻居、定义符号 kind、reads/writes 哪些表、含不含 endpoint)。LLM 只能在**枚举层词表里选一个**, 越界词解析剔除(同 A1 ref 越界剔除) |
| `DomainLabeler`(隔离 LLM) | 新增同构 `LayerLabeler`(入 `FileFactPack` 出 `FileRole`) | Fake 全确定性测、Brain 接 brain。隔离边界照抄 A1 |
| cluster-batch(O(域数)) | 复用 + 改聚合单位 | 按**目录/包 batch**(开发者按层组织目录, `services/`/`repositories/` 是强先验, 如 A1 用 file 作主信号); 甚至可**并入 A1 一次 call**(同 cluster 同时出 business+arch 标签 → 近零增量成本) |
| fingerprint 缓存(含 model+prompt_version) | 直接复用模式 | key = `hash(FileFactPack + labeler.signature)`; file 事实变则失效 |
| ownership 一键纠错 | 直接复用模式 | `set_role_override(project_id, file, role)`, key = file 路径(比 A1 endpoint-set hash 更稳) |

**净新增**: 1 analyzer 类 + 1 labeler 接口 + 1 brain labeler + 2 枚举 + 1 组违规规则 + 3 查询入口。**框架/ingest/impact/store/软硬隔离全不动** —— A1 的护城河基建本为"任意软 analyzer"设计, A2 是验证其可复用的二号租户。

---

## 四、与 codegraph 不重叠(划清边界)

- **codegraph 已有(A2 绝不重做)**: 符号定义、calls/callers/callees、import 依赖、impact 反向遍历。"A 依赖 B"这条事实是 codegraph + 硬边领域, A2 一行不碰。
- **A2 真增量**: 给结构边附"架构语义"标签。codegraph 知道 `OrderRepo.save() calls OrderService.notify()`(事实), 但不知道这是"repository→service 逆向依赖, 违反分层"(它不知道哪个是 repository)。**层角色是项目特定语义, 确定性工具从符号名无法可靠推断**(`UserManager` 是 service 还是 controller? 正则必碎)——这是 LLM labeler 的增量点。
- **一旦有语义标签, 违规检测重新落回确定性**(layer × 硬边 × 规则), 不让 LLM 判违规。LLM 只承担"命名→角色"它独有优势的归纳。

---

## 五、MVP 切法 + 可证伪验收(对齐 A1 ≥70%)

**MVP 范围(狠切)**:
1. schema 加 `ARCH_LAYER`/`PLAYS_ROLE` 并入 SOFT 集合。
2. `LayerLabeler` 接口 + `FileFactPack`/`FileRole` dataclass + Fake/Brain 实现。
3. `ArchLayerAnalyzer`: 确定性建 FileFactPack(纯读已落库硬节点/边, **零新扫描**)→ 按目录 batch 喂 labeler → 回填软节点/边 → 复用缓存/override。
4. 确定性 `detect_arch_violations`: 遍历 calls/imports 硬边按 role 偏序产 `Finding`。
5. 3 个只读查询入口(impact.py 风格, **不在工具内触发 LLM**)+ graph mcp_server 加 `find_arch_role`/`find_arch_violations`/`list_layer_members`。
6. config gate 默认关 + fail-soft + cost cap(目录数上限)。

**分阶段**: A2-1 框架/枚举/接口/FakeLabeler 全确定性测(不调 LLM 跑通)→ A2-2 接 BrainLayerLabeler + 缓存 → A2-3 违规规则 + 验收。

**可证伪验收(硬止损线)**:
- **角色标注准确率 ≥70%**(抽 1 仓 20 file 人工核对, 低于即方向证伪止损 —— 与 A1 同一把尺)。
- **违规检测精确率 ≥80%**(抽 arch_violation finding 人工核对; 噪声大则 agent 学会忽略 = 等于没做)。
- **隔离不变量(继承 A1, 必断言)**: `build_impact_graph(include_soft=False)` 后无 ARCH_LAYER 节点/PLAYS_ROLE 边; 软节点 confidence<1.0; referential-integrity 断言悬空软边被丢。
- **成本**: 全仓首次 LLM call = O(目录数) 非 O(文件数); 二次 ingest 缓存命中 ~100%。

任一条不达标 → A2 止损, 不堆"分层可视化"补救。

---

## 六、A3/A4 处置(会诊明确)

- **A3 tour**: 砍"自由叙事散文"(grounding 不住解释层 + O(路径数)爆); **降级 backlog** —— 仅在 A2 验证通过后, 评估"沿真实 impact 路径逐节点贴已 ground 旁白 + on-demand 不预生成 + 越界 reject + 解释内容标 confidence<1.0/AI推测"。坚持自由叙事则整体砍。
- **A4 web-ui + persona**: **砍 LLM 化**。persona grounding 完全不成立, plan §六已判过度设计。唯一保留: `concise/verbose` 两档确定性开关(控已 ground 内容详略, 不引入 LLM 人格生成)。

---

## 七、关联

- 上游: `../roadmap-2026-06-05/next-plan-2026-06-05.md`(A1 三护栏 + §六不做清单)、A1 实现 `graph/analyzers/`
- 哲学母版: `.claude/rules/agent-provider-architecture.md`(协议族 + registry + config 驱动)
- 复用基建: `graph/analyzers/base.py`(Analyzer 协议 + validate_soft_result)、`business_domain.py`(cluster-batch + 缓存 + ownership 模板)、`domain_labeler.py`(LLM 隔离接口参照)、`schema.py`(SOFT_*_KINDS)、`impact.py`(只读查询入口 + include_soft 分离)
