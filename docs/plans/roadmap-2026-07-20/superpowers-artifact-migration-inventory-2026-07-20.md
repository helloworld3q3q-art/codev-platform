# `.superpowers` 任务文档迁移盘点

> 状态：盘点与长期真值迁移已完成。结论以当前 `dev` 祖先提交和复审证据为准，
> 不照抄旧临时进度中的失效哈希。

## 一、物理存量

迁移前快照中根目录 `.superpowers/sdd` 共 127 个文件；迁出 3 个被 Git 跟踪的长期报告后，
当前该目录剩余 124 个历史临时文件，且不再作为任务真值。迁移过程中在其他临时目录生成的
审查差异仍属于非真值工具证据。

| 系列 | 数量 | 结论 |
|---|---:|---|
| reindex task brief/report | 24 | Task 1–12 全部完成，合并为完成审计 |
| reindex final-review brief/report | 8 | 四轮修复均完成，合并为完成审计 |
| CodeGraph brief/report/review | 15 | Task 1–4 完成，Task 5 由新集成计划承接 |
| runtime-access | 10 | Task 1/2/3A/3B 完成，其余由新代际计划取代或承接 |
| runtime-generation | 16 | Task 1/2 完成，Task 3 重开，Task 4 未提交草稿 |
| systemd-crlf | 6 | 本地任务完成，真实 WSL 验收由新集成计划承接 |
| WSL installer | 1 | 0 字节旧 brief，无长期内容 |
| review diff | 44 | 临时审查载荷，不迁为任务真值 |
| 其他 | 3 | `.gitignore`、共享约束、旧 progress，按内容合并 |

## 二、迁移原则

- 已完成任务只迁移提交、测试、复审和遗留风险摘要，不复制重复 brief 形成第二套计划。
- 进行中的唯一状态写入对应 roadmap 的正式计划与进度文件。
- `review-*.diff`、JUnit、临时 smoke/probe 只作工具证据，不进入计划真值。
- 旧 progress 中的作者改写前哈希和错误状态不复制；以当前 `dev` 祖先链重新核对。
- 本次不批量删除未跟踪历史文件；迁移后不得继续更新或引用它们。

## 三、去重后的任务状态

| 系列 | 完成状态 | 独立剩余 | 处理 |
|---|---|---:|---|
| reindex-worker-v2 | 12/12，四轮终审完成 | 0 | 归档完成审计 |
| CodeGraph 推送协调 | 4/5 | 0 | Task 5 并入 runtime-generation Integration 9–13 |
| runtime-access | Task 1/2/3A/3B 完成 | 0 | 3C 接线及 Task 4–6 由新计划替代/承接 |
| systemd-crlf | 本地 Task 1–3 完成 | 0 | 正式 WSL 验收并入 Integration 10–13 |
| 旧 WSL installer | 基线已完成 | 0 | 新代际扩展属于 Integration Task 7 |
| runtime-generation | 严格 2/29 完成，Task 3 重开 | 27 | 唯一活动执行队列 |

## 四、唯一执行顺序

1. 原 Foundation Task 3：把早期控制链改绑 O_EXCL `AttemptReservation`，补 journal 显式终态。
2. Foundation Task 4–7。
3. Adapters Task 1–9（全局任务 8–16）。
4. Integration/WSL Task 1–13（全局任务 17–29）。

旧计划中被承接的待办不再单独计数或重复执行。当前严格剩余固定为 27 项。
