---
name: no-absolute-paths
description: 文档 / SKILL / rule / commit / 命令示例不写 D:\WorkSpace\... 绝对路径(用相对 / <repo-root>);agent 找平台规则要走 platform-docs MCP 召回,禁 glob 工作目录 + Read 绝对路径硬取
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

---

**获取 / 读取场景(agent 行为,非仅"写进文件")**:找平台 rule / docs / skill / memory **先走 `platform-docs` MCP** —— `search_docs(query, category)` 语义召回,已知文件用 `get_by_file`。codev-platform 的 rules/skills/docs/memory 已通过业务仓 `.claude/index.json` 的 `external_doc_paths` 进同一 **multi-tenant chroma**(`list_collections` 的 `loaded_projects` 含 codev-platform + 业务项目),所以业务仓 session **跨仓直接搜得到**。

- ❌ glob / Grep 当前工作目录找不到 → 就 Read `D:\WorkSpace\codev-platform\...` 绝对路径硬取
- ❌ 更禁据"工作目录里没有"判定"规则文件缺失"(规则物理在 codev-platform、逻辑经 chroma 对所有业务仓透明可召回)
- ✅ 绝对路径 Read 仅作 **MCP 不可用 / 索引滞后 / 看未提交改动** 的兜底(同 workflow §0.9 Grep 例外)

**Why**:5-28 找 `workflow.md`,我 glob 工作目录零命中就误判"工作流规则缺失",其实它是 codev-platform 真值源、且早被 chroma 索引,`search_docs` 一查 rerank 0.998 直接召回。根因是跳过"知识库统一召回"抽象、退回原始文件系统 glob —— 而硬编码 `D:\WorkSpace\...` 去取,服务上服务器后路径必坏,与本规则"写进产物的路径要 portable"同源。

**相关**:[[reference-mcp-tools]] / [[no-cargo-cult-dates]] / `.claude/rules/file-discipline.md §4`(docs/ 目录归类硬规定)
