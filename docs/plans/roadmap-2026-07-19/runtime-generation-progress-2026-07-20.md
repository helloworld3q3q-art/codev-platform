# Runtime Generation 执行进度

> 状态：Foundation Task 4 的专项任务 1–7 已完成并收口。总数冻结为 29；旧计划承接项不额外计数。本轮仅提交、推送和更新索引，不进入 Task 8。

## 当前状态

| 阶段 | 状态 |
|---|---|
| Foundation Task 1 | complete：`b643a8d..e1511a0`，三方 clean after fixes |
| Foundation Task 2 | complete：`e1511a0..62f59b9`，三方 clean after fixes |
| Foundation Task 3 | complete：reservation 控制链、显式 journal 终态、lease lineage、终态聚合及第二轮职责边界回修均已终审通过 |
| Foundation Task 4 | complete：专项计划的任务 1–7 已完成，覆盖布局/协议、预租约 bootstrap、ControlLease CAS、post-lease transaction、generation、rollback、恢复竞争与 root-fd worker 生命周期闭环 |
| Foundation Task 5–7 | pending |
| Adapters Task 1–9 | pending |
| Integration/WSL Task 1–13 | pending |

严格口径为 4/29 完成、25 项未完成。

## Task 3 重开原因

现有 journal、RecoveryEnvelope 和 initial/takeover ControlLease 依赖完整 `DeploymentAttempt`；批准的
集成顺序却要求先完成：

```text
AttemptReservation
→ journal genesis
→ per-attempt recovery envelope
→ active envelope
→ initial lease
→ baseline / candidate / generation
→ 完整 DeploymentAttempt
```

因此早期控制链必须精确绑定 O_EXCL `AttemptReservation`。同时 journal 需要自身明确终态，才能证明
`complete journal → retire lease → clear envelope`。这两项在原 Task 3 内修正，不新增任务编号。

## 完成证据

- Task 1：Windows/WSL 验证完成，三方终审无 Critical/Important。
- Task 2：最终 `1245 passed, 160 skipped`，独立审查无 Critical/Important/Minor。
- Task 3：reservation 控制链与 journal 终态契约已闭合；第二轮终审完成职责分离、显式
  `None` fail-closed、测试拆分与终态异常边界修复。定向 `240 passed`，全量 runtime
  `1619 passed, 162 skipped`，三角色终审无 Critical/Important。
- Task 4 / 任务 1–7：固定布局/窄协议、预租约 reservation → journal genesis → envelope → active envelope、ControlLease history/current CAS、post-lease transaction、generation、rollback、跨域恢复竞争、descriptor-bound 隔离与 root-fd worker 生命周期已完成。最新本地完整 `tests/test_runtime_*.py` 聚焦集为 `1676 passed, 458 skipped`；本次 139 个 Python 文件的 Ruff、格式和编译检查全部通过。WSL 临时根已分别通过 root-fd worker `38 passed in 17.61s` 与恢复/生命周期/隔离/对象锁 `41 passed in 8.54s`。一次完整 WSL 核心集在 68% 时因宿主重启清空 `/tmp` 临时环境而中断，未出现 pytest 失败；该事件不记作全绿证据。用户于 2026-07-22 明确确认 Task 7 无问题并要求收口。

## 收口边界

Task 7 已收口。本次只完成本地提交、仅向 WSL Git 远端推送和本地 AI 索引更新，不进入 Task 8；后续若启动
Foundation Task 5，必须在对应 roadmap 计划中重新登记，且不得从 `.superpowers/sdd/progress.md` 恢复进度。
