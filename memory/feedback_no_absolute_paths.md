---
name: no-absolute-paths
description: 文档 / SKILL / rule / commit message / commands example 不要写 D:\WorkSpace\platform\... 绝对路径,用相对路径或 <repo-root> placeholder
metadata:
  type: feedback
---

文档 / SKILL.md / rule / commit message / 命令示例**不要**写 `D:\WorkSpace\platform\...` 绝对路径。

**Why**:5-27 一次性清理 11 文件 50 处绝对路径污染,换机器 / 别人 clone / 团队部署都会坏。git blame 显示是历史长期 cargo cult。本类问题同 `feedback_no_cargo_cult_dates.md` 是同一种"随手写但 portability 差"。

**允许保留绝对路径的场景**:
- ✅ Windows Task Scheduler 注册命令(`.ps1` / `.cmd`)— Scheduler 不支持相对路径
- ✅ `.py` 里 `.env` loader 写死路径(改了可能 break runtime)
- ✅ archive / log 历史归档(不动原貌)
- ✅ 文档里**故意展示**绝对路径例子(如"如 `D:\WorkSpace\platform`")— 但必须前后文说明这是占位符示例

**禁止(默认)**:
- ❌ Markdown 文档里 `cd D:\WorkSpace\platform\xxx` → 改 `cd xxx` + 顶部声明 "cwd = 仓库根目录"
- ❌ SKILL.md 命令例子 `D:\WorkSpace\platform\tools\dev\xxx.ps1` → 改 `tools\dev\xxx.ps1`
- ❌ commit message / rule grep 自检命令 → 改相对
- ❌ HTML / 文档 prose `D:\WorkSpace\platform/xxx` → 改 `<repo-root>/xxx`

**How to apply**:写命令 / 路径前停一秒,问"这条命令换机器还能跑吗?" 不能就改相对 + 加 cwd 前提。

**相关**:[[no-cargo-cult-dates]] / `.claude/rules/file-discipline.md §4`(docs/ 目录归类硬规定)
