# 开发工具 / 流程演化计划

> 记录**未来要做的工具栈 / 流程改造**计划:与产品功能 plan(`docs/architecture/*plan*.md`)分离。
>
> 与同目录 `log/`(已做决策日志)、`incidents/`(已发生事故复盘)平行 — 这里是 **未来时**。

## 命名

`<topic>-<creation-date>.md`,如 `team-deploy-2026-05-27.md`。

## 写入约束

- 每文件 6 字段:**定位 / 决策前提 / Phase 划分 / 风险 / 替代方案 / 启动条件**
- 不写实施细节(那是真启动后开 design doc 的事)
- 每 Phase 必带 estimate + Gate 验收口径
- plan 落地 / 取消 / 改方向时,**不删原文件**,在头部加状态标记(`✅ 已落地` / `❌ 已取消` / `🟡 进行中`)

## 怎么找

- 列表:`ls`
- 主题搜:`search_docs("...", category="dev_log")`(plans/ 落 dev_log,与 log/ 同 category)
- 跟踪 plan 演化:`git log -- docs/plans/`

## 与产品 plan 的区别

| | docs/plans/ | docs/architecture/*-plan-*.md |
|---|---|---|
| **范围** | 工具栈 / 协作流程 / AI 纪律 | 产品功能 / 业务模块 / 架构演化 |
| **例子** | 团队化部署 / RAG 升级 / hook 重构 | java-optimization / python-optimization / frontend-optimization |
| **客户** | 你 + Claude + 未来潜在协作者 | 量化平台终端用户 |
| **搜得到** | `category="dev_log"` | `category="design"` |
