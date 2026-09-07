# Reindex 隔离执行与精确提交设计

> 状态：🟡 进行中  
> 日期：2026-07-11  
> 范围：reindex worker、跨平台进程管理、webhook workspace、WSL systemd 运行时  
> 决策：终态彻底、迁移渐进；保留 queue v2、manifest 和现有 runner CLI

当前状态只表示设计、Task 5 进程后端和 Task 6 发布边界已经形成可验证切片；两个真实 cgroup
故障用例已在临时 `Delegate=yes` unit 中定向验证，但 Task 7 的 finalization journal、artifact
store、依赖门禁、生产选择器、健康刷新和实际生产 unit 尚未全链接线，不能把定向结果视为完成。

## 1. 定位

本设计解决两个已经复现的生产级故障，而不是做通用作业平台：

1. Windows 短驻 worker 的 runner 子进程已经退出，但 stdout 管道被后代进程继承，
   worker 主线程卡在日志回收；heartbeat 和 lease 线程仍刷新，形成“假活”。
2. `dev` 强推改写历史后，WSL 工作副本无法 `git pull --ff-only`，旧 worker 仍在旧
   HEAD 上完成索引，manifest 与远端目标提交不一致。

当前 `worker.py`、`queue.py`、`supervisor.py` 已超过 600 行，且职责交叉。实现必须同步
完成职责拆分，不能继续向热路径堆分支。

## 2. 决策前提

### 2.1 已确认事实

- Windows worker 的 runner phase 超过 6000 秒，进程树中已没有 runner 子进程，日志和
  CPU 均无进展；只有 heartbeat 与 lease renewer 继续活动。
- `runner_logs.run_logged_process()` 的 reader/close 仍在 worker 进程内，任一无界系统
  调用都能阻塞整个串行消费者。
- WSL worker 正常、队列为空；它索引的是旧 `6825f58`，远端是非祖先新链
  `9e43a3f`，ff-only 失败是确定行为，不是 runner 卡死。
- WSL systemd 从共享 venv + editable checkout 启动，运行版本依赖可变源码目录和是否
  重启，缺少可审计发布边界。
- webhook 已携带 `target_commit`，queue v2 已有 claim token、lease、retry 和 File/PG
  双后端；这些能力应复用。

### 2.2 设计目标

- 任意 runner、Git、日志、native 库卡死，都只能影响一个 attempt，不能卡住 worker。
- webhook 索引输入必须精确等于完整目标 SHA；拿不到目标时失败闭合，绝不回退本地 HEAD。
- worker 的运行版本、目标提交、workspace 提交和最终 manifest 可被独立证明。
- Windows 与 WSL 都能在确定上界内终止整个执行树。
- FileSpool 与 PG 继续遵守同一 queue 契约，现有 CLI、hook 和 manifest 保持兼容。
- 首版保持全局串行，不借重构偷偷引入并发写。
- 所有新接口有真实调用方；不增加通用 DAG、插件 DSL 或对象化 State 层级。
- 所有新增沟通文档、代码注释和 docstring 使用中文；代码标识符与外部协议字段保持英文。

### 2.3 非目标

- 本轮不实现多 job 并行。
- 本轮不重写 Chroma、CodeGraph、Graph、CodeVec 为统一数据库。
- 本轮不引入跨四种索引的 active-generation 原子切换。单写安全通过 executor fencing、
  串行调度和“确认旧执行树死亡后才能重领”保证；跨库原子发布需要独立数据迁移设计。
- 不自动 reset 开发者工作树，也不执行仓库 hooks、submodule 或 LFS 脚本。

## 3. 方案比较

### 方案 A：继续修补现有 worker

修 `close_fds`、补 `wait(timeout)`、强化 `taskkill`。改动小，但 Python 主线程或 native
调用仍可卡死，后台 heartbeat 仍可能制造假活，只适合作为止血。

### 方案 B：模块拆分但仍同进程执行

抽若干处理器、`WorkspaceProvider` 和结构化结果，维护性改善，但同进程失败域没有
消失，不能满足“彻底解决”。

### 方案 C：独立 attempt executor（采用）

长期 Orchestrator 只管理 queue、lease、deadline 和进程围栏；每次 attempt 在独立 OS
进程中执行 Git、runner 和 proof，fencing 通过后由独立 publisher 写 manifest。webhook 使用
精确 SHA 的 detached worktree，服务器 worker 从版本化 wheel 运行。

采用 C，但按 seam 渐进替换：复用 queue v2、manifest、runner registry 和 systemd，
不做大爆炸重写。

## 4. 总体架构

