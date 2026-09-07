# Reindex Attempt 围栏与发布实施子计划（任务 5–6）

> **供自动化执行代理使用：** 必需子技能：使用 `superpowers:subagent-driven-development`（推荐）或 `superpowers:executing-plans`，逐项实施本计划。各步骤使用复选框（`- [ ]`）跟踪。
>
> 上级总览与唯一全局契约：[Reindex 尝试隔离实施计划](./reindex-attempt-isolation-implementation-plan-2026-07-11.md)

**目标：** 实现 Windows/WSL 可证明进程树死亡和只接受已验证结果的幂等 manifest 发布。

**架构：** 平台无关进程契约下分离 Windows Job、delegated cgroup 与诊断 POSIX 后端，共享绝对 Deadline；发布器仅消费 ValidatedAttemptResult，并在单个 manifest 事务内比较 attempt digest 与全部持久字段。

**技术栈：** Python 3.10+、`dataclass`/`Protocol`、严格 JSON、pytest，以及上级总览指定的跨平台进程与耐久能力。

## 执行前置

- 必须先读取上级总览的“全局约束”“固定跨计划契约”“文件映射”；这些内容只在上级维护，本子计划不复制第二份真值。
- 本子计划只覆盖标题所列任务；相邻任务的产出通过上级契约中的精确接口消费。
- 所有沟通、注释和文档使用中文；代码标识符与外部协议字段保持英文。

---
### 任务 5：实现严格有界的进程封装边界

> 状态口径：当前共享工作树已经形成下述 Task 5 主接口和职责拆分；供 Task 7 health operation
> 复用的 `recover_handle` 是本任务尚需补齐的同后端窄接缝，补齐前不得开始 health 接线。两个
> `CODEV_REINDEX_REAL_CGROUP=1` 用例已在临时 `Delegate=yes` unit 中定向验证，但生产 unit、
> Task 7 生产选择器和整条 Orchestrator 链路尚未接线验收，不能由定向测试外推为全链完成。

**文件：**
- 公共契约与共享叶子：`codev_platform/reindex/attempt_process.py`、
  `codev_platform/reindex/bootstrap_runtime.py`、`codev_platform/reindex/process_stdio.py`
- Windows：`codev_platform/reindex/windows_job.py`、`windows_job_native.py`、
  `windows_job_win32.py`、`windows_job_resources.py`、`windows_process_identity.py`、
  `windows_process_evidence.py`
- POSIX：`codev_platform/reindex/posix_process.py`、`posix_bootstrap.py`、
  `posix_process_identity.py`、`posix_process_state.py`
- cgroup v2：`codev_platform/reindex/cgroup_process.py`、`cgroup_process_state.py`、
  `cgroup_abort.py`、`cgroup_spawn.py`、`cgroup_recovery.py`、`cgroup_termination.py`、
  `cgroup_v2.py`、`cgroup_bootstrap.py`
- 测试：`tests/test_reindex_attempt_process_values.py tests/test_reindex_attempt_process_reports.py tests/test_reindex_attempt_process_start.py tests/test_reindex_attempt_process_protocol.py`；Windows Job：`reindex_windows_job_support.py`、
  `test_reindex_windows_job_{lifecycle,recovery,cleanup,native}.py`、
  `test_reindex_windows_job_real.py`；POSIX：`reindex_posix_process_support.py`、
  `test_reindex_posix_process_{lifecycle,stdio,identity,termination,recovery}.py`；
  cgroup：`reindex_cgroup_process_support.py`、
  `test_reindex_cgroup_{readiness,lifecycle,bootstrap,integration}.py`；
  其余：`test_process_tree.py`、`test_reindex_runner_logs.py`、
  `test_reindex_runner_timeout.py`
- 故障夹具：`tests/fixtures/reindex_process_fixture.py`
- 修改：`codev_platform/core/process_tree.py`：移除 kill 后的无界等待
- 修改：`codev_platform/reindex/runner_logs.py`、`runners.py`：runner 运行预算始终解析为有限正数；
  timeout 后的 kill、二次 `proc.wait()`、日志 reader 收口共享一个有限清理截止点

