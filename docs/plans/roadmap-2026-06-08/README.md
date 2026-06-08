# roadmap-2026-06-08 迭代规划目录(当前主目录)

> **当前主目录** —— 承 [`../roadmap-2026-06-05/`](../roadmap-2026-06-05/)(代码需求已闭环, 见其落地状态表)。
> **主题**: 综合理解层 **A2 架构分层映射**(A1 业务域之后的第二个 analyzer)+ 上轮收尾继承。
> **纪律延续**: A1 的狠切 MVP + grounding-first + 软硬隔离 + ≥70% 可证伪验收, A2 全套复用。

---

## 文件清单

| 文件 | 内容 | 状态 |
|---|---|---|
| [a2-architecture-layer-design-2026-06-08.md](a2-architecture-layer-design-2026-06-08.md) | **子设计(三兄弟会诊: 产品/ROI · 架构复用 · AI 抗幻觉)**: A2 架构分层映射 —— 复用 A1 全套护栏给 file 归架构角色软标签(`ARCH_LAYER`/`PLAYS_ROLE`)+ 确定性违规检测; 用 grounding 判据**证明 plan §六怀疑正确**(A3 降级、A4 砍) | 📐 设计收敛(待实现) |

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
