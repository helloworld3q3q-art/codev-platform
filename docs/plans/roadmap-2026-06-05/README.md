# roadmap-2026-06-05 迭代规划目录(当前主目录)

> **当前主目录** —— 承上一迭代 [`../roadmap-2026-06-03/`](../roadmap-2026-06-03/)(主线已闭环)。
> **主题**: 综合理解层 —— 在技术血缘之上加 LLM 业务域映射,**但收尾先行、新特性狠切 MVP**。

---

## 文件清单

| 文件 | 内容 | 状态 |
|---|---|---|
| [next-plan-2026-06-05.md](next-plan-2026-06-05.md) | 主 plan **v2(四专家会诊修订)**:ROI 重排 `C1→B1→A-MVP`(收尾先于新特性)+ A 砍只读 MVP(≥70% 验收)+ 软/硬节点隔离 + grounding-first + A2/A3/A4 推下轮 | 📋 v2 规划 |
| [agent-loop-guard-redesign-2026-06-05.md](agent-loop-guard-redesign-2026-06-05.md) | loop guard 重构(5 专家两轮会诊):工具三分类护栏(只读近乎不限 / 检索类输出侧零增量 / 无效调用单独防线)+ 收尾禁脑补 + 读取充分性门。修 `per_tool_cap=3` 误杀 read_file 的主瓶颈 | 📋 待启动 |

---

## 继承自 roadmap-2026-06-03(2026-06-04 代码级核实)

Memory **M0–M4 全闭环**、影响分析三层全在、18082 退役、agent-chat-sessions A/B/C 做完。真未做仅:
M5 压测(C2 起步)/ M3 向量(B1)/ M4 cron(B2)/ Store project_id(C1)/ 前端 backlog(D)。

## 本轮核心(四专家会诊后修订)

对比 [Understand-Anything](https://github.com/Lum1104/Understand-Anything) 定位 —— borrow 其
domain/tour **高层分析**,用 codev 独有 **Agent+Memory 底座**实现(❌ 不抄 tree-sitter, 和 codegraph 重叠)。

**经架构/产品/AI/实施四专家会诊,原 v1 修订四点**:
1. **ROI 重排**: `C1(多租户安全)→ B1(向量召回)→ A1-MVP → C2/B2/D`——收尾确定 > 新特性未验;且 C1 是 A 的硬依赖。
2. **A 狠切 MVP**: 本轮只交 A1 业务域映射只读文本(**≥70% 准确率验收**, 可证伪止损);A2/A3/A4(架构/tour/web-ui/persona)推下轮专轮。
3. **软/硬节点隔离**: LLM 软节点独立 kind + confidence<1.0 + impact 默认过滤软边(保护"查依赖"护城河)。
4. **grounding-first**: LLM 当标注者不当发现者, 越界节点代码 reject(硬约束>prompt), 比 UA 纯 LLM 更抗幻觉。

**定位**: UA = 教人读懂任意代码(广浅, human-first);codev = 让 agent+团队在多项目间共享对自有系统的
精确理解(深专, **AI-first**)。胜负手在业务域映射的**准确 + 可纠错 + 可被 agent 复用**, 不在 tour/persona 视觉。

graphs that **query** → graphs that **explain on a deterministic skeleton**。

## 关联

- 上一主目录: [`../roadmap-2026-06-03/`](../roadmap-2026-06-03/)
- 周期生命周期 SOP: `.claude/rules/weekly-iteration-cadence.md`
