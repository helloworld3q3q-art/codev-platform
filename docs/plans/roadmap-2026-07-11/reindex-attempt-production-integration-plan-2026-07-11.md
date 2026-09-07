# Reindex Attempt 生产集成与故障验收实施子计划（任务 7–8）

> **供自动化执行代理使用：** 必需子技能：使用 `superpowers:subagent-driven-development`（推荐）或 `superpowers:executing-plans`，逐项实施本计划。各步骤使用复选框（`- [ ]`）跟踪。
>
> 上级总览与唯一全局契约：[Reindex 尝试隔离实施计划](./reindex-attempt-isolation-implementation-plan-2026-07-11.md)

**目标：** 接入唯一 Orchestrator、可恢复 FINALIZING、依赖/legacy 门禁、健康刷新和生产 systemd，并完成真实故障矩阵。

**架构：** outer observer 与 write-once receipt 提供完成线性化；FINALIZING 按 death→renew→input cleanup→guarded queue action→artifact tombstone→journal clear 收口。生产组合根只接 fail-soft=false queue 和通过 readiness 的 Windows Job/cgroup 后端。

**技术栈：** Python 3.10+、`dataclass`/`Protocol`、严格 JSON、pytest，以及上级总览指定的跨平台进程与耐久能力。

## 执行前置

- 必须先读取上级总览的“全局约束”“固定跨计划契约”“文件映射”；这些内容只在上级维护，本子计划不复制第二份真值。
- 本子计划只覆盖标题所列任务；相邻任务的产出通过上级契约中的精确接口消费。
- 所有沟通、注释和文档使用中文；代码标识符与外部协议字段保持英文。

---

### 任务 7：接入唯一编排器、恢复、健康检查封装和 systemd

**文件：**
- 新建：`codev_platform/reindex/file_durability.py`
- 新建：`codev_platform/reindex/attempt_artifacts.py`
- 新建：`codev_platform/reindex/attempt_finalization.py`
- 新建：`codev_platform/reindex/attempt_validation.py`
- 新建：`codev_platform/reindex/dependency_gate.py`
- 新建：`codev_platform/reindex/executor_observer.py`
- 新建：`codev_platform/reindex/health_refresh.py`
- 新建：`codev_platform/reindex/orchestrator.py`
- 新建：`codev_platform/reindex/worker_launcher.py`
- 新建：`tests/test_reindex_file_durability.py`
- 新建：`tests/test_reindex_attempt_completion.py`
- 新建：`tests/test_reindex_attempt_artifacts.py`
- 新建：`tests/test_reindex_attempt_finalization.py`
- 新建：`tests/test_reindex_dependency_gate.py`
- 新建：`tests/test_reindex_executor_observer.py`
- 新建：`tests/test_reindex_health_refresh.py`
- 新建：`tests/test_reindex_orchestrator.py`
- 新建：`tests/test_reindex_composition_root.py`
- 修改：`codev_platform/reindex/attempt_completion.py`：增加 receipt 模型、严格 codec 与摘要绑定
- 修改：`codev_platform/reindex/attempts.py`：spec/result write-once 与 finalization checkpoint journal 字段
- 修改：`codev_platform/reindex/executor.py`：result 只能 write-once，退出码不再由父侧直接采信
- 修改：`codev_platform/reindex/queue_ports.py`：增加 token-fenced reject、依赖只读视图和 retry 入队尾语义
- 修改：`codev_platform/reindex/queue.py`：兼容重导出新队列契约
- 修改：`codev_platform/reindex/file_queue.py`、`codev_platform/reindex/pg_queue.py`：实现同一队列契约
- 修改：`codev_platform/reindex/file_queue_store.py`、`file_queue_view.py`、`pg_queue_sql.py`：复用既有耐久/只读/SQL 真值源
- 新建：`codev_platform/reindex/file_queue_transitions.py`：File retry/reject 状态转换
- 新建：`codev_platform/reindex/pg_queue_codec.py`、`pg_queue_transitions.py`、`pg_queue_view.py`：PG 严格转换、单事务转换与依赖视图
- 修改：`codev_platform/reindex/worker.py`：只保留兼容 façade
- 修改：`codev_platform/reindex/supervisor.py`：0600 durable replace/clear journal、同文件内 health operation 子记录和脱敏状态
- 修改：`codev_platform/reindex/status.py`
- 修改：`codev_platform/reindex/__init__.py`
- 修改：`codev_platform/ops/reindex_queue.py`：唯一 isolated 组合根
- 修改：`codev_platform/mcp_systemd.py`：`Delegate=yes` 与 `KillMode=control-group`
- 修改：`tests/test_reindex_worker_supervisor.py`
- 修改：`tests/test_reindex_attempt_protocol.py`
- 修改：`tests/test_reindex_executor_strategy.py tests/test_reindex_executor_failures.py tests/test_reindex_executor_cli.py`
- 修改：`tests/test_reindex_result_publisher.py`
- 修改：`tests/test_reindex_queue_contract.py`、`tests/test_reindex_queue.py`、`tests/test_pg_queue.py`
- 新建：`tests/queue_transition_contract.py`、`tests/test_reindex_queue_transitions.py`、
  `tests/test_pg_queue_transitions.py`、`tests/test_pg_queue_view.py`
- 修改：`tests/test_pg_queue_transaction_boundaries.py`
- 修改：`tests/test_reindex_queue_cli.py`
- 修改：`tests/test_reindex_status_core.py tests/test_reindex_status_owner.py`
- 修改：`tests/test_systemd_restart.py`
- 修改：`tests/test_health_reindex_worker.py`
- 修改：`docs/plans/roadmap-2026-07-11/README.md`
- 修改：`docs/plans/roadmap-2026-07-11/reindex-attempt-isolation-implementation-plan-2026-07-11.md`
- 修改：`docs/plans/roadmap-2026-07-11/reindex-isolated-execution-design-2026-07-11.md`
- 本子计划：`docs/plans/roadmap-2026-07-11/reindex-attempt-production-integration-plan-2026-07-11.md`

