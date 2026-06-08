# roadmap-2026-06-08 迭代规划目录(当前主目录)

> **当前主目录** —— 承 [`../roadmap-2026-06-05/`](../roadmap-2026-06-05/)(代码需求已闭环, 见其落地状态表)。
> **主题**: 综合理解层 **A2 架构分层映射**(A1 业务域之后的第二个 analyzer)+ 上轮收尾继承。
> **纪律延续**: A1 的狠切 MVP + grounding-first + 软硬隔离 + ≥70% 可证伪验收, A2 全套复用。

---

## 文件清单

| 文件 | 内容 | 状态 |
|---|---|---|
| [a2-architecture-layer-design-2026-06-08.md](a2-architecture-layer-design-2026-06-08.md) | **子设计(三兄弟会诊: 产品/ROI · 架构复用 · AI 抗幻觉)**: A2 架构分层映射 —— 复用 A1 全套护栏给 file 归架构角色软标签(`ARCH_LAYER`/`PLAYS_ROLE`)+ 确定性违规检测; 用 grounding 判据**证明 plan §六怀疑正确**(A3 降级、A4 砍) | ✅ **代码全落地 + 真 deepseek 全栈验收通过**(171 file: 后端 ~91% + 前端分层准, prompt v4) |

## A2 落地状态(2026-06-08)

| 阶段 | 内容 | commit |
|---|---|---|
| **A2-1** 确定性骨架 | `ARCH_LAYER`/`PLAYS_ROLE` 软枚举 + `LayerLabeler`(closed-world)+ `FakeLayerLabeler` + `ArchLayerAnalyzer`(建 FileFact / 按目录 batch / 越界剔除 / validate 复用) | `54c8662` |
| **A2-2** 接 LLM | `BrainLayerLabeler`(双层 grounding + 双越界剔除 + 韧性解析)+ per-batch fingerprint 缓存 + `__init__` config gate 注册 | `8cb7ca5` |
| **A2-3** 查询 + 违规检测 | `find_arch_role`/`list_layer_members`/`find_arch_violations`(graph mcp 第 9-11 tool); 违规=layer 软标签 × calls/imports 硬边 × 偏序规则(确定性, LLM 只供标签) | `b2b5dde` |

**全套 1216 passed, 0 failed**; 全程复用 A1 护栏(软隔离 / 越界 reject / fail-soft / 缓存)。

### ✅ 真 deepseek 验收通过(WSL, 2026-06-08) —— 含 2 个只有真图谱才暴露的 bug 修复

| 项 | 结果 |
|---|---|
| **真图谱 bug ①** | graph **不建 FILE kind 节点**(451/455 节点用 `node.file` 属性) → `ArchLayerAnalyzer` 找 FILE 节点恒空 → 整个 A2 no-op。改用 `node.file` 聚合 + 节点级 `PLAYS_ROLE`(`6f09d3a`)。FakeLabeler 单测造 FILE 节点发现不了 |
| **真图谱 bug ②** | `LAYER_ROLES` 是后端分层词表 → web-ui **133 前端文件全被硬塞 util 噪声**。先跳过纯前端(`87a5ab5`), 后做前端词表(见下) |
| **后端准确率(v1)** | 33 后端文件人工核对: **79% 严格 / 94% 宽松, ≥70% 达标**。`web/routes/*` 17 全对 controller / `repositories/`+`*_pg` 准 repository / `tables.py` domain_model。2 错(`_checks.py`/`platform_status.py` 应 service 被标 repository) |
| **service few-shot(v2, `093a32e`)** | prompt 加 service vs repository 判据 + 4 角色 few-shot → 准确率 **79%→~91% 严格**。`_checks.py`/`platform_status.py` repository→**service** 修对; `codegraph_client.py`/`bridge_codegraph.py` repository→**adapter** 修对。v2 分布 service 3 / repository 10 / controller 17 / adapter 2 / domain_model 1(service 0→3、adapter 0→2) |
| **前端分层词表(v3+v4, `d2434dd`/`1baf035`)** | 前后端各用各词表(前端 page/component/api/store/hook), `FileFact.is_frontend`/`is_page`(确定性 is_page 喂 LLM 作 page 强先验), labeler 按 file 选词表校验(跨词表即剔)。**133 前端文件从全 util 噪声 → 有意义分层**: `component 60 / page 17 / api 17 / store 7 / config 3 / util 29`(components/→component、pages→page、services/apis→api 全准)。v4 修 v3 加前端时压缩后端致 `platform_status` 退化(明确 domain_model=纯数据结构无逻辑) |
| **违规检测** | 102 条 calls 边**全覆盖角色**(controller→repository 正向), 判定 violations=0 是真实(codev-platform 后端架构干净, 无逆向依赖), 非漏检假 0 |
| **最终 v4 全栈** | 后端 `service 5 / repository 8 / controller 17 / adapter 2 / domain_model 1`(~91%, domain_model 只 tables.py 精确)+ 前端分层(见上) = **171 文件全栈覆盖**。少量边界 file(tools 工具封装)角色可辩护 |

**结论: A2 全栈验收通过(后端 ~91% + 前端分层准), 方向证实。** A3 降级版可评估(余下唯一 backlog)。

---

## 本轮核心(会诊收敛)

**统一判据**: LLM 输出能否解析回 closed-world ref 集 + 成本是否 O(域数)。据此三兄弟一致:

| 特性 | 判定 | 一句话 |
|---|---|---|
| **A2 架构分层映射** | ✅ **做(本轮主线)** | A1 第二个 analyzer, 复用全套范式, 离散标签可 reject + O(域数), 近零增量, grounding 比 A1 强(枚举闭集) |
| **A3 代码导览 tour** | 🟡 **降 backlog** | 自由叙事 ground 不住 + 成本爆; 仅 A2 验证后评估"真实路径逐节点旁白 + on-demand" |
| **A4 web-ui + persona** | ❌ **砍 LLM 化** | grounding 完全不成立 + human-first 花架子; 最多 concise/verbose 确定性开关 |

A2 MVP 验收(对齐 A1): 20 file 角色准确率 ≥70% + 违规精确率 ≥80% + 隔离不变量(impact 默认不含软边)。

---

## 继承自 roadmap-2026-06-05(代码需求全闭环, 继承未完结项)

上轮代码全落地;继承项都是上轮主动排到本仓外/部署的:
- ⏳ **M 换机同步 e2e 验收** —— 脚本就绪(`scripts/e2e_agent_memory_sync.py`), 待 WSL `serve-mcp start` 后跑(运维动作)。
- ⏳ **D1/D2** 前端依赖图(is_page Next / vue 仓 scl-www-10 登记)—— **业务仓任务**, 不在 codev-platform 本仓。
- 📐 **A2** 架构分层映射 —— 本轮主线(设计已收敛, 见上文件清单)。
- backlog: **A3** 路径旁白 on-demand / **D3** endpoint→表 DI 精确版 —— 有真盲区案例再投, 不预先过度工程。

---

## 关联

- 上一主目录: [`../roadmap-2026-06-05/`](../roadmap-2026-06-05/)
- 周期生命周期 SOP: `.claude/rules/weekly-iteration-cadence.md`