```text
webhook / post-commit
        |
        v
open_default_queue(fail_soft=False) -> QueuePort (File / PG)
        |
        v
target fence -> DependencyGate -- WAIT --> retry-to-tail
        |                    \-- BLOCK --> 无进程 FINALIZING
        v RUN
AttemptOrchestrator -- monotonic deadline / lease / fencing / state
        |                         |
        |                         +--> 0600 AttemptJournal / worker status
        v
空 CLAIMED journal durable replace
        |
        +--> 0700 hash artifact / 0600 write-once spec
        |
        v
AttemptProcessBackend.prepare（已阻塞 / 已挂起）
        |
        +--> Windows Job Object
        +--> delegated cgroup v2
        +--> 仅供开发/协作式诊断的 POSIX 启动门
        |
        v
带 ExecutionHandle 的 EXECUTING journal durable replace
        |                         |
        |                         +--> 失败：terminate（共享 Deadline），绝不 activate
        v
AttemptProcessBackend.activate
        |
        v
executor_observer --> executor CLI --> AttemptInputStrategy --> WorkspaceProvider
        |                                  |                       |
        |                                  +--> existing runner <--+
        |                                  +--> post-run proof
        |
        v
write-once AttemptResult -- observer 独立观测 inner rc --> write-once CompletionReceipt
        |
confirmed process death + receipt-aware validation
        |
FINALIZING checkpoint -> renew -> attempt-scoped input cleanup
        |
        +--> desired-revision guard -> ResultPublisher -> ack
        +--> supersede/ack 或 retry-to-tail
        |
artifact root write-through tombstone -> journal 最后 clear
        |
HealthRefreshPort.request（idle 时按 project 轻量聚合、有界 flush）
```

依赖方向固定为：`ops/CLI -> orchestration -> ports/data models -> adapters`。下层模块不 import
CLI、webhook 或 supervisor；项目配置由组合根解析后注入。

## 5. 组件与单一职责

### 5.1 QueuePort

补齐现有隐式能力探测，显式定义：

- `claim(limit, owner) -> list[ClaimedJob]`
- `ack(job, token)`
- `retry(job, token, reason)`
- `reject(job, token, reason)`
- `renew(job, token, ttl)`
- `quarantine(job, token, process_identity)`
- `recover_owned(worker_incarnation)`
- `begin_publish(job, desired_revision)`
- `snapshot()`

管理员能力拆成 `AdminQueuePort`，承载 `break_lease`、`prune` 等操作。FileSpool 与 PG
运行同一套 contract tests；worker 不再 `getattr`、`inspect.signature` 或判断具体后端类型。
File/PG 具体后端继续由共享 `reindex.open_default_queue()` 为 webhook、dispatch、wait、onboard、
status 与 worker 提供端口；它是无业务编排的基础工厂，不属于 isolated worker 组合根，也不能为
满足 worker 接线反向依赖 `ops/reindex_queue.py`。

isolated worker 必须显式调用 `open_default_queue(fail_soft=False)`；连接、配置或存储错误必须在
恢复/claim 前致命，不能伪装为“队列为空”。`retry` 在同一 token-fenced 事务中更新重入时间与排序
序号到队尾，保证等待依赖或临时失败的任务不反复占住队首。`reject` 只处理尚无 journal/process 的
legacy malformed claim（缺失 target、短 SHA、非法 revision），原子写失败审计并退休精确 token，
不得用 ack 或 quarantine 冒充。

### 5.2 AttemptOrchestrator

只负责：

- claim、依赖顺序、attempt ID、绝对 deadline；
- 构造无秘密 `AttemptSpec`，按“空 journal 落盘、prepare blocked、handle journal 落盘、activate”
  的事务顺序启动 executor；
- 单个非阻塞控制循环中的 heartbeat、lease renew、进程监控；
- deadline、lease 丢失或 shutdown 时终止 executor；
- 严格确认 containment 死亡，校验 completion receipt 与 result fencing；
- 持久化 FINALIZING 意图，先清理 attempt 输入，再 ack/retry，最后清 artifact/journal；
- 仅在 attempt 事务收口后请求健康刷新。

Orchestrator 不执行 Git、不运行索引、不读取 runner stdout。它只在 fencing 校验通过后调用
`ResultPublisher`；首版 `max_concurrency=1`，保持现有串行语义。

isolated worker 的父侧 selector/spec/process/publisher/journal/cleanup/orchestrator 只在
`ops/reindex_queue.py` 组合；子侧 strategy 构造与映射冻结只在 `executor_bootstrap.py`。这两个
业务组合根均依赖 queue 端口，不直接构造 File/PG 后端。

组合边界由三套静态白名单固定：父侧业务对象（含 artifact、dependency、health）只在
`ops/reindex_queue.py` 构造；子侧 strategy/冻结映射只在 `executor_bootstrap.py` 构造；
`FileSpoolQueue`/`PgJobQueue` 只在共享 `open_default_queue()` 工厂构造。生产包其他位置出现实例化
即视为架构回归。

### 5.3 AttemptInputStrategy 与 WorkspaceProvider

executor 内使用固定、非动态加载的 `AttemptInputStrategy`：

```python
materialize(spec: AttemptSpec) -> MaterializedInput
verify(spec: AttemptSpec, materialized: MaterializedInput) -> CanonicalJsonObject
```

`configured` 由 Plan A 提供，`exact_workspace` 由 Plan B 提供。每种策略严格校验自己的
canonical JSON payload；payload 不能携带 remote URL、任意绝对路径或 Python 类型名。
策略内部的 WorkspaceProvider 仍是窄 Protocol：

```python
materialize(spec: WorkspaceSpec) -> WorkspaceLease
release(lease: WorkspaceLease) -> None
```

精确 workspace 提供两个真实 adapter：

- `LocalWorkspaceProvider`：本地 post-commit 只读受信 live clone 的对象库，把目标提交复制到
  受管 bare cache + detached worktree；允许 live clone 存在 dirty 内容，但绝不读取或索引它。
