---
name: feedback-weekly-iteration-cadence
description: "每周一个大迭代,每周六新 plan + 上周归档 + 继承未完结项;不丢任务不重做"
metadata: 
  node_type: memory
  type: feedback
  originSessionId: f3b64d25-4208-408d-a752-2c78ac33f92b
---

用户采用 **每周一个大迭代** 的工作节奏(2026-05-23 起):
- 每周新建 `docs/architecture/roadmap-YYYY-MM-DD/`(日期取启动当天,**大概率周六 / 周日,不强制**)
- 上周目录加归档标注 + 保持不动
- 新 plan **继承上周未完结项**(⏳ 进行中 / 🔒 锁定 / backlog 候选)
- 已完成项 ✅ 不再出现

**Why**:周末 A 股不开盘,有完整时间盘点 + 排下周计划;但用户可能周五晚 / 周日 / 周一早任意时段触发,不强制周六。规则化能让 AI / 协作者跨周不丢任务。

**How to apply**:
- **不要主动等到周六**:用户任何时候说"新一周 plan" / "归档" / "开新迭代"都按 SOP 执行
- AI 也不要在周六主动提醒"该建新 plan 了"(让用户自己决定触发时机),除非用户已在累积期看板表态需要节奏提醒
- 跨周决策时引用 `.claude/rules/weekly-iteration-cadence.md` SOP
- 当前主目录在 `CLAUDE.md` §五最后一段维护(目前 = `roadmap-2026-05-23/`)
- **禁止**:在归档目录新增 / 修改任务状态;新发现一律进当前主目录
- 见 [[reference-roadmap-dirs]] 累积期目录索引(如有)

**周回看 ai-health stats**(2026-05-23 起):

每次启动新一周迭代时,顺手跑一次 `powershell -File tools\dev\ai-health.ps1`,看最后 4 行 stats:
- `search_recall` hit_rate 应 ≥ 80%,median_top1_dist 应 ≤ 0.4(高了说明 Chroma 召回精度退化,可能要重建索引或换 query)
- `reindex 7d` 应 ≥ 5 次(低了说明 post-commit hook 漏触发或本周改动太少)
- `mcp/commit ratio` 关注**趋势**:基线 0.05(2026-05-23),目标涨到 ≥ 0.5,说明 agent 真在按 `ai-tools-mcp.md §2.5` 主动调 search_docs。维持 0.05 → 规则失效,要换强制机制

记录数字到当周 roadmap 顶部 `weekly-ai-health-stats` 小节,跨周对比看趋势,而不是凭感觉评判 AI 工具栈好用与否。

依据 [[feedback-assertions-over-rules]] —— 用数据说话,不靠直觉。