**接口：**

    @dataclass(frozen=True, slots=True)
    class Deadline:
        expires_at: float  # 单调时钟的绝对截止点


    @dataclass(frozen=True, slots=True)
    class ExecutionHandle:
        attempt_id: str
        pid: int
        process_identity: str
        containment_kind: str
        native_ref: str
        started_at: float


    @dataclass(frozen=True, slots=True)
    class ProcessReference:
        process_identity: str
        containment_kind: str
        native_ref: str


    @dataclass(frozen=True, slots=True)
    class TerminationReport:
        requested_at: float
        finished_at: float
        graceful: bool
        forced: bool
        confirmed_dead: bool
        death_proof: ConfirmedProcessDeath | None
        note: str


    class RecoveryState(str, Enum):
        ACTIVE = "active"
        CONFIRMED_DEAD = "confirmed_dead"
        NEVER_STARTED = "never_started"
        UNCONFIRMED = "unconfirmed"


    @dataclass(frozen=True, slots=True)
    class RecoveryReport:
        state: RecoveryState
        handle: ExecutionHandle | None
        death_proof: ConfirmedProcessDeath | None
        note: str


    class AttemptProcessBackend(Protocol):
        def prepare(self, *, attempt_id: str, argv: Sequence[str], cwd: Path,
                    bootstrap_log: Path, deadline: Deadline) -> ExecutionHandle:
            raise NotImplementedError("进程后端方法")
        def activate(self, handle: ExecutionHandle, deadline: Deadline) -> None:
            raise NotImplementedError("进程后端方法")
        def poll(self, handle: ExecutionHandle) -> int | None:
            raise NotImplementedError("进程后端方法")
        def terminate(self, handle: ExecutionHandle, *, grace_sec: float,
                      deadline: Deadline) -> TerminationReport:
            raise NotImplementedError("进程后端方法")
        def recover(self, journal: AttemptJournalEntry,
                    deadline: Deadline) -> RecoveryReport:
            raise NotImplementedError("进程后端方法")
        def recover_handle(self, handle: ExecutionHandle,
                           deadline: Deadline) -> RecoveryReport:
            raise NotImplementedError("精确 handle 恢复方法")
        def confirm_dead(self, handle: ExecutionHandle,
                         deadline: Deadline) -> ConfirmedProcessDeath | None:
            raise NotImplementedError("进程后端方法")
        def confirm_reference_dead(self, reference: ProcessReference,
                                   deadline: Deadline) -> ConfirmedProcessDeath | None:
            raise NotImplementedError("进程后端方法")

`prepare` 只能返回已经受 containment 保护、但目标代码仍被启动门阻塞的 `ExecutionHandle`。
调用方必须按以下顺序推进，后端不得把 `prepare` 偷换成单阶段 spawn：

    claim
      -> 持久化空进程字段的 CLAIMED journal 并 fsync
      -> prepare(blocked)
      -> 持久化带精确 handle 的 journal 并 fsync
      -> activate

空 journal 保存失败时不得调用 `prepare`。带 handle 的 journal 保存或 `fsync` 失败时，调用方只能
在同一个 `Deadline` 内调用 `terminate`；
无论终止结果是否明确，都绝不能调用 `activate`。`activate` 失败必须抛出携带同一精确 handle 的
`AttemptProcessStartError`，只有取得与该 handle 完全匹配的 `ConfirmedProcessDeath` 后才允许重试。

`RecoveryReport` 的字段组合是封闭契约：

| 状态 | handle | death_proof | 含义 |
|---|---|---|---|
| `ACTIVE` | 精确 handle | 无 | containment 仍活动，可继续管理 |
| `CONFIRMED_DEAD` | 精确 handle | 与其身份匹配的证明 | 已确认同一执行树死亡 |
| `NEVER_STARTED` | 无 | 无 | 只有 blocked prepare 未发生或未发布目标已被安全清空 |
| `UNCONFIRMED` | 无 | 无 | 查询、终止或身份存在歧义，必须失败关闭并 quarantine |

