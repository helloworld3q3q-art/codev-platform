# roadmap-2026-06-05 迭代规划目录(当前主目录)

> **当前主目录** —— 承上一迭代 [`../roadmap-2026-06-03/`](../roadmap-2026-06-03/)(主线 4 轨道已闭环)。
> **主题**: **综合理解层** —— 在技术血缘之上加 LLM 业务域/架构/tour,从"查依赖"到"懂系统"。

---

## 文件清单

| 文件 | 内容 | 状态 |
|---|---|---|
| [next-plan-2026-06-05.md](next-plan-2026-06-05.md) | 主 plan:4 轨道(A 综合理解 spearhead / B Memory 收尾 / C 多租户加固 / D 前端+DI)+ 排期 + 指标 + 不做清单 + 设计哲学 | 📋 规划 |

---

## 继承自 roadmap-2026-06-03(2026-06-04 代码级核实结论)

Memory **M0–M4 全闭环**、影响分析三层全在、18082 退役完成、agent-chat-sessions A/B/C 做完。
真未做: M5 压测(本迭代 C2 起步)/ M3 向量(B1)/ M4 cron(B2)/ Store project_id(C1)/ 前端 backlog(D)。

## 本轮新增核心: 综合理解层

对比 [Understand-Anything](https://github.com/Lum1104/Understand-Anything) 后定位 —— codev
**技术血缘强、缺业务域/tour**。借鉴其 domain/tour **高层分析**,用 codev 独有的 **Agent + Memory
底座**实现(❌ 不抄它的 tree-sitter,那和 codegraph 重叠;plugins 是护城河不动)。

技术血缘是骨架(plugins 产),综合理解是血肉(LLM analyzers 产),二者在统一图谱上长在一起 ——
graphs that **query** → graphs that **explain**。

## 关联

- 上一主目录: [`../roadmap-2026-06-03/`](../roadmap-2026-06-03/)
- 周期生命周期 SOP: `.claude/rules/weekly-iteration-cadence.md`