**接口：**

    def durable_create_once(path: Path, payload: bytes, *, mode: int = 0o600) -> None:
        """目标已存在时失败，绝不覆盖；返回前完成文件和目录耐久同步。"""


    def durable_replace(path: Path, payload: bytes, *, mode: int = 0o600) -> None:
        """只供 journal 等可替换状态使用；返回前旧值或完整新值必有一个耐久。"""


    def durable_unlink(path: Path) -> None:
        """目标不存在时幂等；返回前目标路径的消失已经耐久。"""


    @dataclass(frozen=True, slots=True)
    class AttemptArtifactPaths:
        root: Path
        spec: Path
        result: Path
        receipt: Path
        bootstrap_log: Path


    class AttemptArtifactStore(Protocol):
        def expected(self, attempt_id: str) -> AttemptArtifactPaths:
            raise NotImplementedError("artifact store 方法")
        def initialize(self, spec: AttemptSpec) -> AttemptArtifactPaths:
            raise NotImplementedError("artifact store 方法")
        def verify_journal(self, entry: AttemptJournalEntry) -> AttemptArtifactPaths:
            raise NotImplementedError("artifact store 方法")
        def read_spec(self, paths: AttemptArtifactPaths) -> AttemptSpec:
            raise NotImplementedError("artifact store 方法")
        def read_result(self, paths: AttemptArtifactPaths) -> AttemptResult | None:
            raise NotImplementedError("artifact store 方法")
        def read_receipt(
            self, paths: AttemptArtifactPaths,
        ) -> AttemptCompletionReceipt | None:
            raise NotImplementedError("artifact store 方法")
        def write_result_once(
            self, paths: AttemptArtifactPaths, result: AttemptResult,
        ) -> None:
            raise NotImplementedError("artifact store 方法")
        def cleanup(self, paths: AttemptArtifactPaths) -> None:
            raise NotImplementedError("artifact store 方法")


    class FinalizationAction(str, Enum):
        GUARDED_ACK = "guarded_ack"
        RETRY = "retry"


    class FinalizationEvidence(str, Enum):
        COMPLETION_RECEIPT = "completion_receipt"
        INCOMPLETE_ARTIFACT = "incomplete_artifact"
        DEPENDENCY_BLOCK = "dependency_block"


    @dataclass(frozen=True, slots=True)
    class AttemptFinalizationCheckpoint:
        schema_version: int
        attempt_id: str
        fence: str
        target_commit: str
        action: FinalizationAction
        evidence: FinalizationEvidence
        result_digest: str | None


    class DependencyDisposition(str, Enum):
        RUN = "run"
        WAIT = "wait"
        BLOCK = "block"


    @dataclass(frozen=True, slots=True)
    class DependencyDecision:
        disposition: DependencyDisposition
        dependency_kind: str | None
        target_commit: str
        proof: CanonicalJsonObject
        reason: str


    class DependencyQueueViewPort(Protocol):
        def dependency_state(
            self, *, project_id: str, kind: str, target_commit: str,
            timeout_sec: float,
        ) -> str:
            raise NotImplementedError("依赖队列只读视图方法")


    class DependencyGate(Protocol):
        def evaluate(self, claim: ClaimedJob) -> DependencyDecision:
            raise NotImplementedError("依赖门禁方法")


    @dataclass(frozen=True, slots=True)
    class OrchestratorSettings:
        poll_sec: float
        queue_op_timeout_sec: float
        heartbeat_sec: float
        renew_sec: float
        lease_ttl_sec: float
        kill_grace_sec: float
        kill_timeout_sec: float
        cleanup_timeout_sec: float
        health_timeout_sec: float
        max_concurrency: int = 1


    class AttemptJournalPort(Protocol):
        def load(self) -> AttemptJournalEntry | None:
            raise NotImplementedError("journal adapter method")
        def save(self, entry: AttemptJournalEntry) -> None:
            raise NotImplementedError("journal adapter method")
        def clear(self, *, attempt_id: str, fence: str) -> None:
            raise NotImplementedError("journal adapter method")


    class Clock(Protocol):
        def monotonic(self) -> float:
            raise NotImplementedError("clock method")
        def time(self) -> float:
            raise NotImplementedError("clock method")
        def wait(self, timeout_sec: float) -> None:
            raise NotImplementedError("clock method")


    @dataclass(frozen=True, slots=True)
    class HealthOperationEntry:
        schema_version: int
        operation_id: str
        project_id: str
        handle: ExecutionHandle
        started_at: float
        timeout_sec: float


    @dataclass(frozen=True, slots=True)
    class HealthRefreshReport:
        attempted_projects: tuple[str, ...]
        succeeded_projects: tuple[str, ...]
        failed_projects: tuple[str, ...]
        containment_confirmed_dead: bool


    class HealthOperationJournalPort(Protocol):
        def load_health(self) -> HealthOperationEntry | None:
            raise NotImplementedError("health operation journal 读取方法")
        def save_health(self, entry: HealthOperationEntry) -> None:
            raise NotImplementedError("health operation journal 保存方法")
        def clear_health(self, *, operation_id: str) -> None:
            raise NotImplementedError("health operation journal 清理方法")


    class HealthRefreshPort(Protocol):
        def request(self, project_id: str) -> None:
            raise NotImplementedError("health refresh 请求方法")
        def flush(self, deadline: Deadline) -> HealthRefreshReport:
            raise NotImplementedError("health refresh 执行方法")
        def recover(self, deadline: Deadline) -> None:
            raise NotImplementedError("health refresh 恢复方法")

`HealthOperationEntry` 使用严格嵌套 codec，只保存无秘密的完整 handle；不得保存 monotonic 绝对值跨
重启比较。`recover` 为本次清理创建新的有限 `Deadline`，调用 `recover_handle`，ACTIVE 就 terminate，
CONFIRMED_DEAD 也不得猜测遗失的 exit code：设置 `health_failed=true`、重新 request 该 project 后
清记录；UNCONFIRMED 则失败停止。正常启动固定为 prepare blocked → 0600 durable
replace health operation → activate；记录保存失败只能 terminate，绝不 activate。

`ContainedHealthRefresher` 只依赖窄 `HealthOperationJournalPort`，不得 import 或直接改
`supervisor.py`。`SupervisorAttemptJournal` 是 supervisor 状态文件唯一写适配器，同时实现 attempt
与 health 两个窄端口，并在一次 0600 durable replace 中保留未修改字段。health 只在 attempt journal
已 clear 后运行；启动恢复 health 完成前不创建 attempt，因而两个子记录不会并发覆盖。


`file_durability.py` 的临时文件使用显式独占创建和 0600（Windows 使用仅当前服务身份可读写的
等价 ACL），写满后执行文件 `fsync`。POSIX create-once 使用不覆盖目标的原子安装并同步父目录；
journal replace 使用同目录替换并同步父目录。Windows create-once/replace 使用
`MoveFileExW` 的 `MOVEFILE_WRITE_THROUGH`，replace 额外带 `MOVEFILE_REPLACE_EXISTING`；
durable unlink 先 write-through 改名为同目录 tombstone，再删除 tombstone，恢复时只认固定目标名。
任何 umask 都不得把 claim token 所在 journal 放宽为组或全局可读。

`AttemptArtifactStore.expected` 只按小写 `sha256(attempt_id UTF-8)` 派生固定路径
`<artifact_root>/<64-hex-digest>/`；目录权限固定为 0700（Windows 使用仅服务身份可访问的等价
ACL），固定文件名为 `spec.json`、`result.json`、`completion.json` 和 `bootstrap.log`。
spec/result/receipt 使用 0600 create-once，bootstrap log 也必须以 0600、`O_NOFOLLOW`/等价句柄检查
创建为普通文件，任何已存在的非本 attempt 文件、额外目录项或宽权限都失败关闭。journal 中已有的
`spec_path`/`result_path` 只是冗余审计字段；`verify_journal` 在打开任何 artifact 前，必须从
`attempt_id` 重新派生路径并要求规范化后的字段精确一致，同时拒绝 symlink、junction、reparse
point 与越界路径。Orchestrator 不直接读取路径，也不自行拼接文件名。

`AttemptArtifactStore.cleanup` 只接受上述固定路径。queue 动作已有耐久见证后，它先以同目录
write-through rename 把整个根改名为确定性 `<64-hex-digest>.cleanup`，再只删除固定白名单成员并
同步父目录；恢复时 root、tombstone 或两者均不存在分别表示“未开始”“清理中”“已完成”，两者同时
存在或出现额外成员一律失败关闭。输入清理必须在该 rename 之前完成，journal 必须在 tombstone
删除耐久之后最后清除。

`write_result_once` 只给父侧 `DEPENDENCY_BLOCK` 路径保存已经由
`validate_dependency_block` 构造的规范失败结果；进程路径仍只能由 executor 写 result。组合/AST
测试必须保证 Orchestrator 的 RUN、timeout、raw-result 和恢复分支都不能调用该方法，避免父进程
伪造 executor 结果。

`executor_observer` 接收固定 paths 和 inner executor argv，在同一 containment 内以无 PIPE 方式
启动 inner executor，独立 `wait()` 得到真实 `process_rc`，严格读取 spec/result，计算规范摘要，
最后用 `durable_create_once` 写 receipt。receipt 文件与目录同步成功后 observer 才返回 0；缺少或
损坏 result 时不写 receipt 并返回固定协议错误码。outer observer 的退出码只用于进程监控，绝不
作为 inner `process_rc`。

