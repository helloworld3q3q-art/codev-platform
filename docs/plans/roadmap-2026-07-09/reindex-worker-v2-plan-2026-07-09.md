# reindex worker v2 已落地方案与操作说明

## 状态和目标

- 状态：已落地
- 回滚 tag：`pre-reindex-worker-v2-20260709`

本轮改造的目标，是把 `reindex worker` 从“看起来在跑、实际上可能卡住”的短驻执行模型，升级成可诊断、可恢复、可迁移的明确契约。重点解决四类问题：

- 短驻 worker 被外部子进程拖住后，状态仍显示 running，难以判断是否真卡死。
- Git/GCM/SSH 在 worker 路径里可能触发隐式交互，导致后台任务无声阻塞。
- stale job、旧 lease、dirty requeue 信号混在一起，`prune-stale` 很难安全操作。
- `code_vec` 依赖 `codegraph` 的语义不清晰，manifest 与状态粒度过粗，operator 无法快速判断 freshness 和恢复步骤。

## 核心设计

### 1. process-tree executor

worker、Git 同步、健康刷新等外部命令统一走 `process-tree executor`。超时语义不再只是杀父进程，而是清理整棵子进程树，避免 `git-remote-http`、`git credential-manager`、SSH 等残留。

### 2. Git 非交互执行

所有 worker 路径下的 Git 调用都以非交互方式运行，显式关闭终端提示、GCM 交互和 askpass 兜底。超时或失败一律返回可诊断结果，不把异常直接泄漏成后台挂死。

### 3. queue v2：`pending / active / results`

队列协议升级为 v2：

- `pending`：待执行请求。
- `active`：已被 worker claim、正在持有 lease 的请求。
- `results`：最近一次执行结果。

在此基础上补出两个关键状态：

- `dirty-after-active`：同一个 job 在 active 期间又收到新的 pending。
- `expired-active`：active lease 过期，说明 worker 可能异常退出或长期卡住。

### 4. job metadata

每个 job 记录以下元数据：

- `source`
- `pull_policy`
- `target_commit`

必要时还可附带 `repo_targets` 等仓库目标信息。这样 worker 不再靠“调用路径猜来源”，而是直接根据 job 契约决定是否拉取、期望同步到哪个 commit。

### 5. local hook no-pull

本地 `post-commit` / hook 触发的任务已经位于最新提交，v2 明确规定：

- `source=local_hook`
- `pull_policy=never`

worker 只读取本地 HEAD，不做任何拉取，避免本地开发仓被远端同步逻辑打断。

### 6. webhook ff-only

远端 webhook 触发的任务可能落在 worker 本地镜像之后，因此 v2 明确规定：

- `source=webhook`
- `pull_policy=ff_only`

worker 只允许通过非交互、fast-forward only 的方式追平，不做 merge、不做交互式认证修复。

### 7. worker repository preparation

仓库准备逻辑统一收口到 worker。worker 在 drain 流程中先完成仓库准备，再决定是否执行 runner。这样 Git 同步、目标 commit 覆盖检查、同一轮 drain 的仓库复用都发生在同一层，不再散落在各命令里。

### 8. dependency scheduler

v2 把依赖关系显式化：

- `code_vec -> codegraph`
- `ingest -> codegraph`

`code_vec` 只负责自己的向量阶段，不再偷偷连带 `codegraph`。如果上游依赖失败、未新鲜或仅可重试，worker 会按结果语义阻塞、释放或重排下游任务。

### 9. worker phase/result status v2

worker 状态从“只有 heartbeat 和 last_job”升级为：

- 当前 `phase`
- `phase_started_at`
- `active_job`
- `last_result`
- `manifest_ok`
- `health_failed`

这样 operator 能区分是卡在 `git_sync`、`runner` 还是 `manifest`，而不是只看到模糊的 running。

### 10. manifest dependency semantics

manifest 补充 `target_commit`、`source`、`pull_policy`、`repo_commits_json`、`depends_json` 等字段，用于判断本次结果依赖了哪个上游、是否覆盖目标提交、是否满足 freshness 约束。

## Operator 命令

### 查看当前状态

```powershell
codev-platform reindex-queue status
```

适用场景：

- 想确认 worker 当前在跑什么 phase。
- 想知道某个 job 是否处于 `pending`、`active`、`dirty-after-active` 或 `expired-active`。
- 想判断下一步应当 `wait`、看日志、`prune-stale` 还是 `break-lease`。

### 等待目标提交完成

```powershell
codev-platform wait-for-reindex --commit HEAD --timeout-sec 180
```

适用场景：

- 本地 hook 或手动触发后，需要等待当前提交相关索引完成。
- webhook 到达后，需要确认目标 commit 已经被 `codegraph` / `code_vec` 等阶段消费。

如果状态显示 phase 仍在推进且 lease 未过期，优先 `wait`；不要先做人工清理。

### 清理陈旧 pending

```powershell
codev-platform reindex-queue prune-stale
```

