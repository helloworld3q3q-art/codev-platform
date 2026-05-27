# 开发流程决策日志

> 记录 **开发过程本身** 的演化决策:AI 协作纪律 / 工作流 / 规则梳理 / 工具链改动 / memory 调整 / 文档结构。
>
> 与产品功能演化(`architecture/changelog.md`)、每周迭代(`roadmap-*/`)、用户偏好(`memory/`)、工具栈事故(`../incidents/`)区分。

## 命名

`YYYY-MM-DD-<slug>.md`,slug 用 kebab-case 描述主题(如 `2026-05-27-claude-md-0-13-trigger.md`)。

## 分类标签

每文件正文首行用方括号:`[AI 纪律] / [规则梳理] / [工作流] / [工具链] / [memory 调整] / [文档结构]`

## 写入约束

- 4 字段固定:**起因 / 决策 / 备选(放弃)/ 验证**
- 不写流水账,只记决策点 + 关键 trade-off
- 关联 commit short hash,`git show <hash>` 看具体 diff
- 历史文件不改,新决策开新文件

## 怎么找

- 列表:`ls`
- 主题搜:`search_docs("...", category="dev_log")`
- 跟踪某文件历史:`git log -- docs/dev-evolution/log/`