`AttemptFinalizationCheckpoint` 是 journal 内的恢复意图，不是第四份结果真值。它必须与 journal 的
attempt/fence/target 精确一致；`GUARDED_ACK` 必须带规范 `result_digest`，`INCOMPLETE_ARTIFACT` 只
允许 `RETRY` 且 digest 为空，`DEPENDENCY_BLOCK` 只允许进程字段全空的 `GUARDED_ACK`。进程路径
进入或恢复 `FINALIZING` 时仍必须从 process backend 取得与精确 handle 匹配的死亡证明；checkpoint
绝不能替代死亡证明。所有 input cleanup 必须按 attempt_id 幂等，且在每次执行前为
`cleanup_timeout_sec + queue_op_timeout_sec + 2 * poll_sec` 续租。

任务 7 给 `AttemptJournalEntry` 增加唯一可选字段
`finalization: CanonicalJsonObject | None`；编码内容只能由 `attempt_finalization.py` 的严格 codec
产生。`CLAIMED/EXECUTING/TERMINATING` 必须为空，`FINALIZING` 必须非空，`QUARANTINED` 保留进入
隔离前的原值但自动路径不得解释或推进它。未知键、部分字段、非法 action/evidence 组合和与外层
journal 身份不一致都按损坏处理，不允许用默认值修补。

`ManifestDependencyGate` 只读取 manifest 与 `DependencyQueueViewPort`。无依赖 kind 直接 `RUN`；
`ingest`、`code_vec` 只有在相同 project、相同完整 target commit 的 codegraph manifest 明确成功时
才 `RUN`。依赖缺失或相同目标仍有 codegraph pending/active 时返回 `WAIT`；Orchestrator 不创建
spec/journal/process，调用 token-fenced `queue.retry`，而 File/PG 必须在同一事务把排序序号和
重入时间更新到当前队尾，避免 dependent 抢占队首。依赖 manifest 明确失败且没有更新的
codegraph pending/active 时返回 `BLOCK`。

`BLOCK` 不伪造 receipt，但必须进入可恢复的无进程最终化事务。Orchestrator 调用
`AttemptSpecFactory` 一次，由
`validate_dependency_block(spec, decision, claim, validated_at)` 构造规范 `FAILED AttemptResult` 和
`ValidatedAttemptResult(evidence=DEPENDENCY_BLOCK, process_rc=None)`；其 proof 绑定 dependency kind、
target、失败 manifest 的 attempt/digest 和 queue view。父侧先写空 `CLAIMED` journal，再以
write-once spec/result 初始化 artifact，最后写进程字段全空、证据为 `DEPENDENCY_BLOCK` 的
`FINALIZING` checkpoint；只有随后才在 `begin_publish` guard 内由同一 `ResultPublisher` 记录依赖
失败并 ack。该边界只接受 gate 产生的 `BLOCK` 决策，不能用于 runner、result 缺失或任何已经启动
进程的路径。

`WorkerQueuePort.reject(claim, reason, timeout_sec)` 只用于进程启动前已经确定不可执行的 malformed
claim，例如 legacy `JobMeta.target_commit=None`、短 SHA 或非法 revision。它必须精确校验 token，
原子写入失败审计并退休该 active 项，不写成功 manifest，也不需要进程死亡证明；一旦 journal 已有
完整 handle 就禁止调用 reject。

`AttemptOrchestrator` 构造参数固定为 `queue: WorkerQueuePort`、
`process_backend: AttemptProcessBackend`、`artifacts: AttemptArtifactStore`、
`dependency_gate: DependencyGate`、`publisher: ResultPublisher`、`journal: AttemptJournalPort`、
`cleanup_router: AttemptCleanupRouter`、`spec_factory: AttemptSpecFactory`、
`observer_argv: Callable[[AttemptArtifactPaths], Sequence[str]]`、
`health_refresh: HealthRefreshPort`、`clock: Clock` 和
`settings: OrchestratorSettings`。Orchestrator 不接收第二个 selector、factory、artifact store、
cleanup adapter 或具体后端配置。

精确的公共声明如下：
- def recover_incomplete(self) -> None
- def run(self, claim: ClaimedJob, spec: AttemptSpec) -> AttemptResult
- def drain_once(self) -> int

Orchestrator 只使用以下五个 journal 持久状态，不增加 GoF State 类，也不把 executor 内部 phase
复制成父侧状态真值：

| journal 状态 | 进程字段 | 含义与允许转换 |
|---|---|---|
| `CLAIMED` | 全空 | claim 已有耐久锚点；可写 spec、prepare，或在确定未启动后 retry |
| `EXECUTING` | 全有 | handle 已在 activate 前耐久；目标可能尚未放行、正在运行或已退出，恢复一律保守视为可能运行 |
| `TERMINATING` | 全有 | 正在同一 deadline 内终止；只能进入 `FINALIZING` 或 `QUARANTINED` |
| `FINALIZING` | 进程路径全有；仅依赖 BLOCK 全空 | checkpoint 已耐久；进程路径每次恢复仍须重新取得匹配死亡证明，依赖 BLOCK 证明从未 prepare；不得再启动 executor |
| `QUARANTINED` | 保留原形 | 死亡或身份无法确认；自动恢复不得清除、retry、cleanup 或继续 claim |

正常进程路径只有 `CLAIMED -> EXECUTING -> FINALIZING -> durable clear`；依赖 BLOCK 是
`CLAIMED -> FINALIZING -> durable clear`；timeout、lease 丢失和
shutdown 才经过 `TERMINATING`。业务 `SUCCEEDED/FAILED/RETRYABLE/TIMED_OUT/SUPERSEDED` 只属于
result 与 queue 决策，不再复制成 journal 终态。`EXECUTING` 在 activate 前落盘，因此 crash
发生在 gate 放行后、任何后续状态保存前也不会产生“可当作从未启动”的错误恢复结论。

- [ ] **步骤 1：编写耐久 artifact、completion observer 与确定性事件顺序红灯测试**

在 `tests/test_reindex_file_durability.py`、`tests/test_reindex_attempt_completion.py`、
`tests/test_reindex_attempt_artifacts.py`、`tests/test_reindex_attempt_finalization.py` 和
`tests/test_reindex_executor_observer.py` 增加：

- test_create_once_uses_0600_even_with_umask_022_and_never_overwrites
- test_no_legacy_write_json_atomic_or_direct_attempt_replace_remains
- test_durable_replace_failure_keeps_complete_old_or_new_journal
- test_windows_replace_and_create_once_request_write_through
- test_durable_unlink_removes_fixed_name_before_tombstone_cleanup
- test_completion_receipt_binds_spec_result_digest_and_observed_process_rc
- test_result_rc_cannot_substitute_for_missing_completion_receipt
- test_result_rc_none_cannot_match_completion_receipt
- test_artifact_paths_are_exact_lowercase_hash_directory_with_0700_mode
- test_artifact_files_and_bootstrap_log_are_0600_regular_and_never_follow_links
- test_artifact_store_rejects_journal_path_tampering_symlink_and_reparse_point
- test_artifact_cleanup_uses_write_through_directory_tombstone_and_rejects_extra_entries
- test_artifact_cleanup_is_idempotent_and_journal_is_always_cleared_last
- test_finalization_checkpoint_rejects_action_evidence_digest_or_process_shape_conflicts
- test_observer_writes_receipt_only_after_inner_exit_and_result_validation
- test_observer_missing_or_partial_result_never_writes_receipt
- test_observer_records_real_inner_rc_even_when_result_rc_disagrees
- test_observer_never_uses_stdout_or_stderr_pipe

