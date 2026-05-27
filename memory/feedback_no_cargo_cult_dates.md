---
name: no-cargo-cult-dates
description: 新加规则/章节标题不要反射加 (YYYY-MM-DD 起) 后缀,git blame 是真值源
metadata:
  type: feedback
---

新加 `.claude/rules/*.md` / `CLAUDE.md` / skill 文档时,标题**不要**反射加 `(YYYY-MM-DD 起 / 新增 / 修复)` 后缀。

**Why**:5-27 一次性清理 14 文件 / 28 处 cargo cult 日期,当天自己加 §0.13 又立刻犯一次被用户抓出 → 证明纯靠"扫一次"无效,要源头不写。

**允许的日期场景(留)**:事故复盘(`5-14 资金流崩溃`) / 数据 cut-off(`5-08 样本起点`) / 路径引用(`roadmap-2026-05-23/`) / 任务锚点(`N12 / D6 / Sprint-2 #9` 配日期)。

**禁止(删)**:纯标题装饰"(2026-05-XX 起/新增/修复)"。

**How to apply**:写新标题时停一秒自问"这日期是事故/cut-off/路径/任务编号,还是纯装饰"。装饰就不写。

**相关**:[[rules-concise]]