适用场景：

- `status` 已显示没有有效 active，但存在明显过旧的 pending。
- 需要清理由旧协议、旧进程或误触发残留的待执行请求。

注意：`prune-stale` 只应该处理 stale pending。若同 key 仍有 active lease，先判断是否需要 `break-lease`，不要直接删。

### 强制释放 active lease

```powershell
codev-platform reindex-queue break-lease --project <project_id> --kind <kind> --older-than-sec 300 --yes
```

适用场景：

- `status` 显示 `expired-active`，确认旧 worker 已退出或已长期失活。
- phase 长时间停在同一位置，日志也不再更新，需要人工抢救。

`break-lease` 只管理 active lease，本质上是把“占着锁但不再工作”的请求释放出来；若同时存在 dirty pending，释放后应保留 pending 让新 worker 接手。

`break-lease --yes` 需要 fail-closed：

- worker 仍 running 且 heartbeat / phase 未失败时，拒绝 break，先看 `status` 或继续等待。
- 若传 `--older-than-sec`，active lease 年龄未达到阈值时也拒绝。
- 只有目标 active lease 处于 `expired-active`，或目标自身 `lease_expires_at <= now`，或 worker 已 stopped、heartbeat FAIL、phase FAIL 时才允许真正释放。

### 什么时候 wait、看日志、prune、break lease

- phase 在变化、heartbeat 新鲜、lease 未过期：先 `wait-for-reindex`。
- phase 长时间固定，但日志仍增长：先看日志，确认是否只是耗时任务。
- 没有有效 active，只剩明显旧 pending：`prune-stale`。
- active 已过期、日志停滞、旧进程确认不存在：`break-lease`。

## Git / pull policy

v2 的核心规则是“是否拉取”由 job metadata 决定，而不是由 worker 或命令实现随意猜测。

### local hook

- `source=local_hook`
- `pull_policy=never`
- 不拉取，只读取当前本地提交。

### webhook

- `source=webhook`
- `pull_policy=ff_only`
- 只允许 worker 通过非交互 `ff-only` 同步。

### manual / admin / onboard

- 默认都不拉取。
- 如需拉取，应显式通过 enqueue 参数或上层调用传入对应策略，而不是隐式复用 webhook 行为。

## 迁移行为

v2 的迁移策略是“增量兼容、惰性升级”，避免一次性强制切断旧数据。

- legacy `FileSpool` markers：按需 lazy migration。旧 marker 在首次被读取或处理时，迁移成 `pending` 侧的 v2 记录。
- old PG rows：采用 additive upgrade，在原位补充新字段；旧行在新代码下仍可读取。
- old worker state without v2 fields：仍然可读，缺失字段按兼容逻辑降级处理。
- old manifest rows：新增字段允许为空，旧记录在读取时不会因字段缺失报错。

## 状态解释

### worker phase

- `queue_scan`：扫描队列、挑选下一批可执行任务。
- `git_sync`：按 `pull_policy` 做仓库准备或 fast-forward 校验。
- `runner`：执行实际索引命令。
- `manifest`：写入或校验 manifest 结果、依赖与 freshness 元数据。
- `queue_complete`：完成当前 job、释放或转移队列状态。
- `health_refresh`：刷新健康信息与补充状态摘要。
- `idle`：worker 存活，但当前没有可执行任务。
- `stopping`：收到停止信号，正在退出。
- `stopped`：worker 已退出。

### queue 衍生状态

- `dirty-after-active`：active 期间又收到了新的 pending，说明当前结果产出后还需要再跑一轮。
- `expired-active`：active lease 已过期，通常意味着旧 worker 已死或已卡住太久。

### 结果字段

- `last_result`：最近一次 job 的执行结果，常见取值包括 `ok`、`failed`、`retry`、`blocked`。
- `manifest_ok`：manifest 写入和依赖语义是否成立；它可以失败而不必等同于 runner 进程失败。
- `health_failed`：健康刷新流程失败，说明状态观测本身存在风险，需要配合日志和 phase 一起判断。

## 风险和后续

- `repo_commits_json` 当前主要记录主仓 `main` commit；如果后续一个 project 需要同时追踪多个 repo 的细粒度 commit 映射，可以继续扩展结构。
- live PG migration 因本轮没有可用 DSN，未做真实在线迁移演练；当前依赖 SQL/schema/unit 覆盖来保证兼容性。
- `docs/` 顶层历史散文件如果在归位检查中出现，应视为既有违规，非本轮处理范围，不在本次文档任务中移动。

## 回滚与恢复提示

- 若 v2 行为需要紧急回退，优先使用回滚 tag `pre-reindex-worker-v2-20260709`。
- 若是单个 job 依赖判断异常，可先人工按顺序补跑 `codegraph`，再补跑 `code_vec`，用于临时恢复。
- 若状态文件不可解析，应按兼容逻辑降级为 `stopped/WARN`，而不是让状态命令直接崩溃。