在 `tests/test_reindex_dependency_gate.py` 与 File/PG queue contract 中增加：

- test_ingest_and_code_vec_run_only_after_same_target_codegraph_success
- test_missing_or_active_same_target_dependency_waits_and_requeues_to_tail
- test_failed_dependency_with_new_codegraph_work_waits_instead_of_blocking
- test_failed_dependency_without_update_returns_stable_block_proof
- test_dependency_block_builds_parent_validated_failure_without_completion_receipt
- test_dependency_block_persists_empty_process_finalizing_journal_before_publish
- test_parent_result_writer_is_reachable_only_from_dependency_block_path
- test_malformed_legacy_target_is_rejected_without_spec_journal_or_process
- test_retry_tail_order_is_identical_for_file_and_pg
- test_reject_is_token_fenced_and_never_writes_success_manifest

在 `tests/test_reindex_composition_root.py` 中用 AST 扫描生产包，并维护三套互不混淆的白名单：

1. 父侧 worker 专属对象只允许在 `codev_platform/ops/reindex_queue.py` 构造：
   `ConfiguredInputSelector`、`AttemptSpecFactory`、`WindowsJobAttemptProcessBackend`、
   `CgroupAttemptProcessBackend`、`ResultPublisher`、`FilesystemAttemptArtifactStore`、
   `SupervisorAttemptJournal`、`ConfiguredAttemptCleanup`、`ManifestDependencyGate`、
   `ContainedHealthRefresher`、`AttemptCleanupRouter` 和 `AttemptOrchestrator`。
2. `ConfiguredAttemptInputStrategy` 与冻结 strategy 映射只允许在
   `codev_platform/reindex/executor_bootstrap.py` 构造。
3. `FileSpoolQueue`、`PgJobQueue` 只允许由共享
   `codev_platform/reindex/__init__.py:open_default_queue()` 工厂构造。

`PosixAttemptProcessBackend` 在生产包内不得有构造调用；类定义、导入和类型注解不算调用。增加：

- test_three_composition_allowlists_have_no_out_of_root_constructors
- test_isolated_worker_opens_default_queue_with_fail_soft_false
- test_linux_production_selector_fails_before_recover_or_claim_without_delegate
- test_windows_production_selector_fails_before_recover_or_claim_without_atomic_job_capability
- test_executor_imports_no_queue_supervisor_manifest_or_health_state

在 `tests/test_reindex_orchestrator.py` 增加：

- test_orchestrator_calls_spec_factory_once_per_claim
- test_dependency_wait_requeues_before_spec_factory_journal_and_process
- test_dependency_block_publishes_failure_inside_guard_then_acks_without_process
- test_dependency_block_never_fabricates_completion_receipt
- test_malformed_target_uses_reject_before_spec_factory_and_journal
- test_timeout_renews_for_kill_window_then_retries_after_confirmed_death
- test_lease_loss_kills_before_any_publish
- test_unconfirmed_kill_persists_quarantine_and_stops_claiming
- test_dirty_desired_revision_marks_old_attempt_superseded_without_publish
- test_empty_journal_precedes_spec_write_and_artifact_initialization
- test_activate_never_precedes_handle_journal_durable_replace
- test_handle_journal_save_failure_terminates_without_activate
- test_orchestrator_never_builds_or_trusts_artifact_paths_itself
- test_outer_observer_rc_is_never_used_as_inner_process_rc
- test_live_and_recovery_paths_share_receipt_aware_validation
- test_raw_result_without_receipt_retries_only_after_confirmed_death
- test_valid_receipt_enters_finalizing_without_executor_rerun
- test_finalizing_renews_then_input_cleanup_precedes_guarded_ack
- test_finalizing_renews_then_input_cleanup_precedes_retry
- test_input_cleanup_never_holds_publish_guard_or_queue_transaction
- test_desired_change_during_cleanup_is_superseded_inside_single_guard
- test_crash_after_input_cleanup_before_queue_action_replays_idempotently
- test_publish_ack_crash_recovers_from_exact_manifest_witness
- test_retry_crash_recovers_from_same_key_pending_witness
- test_supersede_ack_crash_recovers_from_new_desired_witness
- test_lost_token_with_replacement_work_finishes_old_attempt_scoped_cleanup
- test_missing_claim_manifest_and_queue_witness_fails_closed
- test_artifact_cleanup_precedes_journal_clear_and_never_precedes_queue_action
- test_artifact_cleanup_failure_keeps_finalizing_journal_and_blocks_new_claim
- test_dependency_block_manifest_crash_replays_without_process_or_receipt
- test_crash_after_claim_before_journal_recovers_owned_without_spawn
- test_previous_owner_is_read_before_new_worker_state_overwrite
- test_health_refresh_hang_uses_own_durable_handle_without_job_lease
- test_health_handle_save_failure_terminates_and_never_activates
- test_health_recovery_uses_recover_handle_before_any_claim
- test_health_recovery_without_exit_code_marks_failed_and_requeues_project
- test_supervisor_is_only_state_writer_and_health_update_preserves_attempt_fields
- test_health_refresher_depends_only_on_health_journal_port_not_supervisor
- test_health_refresh_requests_are_deduplicated_per_project_before_flush
- test_health_failure_never_changes_published_or_acked_job_outcome
- test_health_refresh_unconfirmed_death_stops_new_claims
- test_cleanup_runs_after_confirmed_death_and_before_queue_release
- test_quarantine_never_calls_cleanup
- test_break_lease_refuses_quarantined_key
- test_clear_quarantine_cli_requires_backend_death_proof
- test_max_concurrency_is_one

超时测试必须断言结果为 `TIMED_OUT`，并精确断言事件顺序：先在 kill 前为终止窗口续租，再在 retry
前确认死亡。cleanup 用例必须断言清理窗口续租发生在 input cleanup 前，且 cleanup 不持有 publish
guard。

- [ ] **步骤 2：运行红灯测试**

运行：

    python -m pytest tests/test_reindex_file_durability.py tests/test_reindex_attempt_completion.py tests/test_reindex_attempt_artifacts.py tests/test_reindex_attempt_finalization.py tests/test_reindex_executor_observer.py tests/test_reindex_dependency_gate.py tests/test_reindex_health_refresh.py tests/test_reindex_queue_contract.py tests/test_reindex_queue_transitions.py tests/test_pg_queue_transitions.py tests/test_pg_queue_view.py tests/test_reindex_orchestrator.py tests/test_reindex_composition_root.py -q

预期：缺少耐久叶子、artifact store、finalization checkpoint、completion observer、dependency
gate、有界 health refresher、queue additive 契约、Orchestrator 和唯一组合根接线。

- [ ] **步骤 3：实现耐久叶子、artifact、observer 与门禁**

先实现 `file_durability.py`，再由 `attempts.py` 的 spec/result writer、
`attempt_completion.py` 的 receipt writer、`supervisor.py` 的 journal writer 复用；任何模块不得
复制临时文件、`fsync` 或 Windows write-through 分支。spec/result/receipt 只能调用
`durable_create_once`，journal save 只能调用 `durable_replace`，journal clear 只能在 artifact
tombstone 删除耐久后调用 `durable_unlink`。

随后实现 `FilesystemAttemptArtifactStore`、`AttemptFinalizationCheckpoint` 严格 codec 和
`executor_observer`。artifact 初始化必须发生在空 `CLAIMED` journal 已耐久之后、`prepare` 之前；
observer 是 process backend 实际启动和监控的 root，inner executor 及其后代由同一 Job/cgroup
纳管。observer 只有在 inner 已退出、result 严格解码且 receipt 已耐久时才返回成功；它不验证
claim、不发布 manifest，也不读取 queue。

