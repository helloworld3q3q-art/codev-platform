# 工具栈 / 协作流程事故复盘

> 记录 **开发工具栈 / AI 协作流程本身** 的事故复盘:chroma / codegraph / cross-link / MCP / hook / git workflow / AI 协作纪律相关的 bug 和踩坑。
>
> 与产品业务事故(`docs/operations/incident-*.md`)区分:那里记的是股票数据 / pipeline / 推荐链路 / Java API 的事故;这里记的是 **支持开发本身** 的工具栈事故。

## 命名

`YYYY-MM-DD-<topic>.md`,topic 用 kebab-case 描述根因(如 `2026-05-27-chroma-daemon-err-gitignore.md`)。

## 写入约束

- 4 字段固定:**现象 / 根因 / 修复 / 预防**
- 关联 commit short hash + 业务事故关联(如有跨域影响)
- 历史文件不改,新事故开新文件

## 怎么找

- 列表:`ls`
- 主题搜:`search_docs("...", category="tooling_incident")`

## 边界判定

如果事故**横跨**工具栈和业务(如 chroma 召回错导致业务规则误用)→ 主写一份(根因在哪侧),另一侧加一行引用。**不双写**,避免 search_docs 重复召回。