`recover_handle` 只接受已经耐久保存且通过身份 codec 的完整 handle，因此只允许返回 `ACTIVE`、
`CONFIRMED_DEAD` 或 `UNCONFIRMED`，永不返回 `NEVER_STARTED`；各后端的完整 journal 恢复必须委派
同一实现，不能复制第二套身份判断。Task 7 的 health operation 复用该窄接缝，空 CLAIMED 的
确定性 containment 清理仍只走 `recover(journal)`。

死亡证明绑定版本化 `process_identity`、`containment_kind` 和 epoch `confirmed_at`；
`CONFIRMED_DEAD` 还必须保留 journal 中的精确 handle，禁止用“PID 当前不存在”冒充同一进程的证明。
等待统一使用单调绝对 `Deadline`，持久时间戳只用 epoch wall clock；prepare 失败回收、activate
失败回收、TERM/KILL、Job/cgroup 查询、二次 wait 和 reader 收口不得各自重置预算。

- [ ] **步骤 1：编写真实进程树测试**

使用 `tests/fixtures/reindex_process_fixture.py` 和专用 bootstrap 文件。增加：
- test_backend_protocol_exposes_prepare_activate_and_shared_deadline
- test_recover_handle_shares_full_journal_identity_and_never_returns_never_started
- test_recovery_report_accepts_only_each_states_canonical_fields
- test_death_proof_must_match_target_handle
- test_posix_prepare_blocks_target_until_activate
- test_posix_blocked_prepare_can_prove_target_never_activated
- test_posix_parent_crash_before_activate_never_executes_target
- test_posix_parent_guard_rejects_changed_parent
- test_posix_bootstrap_flags_are_isolated_from_target_argv
- test_posix_stdout_and_stderr_are_direct_files
- test_posix_term_then_kill_stays_inside_deadline
- test_activated_posix_graceful_exit_remains_unconfirmed
- test_posix_group_kill_removes_grandchild
- test_posix_setsid_escape_is_permanently_unconfirmed
- test_posix_fast_reparented_setsid_escape_never_gets_strict_proof
- test_cgroup_death_proof_requires_populated_zero
- test_cgroup_backend_fails_closed_without_delegation
- test_bootstrap_import_surface_contains_only_standard_library
- test_bootstrap_isolated_mode_never_loads_sitecustomize
- test_cgroup_parent_guard_rejects_changed_parent
- test_cgroup_abort_cleanup_uses_one_shared_deadline
- test_cgroup_pre_spawn_resource_failure_is_transactionally_cleaned
- test_cgroup_environment_preflight_failure_cleans_before_opening_pipe
- test_real_cgroup_parent_crash_before_activate_is_recoverable
- test_real_cgroup_kill_removes_setsid_grandchild
- test_cgroup_bootstrap_assignment_failure_never_execs_target
- test_cgroup_backend_uses_absolute_bootstrap_script
- test_linux_identity_rejects_reused_pid_starttime
- test_real_windows_native_job_handle_is_not_inheritable
- test_windows_assigns_suspended_process_before_resume
- test_windows_assignment_failure_never_falls_back_to_raw_process
- test_windows_parent_crash_kills_job_grandchild_without_holding_outer_pipe
- test_atomic_job_assignment_kills_suspended_root_on_prepare_parent_crash
- test_windows_confirmed_dead_requires_zero_active_job_processes
- test_timeout_does_not_wait_on_descendant_holding_stdout
- test_timeout_process_wait_and_reader_cleanup_share_one_bound
- test_run_tree_timeout_cleanup_has_bound_and_never_uses_popen_context

- [ ] **步骤 2：固定两阶段协议、恢复四态与单一时间预算**

`attempt_process.py` 只定义平台无关值对象和窄 Protocol。三个后端共享同一
`prepare/activate/poll/terminate/recover/confirm_dead/confirm_reference_dead` 形状，
但互不 import 具体实现。恢复只返回上述四态；任何不完整 journal、复用 PID、出生标记不匹配、
控制文件解析异常或原生调用歧义都失败关闭，绝不合成死亡证明。