最后实现 `ManifestDependencyGate`、queue 依赖只读视图、token-fenced `reject` 和 retry-to-tail。
dependency gate 只能读取 manifest 与 queue view；它不创建 spec、不写 manifest，也不持有 queue
发布事务。父侧 `validate_dependency_block` 是唯一把 BLOCK 转成可发布失败结果的边界。

运行：

    python -m pytest tests/test_reindex_file_durability.py tests/test_reindex_attempt_completion.py tests/test_reindex_attempt_artifacts.py tests/test_reindex_attempt_finalization.py tests/test_reindex_executor_observer.py tests/test_reindex_dependency_gate.py tests/test_reindex_health_refresh.py tests/test_reindex_queue_contract.py tests/test_reindex_queue_transitions.py tests/test_pg_queue_transitions.py tests/test_pg_queue_view.py tests/test_pg_queue_transaction_boundaries.py tests/test_reindex_queue.py tests/test_pg_queue.py tests/test_reindex_attempt_protocol.py tests/test_reindex_executor_strategy.py tests/test_reindex_executor_failures.py tests/test_reindex_executor_cli.py -q

预期：耐久、路径、摘要、observer、依赖门禁、health handle 和 File/PG 公平重入契约通过。
当前队列切片证据：上述 queue transition/view/transaction 契约得到 `174 passed, 25 skipped`；该数字
只记录本轮基线，最终仍以零失败、适用用例不跳过和收集数不下降为门禁。

- [ ] **步骤 4：实现单一控制循环**

worker 启动时，唯一组合根必须先获取 run lock，以 `open_default_queue(fail_soft=False)` 打开真实
queue，调用一次 `core.runtime_identity.runtime_identity`，选择生产 process backend，并完成 Windows
能力或 Linux delegation/cgroup.kill readiness。任一环节失败都必须在 `recover_owned` 和新 claim
之前致命退出，不允许空队列、回退 revision 或 POSIX 后端。缓存 identity 只交给唯一
`AttemptSpecFactory`。

`drain_once` 取得 claim 后先验证完整 target。legacy `target_commit=None`、短 SHA 或非法 revision
在尚无 journal/process 时调用 `queue.reject`。合法 claim 再进入 `DependencyGate`：WAIT 不创建
spec/journal/process，直接 retry-to-tail；BLOCK 创建一次 spec 与规范父侧失败 result，进入无进程
artifact/journal 最终化；只有 RUN 进入进程事务。

RUN 的启动顺序固定为：

    claim + target/dependency gate=RUN
      -> spec_factory.create(claim)
      -> artifacts.expected(attempt_id)
      -> 保存进程字段全空的 CLAIMED journal 并 durable replace
      -> artifacts.initialize(spec)  # 0700 目录 + 0600 write-once spec
      -> process_backend.prepare(observer_argv(paths), bootstrap_log, deadline)
      -> 保存带精确 handle 的 EXECUTING journal 并 durable replace
      -> process_backend.activate(handle, deadline)

空 journal 保存失败不得创建 artifact/prepare。`prepare` 后 handle journal 任一步失败，只能在同一
Deadline 内 terminate，绝不 activate；死亡未确认就 quarantine 并停止 claim。

BLOCK 的顺序固定为：

    claim + target 合法 + dependency gate=BLOCK
      -> spec_factory.create(claim) + validate_dependency_block(...)
      -> 保存进程字段全空的 CLAIMED journal
      -> artifacts.initialize(spec) + write_result_once(result)
      -> 保存进程字段全空、GUARDED_ACK/DEPENDENCY_BLOCK 的 FINALIZING checkpoint
      -> begin_publish -> publisher -> ack

checkpoint 前崩溃只能忽略不完整 artifact、retry-to-tail；checkpoint 后必须重放同一 digest，不能
重新生成 attempt ID。`write_result_once` 的父侧调用只允许该 BLOCK 分支。

控制循环使用单调时间，每 tick 轮询 outer observer、续租、写 heartbeat/phase，并最多等待
`poll_sec`；outer rc 不能传给验证边界。调用 terminate 前再续租，TTL 至少覆盖
`kill_grace_sec + kill_timeout_sec + 2 * poll_sec`。不得创建 heartbeat/lease 线程。

outer 退出后先确认整个 containment 死亡，再由 artifact store 严格读 spec/result/receipt。raw
result、receipt 摘要/身份/rc 不匹配都不能构造 `ValidatedAttemptResult`；死亡已确认则 RETRY，未
确认则 quarantine。合法结果处理为：SUCCEEDED/FAILED 进入 GUARDED_ACK；RETRYABLE/TIMED_OUT
进入 RETRY；GUARDED_ACK 在 cleanup 后只进入一次 begin_publish，并由 guard 内当时 desired 原子
决定 publisher+ack 或 supersede/ack。

进程最终化顺序固定为：匹配死亡证明 → 保存 FINALIZING checkpoint → 为
`cleanup_timeout_sec + queue_op_timeout_sec + 2 * poll_sec` 续租 →
`AttemptCleanupRouter.release` → GUARDED_ACK 或 RETRY queue 动作 → artifact 根 write-through
tombstone cleanup → journal 最后 durable clear。Dependency BLOCK 的 input cleanup 是不调用 router
的真实 no-op。publisher 只消费冻结结果，不读 workspace；cleanup 期间禁止持有 publish guard。

旧 token 仍 active 就重放 checkpoint。token 已失时，只接受精确 manifest 或同 key replacement
pending/active 见证；不得再写 queue/manifest。artifact root 尚在仍按旧 attempt_id 幂等补做 input
cleanup，再清 artifact/journal；root 已是 tombstone/不存在才证明 cleanup 已越过。没有任何见证就
失败关闭。manifest 临时失败保留 active claim 与 FINALIZING；同 attempt 冲突 quarantine。

死亡未确认时同时持久化 journal 与 queue quarantine 并停止后续 claim；queue 不可用导致 quarantine
无法持久化时 worker 致命退出，由 systemd control-group 收口。

- [ ] **步骤 5：实现 claim 前的崩溃恢复**

`supervisor.py` 提供唯一 `SupervisorAttemptJournal`。启动顺序固定为：获取 run lock；
`open_default_queue(fail_soft=False)`；runtime/process readiness；在覆盖 owner 前读取上一代 owner、
attempt journal 与 health operation；恢复 health handle；校验 artifact 派生路径；恢复/确认进程树；
对精确旧 owner 调用 `recover_owned`；对账孤立 claim；按 checkpoint 校验 completion/manifest/queue
见证；先幂等 input cleanup，再完成 queue 动作、artifact cleanup、journal clear；然后才新 claim。
`AttemptJournalPort` 不读取 artifact，`AttemptArtifactStore` 不访问 queue。

没有 journal、但 `recover_owned` 返回旧 owner claim 时，表示崩溃发生在 claim 与第一次 journal
durable replace 之间；此时进程绝不可能已启动，可 retry 或对 malformed legacy claim 调用 reject，
但不得凭空构造 attempt/result。存在 journal 时，任何 `spec_path`/`result_path` 与派生固定路径不一致
都必须在读取文件前失败关闭。

恢复必须解释两种 `CLAIMED` 形态：空进程字段表示崩溃发生在 `prepare` 前或 handle 落盘前，后端需按
确定性 Job/cgroup 名称清理可能存在的 blocked containment，只有安全不存在或确认清空时才返回
`NEVER_STARTED`；完整进程字段则恢复精确 handle，并只接受 `ACTIVE`、带匹配死亡证据的
`CONFIRMED_DEAD` 或失败关闭的 `UNCONFIRMED`。部分进程字段、身份复用和原生查询歧义一律
`UNCONFIRMED`。事件顺序测试必须精确约束上文五个事件，不能继续用含糊的
“journal-fsync、process-start”二事件表述。结果写入后、manifest 写入后及 ack 前的崩溃点通过
`attempt_id + result_digest` 保持幂等。