- `GitWorktreeProvider`：服务器 webhook 使用受管 object cache，fetch 固定远端 ref，验证
  完整 SHA 对象后创建 detached worktree；force-push 不影响已 pin 的对象。

`WorkspaceLease` 包含 project、attempt、每仓 commit/tree SHA、root、创建时间和释放凭据。
runner 只接收 lease 中的路径，不再解析 live clone。额外仓形成明确 commit vector，不能用
“任一仓覆盖目标”代替。

### 5.4 DependencyGate

门禁只读取 manifest 与 queue 的依赖只读视图，不创建 spec、不写 manifest。无依赖 kind 直接
`RUN`；`ingest`、`code_vec` 只在同 project、同完整 target commit 的 codegraph manifest 明确成功
时 `RUN`：

- 依赖缺失，或同 target 的 codegraph pending/active：`WAIT`，不创建 spec/journal/process，
  token-fenced retry 并原子移到队尾；
- 依赖 manifest 明确失败，但又有更新 codegraph pending/active：仍 `WAIT`；
- 依赖明确失败且没有更新工作：`BLOCK`，创建一次 spec 和规范父侧 FAILED result，进入无进程
  FINALIZING，发布失败 manifest 后 ack。

BLOCK 不能伪造 completion receipt。为了封闭 manifest 与 queue 之间的崩溃窗口，它仍须持久化空
进程字段的 CLAIMED journal、0600 write-once spec/result，以及证据为 `DEPENDENCY_BLOCK` 的
FINALIZING checkpoint；恢复只重放同一 attempt/digest，绝不启动 executor。

### 5.5 Attempt executor、observer 与两阶段进程边界

每个 RUN attempt 启动 outer observer，observer 再启动一次 inner executor：

```text
python -m codev_platform.reindex.executor_observer \
  --spec <attempt-spec.json> --result <attempt-result.json> \
  --receipt <completion.json> -- <固定 inner executor argv>
```

executor 负责输入物化、Git、runner 和 post-run proof；不访问 queue、不续 lease、
不写 worker heartbeat。runner 的输出在 executor 内完成脱敏和限长，父进程不建立 stdout
PIPE，从结构上切断本次 Windows 管道继承死锁。

executor 只能 0600 write-once 写 result。observer 不解释业务 outcome，只在 inner 已退出后独立取得
真实 `process_rc`，严格解码 spec/result、计算规范 SHA-256，并 0600 write-once 写
`AttemptCompletionReceipt`。receipt 文件和目录耐久同步成功是“可恢复完成”的线性化点；outer
observer rc 只表示 observer 生命周期，不能替代 inner rc。receipt 路径要求 `result.rc` 是整数且与
`process_rc` 完全一致；raw result、缺失/损坏 receipt 或 `result.rc=None` 都只能在执行树确认死亡后
安全 retry，绝不发布。

父进程只通过平台无关 `AttemptProcessBackend` 管理 executor。`prepare` 返回的进程必须已经进入
严格 containment 或诊断启动门，但目标代码仍 blocked/suspended；调用方完成带精确
`ExecutionHandle` 的 journal durable replace（文件与目录均同步）后才调用 `activate`。完整启动事件序固定为：

```text
claim
  -> 空 CLAIMED journal durable replace
  -> prepare(blocked)
  -> 带 handle journal durable replace
  -> activate
```

空 journal 保存失败时禁止 prepare；带 handle 保存失败时只能在同一绝对 `Deadline` 内 terminate，
无论终止结论如何都绝不 activate。`recover` 返回封闭四态：`ACTIVE` 必须带精确 handle，
`CONFIRMED_DEAD` 必须带同一 handle 和匹配的 `ConfirmedProcessDeath`，`NEVER_STARTED` 与
`UNCONFIRMED` 均不带伪造 handle/proof。查询、身份或终止有歧义只能是 `UNCONFIRMED` 并 quarantine。

结果采用版本化、0600 原子 create-once JSON，至少包含：

- schema version、attempt ID、由 Orchestrator 生成的随机 attempt fence；
- project/kind、target commit、workspace commit vector；
- runtime revision、phase timing、exit code、outcome、retryable；
- proof 引用和有界 log ref。

退出码 0 不是成功凭据；只有独立 receipt、完整结果、fencing 匹配、proof 和 manifest 成功同时成立
才 ack。

### 5.6 AttemptArtifactStore 与 FINALIZING

artifact 路径只能是 `<artifact_root>/<sha256(attempt_id UTF-8)>/`，digest 为 64 位小写十六进制；
目录固定 0700（Windows 等价服务身份 ACL），成员固定为 0600 `spec.json`、`result.json`、
`completion.json`、`bootstrap.log`。spec/result/receipt create-once，bootstrap log 必须是 no-follow
普通文件。journal 中的 spec/result 路径只是冗余审计值；store 在打开任何文件前重新派生并精确
比对，拒绝 symlink、junction、reparse point、越界、额外成员和宽权限。