- [ ] **步骤 3：实现 Windows 失败关闭式 containment**

`WindowsJobAttemptProcessBackend` 创建带 `JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE` 的不可继承 Job。
`windows_job_win32.py` 只声明 ABI，`windows_job_native.py` 封装安全原生调用，资源、身份和证据分别
落在独立叶子。`CreateProcessW` 必须同时使用 `CREATE_SUSPENDED` 与
`PROC_THREAD_ATTRIBUTE_JOB_LIST`，在内核创建动作中原子加入 Job；`HANDLE_LIST` 只允许标准日志句柄，
Job 句柄本身不得继承。该原子 Job List 能力最低要求 Windows 10 / Server 2016。

`prepare` 验证 Job 归属后仍保持主线程 suspended；只有调用方完成带 handle journal 的原子替换和
`fsync` 后，`activate` 才调用 `ResumeThread`。父进程在 create 返回到 handle 落盘之间崩溃时，
最后一个 Job 句柄关闭触发 `KILL_ON_JOB_CLOSE`，目标不能逃出挂起态。timeout 使用
`TerminateJobObject`，只有 `ActiveProcesses=0` 才生成死亡证明；空 `CLAIMED` journal 对应的
确定性 Job 不存在，或存在但被终止并确认清空，才返回 `NEVER_STARTED`。分配、查询或终止有歧义
一律 `UNCONFIRMED`，禁止回退到裸 `Popen`。

- [ ] **步骤 4：实现 delegated cgroup v2 与诊断用 POSIX 路径**

POSIX 与 cgroup 都直接执行绝对路径 bootstrap，并用父侧控制管道实现启动门。bootstrap 必须先用
标准库 `ctypes` 设置 Linux `PR_SET_PDEATHSIG(SIGKILL)`，再复核 `getppid()` 等于传入的父 PID，
以封闭“读取父 PID 到设置死亡信号”之间的竞态；父进程崩溃或启动门关闭时目标绝不 exec。
`PosixAttemptProcessBackend` 在新 session 中固化 PID/PGID/SID/boot-id/start-ticks 身份，返回 handle
时目标仍被 gate 阻塞，`activate` 才写入放行 token。但普通 POSIX 无法证明两次 `/proc` 扫描之间
没有发生未观测的 `fork -> setsid -> reparent` 逃逸，因此它不是严格 containment：只允许用于
DEV/协作式诊断，目标一旦 activate，之后任何 terminate/recover/confirm 路径都永久不得签发
`ConfirmedProcessDeath`。

在 WSL/systemd 上，`CgroupAttemptProcessBackend` 先在 delegated worker cgroup 下创建确定性的
attempt 子组，随后只启动 `cgroup_bootstrap.py`。在写入并回读 `cgroup.procs` 证明自身已经纳管、
且等待父侧放行之前，bootstrap 只能 import Python 标准库；禁止使用 `-m`，也不得导入
`codev_platform` package、executor、物化器或 runner。分配、父 PID 复核或 gate 验证失败以 125
退出，绝不 exec 目标。

仅使用绝对脚本路径还不够：Python 默认会在执行 bootstrap 脚本前初始化 `site`，其中 `.pth`、
`sitecustomize` 或 `usercustomize` 可以提前执行任意导入。POSIX 与 cgroup 父侧都必须固定使用
`python -I -S <bootstrap.py 的绝对路径> ...`；`-I` 隔离用户环境和不可信路径，`-S` 禁止自动
加载 `site`，两个 flag 只能属于 bootstrap 解释器，不能混入目标 argv。真实 marker 故障测试同时
放置会写文件的 `sitecustomize.py` 与 import 型 `.pth`，并证明两个 bootstrap 返回 125 时 marker
均不存在、目标也未执行。