进程恢复表固定为：

| journal 形态 | 后端结论 | 动作 |
|---|---|---|
| 无 journal、旧 owner claim | 不调用后端 | malformed 则 reject；否则 retry-to-tail；不 spawn |
| `CLAIMED`、进程字段全空 | `NEVER_STARTED` | 不调用 input cleanup；先 retry-to-tail，再清理可能存在的 artifact，最后清 journal |
| `CLAIMED`、进程字段全空 | `UNCONFIRMED` 或其他非法组合 | 持久 quarantine，停止 claim |
| `EXECUTING/TERMINATING/FINALIZING`、字段全有 | `ACTIVE` | 续租终止窗口，terminate；有严格 proof 才进入或重入最终化 |
| 同上 | `CONFIRMED_DEAD` | 校验证明绑定精确 handle，进入或重入 `FINALIZING` |
| 同上 | `UNCONFIRMED`、`NEVER_STARTED` 或非法组合 | 持久 quarantine，停止 claim |
| `FINALIZING`、字段全空、证据为 `DEPENDENCY_BLOCK` | 不调用后端 | 重放同一父侧 result 的 publish/ack；绝不 spawn、绝不要求 receipt |
| `FINALIZING`、字段全空、其他证据 | 不调用后端 | journal 损坏，失败关闭并停止 claim |
| `QUARANTINED` | 任意 | 自动恢复不得 clear/retry/cleanup；只允许管理面严格确认 |

取得死亡证明后的 artifact/queue 恢复表固定为：

| 持久事实 | 恢复动作 |
|---|---|
| 无 result、或只有 raw result 没有 receipt | 持久 `INCOMPLETE_ARTIFACT/RETRY` checkpoint；先续租、幂等 input cleanup，再 retry；最后清 artifact/journal |
| receipt 缺字段、摘要/身份/rc 不匹配 | 不信任 result；按同一 RETRY 顺序处理；同 ID manifest 冲突则 quarantine |
| receipt 与 result 合法，旧 claim 仍 active | 共用 `validate_attempt_result`；先续租和 input cleanup，再按 outcome publish/ack、supersede/ack 或 retry |
| manifest 的 attempt/digest/全部字段精确一致，旧 claim 仍 active | publisher 幂等成功，guard 内 ack；不重跑 executor、不重建 workspace |
| manifest 精确一致，旧 claim 已消失 | GUARDED_ACK 发布见证；root 尚在则幂等复核旧 input cleanup，再清 artifact/journal |
| 旧 claim 已消失，且同 key 有 pending/active | 旧 authority 已结束或被取代；不得再写 queue/manifest；root 尚在则按旧 attempt_id 补 cleanup，再清 artifact/journal |
| artifact root 已变为 cleanup tombstone 或已不存在，且 queue/manifest 见证成立 | input cleanup 与 queue 动作已经越过固定顺序；幂等完成 tombstone 删除并清 journal，不再要求 spec |
| manifest 同 attempt 但 digest 或任一字段冲突 | 失败关闭并 quarantine，绝不覆盖 |
| queue 无 active/pending、manifest 也不能证明终态 | 状态丢失，失败关闭并停止 claim，不猜测 ack/retry |

Windows 恢复预期 `KILL_ON_JOB_CLOSE` 已终止进程树，并验证进程 identity。WSL 恢复使用记录的
attempt cgroup 和 `cgroup.events` 的 populated 状态。诊断用 POSIX 可尽力终止已记录 PGID，但
activate 后始终无法证明未观测逃逸已经死亡，只能保持 `UNCONFIRMED`。`QUARANTINED` 项一直保留，
直到管理面取得严格后端产生的匹配 `ConfirmedProcessDeath`；自动恢复永不触发 cleanup。

- [ ] **步骤 6：移除虚假存活接线并 containment health**

`ops/reindex_queue.py` 的 isolated 路径不得进入 `supervisor.heartbeat_thread`。`ReindexWorker` 把
drain/run 委派给 `AttemptOrchestrator`，自身不得包含 runner、Git、publisher、lease renewer 或
heartbeat 线程。attempt 的 artifact/journal 全部收口后，Orchestrator 只调用
`HealthRefreshPort.request(project_id)`；worker 到达本轮 idle 时对 project_id 去重后调用一次
`flush`，不为每个 kind 重复刷新。启动时还会请求全部受管 project，补偿“journal 已清、request 前
崩溃”的非关键窗口。

`ContainedHealthRefresher` 是窄适配器，不是第二个编排器：它不访问 queue、manifest、attempt
artifact，不续 job lease，也不创建线程。它复用生产 `AttemptProcessBackend` 时必须遵守同一两阶段
门禁：`prepare` 后先把独立 health handle 以 0600 durable replace 写入现有 supervisor 状态文件的
`health_operation` 子记录，再 `activate`；启动时在任何新 claim 前恢复/终止该子记录。命令退出非零
或 timeout 且 containment 已确认死亡时只设置 `health_failed=true`，不得回滚已发布/已 ack 的 job；
只有刷新成功才能清除该位。health containment 死亡无法确认时 worker 必须停止新 claim，让
systemd control-group 收口，不能把 telemetry 失败伪装成 idle 健康。

把 launcher 专属的 start-lock/spawn 函数从 `supervisor.py` 移到 `worker_launcher.py`，并保留兼容性重新导出。增加 `execution_mode=isolated|legacy`，最终默认值为 `isolated`，并在同一提交中接入真实调用方。`reindex-queue status` 列出 quarantined 记录；`break-lease` 拒绝这些记录。`clear-quarantine` 对完整进程引用调用匹配后端的 `confirm_reference_dead`；对进程字段全空的精确 journal 重新调用 `recover`，且只接受 `NEVER_STARTED`；部分字段损坏或 `UNCONFIRMED` 不得自动清除。管理面取得证明后仍必须保持 quarantine，先按 attempt_id 幂等 input cleanup，再执行 queue clearance、artifact tombstone cleanup 和 journal durable clear；`--yes` 永远不能替代死亡或从未启动证明。

`codev_platform/ops/reindex_queue.py` 必须是 isolated worker 父侧唯一业务组合根：它先从共享
`reindex.open_default_queue(fail_soft=False)` 取得 queue port，再取得一次缓存 identity，创建一个
`ConfiguredInputSelector`，把二者注入一个 `AttemptSpecFactory`；随后创建一个
`ManifestDependencyGate`、一个 `FilesystemAttemptArtifactStore` 和
`AttemptCleanupRouter({"configured": ConfiguredAttemptCleanup()})`，并以同一个 production process
backend 创建一个 `ContainedHealthRefresher`；最后把 factory、gate、artifact store、router、health
port 各注入唯一一个 `AttemptOrchestrator`。process backend、publisher 和
`SupervisorAttemptJournal`
具体实现只能在这里选择和构造；File/PG queue 的共享工厂继续服务其他入口，不属于 worker 业务
编排。`worker.py`、`orchestrator.py` 和 `supervisor.py` 只能依赖端口或 façade，不得实例化父侧
具体对象。子进程 strategy 映射只能在 `executor_bootstrap.py` 构造并冻结；Plan B 后续只修改父侧
selector/router 接线与该子侧固定映射，不增加第三个组合根。

三套构造白名单逐字固定为：

