# 任务计划真值源

> 本仓每个会修改文件或外部状态的独立任务，都必须先在这里建立计划。
> `docs/architecture/` 只保存不承担执行跟踪职责的纯架构参考；`.superpowers/**` 和
> `docs/superpowers/**` 不得作为计划或任务真值源。

## 命名

计划按启动日期进入 `roadmap-YYYY-MM-DD/`，文件名为
`<topic>-<creation-date>.md`，如 `roadmap-2026-05-27/team-deploy-2026-05-27.md`。

## 写入约束

- 计划文件必须是任务的首个写入；计划落盘后，第二步登记对应 roadmap `README.md`。
  两步完成前不得修改其他文件或外部状态。
- 计划至少包含目标、范围、规则与约束、有序步骤、验证命令和当前状态。
- design、task brief、progress、completion report 与计划放在同一 roadmap，避免多套真值。
- 同一任务的继续、复审返修和补验证更新原计划，不重复创建新任务计划。
- plan 落地 / 取消 / 改方向时,**不删原文件**,在头部加状态标记(`✅ 已落地` / `❌ 已取消` / `🟡 进行中`)

## 怎么找

- 列表:`ls`
- 主题搜:`search_docs("...", category="dev_log")`(plans/ 落 dev_log,与 log/ 同 category)
- 跟踪 plan 演化:`git log -- docs/plans/`

## 与纯架构参考的区别

| | `docs/plans/` | `docs/architecture/` |
|---|---|---|
| **职责** | 任务目标、步骤、状态、验证和交付真值 | 不承担任务状态的长期架构说明 |
| **是否可放 plan/task** | 是，唯一允许位置 | 否 |
