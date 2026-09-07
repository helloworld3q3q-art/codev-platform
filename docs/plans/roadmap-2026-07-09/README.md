# roadmap-2026-07-09

本目录记录 2026-07-09 这轮 `reindex worker v2` 的计划、落地设计与操作说明，供 operator 和维护者在排障、迁移、回滚时直接查阅。

| 文件 | 用途 | 状态 |
|---|---|---|
| `reindex-worker-v2-plan-2026-07-09.md` | `reindex worker v2` 的已落地方案、迁移说明与操作 runbook | 已落地 |
| `reindex-worker-v2-completion-audit-2026-07-20.md` | Task 1–12、终审修复和遗留风险的合并证据 | 已完成 |
| `reindex-worker-v2-task-3-report-2026-07-14.md` | Task 3 交付报告的历史审计证据 | 已完成 |
| `reindex-worker-v2-task-4-report-2026-07-09.md` | Task 4 交付报告的历史审计证据 | 已完成 |

## 本轮交付摘要

- `process-tree executor`：统一托管外部进程树，避免超时后残留子进程。
- `non-interactive Git sync`：worker 路径下 Git/GCM/SSH 全部走非交互执行。
- `FileSpool/PG queue v2`：队列状态拆分为 `pending / active / results`，可识别 dirty pending 与 stale active。
- `worker repository preparation`：worker 在执行前统一准备仓库，不再把拉取逻辑散落到阶段命令里。
- `dependency scheduler`：显式保证 `code_vec -> codegraph`、`ingest -> codegraph`。
- `phase/result status v2`：状态细化到 phase、active job、last result、manifest/health 信号。
- `status/health/CLI operations`：补齐 `status`、`prune-stale`、`break-lease`、`wait-for-reindex` 的操作口径。
- `manifest dependency semantics`：manifest 记录目标 commit、依赖关系与结果语义，便于判断 freshness。

## 操作入口

- 日常查看、等待、清理和抢救命令见 [reindex-worker-v2-plan-2026-07-09.md](./reindex-worker-v2-plan-2026-07-09.md) 的“Operator 命令”和“状态解释”章节。
- 遇到队列卡住、dirty pending、旧 lease 未释放时，先看同文档的“Git / pull policy”“迁移行为”“风险和后续”章节再执行恢复命令。
