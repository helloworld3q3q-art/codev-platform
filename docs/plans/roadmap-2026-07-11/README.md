# 2026-07-11 Reindex 可靠性迭代

> 状态：🟡 进行中

本轮聚焦 reindex 控制面与执行面的故障隔离，以及 webhook 强推后的精确提交索引。

实施顺序固定为：运行时任务 1–2（运行身份叶子）→ attempt 隔离 → 精确 workspace
→ 运行时任务 3–10（依赖基座、版本发布、systemd 与切换）。每个任务均先红灯、再最小实现，
并在独立 worktree 中逐任务评审。

本轮新增文档、代码注释和 docstring 全部使用中文；代码标识符、命令和外部协议字段保持英文。

| 文件 | 说明 | 状态 |
|---|---|---|
| [总体设计](./reindex-isolated-execution-design-2026-07-11.md) | 总体设计与跨计划不变量 | 已评审 |
| [Attempt 隔离总览](./reindex-attempt-isolation-implementation-plan-2026-07-11.md) | 唯一全局契约、文件映射、子计划索引与退出标准 | 已评审，实施中 |
| [基础契约子计划](./reindex-attempt-isolation-foundation-plan-2026-07-11.md) | 任务 1–4：启动器、attempt/queue 契约与 executor 输入边界 | 已评审，实施中 |
| [围栏与发布子计划](./reindex-attempt-containment-publishing-plan-2026-07-11.md) | 任务 5–6：进程 containment 与幂等 manifest 发布 | 已评审，实施中 |
| [生产集成子计划](./reindex-attempt-production-integration-plan-2026-07-11.md) | 任务 7–8：finalization、依赖/legacy 门禁、health、systemd 与故障验收 | 已评审，待实施 |
| [精确 workspace 子计划](./reindex-exact-workspace-implementation-plan-2026-07-11.md) | force-push 精确 SHA、多仓 workspace 与发布证明 | 已评审，待实施 |
| [版本化 WSL runtime 子计划](./reindex-versioned-wsl-runtime-implementation-plan-2026-07-11.md) | 运行身份、共享依赖基座、薄 release、原子切换与回滚 | 已实施并完成正式 release 验证 |
| [隔离维护窗口与 WSL 发布/回滚手册](./reindex-isolated-maintenance-wsl-runbook-2026-07-13.md) | codev-reindex 专用维护门禁、共享配置快照证明、严格 health 恢复、精确 SHA manifest 验收与回滚 | WSL 实测完成 |
| [CodeGraph runtime mask 与恢复契约](./reindex-codegraph-maintenance-contract-2026-07-13.md) | CodeGraph 启动禁令、共享操作租约、只读 manifest 证明与显式恢复 | 已实施并完成 WSL 验证 |
| [冲突语义整合与受控交付计划](./reindex-conflict-reconciliation-implementation-plan-2026-07-14.md) | 保存外层 migration 严格语义、修复 owner/systemd 架构缺口并无损交付 | 已完成代码与 WSL 验证 |
| [WSL 运行时安装器设计](./wsl-runtime-installer-bat-design-2026-07-14.md) | 服务器 SH 真值源、Windows BAT 薄入口、CUDA 与 release 安全门禁 | 已替换旧 SHA 专用脚本并实测 |
| [CodeGraph 日常推送协调设计](./codegraph-push-coordination-design-2026-07-16.md) | 写意图、后端协作排空与可重试三态契约 | 已确认，实施中 |
| [CodeGraph 日常推送协调实施计划](./codegraph-push-coordination-implementation-plan-2026-07-16.md) | 跨进程锁、异步收件箱、HTTP 接线、队列回归与 WSL 验收 | Task 1–4 完成，Task 5 由新代际计划承接 |
| [旧运行时可靠性任务移交审计](./runtime-reliability-handoff-2026-07-20.md) | CodeGraph、runtime access、systemd 与 installer 的完成证据及新计划承接关系 | 已完成 |
| [2026-07-16 开发记录](./daily-summary-2026-07-16.md) | systemd 兼容性收口、MCP 401 修复、服务器运行时安装器与 WSL 推送门禁验收 | 已完成 |
| [2026-07-18 开发记录](./daily-summary-2026-07-18.md) | 生产部署状态机、数据库迁移、四库单一真值与 MCP 真实工具验收 | 本地门禁完成，待 WSL 受控切换 |
| [2026-07-19 开发记录](./daily-summary-2026-07-19.md) | 部署防卡死、稳定产物 descriptor 路径、锁竞态与最终全仓回归 | 本地门禁完成，待提交和 WSL 受控切换 |