create-once 和 journal replace 共享单一耐久叶子：POSIX 同步文件与父目录；Windows 使用
`MoveFileExW(..., MOVEFILE_WRITE_THROUGH)`，replace 才额外允许
`MOVEFILE_REPLACE_EXISTING`。attempt artifact 永不覆盖，journal 才可替换；任何直接
`os.replace`/普通 rename 旁路都属于门禁失败。

FINALIZING checkpoint 只保存 attempt/fence/target、动作（guarded-ack、retry）、
证据类型和可选 result digest，不复制 result 真值，也不替代 process backend 的死亡证明。固定顺序
是：匹配死亡证明 → checkpoint durable replace → renew 覆盖清理窗 → attempt-scoped 幂等 input
cleanup → queue 动作 → artifact 根 write-through rename 为确定性 `.cleanup` tombstone 并删除固定
成员 → journal 最后 clear。`ResultPublisher` 只消费冻结的 `ValidatedAttemptResult`，不读取
workspace，因此 cleanup 前置不会破坏发布证据，反而阻止新 attempt 与旧 workspace 清理重叠。

`GUARDED_ACK` 不在 cleanup 前猜测 publish/supersede。cleanup 完成后仅进入一次
`begin_publish`，在同一 File key 锁或 PG 行事务中按当时 desired revision 原子选择 publisher+ack
或 supersede/ack；cleanup 期间不得持有该 guard，也不得用 guard 外预读结果选择分支。

崩溃后旧 token 仍 active 就重放同一动作；token 已失时，精确 manifest 或同 key 的 replacement
pending/active 是耐久见证，禁止再改 queue/manifest；artifact root 尚在仍按旧 attempt_id 幂等补做
input cleanup，再清 artifact/journal。root 已进入 tombstone 或消失才证明 cleanup 已越过。两类见证
均无时失败关闭。QUARANTINED 自动路径永不 cleanup；管理面取得严格死亡证明后才可收口。

### 5.7 ResultPublisher

`ResultPublisher` 是 manifest 的唯一写入口。它接收已通过 fencing 校验的不可变 result，
再次核对 target/workspace/runtime/attempt 后持久化 manifest；manifest 写成功才允许 queue
ack。manifest 同时保存规范 `result_digest`；同一 attempt 只有 digest 与全部持久字段一致才是幂等，
任何冲突都失败关闭。发布在单个 SQLite 写事务中显式 insert/update，禁止 `INSERT OR REPLACE`
吞掉冲突。executor 和 WorkspaceProvider 均不能 import manifest store。

### 5.8 进程围栏

不新增第二套 worker supervisor；把现有 supervisor 演进为 executor containment adapter。

- Windows：Job Object + `JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE`。使用 `CreateProcessW` 的
  `CREATE_SUSPENDED` 与 `PROC_THREAD_ATTRIBUTE_JOB_LIST` 在内核创建动作中原子纳管，避免
  “已创建、尚未 Assign”崩溃窗口；handle journal 落盘后才 `ResumeThread`。timeout 使用
  `TerminateJobObject`，只有 `ActiveProcesses=0` 才能签发精确死亡证明。该原子路径最低要求
  Windows 10 / Server 2016。
- WSL/systemd：reindex unit 使用 `Delegate=yes`，每个 attempt 进入独立 cgroup v2 子树；
  最小 bootstrap 通过 `python -I -S <绝对脚本>` 启动且只依赖标准库，先设置
  `PR_SET_PDEATHSIG(SIGKILL)` 并复核预期父 PID，再把自身加入子 cgroup、回读成员身份并等待
  父侧 gate；handle journal 落盘后才 exec executor。
  timeout 使用 `cgroup.kill`，只有 `cgroup.events populated=0` 才生成死亡证明。
- 普通 POSIX：只作为显式 DEV/协作式诊断。bootstrap 同样使用启动门、PDEATHSIG 和父 PID 复核，
  并以 `-I -S` 禁止脚本执行前加载 `site/.pth/sitecustomize`；但 process group 扫描无法排除
  两次扫描之间未观测的 `fork -> setsid -> reparent` 逃逸，因此 activate 后永久不得签发
  `ConfirmedProcessDeath`，也不得作为生产回退。

生产选择器只允许 Windows Job 或 Linux delegated cgroup。Linux 委派/就绪检查失败必须
在 `recover_owned`/claim 前致命退出，绝不构造 POSIX 后端；Windows 原子 Job List、
KILL_ON_JOB_CLOSE 或受限 handle list 能力缺失同样致命，禁止裸 `Popen` 回退。所有
prepare/activate/terminate/recover 清理共享
调用方传入的单调绝对 `Deadline`；runner 业务 timeout 也必须始终解析为有限正数，timeout 后的
kill、二次 wait 与 reader join 共享一个额外有限清理截止点。无法确认执行树死亡时进入
`QUARANTINED`，不得释放后立即重跑。

### 5.9 有界健康刷新

健康刷新不是 attempt 成功条件。Orchestrator 只在 artifact/journal 收口后调用
`HealthRefreshPort.request(project_id)`，worker 到 idle 时按 project 去重并同步 `flush`，不创建线程、
不持有 job lease。启动时请求全部受管 project，补偿 request 前崩溃。

