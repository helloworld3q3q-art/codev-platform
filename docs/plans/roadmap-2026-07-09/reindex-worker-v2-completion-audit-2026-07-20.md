# Reindex Worker V2 完成审计

> 状态：完成。本文合并 `.superpowers/sdd` 中 Task 1–12、四轮终审修复和旧 progress 的长期证据；旧 brief/report/diff 不再作为任务真值。

## 完成状态

| 任务 | 提交范围 | 复审 |
|---|---|---|
| 1 | `bcbbdfd..b23518f` | clean |
| 2 | `b23518f..e8eeeec` | clean |
| 3 | `e8eeeec..be5bbc3` | clean after fix |
| 4 | `be5bbc3..50d464e` | clean after fix |
| 5 | `50d464e..48fac0c` | clean |
| 6 | `48fac0c..cea3522` | clean after fix |
| 7 | `cea3522..59bc948` | clean |
| 8 | `59bc948..56bf406` | clean |
| 9 | `56bf406..c1fdb50` | clean after fix |
| 10 | `c1fdb50..3733a59` | clean after fix |
| 11 | `3733a59..ed94001` | clean after fixes |
| 12 | `ed94001..203cdd1` | clean |

四轮整分支审查修复均已进入当前 `dev` 祖先链。该计划为 12/12 完成，不再产生独立待办。

## 已知非阻断风险

- `PgJobQueue.snapshot()` 与运行时 SQL/Alembic 没有可用 DSN 时只完成协议、SQL 形状和 schema 测试。
- FileSpool 普通异常清锁已有覆盖；hard-kill 后遗留 key lock 的专门测试明确由
  runtime-generation Integration Task 6 的故障注入矩阵承接。
- `ingest` 与 `code_vec` 共享同一依赖分支，既有测试重点覆盖 `codegraph → code_vec`。

这些风险已经被 runtime-generation 的数据库、故障注入和真实 WSL 阶段承接，不重复建立任务编号。