`SystemdCgroupV2` 构造时同时验证 systemd manager 的 `Delegate=yes`、可写 delegation、domain 类型、
可创建/删除直接子组和可打开 `cgroup.kill`；任一条件缺失即抛 `CgroupDelegationError`。当前 Task 5
后端自身已经失败关闭，但生产后端选择器属于 Task 7。生产选择器只允许 Windows Job 或 Linux
delegated cgroup；Linux 就绪检查/委派任一校验失败都必须在 claim 前致命退出，绝不构造
`PosixAttemptProcessBackend`，也不得把 POSIX 当成 cgroup 失败后的回退。

`CgroupAttemptProcessBackend` 通过 `cgroup.kill` 终止进程，并且仅在共享 `Deadline` 内观察到
`cgroup.events` 精确报告 `populated 0` 后才生成 `ConfirmedProcessDeath`。任务 7 的 reindex unit
必须提供 `Delegate=yes`。

诊断用 `PosixAttemptProcessBackend` 仍发送 TERM，在 `grace_sec` 与共享截止点的较早者停止等待，
再发送 KILL；这些动作只负责尽力清理，不构成严格死亡证明。blocked prepare 在放行前被关闭时可以
证明“目标从未执行”，一旦 activate 则死亡状态永久 `UNCONFIRMED` 并强制 quarantine。共享
`process_tree` 以显式上界和结构化失败替换 kill 后的裸 `wait`/`communicate`。

`test_posix_blocked_prepare_can_prove_target_never_activated` 只覆盖放行前的“从未执行”证明；
`test_activated_posix_graceful_exit_remains_unconfirmed` 与 setsid/恢复用例共同固定“activate 后永久
不签 `ConfirmedProcessDeath`”。Task 7 还必须用精确测试固定 Linux 生产选择器在缺少委派时于
claim 前失败，并且不会构造 POSIX 回退。

- [ ] **步骤 5：验证挂钟时间上界并提交**

runner 的业务 timeout 必须总是有限正数：缺失时使用安全默认值，无效、非数值或非正配置也回退
该有限默认值；`run_logged_process` 在 spawn 前拒绝无界预算。timeout 清理只创建一个额外有限
`cleanup_deadline`，kill tree、二次 `proc.wait()` 和日志 reader join 都消费其剩余时间，任何清理
异常不得覆盖原始 timeout。

运行：

    python -m pytest tests/test_reindex_attempt_process_values.py tests/test_reindex_attempt_process_reports.py tests/test_reindex_attempt_process_start.py tests/test_reindex_attempt_process_protocol.py tests/test_reindex_windows_job_lifecycle.py tests/test_reindex_windows_job_recovery.py tests/test_reindex_windows_job_cleanup.py tests/test_reindex_windows_job_native.py tests/test_reindex_windows_job_real.py tests/test_reindex_posix_process_lifecycle.py tests/test_reindex_posix_process_stdio.py tests/test_reindex_posix_process_identity.py tests/test_reindex_posix_process_termination.py tests/test_reindex_posix_process_recovery.py tests/test_reindex_cgroup_readiness.py tests/test_reindex_cgroup_lifecycle.py tests/test_reindex_cgroup_bootstrap.py tests/test_reindex_cgroup_integration.py tests/test_process_tree.py tests/test_reindex_runner_logs.py tests/test_reindex_runner_timeout.py -q

预期：当前操作系统适用的测试通过；Windows 真机用例只在 Windows 10 / Server 2016 及以上运行。
两个 `test_real_cgroup_*` 用例只有在 `CODEV_REINDEX_REAL_CGROUP=1` 且测试进程实际位于
`Delegate=yes` 临时 unit 时才运行；默认跳过不能当成 WSL 实机验证通过。每个 timeout 用例必须
落在传入共享 deadline 与测试抖动余量内，不得用重新起算的 kill timeout 掩盖超时。

当前定向证据：WSL 普通环境运行 POSIX+cgroup 集得到 `88 passed, 2 skipped`；另在临时
`Delegate=yes`、`KillMode=control-group` unit 中设置 `CODEV_REINDEX_REAL_CGROUP=1`，定向运行
`-k real_cgroup` 得到 `2 passed, 48 deselected`。这只证明 Task 5 后端真实故障路径，不代表生产
unit、Task 7 选择器或 Orchestrator 全链已经接线。