`ContainedHealthRefresher` 复用严格生产 process backend，但必须为 health 自己执行
prepare → 将 handle 以 0600 durable replace 写入现有 supervisor 状态文件的
`health_operation` 子记录 → activate；新 worker 在 claim 前通过与完整 attempt journal 共用身份判断
的 `recover_handle` 恢复该子记录，不能复制进程身份逻辑。命令失败/timeout 且树已
确认死亡只设置 `health_failed=true`，不得改写已发布/已 ack 的 job；只有真实成功才清除此位。死亡
无法确认则停止新 claim，由 systemd control-group 收口。重启恢复只见“树已死”但拿不到原 exit
code 时必须保守标记失败并重新请求 project，不能从快照文件猜成功。它是窄适配器，不是第二个
orchestrator。

health 模块只依赖 `HealthOperationJournalPort`，不 import supervisor。supervisor 的同一个状态适配器
是文件唯一写者，同时向 Orchestrator/health 暴露两个窄端口；每次 durable replace 保留另一子记录。
health 只在 attempt journal 已清后 flush，启动时 health 恢复完成前不 claim，避免双写覆盖。

### 5.10 运行时发布

服务器不再从 editable checkout 运行任何受管 Python unit：

- 从精确提交构建 wheel，记录 Git SHA 与 wheel SHA-256；
- 依赖按受审 hash lock 构建内容寻址 `bases/<requirements-sha>/venv`，Torch/CUDA 等重依赖
  只安装一次；每个 release 使用薄 venv，只安装 app wheel，并通过受控纯路径 `.pth` 复用 base；
- base/release 直接在最终路径创建，以 `.incomplete` 门禁，不移动已生成的 venv；
- `current` symlink 在同文件系统原子切换；systemd 从 `current` 启动；
- readiness/status 报告 release ID、wheel digest、解释器 realpath 和源码 revision；
- 升级采用“预检 -> 停止 claim -> 等当前 attempt 收口 -> 切换 -> restart -> 真工具冒烟”；
- 失败时切回上一 release。schema 变更只允许 expand-contract。

Windows 开发态可继续 editable，但状态必须明确标记 `runtime_mode=editable` 和真实 source root，
不能与服务器 release 混淆。

## 6. 状态、不变量与错误语义

### 6.1 状态

父侧 journal 只使用 enum + transition table，不创建 GoF State 类：

```text
RUN:   CLAIMED -> EXECUTING -> [TERMINATING] -> FINALIZING -> durable clear
BLOCK: CLAIMED -------------------------------> FINALIZING -> durable clear
任意进程身份/死亡歧义 -------------------------> QUARANTINED
```

`MATERIALIZING/RUNNING/PERSISTING` 等只属于 executor timing/proof，不复制成父侧真值；
`SUCCEEDED/FAILED/RETRYABLE/TIMED_OUT/SUPERSEDED` 只属于 result/queue 决策。

权威状态仍只有三层：queue 表示待办/claim；worker journal 表示当前 attempt；manifest 表示
已完成构建。不得再创建第四份“综合状态真值”。`health_operation` 只是同一个 supervisor 文件内的
短期进程 handle，启动恢复完成后即清除，不表达 job/result 状态，也不能被 health/status 当成构建
成功事实。

`CLAIMED` 只能进程字段全空且无 finalization；`EXECUTING/TERMINATING` 必须四个进程字段全有且无
finalization。`FINALIZING` 必须带严格 checkpoint：进程路径字段全有并在每次恢复重新取得匹配死亡
证明，只有 `DEPENDENCY_BLOCK` 允许字段全空且不调用后端。`QUARANTINED` 保留原形，自动路径不得
推进。任何部分字段、非法 action/evidence/digest 组合都不是中间态，而是损坏输入。

### 6.2 必须保持的不变量

1. 同一 `(project, kind)` 最多一个未被围栏的 executor。
2. 新 attempt 启动前，旧执行树必须确认死亡；否则 quarantine。
3. lease renewal 仅由 Orchestrator 控制循环执行；executor 无权续租。
4. lease/token 丢失且 executor 存活时，立即进入终止流程。
5. TERMINATING 可续租到确认死亡，禁止先释放 lease 再 kill。
6. deadline 使用 monotonic clock；wall clock 只用于审计时间。
7. workspace 每个 repo 的 HEAD/tree 必须与 result 中 commit vector 完全一致。
8. webhook 目标暂不可取得时 retry/supersede；legacy target 缺失、短 SHA 或非法 revision 在任何
   spec/journal/process 前 token-fenced reject；两者都绝不回退本地 HEAD。
9. 迟到结果、旧 token、残缺 JSON、错误 release/attempt 永不发布；同 attempt manifest 冲突必须
   quarantine，而不是覆盖或静默忽略。
10. heartbeat 仅表示控制循环活跃；job progress/deadline/kill 独立展示；health 成功与 job 成功分离。
11. 日志洪泛、磁盘满、脱敏异常不能延迟 deadline 或阻止 kill。
12. File/PG 后端的 ack/retry/reject/renew/fencing 语义一致，retry 原子移动到队尾。
13. claim 后必须先 durable replace 空 `CLAIMED` journal，再调用 `prepare`；带 handle journal 完成
    文件和目录耐久同步前永远不得 `activate`。
