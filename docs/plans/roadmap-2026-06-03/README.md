# roadmap-2026-06-03 迭代规划目录(当前主目录)

> **当前主目录** —— 承上一迭代 [`../roadmap-2026-06-01/`](../roadmap-2026-06-01/)。
> 本迭代战略重心:**统一图谱 store 做成连通的全链路单一真值源 → 解锁影响分析报告(核心商业价值)**,
> 同步 web-backend 收尾、Agent Memory 起步、技术债清理。

## 本迭代主题

把上一迭代刚收敛干净的统一图谱 store(架构级跨层血缘),用 **codegraph 桥接**补上唯一断点
(`endpoint → function`),使其成为**连通的全链路单一真值源**,据此:
1. 退役 cross-link / `build_index.py`(少一路 per-repo scanner);
2. 暴露成 Agent **影响分析**工具 + 报告("改一处 → 跨层影响清单")—— README 定义的核心卖点。

## 文件清单

| 文件 | 内容 | 状态 |
|---|---|---|
| [next-plan-2026-06-03.md](next-plan-2026-06-03.md) | 主 plan:4 轨道(A 图谱收敛+影响分析 spearhead / B web-backend 收尾 / C Memory M1 起步 / D 技术债+质量+Demo)+ 依赖排期 + 成功指标 + 红线 | 执行中:**Track A spearhead(A1/A4/A5)✅ 交付** |
| [daily-summary-2026-06-03.md](daily-summary-2026-06-03.md) | 首日日报:Track A 影响分析完整交付(桥接 + 引擎 + web API + agent 工具 + 对抗审计),766 passed | ✅ |

## 上一迭代继承速查

| 来源(roadmap-2026-06-01) | 归宿(本迭代) |
|---|---|
| pluginized Phase 5 影响分析 + Reports | **Track A**(A4/A5)+ Track B(B2) |
| web-backend Phase 7/8 + 退役 codegraph-api | **Track B** |
| agent-memory M1–M2 | **Track C** |
| sqlalchemy-migration-plan-2026-06-03 | **Track D**(D1) |
| refactor 收尾(file-size budget / agent 路由错误码) | **Track D**(D2/D3) |
| Demo + POC(pluginized Phase 7/8) | **Track D**(D4) |
| ✅ 血缘专题 P1–P4 | 已完成,不重做(`../roadmap-2026-06-01/unified-graph-lineage-2026-06-03/`) |

## 关联

- 上一迭代:[`../roadmap-2026-06-01/`](../roadmap-2026-06-01/)(各 Track 的详细实施 plan 仍在那里,本目录只排序+追踪)
- 纪律真值源:`weekly-iteration-cadence.md` / `verification-checklist.md` / `commit-pr-conventions.md`