提交：

    git add codev_platform/reindex/attempt_process.py codev_platform/reindex/bootstrap_runtime.py codev_platform/reindex/process_stdio.py codev_platform/reindex/windows_job.py codev_platform/reindex/windows_job_native.py codev_platform/reindex/windows_job_win32.py codev_platform/reindex/windows_job_resources.py codev_platform/reindex/windows_process_identity.py codev_platform/reindex/windows_process_evidence.py codev_platform/reindex/posix_process.py codev_platform/reindex/posix_bootstrap.py codev_platform/reindex/posix_process_identity.py codev_platform/reindex/posix_process_state.py codev_platform/reindex/cgroup_process.py codev_platform/reindex/cgroup_process_state.py codev_platform/reindex/cgroup_abort.py codev_platform/reindex/cgroup_spawn.py codev_platform/reindex/cgroup_recovery.py codev_platform/reindex/cgroup_termination.py codev_platform/reindex/cgroup_v2.py codev_platform/reindex/cgroup_bootstrap.py codev_platform/reindex/runner_logs.py codev_platform/reindex/runners.py codev_platform/core/process_tree.py tests/fixtures/reindex_process_fixture.py tests/reindex_attempt_process_support.py tests/test_reindex_attempt_process_values.py tests/test_reindex_attempt_process_reports.py tests/test_reindex_attempt_process_start.py tests/test_reindex_attempt_process_protocol.py tests/reindex_windows_job_support.py tests/test_reindex_windows_job_lifecycle.py tests/test_reindex_windows_job_recovery.py tests/test_reindex_windows_job_cleanup.py tests/test_reindex_windows_job_native.py tests/test_reindex_windows_job_real.py tests/reindex_posix_process_support.py tests/test_reindex_posix_process_lifecycle.py tests/test_reindex_posix_process_stdio.py tests/test_reindex_posix_process_identity.py tests/test_reindex_posix_process_termination.py tests/test_reindex_posix_process_recovery.py tests/reindex_cgroup_process_support.py tests/test_reindex_cgroup_readiness.py tests/test_reindex_cgroup_lifecycle.py tests/test_reindex_cgroup_bootstrap.py tests/test_reindex_cgroup_integration.py tests/test_process_tree.py tests/test_reindex_runner_logs.py tests/test_reindex_runner_timeout.py docs/plans/roadmap-2026-07-11/reindex-attempt-isolation-implementation-plan-2026-07-11.md docs/plans/roadmap-2026-07-11/reindex-attempt-containment-publishing-plan-2026-07-11.md docs/plans/roadmap-2026-07-11/reindex-isolated-execution-design-2026-07-11.md
    git commit -m "feat(reindex): 有界围栏 attempt 进程树"

---

### 任务 6：增加受围栏且幂等的清单发布器

**文件：**
- 新建：`codev_platform/reindex/result_publisher.py`
- 新建：`codev_platform/reindex/attempt_completion.py`：先提供规范 spec/result SHA-256 纯函数；
  任务 7 在同一单一职责模块增加 receipt 模型与 codec
- 新建：`tests/test_reindex_result_publisher.py`
- 修改：`codev_platform/index_manifest.py`：增量增加 attempt 字段
- 修改：`tests/test_index_manifest.py`