14. 带 handle journal 保存失败只能 terminate 同一 handle，绝不 activate；死亡未确认则 quarantine。
15. `RecoveryReport` 只允许 `ACTIVE`、`CONFIRMED_DEAD`、`NEVER_STARTED`、`UNCONFIRMED` 四态；
    只有 `CONFIRMED_DEAD` 可携带与精确 handle 匹配的死亡证据。
16. 生产进程后端只能是 Windows Job 或 Linux delegated cgroup；Linux 就绪检查/委派失败
    必须在 claim 前致命，POSIX 永不成为回退。
17. 同一次进程操作的所有清理共享一个 `Deadline`，禁止在内部为 kill/wait 重置总预算。
18. receipt 文件和目录耐久同步是完成线性化点；outer rc、raw result、`result.rc=None` 都不能替代
    独立 inner rc，且 receipt 路径的两个 rc 必须完全一致。
19. artifact 只使用 0700 hash 目录与固定 0600 成员；journal 路径不受信任，打开前必须重新派生、
    比对并拒绝 link/reparse/越界。
20. FINALIZING 必须先 renew、再 attempt-scoped 幂等 input cleanup、再释放 queue；artifact 使用
    write-through tombstone，journal 最后 clear；QUARANTINED 自动路径永不 cleanup。
21. `ingest/code_vec -> codegraph` 依赖不变：WAIT 公平重入队尾，BLOCK 走无进程 FINALIZING 且不
    伪造 receipt。
22. isolated worker 只能以 `open_default_queue(fail_soft=False)` 启动；生产 backend readiness 在
    `recover_owned` 与 claim 前完成，失败路径 queue 调用数为零。
23. 三套组合白名单分别约束父侧、子侧 strategy 和共享 queue 工厂，禁止第三个业务组合根。

### 6.3 失败分类

| 类别 | 处理 |
|---|---|
| 目标对象暂不可得、依赖未完成 | RETRYABLE，保留 target SHA |
| legacy target 缺失、短 SHA、非法 revision | claim token 围栏下 reject 并写失败审计；不创建 spec/journal/process |
| 依赖明确失败且无更新 codegraph 工作 | 无进程 FINALIZING，发布确定性 FAILED 后 ack；不造 receipt |
| 新 webhook 覆盖旧 desired SHA | SUPERSEDED，旧结果不可发布 |
| runner 明确失败 | FAILED，写 proof，不死循环 |
| 空 CLAIMED journal 落盘失败 | 不 prepare；保留 claim 供恢复 |
| 带 handle journal 落盘失败 | 同一 deadline 内 terminate，绝不 activate；未确认则 quarantine |
| executor timeout/lease 丢失 | 先终止并确认死亡，再 RETRYABLE/FAILED |
| 无法确认进程树死亡 | QUARANTINED，需运维介入 |
| Linux 委派/就绪检查缺失 | claim 前致命退出；禁止 POSIX 回退 |
| raw/残缺 result、receipt 缺失或 rc 不匹配 | 确认树死亡后 RETRY；绝不发布，不用 outer rc 补证据 |
| manifest 同 attempt/digest/字段冲突 | QUARANTINED，绝不覆盖 |
| input cleanup 失败/超时 | 保留 active claim 与 FINALIZING，停止新 claim，有界重试 |
| artifact tombstone 清理失败 | queue 动作不回滚；保留 FINALIZING，幂等继续清理，journal 不清 |
| health 命令失败且树已死 | job 终态不变，`health_failed=true`；下一轮可继续刷新 |
| health 树死亡无法确认 | 停止新 claim，由 service control-group 收口 |
| queue 打开失败 | worker 致命退出；禁止 fail-soft 空队列 |
| Orchestrator 崩溃 | systemd/launcher 清执行树；新实例先恢复 health/attempt journal 再 claim |

## 7. 数据流

### 7.1 本地 post-commit

1. hook 写入完整 target SHA。
2. `LocalWorkspaceProvider` 从只读 live object DB 物化受管 bare cache + detached worktree，
   live dirty 内容不进入输入。
3. Orchestrator 依次 durable replace 空 CLAIMED journal、初始化 0700/0600 artifact、`prepare`
   blocked observer、durable replace 精确 handle，最后才 `activate`；任一持久化失败均不放行目标。
4. observer 独立等待 inner executor，result 与 completion receipt 均 write-once；containment 确认
   死亡后才做 receipt-aware validation。
5. FINALIZING 先 renew 并清理 attempt workspace，再由 `ResultPublisher` 写 manifest、ack；随后
   artifact tombstone 收口，journal 最后 clear，才请求聚合健康刷新。

### 7.2 WSL webhook / force-push

1. webhook 验签；project、remote、ref 从服务器配置解析，payload 不能指定任意 URL。
2. 按仓库 `object-format` 接受完整 40/64 位十六进制 OID，拒绝短 SHA 和 revision
   expression。
3. object cache fetch 固定 ref，验证 commit 对象与 webhook SHA 完全一致。
4. detached worktree 物化目标；live clone 不 pull、不 reset。
5. executor 在 immutable workspace 构建索引并记录 commit vector/proof。
6. 若期间又有 push，旧 attempt 可结束，但旧 token/desired SHA 不能覆盖新任务。
7. `ingest/code_vec` 在同 target codegraph 未成功时按 WAIT 移到队尾；明确失败且无更新工作时以
   BLOCK 无进程最终化发布失败，不启动无意义 executor。