1. `ConfiguredInputSelector`、`AttemptSpecFactory`、production process backend、
   `FilesystemAttemptArtifactStore`、`ManifestDependencyGate`、`ResultPublisher`、
   `SupervisorAttemptJournal`、`ContainedHealthRefresher`、`AttemptCleanupRouter` 和
   `AttemptOrchestrator` 只允许在 `codev_platform/ops/reindex_queue.py` 构造。
2. `ConfiguredAttemptInputStrategy` 与冻结的 `AttemptInputStrategy` 映射只允许在
   `codev_platform/reindex/executor_bootstrap.py` 构造。
3. `FileSpoolQueue`、`PgJobQueue` 只允许在共享 `codev_platform/reindex/__init__.py` 的
   `open_default_queue()` 工厂构造；worker 只能请求 `fail_soft=False` 的端口，不能绕过工厂。

- [ ] **步骤 7：增加 systemd、legacy queue 迁移和状态保证**

`render_reindex_unit` 输出 `Delegate=yes`、`KillMode=control-group` 和 `Restart=always`。生产选择器
只允许 Windows 上的 `WindowsJobAttemptProcessBackend` 或 Linux/WSL 上的
`CgroupAttemptProcessBackend`。Linux 必须在 claim 前验证 systemd delegation、可写 `cgroup.kill`
与就绪状态；失败即致命退出，绝不构造 `PosixAttemptProcessBackend`。POSIX 后端只允许显式
DEV/诊断入口使用，不能成为生产回退。Windows 同样必须在 claim 前验证原子 Job List、
KILL_ON_JOB_CLOSE 和受限 handle list 能力；不支持的系统致命退出，不能回退裸 `Popen`。选择与
readiness 的调用点必须位于 `recover_owned` 和 `claim` 之前，测试以记录型 queue 证明失败路径的
queue 调用数为零。状态报告 `execution_mode`、`attempt_id`、deadline、termination state、completion
状态、`result_digest`、health phase/project/deadline/`health_failed`、outcome 和
`runtime_revision`，但绝不报告 `claim_token`、fence、health handle/native_ref 或任意不受管
artifact 路径。

从 legacy 切 isolated 前必须停止旧 worker、等待或由 service cgroup 清空旧执行树，并在 run lock
下审计 File/PG 的 pending/active 数据。`JobMeta.target_commit=None`、短 SHA 或非法 revision 不能
默认不存在，也不能回退当前 HEAD：旧 active claim 先由精确 owner recovery 取回，再调用
token-fenced `reject`；旧 pending 在首次 claim 后同样 reject。部署报告必须列出 reject 数量与
project/kind/reason（不含 token），确认剩余项都有完整 target 后才允许启动正常 drain。新 webhook
会以完整 target 重新入队；`begin_publish` 永远不接收空 desired revision。

- [ ] **步骤 8：验证并提交**

运行：

    python -m pytest tests/test_reindex_file_durability.py tests/test_reindex_attempt_completion.py tests/test_reindex_attempt_artifacts.py tests/test_reindex_attempt_finalization.py tests/test_reindex_executor_observer.py tests/test_reindex_dependency_gate.py tests/test_reindex_health_refresh.py tests/test_reindex_attempt_protocol.py tests/test_reindex_attempt_process_values.py tests/test_reindex_attempt_process_reports.py tests/test_reindex_attempt_process_start.py tests/test_reindex_attempt_process_protocol.py tests/test_reindex_windows_job_lifecycle.py tests/test_reindex_windows_job_recovery.py tests/test_reindex_windows_job_cleanup.py tests/test_reindex_windows_job_native.py tests/test_reindex_posix_process_lifecycle.py tests/test_reindex_posix_process_stdio.py tests/test_reindex_posix_process_identity.py tests/test_reindex_posix_process_termination.py tests/test_reindex_posix_process_recovery.py tests/test_reindex_cgroup_readiness.py tests/test_reindex_cgroup_lifecycle.py tests/test_reindex_cgroup_bootstrap.py tests/test_reindex_cgroup_integration.py tests/test_reindex_executor_strategy.py tests/test_reindex_executor_failures.py tests/test_reindex_executor_cli.py tests/test_reindex_result_publisher.py tests/test_index_manifest.py tests/test_reindex_queue_contract.py tests/test_reindex_queue_transitions.py tests/test_pg_queue_transitions.py tests/test_pg_queue_view.py tests/test_pg_queue_transaction_boundaries.py tests/test_reindex_queue.py tests/test_pg_queue.py tests/test_reindex_orchestrator.py tests/test_reindex_composition_root.py tests/test_reindex_worker_supervisor.py tests/test_reindex_queue_cli.py tests/test_reindex_status_core.py tests/test_reindex_status_owner.py tests/test_systemd_restart.py tests/test_health_reindex_worker.py -q

提交：

    git add codev_platform/reindex/file_durability.py codev_platform/reindex/attempt_completion.py codev_platform/reindex/attempt_artifacts.py codev_platform/reindex/attempt_finalization.py codev_platform/reindex/attempt_validation.py codev_platform/reindex/dependency_gate.py codev_platform/reindex/executor_observer.py codev_platform/reindex/health_refresh.py codev_platform/reindex/attempts.py codev_platform/reindex/executor.py codev_platform/reindex/queue_ports.py codev_platform/reindex/queue.py codev_platform/reindex/file_queue.py codev_platform/reindex/file_queue_store.py codev_platform/reindex/file_queue_view.py codev_platform/reindex/file_queue_transitions.py codev_platform/reindex/pg_queue.py codev_platform/reindex/pg_queue_sql.py codev_platform/reindex/pg_queue_codec.py codev_platform/reindex/pg_queue_transitions.py codev_platform/reindex/pg_queue_view.py codev_platform/reindex/orchestrator.py codev_platform/reindex/worker_launcher.py codev_platform/reindex/worker.py codev_platform/reindex/supervisor.py codev_platform/reindex/status.py codev_platform/reindex/__init__.py codev_platform/ops/reindex_queue.py codev_platform/mcp_systemd.py tests/queue_transition_contract.py tests/test_reindex_file_durability.py tests/test_reindex_attempt_completion.py tests/test_reindex_attempt_artifacts.py tests/test_reindex_attempt_finalization.py tests/test_reindex_executor_observer.py tests/test_reindex_dependency_gate.py tests/test_reindex_health_refresh.py tests/test_reindex_attempt_protocol.py tests/reindex_executor_support.py tests/test_reindex_executor_strategy.py tests/test_reindex_executor_failures.py tests/test_reindex_executor_cli.py tests/test_reindex_result_publisher.py tests/test_reindex_queue_contract.py tests/test_reindex_queue_transitions.py tests/test_pg_queue_transitions.py tests/test_pg_queue_view.py tests/test_pg_queue_transaction_boundaries.py tests/test_reindex_queue.py tests/test_pg_queue.py tests/test_reindex_orchestrator.py tests/test_reindex_composition_root.py tests/test_reindex_worker_supervisor.py tests/test_reindex_queue_cli.py tests/reindex_status_support.py tests/test_reindex_status_core.py tests/test_reindex_status_owner.py tests/test_systemd_restart.py tests/test_health_reindex_worker.py docs/plans/roadmap-2026-07-11/README.md docs/plans/roadmap-2026-07-11/reindex-attempt-isolation-implementation-plan-2026-07-11.md docs/plans/roadmap-2026-07-11/reindex-attempt-production-integration-plan-2026-07-11.md docs/plans/roadmap-2026-07-11/reindex-isolated-execution-design-2026-07-11.md
    git commit -m "feat(reindex): 接入单循环隔离编排与恢复"

---

### 任务 8：运行故障注入与兼容性门禁