**接口：**

    @dataclass(frozen=True, slots=True)
    class PublishReceipt:
        published: bool
        attempt_id: str
        result_digest: str
        note: str


    class ResultPublisher:
        def publish(self, validated: ValidatedAttemptResult) -> PublishReceipt:
            if not isinstance(validated, ValidatedAttemptResult):
                raise TypeError("publisher 只接受 ValidatedAttemptResult")
            result = validated.result
            spec = validated.spec
            result_digest = validated.result_digest
            identity_matches = (
                result.attempt_id == spec.attempt_id
                and result.fence == spec.fence
                and result.project_id == spec.project_id
                and result.kind == spec.kind
                and result.target_commit == spec.target_commit
                and result.runtime_revision == spec.runtime_revision
            )
            if not identity_matches:
                return PublishReceipt(False, result.attempt_id, result_digest, "attempt 身份冲突")
            timing = dict(result.timing)
            candidate = BuildRecord(
                project_id=result.project_id,
                kind=result.kind,
                git_commit=dict(result.input_commits).get("main"),
                target_commit=result.target_commit,
                repo_commits_json=json.dumps(dict(result.input_commits), sort_keys=True),
                input_trees_json=json.dumps(dict(result.input_trees), sort_keys=True),
                attempt_id=result.attempt_id,
                result_digest=result_digest,
                runtime_revision=result.runtime_revision,
                proof_json=result.proof.text,
                log_ref=result.log_ref,
                started_at=timing.get("started_at"),
                finished_at=timing.get("finished_at"),
                status="ok" if result.outcome is AttemptOutcome.SUCCEEDED else "failed",
                note=result.note,
            )
            outcome = publish_build(candidate)
            if outcome is ManifestPublishOutcome.IDEMPOTENT:
                return PublishReceipt(True, result.attempt_id, result_digest, "已幂等发布")
            if outcome is ManifestPublishOutcome.CONFLICT:
                return PublishReceipt(False, result.attempt_id, result_digest, "同 attempt manifest 冲突")
            return PublishReceipt(True, result.attempt_id, result_digest, "已发布")

`ResultPublisher` 导入 `index_manifest`，但绝不导入 queue。它只接受 receipt-aware 的
`ValidatedAttemptResult`。Manifest 通过增量迁移增加 `attempt_id`、`result_digest`、
`runtime_revision`、`input_trees_json`、`proof_json` 和 `log_ref`。`result_digest` 复用
`attempt_completion.attempt_result_digest` 的规范 SHA-256；`index_manifest` 只持久化字符串，
不依赖 receipt 类型。`publish_build` 在单个 SQLite 写事务中读取当前 project/kind 行、比较同
attempt 的 digest 与全部持久字段，再执行显式 insert/update；发布路径禁止调用 legacy
`record_build` 或 `INSERT OR REPLACE`。不同 attempt 只能在外层 desired-revision guard 持有期间
进入该事务。

- [ ] **步骤 1：编写红灯测试**

增加：
- test_publisher_type_boundary_rejects_raw_attempt_result
- test_wrong_fence_target_or_runtime_never_writes_manifest
- test_same_attempt_same_digest_and_fields_publish_is_idempotent
- test_same_attempt_different_digest_fails_closed
- test_same_attempt_same_digest_but_persisted_fields_conflict_fails_closed
- test_manifest_write_failure_returns_unpublished_receipt
- test_manifest_contains_attempt_result_digest_runtime_input_and_proof

- [ ] **步骤 2：运行红灯测试**

运行：`python -m pytest tests/test_reindex_result_publisher.py tests/test_index_manifest.py -q`

预期：缺少 publisher 和 manifest 字段。

- [ ] **步骤 3：实现幂等比较**

在任何写入前构造完整候选记录。已有记录只有在 `attempt_id`、`result_digest` 和全部持久字段逐项
一致时才按成功幂等处理；同 ID 任一字段冲突都返回未发布并失败关闭，禁止覆盖。不同 attempt
只能通过任务 7 的 `QueuePort.begin_publish` 期望版本围栏到达该方法；publisher 不重复实现 queue
所有权逻辑。

- [ ] **步骤 4：验证并提交**

运行：`python -m pytest tests/test_reindex_result_publisher.py tests/test_index_manifest.py -q`

提交：

    git add codev_platform/reindex/attempt_completion.py codev_platform/reindex/result_publisher.py codev_platform/index_manifest.py tests/test_reindex_result_publisher.py tests/test_index_manifest.py
    git commit -m "feat(reindex): 按 attempt 围栏发布索引结果"

---