### 7.3 崩溃恢复

1. 获取 run lock，以 `fail_soft=False` 打开 queue，完成 runtime 与生产 backend readiness；先从
   supervisor 同一状态文件恢复 health operation，以上任何失败都不得调用 `recover_owned`/claim。
2. 读取上一代 owner 与 attempt journal；artifact store 在读文件前重新派生 hash 路径并校验
   journal，路径篡改或 reparse 失败关闭。
3. 空 CLAIMED 进程字段按确定性 Job/cgroup 清理 blocked containment；只有 `NEVER_STARTED` 才可
   retry。完整字段重新校验 identity，只接受 ACTIVE、带匹配证据的 CONFIRMED_DEAD 或
   UNCONFIRMED；部分字段/PID 复用/查询歧义 quarantine。
4. FINALIZING 的进程路径每次恢复重新取得死亡证明；DEPENDENCY_BLOCK 字段必须全空且只重放同一
   result digest。旧 token active 就按 checkpoint 重放；token 已失只接受精确 manifest 或同 key
   replacement queue 见证；root 尚在仍补做旧 attempt cleanup，见证缺失则停止 claim。
5. input cleanup 总在 queue 释放前且按 attempt_id 幂等；queue 动作后只收口固定 artifact
   root/tombstone，journal 最后 clear。QUARANTINED 自动恢复不 cleanup。
6. 没有 journal 但存在旧 owner claim 表示 claim→首次 durable replace 窗口：malformed target
   reject，其他项 retry-to-tail，不构造 attempt/result。

### 7.4 Legacy queue 切换

切 isolated 前停止旧 worker，在 run lock 下审计 File/PG pending/active。旧 active 必须先由精确
owner `recover_owned` 取回，再以原 token reject；旧 pending 只能在首次 claim 后 reject。缺
`target_commit`、短 SHA 或非法 revision 都不能回退 HEAD，也不能构造临时 spec。部署报告记录
project/kind/reason 和数量（不含 token/fence），确认剩余项均有完整 target 后才开始正常 drain；新
webhook 以完整 target 重新入队。

## 8. 阶段与门禁

跨计划实施顺序固定为：先完成 Plan C Task 1–2 的 runtime models/identity 叶子能力，
再完成 Plan A attempt isolation，随后完成 Plan B exact workspace，最后完成 Plan C
Task 3–10 的依赖基座、release、systemd 与切换。Plan A 直接消费启动时缓存的
`runtime_identity().runtime_revision`，不在 reindex 内复制版本探测。

### 阶段 0：兼容基线与事故回归（0.5 天）

- 冻结 CLI、queue v2、manifest、status、hook/systemd 行为。
- 建立“孙进程持 stdout”“force-push 非祖先”“运行 revision 漂移”三个红灯测试。
- 门禁：测试稳定复现当前两类故障；不改生产行为。

### 阶段 1：协议和职责拆分（1 天）

- 引入不可变 `AttemptResult`、显式 QueuePort/AdminQueuePort。
- 固化 `fail_soft=False` worker、retry-to-tail、legacy reject 和 dependency RUN/WAIT/BLOCK 契约。
- 引入固定 `AttemptInputStrategy` 与单一 Orchestrator，拆分 worker/supervisor/queue 超限职责，
  不创建第二个有副作用编排层。
- 门禁：File/PG 契约测试同时通过；现有回归零下降。

### 阶段 2：attempt 进程隔离（1.5 天）

- observer/executor CLI、write-once result/receipt、0700 hash artifact、FINALIZING checkpoint、
  Windows 原子 Job List、Linux delegated cgroup，以及仅用于
  DEV/诊断的 gated POSIX backend。
- 取消 executor 内 lease 续租与父进程 runner pipe。
- 门禁：prepare 与 activate 之间的 journal 顺序、completion 线性化、raw result 安全重试、
  cleanup-before-queue、父崩溃、忽略终止、持有管道、无穷日志、残缺结果、终止失败、Delegate
  缺失时 claim 前失败关闭等故障注入通过。

### 阶段 3：精确 SHA workspace（1.5 天）

- Local/GitWorktree provider、object pin、lease/janitor、commit vector proof。
- 门禁：旧链到非祖先新链可索引新 SHA，live clone 不变；乱序 webhook 不能发布旧结果。

### 阶段 4：版本化 WSL runtime（1 天）

- hash lock、内容寻址依赖 base、薄 wheel release、原子 current、systemd 渲染、
  runtime revision/status、回滚。
- 门禁：服务全部报告同一 release；旧 release 不能 claim；回滚恢复上一版本。

### 阶段 5：真实环境演练与收口（1 天）

- Windows 短驻、WSL systemd、FileSpool、PG 各跑故障矩阵。
- 安全对齐当前 WSL 服务、重建新 HEAD 索引并确认 MCP 实调。
- 门禁：无双写、无假健康、无无界等待；后继 job 在 deadline+grace 上界内启动；
  manifest/workspace/runtime 均对齐目标提交；健康子进程故障不改写 job 终态，未确认死亡会停止 claim。

## 9. 验证矩阵