**文件：**
- 新建：`tests/test_reindex_attempt_faults.py`
- 修改：`tests/test_reindex_launcher_handles.py`
- 修改：`tests/test_reindex_orchestrator.py`
- 修改：`tests/test_reindex_health_refresh.py`
- 修改：`tests/test_reindex_composition_root.py`
- 修改：`docs/plans/roadmap-2026-07-11/README.md`

**接口：** 不增加生产抽象。

- [ ] **步骤 1：增加精确的高可用矩阵**

增加以下测试，并使用显式挂钟时间断言：
- test_runner_grandchild_holds_pipe_but_next_job_starts_within_bound
- test_executor_ignores_term_and_entire_tree_is_forced_dead
- test_windows_parent_crash_closes_job_and_kills_grandchild
- test_posix_parent_restart_recovers_recorded_process_group
- test_cgroup_bootstrap_failure_never_runs_executor
- test_cgroup_kill_waits_for_populated_zero
- test_orchestrator_crash_after_claim_recovers_before_new_claim
- test_orchestrator_crash_between_claim_and_journal_recovers_owned_claim
- test_orchestrator_crash_after_result_publishes_once
- test_orchestrator_crash_after_manifest_acks_without_rerun
- test_orchestrator_crash_after_input_cleanup_before_ack_replays_without_workspace_overlap
- test_orchestrator_crash_after_retry_uses_pending_witness_and_only_cleans_artifacts
- test_dependency_block_crash_after_manifest_before_ack_reuses_same_attempt_digest
- test_raw_result_without_receipt_is_never_published_and_retries_after_death
- test_queue_renew_timeout_kills_and_never_publishes
- test_quarantine_survives_lease_expiry_and_worker_restart
- test_wrong_fence_partial_json_and_exit_zero_without_result_never_ack
- test_log_flood_invalid_utf8_and_disk_full_do_not_extend_kill_bound
- test_enqueue_during_active_is_preserved_and_old_desired_is_not_published
- test_attempt_cleanup_runs_only_after_confirmed_death
- test_input_cleanup_always_precedes_queue_release_and_artifact_tombstone
- test_quarantined_attempt_never_calls_cleanup_port
- test_unproven_runtime_identity_prevents_any_queue_claim
- test_queue_open_failure_is_fatal_not_empty_queue
- test_delegate_readiness_failure_has_zero_recover_and_claim_calls
- test_health_refresh_parent_crash_recovers_durable_health_handle_before_claim

对于计时用例，断言 `next_job_started_at - first_attempt_started_at` 不超过 `attempt_timeout + kill_grace + kill_timeout + 2 * poll_sec + 0.5` 秒。

- [ ] **步骤 2：运行 Plan A 目标测试集**

运行：

    python -m pytest tests/test_reindex_launcher_handles.py tests/test_reindex_attempt_protocol.py tests/test_reindex_attempt_completion.py tests/test_reindex_attempt_artifacts.py tests/test_reindex_attempt_finalization.py tests/test_reindex_queue_contract.py tests/test_reindex_spec_factory.py tests/test_reindex_executor_strategy.py tests/test_reindex_executor_failures.py tests/test_reindex_executor_cli.py tests/test_reindex_executor_observer.py tests/test_reindex_attempt_cleanup.py tests/test_reindex_attempt_process_values.py tests/test_reindex_attempt_process_reports.py tests/test_reindex_attempt_process_start.py tests/test_reindex_attempt_process_protocol.py tests/test_reindex_windows_job_lifecycle.py tests/test_reindex_windows_job_recovery.py tests/test_reindex_windows_job_cleanup.py tests/test_reindex_windows_job_native.py tests/test_reindex_windows_job_real.py tests/test_reindex_posix_process_lifecycle.py tests/test_reindex_posix_process_stdio.py tests/test_reindex_posix_process_identity.py tests/test_reindex_posix_process_termination.py tests/test_reindex_posix_process_recovery.py tests/test_reindex_cgroup_readiness.py tests/test_reindex_cgroup_lifecycle.py tests/test_reindex_cgroup_bootstrap.py tests/test_reindex_cgroup_integration.py tests/test_reindex_dependency_gate.py tests/test_reindex_result_publisher.py tests/test_reindex_health_refresh.py tests/test_reindex_orchestrator.py tests/test_reindex_composition_root.py tests/test_reindex_attempt_faults.py -q

预期：所有适用测试通过；不适用于当前操作系统的测试带明确原因跳过；目标测试均不依赖外层测试进程在无清理条件下挂起。

- [ ] **步骤 3：运行兼容性与静态架构门禁**

运行：

    python -m pytest tests/test_reindex_queue.py tests/test_pg_queue.py tests/test_reindex_worker_affinity.py tests/test_reindex_worker_supervisor.py tests/test_reindex_queue_cli.py tests/test_reindex_status_core.py tests/test_reindex_status_owner.py tests/test_wait_for_reindex_worker.py tests/test_health_reindex_worker.py tests/test_index_manifest.py tests/test_systemd_restart.py -q
    python -m ruff check codev_platform/reindex codev_platform/core/process_tree.py codev_platform/ops/reindex_queue.py codev_platform/mcp_systemd.py codev_platform/mcp_runtime.py
    rg -n "heartbeat_thread|_start_lease_renewer" codev_platform/reindex codev_platform/ops/reindex_queue.py
    rg -n "FileSpoolQueue\(|PgJobQueue\(|ConfiguredInputSelector\(|AttemptSpecFactory\(|ConfiguredAttemptInputStrategy\(|WindowsJobAttemptProcessBackend\(|PosixAttemptProcessBackend\(|CgroupAttemptProcessBackend\(|FilesystemAttemptArtifactStore\(|ManifestDependencyGate\(|ResultPublisher\(|SupervisorAttemptJournal\(|ContainedHealthRefresher\(|ConfiguredAttemptCleanup\(|AttemptCleanupRouter\(|AttemptOrchestrator\(" codev_platform --glob "*.py"
    rg -n "open_default_queue\(" codev_platform/ops/reindex_queue.py codev_platform/reindex --glob "*.py"

第一条架构扫描可以显示 legacy 定义，但不得显示 isolated 组合根调用。第二条构造扫描必须按任务 7
的三套白名单分别解释：父侧 worker 专属对象只允许在 `ops/reindex_queue.py`，子侧 strategy 构造与
冻结只允许在 `executor_bootstrap.py`，File/PG queue 后端只允许由共享 `reindex.open_default_queue()`
工厂构造；`PosixAttemptProcessBackend` 在生产包构造数必须为零。类定义、类型注解和测试不计为
实例化。另行检查 executor 的导入，要求 queue、supervisor、manifest 和 health-state 依赖数为零。
第三条扫描中 isolated worker 的调用必须逐字带 `fail_soft=False`；其他共享调用方可以保持自己的
显式策略，但不得由 worker 继承默认 fail-soft。最终以 `test_three_composition_allowlists_have_no_out_of_root_constructors`
的 AST 断言为准，`rg` 只供人工复核，不能把字符串/注释误算为实例化。

- [ ] **步骤 4：强制执行文件大小、嵌套和文档完整性约束**

运行：

    $files = Get-ChildItem codev_platform/reindex -Filter *.py
    $files | ForEach-Object { if ((Get-Content $_.FullName).Count -gt 600) { throw "oversized $($_.FullName)" } }
    python -m pytest tests/

预期：生产 Python 文件均不超过 600 行；完整 pytest 以零退出；收集到的测试数量不减少。

- [ ] **步骤 5：提交完成证据**

    git add tests/test_reindex_attempt_faults.py tests/test_reindex_launcher_handles.py tests/test_reindex_orchestrator.py tests/test_reindex_health_refresh.py tests/test_reindex_composition_root.py docs/plans/roadmap-2026-07-11/README.md
    git commit -m "test(reindex): 验证隔离执行故障矩阵"

---