| 维度 | 必验 |
|---|---|
| Queue | File/PG claim、renew、retry-to-tail、reject audit、dirty-after-active、expired、break token、worker fail_soft=false |
| Process | Windows 原子 Job List、delegated cgroup、prepare/activate 启动门、父崩溃、孙进程持 pipe、忽略 TERM、kill 失败、POSIX 永不签严格死亡证明 |
| Completion | observer 独立 inner rc、receipt 文件+目录线性化、raw result、rc None/不匹配、outer rc 不可替代 |
| Artifact | 0700 hash 路径、0600 create-once、WRITE_THROUGH、journal 路径篡改、link/reparse、目录 tombstone |
| Result | exit 0 无结果、partial JSON、旧 attempt、token mismatch、磁盘满、manifest attempt/digest/全字段冲突 |
| Workspace | exact SHA、force-push、删除 ref、对象不可得、worktree crash GC |
| Dependency | code_vec/ingest 只接受同 commit 的 codegraph 成功 proof；WAIT 公平到队尾；BLOCK 无进程可恢复发布 |
| Legacy | target 缺失、短 SHA、非法 revision 在 spec/journal/process 前 token-fenced reject |
| Finalizing | death→renew→input cleanup→queue→artifact tombstone→journal clear；每个边界 kill 后幂等恢复 |
| Crash | 空 CLAIMED durable replace 前后、prepare 后、handle durable replace 前后、activate 后、runner/result/receipt/cleanup/manifest/ack/artifact tombstone 前后分别 kill |
| Runtime | editable/release 识别、wheel digest、原子切换、旧新版本排他 |
| Security | webhook 验签、URL 配置侧解析、完整 SHA、日志脱敏、不执行 hooks、bootstrap `-I -S` 阻断 `.pth/sitecustomize` 抢跑 |
| Health | 独立 health handle 先持久再 activate、project 去重、失败不改 job、未确认死亡停止 claim |
| Composition | 父侧、子侧 strategy、共享 queue 工厂三套 AST 白名单；生产 POSIX 构造数为零 |
| Observability | runtime/target/workspace/attempt/deadline/completion/finalizing/health/outcome/log ref 齐全；无 token/fence/health handle/native_ref |
| E2E | local hook 全链；WSL webhook force-push 全链；真实 MCP tool 可用 |

Python 目标回归至少覆盖 reindex、queue、webhook、manifest、systemd、health；最终按共享底座
风险运行全量 `python -m pytest tests/` 与定向 ruff。

## 10. 风险与回退

| 风险 | 控制 |
|---|---|
| 大爆炸迁移 | Phase 按 seam 独立提交；旧 CLI/格式保持可读 |
| Windows Job Object 兼容 | 独立 adapter + Windows 10/Server 2016+ OS-gated integration；失败进入 quarantine |
| Linux 委派缺失 | claim 前就绪检查致命；只选 cgroup，禁止 POSIX 回退 |
| POSIX 逃逸不可证明 | 仅 DEV/协作式诊断；activate 后永久不签 `ConfirmedProcessDeath` |
| worktree/cache 泄漏 | pin ref + lease + janitor；确认无活 executor 后回收 |
| 新 attempt 与旧 cleanup 重叠 | input cleanup 在 queue 释放前；适配器严格按 attempt_id 幂等，crash 后新 owner 也不受旧路径影响 |
| raw result 被误当成功 | completion receipt 独立观测 inner rc；缺 receipt 只可在死亡确认后 retry |
| journal 路径指向越界文件 | 只从 attempt digest 派生固定路径；打开前校验 link/reparse/权限/成员白名单 |
| queue 动作后崩溃 | exact manifest 或 replacement queue 作为见证；无见证失败关闭，不引入第二套事务协调器 |
| health 假成功或卡死 | 独立持久 handle + 严格 containment；成功位只由真实 rc 清除，未确认死亡停止 claim |
| 新旧 worker 双写 | incarnation/run-lock + claim fencing；切换前停止新 claim |
| release 与 schema 不兼容 | additive migration；release/status 带 schema version |
| 回滚时残留 executor | 先 containment cleanup，再切旧 release |

回退按 Phase 进行：只有无 active attempt 时才允许切回 legacy worker；queue/manifest/additive
字段保持旧版可读。WSL 当前旧 clone 在部署前创建备份 ref，但不再作为索引输入。

## 11. 替代方案

- 仅设置 `close_fds=True`：可修当前 pipe 继承源头，但不能隔离其他系统调用/native 卡死。
- 仅增加 watchdog 线程：Python 线程无法可靠中断另一个阻塞线程，heartbeat 仍可能假活。
- 自动 `reset --hard` live clone：会把运行时、索引输入和人类工作树继续耦合，拒绝采用。
- 通用 workflow/DAG：目前只有固定四 stage 与简单依赖表，没有第二个真实需求，拒绝引入。
- 全索引 generation 原子发布：有价值但涉及四套存储迁移，不与本轮故障隔离捆绑。

## 12. 启动条件

- 用户已批准“彻底解决”并授权多角色架构评审后实施。
- 三方评审完成：高可用、Git/部署、可维护性意见已合并。
- 当前 Windows/WSL 运行态证据已保存到会话，不需要为复现修改线上数据。
- 实现前必须写详细 TDD 计划；每个 Phase 先红灯、后最小实现、再重构。
