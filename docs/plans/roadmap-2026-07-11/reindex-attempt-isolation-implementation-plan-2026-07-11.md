# Reindex 尝试隔离实施计划

> **供自动化执行代理使用：** 必需子技能：使用 `superpowers:subagent-driven-development`（推荐）或 `superpowers:executing-plans`，逐项实施本计划。各步骤使用复选框（`- [ ]`）跟踪。

**目标：** 将每次 reindex attempt 隔离到独立、可围栏的进程树，使输入物化、Git、runner、日志或原生调用卡死时，长期 worker 仍能在确定上界内终止、恢复并继续后继任务。

**架构：** 保留 queue v2、manifest、runner 注册表、supervisor 状态和全局串行语义。唯一的 `AttemptOrchestrator` 管理 claim、journal、deadline、heartbeat、lease、containment、完成凭据校验、期望版本围栏、manifest 发布以及 ack/retry/quarantine；`executor_observer` 在同一 containment 内启动真正 executor，并在独立观测其退出码后耐久写入 `AttemptCompletionReceipt`。executor 通过固定的 `AttemptInputStrategy` 映射完成输入物化、Git、runner 和证明生成。最终化使用 journal checkpoint，并以精确 manifest 或 replacement queue 状态作为 queue 动作后的崩溃见证；健康刷新通过独立窄端口有界执行，不进入 attempt 成功判定。

**技术栈：** Python 3.10+、`dataclass`/`Protocol`、原子 JSON、pytest、Windows Job Object、
Linux systemd delegated cgroup v2，以及仅供开发诊断的 gated POSIX 进程组。

## 全局约束

- 本计划只实现 Plan A；不实现 exact workspace、object cache、版本化 wheel runtime 或并行 job。
- 实施前置是 Plan C 任务 1-2 的叶子模型与 `core.runtime_identity`；顺序固定为 Plan C 任务 1-2、Plan A、Plan B、Plan C 任务 3-10。
- Plan A 在固定的 `AttemptInputStrategy` 映射中提供 `configured`；Plan B 只增加 `exact_workspace` 条目，不改变 attempt/result/orchestrator 接口，也不动态加载策略。
- 生产 Python 文件不超过 600 行；AttemptOrchestrator 不超过 300 行；核心流程嵌套不超过 3 层。
- 只有一个有副作用的编排器。不得新增 `JobProcessor`、第二个 watchdog 或第二个 lease/heartbeat 循环。
- max_concurrency 固定为 1；同一 project_id/kind 最多一个未围栏 executor。
- executor 不访问 queue、supervisor、manifest 或 health state；claim token 永不写入 spec/result。
- executor 只写 write-once result；`executor_observer` 独立观测 inner executor 返回码并写
  write-once completion receipt。父进程正常路径和恢复路径都只能从 receipt 取得
  `process_rc`，禁止把 `result.rc` 回填成外部退出证据；receipt 路径要求 `result.rc` 是整数且与
  `process_rc` 完全一致，`None` 也视为不匹配。
- Orchestrator 不运行 Git、runner 或输入物化器，不读取 runner 的 stdout，也不建立 stdout/stderr 管道。
- runtime revision 只来自 core.runtime_identity.runtime_identity().runtime_revision；不得在 reindex 内复制 Git/package 探测。
- runner 日志只在 executor 内脱敏和限长；bootstrap stdout/stderr 只能落专用文件或 DEVNULL。
- queue 调用、进程等待、终止、reader join 和 cleanup 均有显式上界。
- 启动顺序固定为 claim、空 `CLAIMED` journal durable replace、`prepare` blocked、带 handle journal
  文件与目录 durable replace、`activate`；第二次持久化失败只能 terminate，绝不 activate。
- `AttemptCompletionReceipt` 完成文件与目录耐久同步后才是“可恢复完成”的线性化点。只有 raw
  result 而没有合法 receipt 时，即使 result 自称成功，也只能在确认执行树死亡后安全重试，
  不得直接发布。
- attempt artifact 只能由 `AttemptArtifactStore` 按 `attempt_id` 摘要派生固定目录和
  spec/result/receipt/bootstrap log 路径；Orchestrator 不拼路径，恢复也不信任 journal 中的任意
  路径字符串。
- spec/result/receipt 使用 0600、原子 write-once；journal 使用 0600、原子 durable replace；
  POSIX 同步文件和目录，Windows 使用 `MoveFileExW(..., MOVEFILE_WRITE_THROUGH)`。journal 最后
  durable clear 前必须完成 artifact 清理。
- 进入 `FINALIZING` 前必须持久化确定性动作意图、结果摘要和互斥证据类型；进程路径必须先取得
  匹配死亡证明，依赖阻断路径必须证明从未 `prepare`。最终化先续租覆盖有界清理窗口，再按
  `attempt_id` 幂等清理输入，最后才 ack/retry 释放 queue；禁止让新 attempt 与旧 workspace 清理重叠。
- queue 动作后崩溃不另造重型事务：发布动作优先以同 `attempt_id + result_digest + 全部字段` 的
  manifest 为耐久见证，supersede/retry 以同 key 的更新 pending/active 为见证；若新 owner 已接管
  同 key，旧结果只允许放弃，不得再发布。旧 token 仍 active 时重放；token 已失且存在 manifest 或
  replacement queue 见证时，artifact root 尚在则先按旧 attempt_id 幂等补做 input cleanup，再收口
  artifact/journal；两类见证都没有时失败关闭。
- kill 未确认必须持久 quarantine；quarantined key 不得因 lease 到期重新 claim。
- heartbeat 与 lease renewal 只由同一 Orchestrator control-loop tick 驱动。
- supervisor state 继续是 worker/attempt journal；同文件的 `health_operation` 只保存短期进程 handle，
  不表达 job/result 且恢复后即清；supervisor 单一适配器是文件唯一写者，向 attempt/health 暴露两个
  窄端口并保留未修改字段；不新增第四份综合状态真值。
- manifest publish 必须处于 QueuePort desired revision guard 内；publish 成功后才能 ack。
- manifest 持久化规范 `AttemptResult` 的 `result_digest`。同一 `attempt_id` 只有 digest 与全部
  持久字段均一致才算幂等；任何同 ID 冲突都失败关闭，禁止 `INSERT OR REPLACE` 吞掉差异。
- workspace/resource lease 由具体策略以 `attempt_id` 写入 ledger；父侧 `AttemptCleanupPort` 只有收到 `ConfirmedProcessDeath` 后才能清理；`QUARANTINED` 自动路径不调用 cleanup，只有管理面取得严格证明后才能按同一顺序收口。
- 生产进程后端只允许 Windows Job 或 Linux delegated cgroup v2；Linux 委派/就绪检查失败必须在
  claim 前致命，绝不回退 POSIX。普通 POSIX 仅供开发/协作式诊断，activate 后永久不得生成
  `ConfirmedProcessDeath`。
- `input_payload` 使用策略专属白名单；payload 不得指定任意远端 URL、任意绝对路径或动态 Python 类型。
- 组合边界按进程分开：`codev_platform/ops/reindex_queue.py` 是 isolated worker 父侧唯一业务组合根，
  从共享 `reindex.open_default_queue(fail_soft=False)` 取得 `WorkerQueuePort` 后，只连接 selector、
  spec factory、process backend、artifact store、publisher、journal、cleanup router、health
  refresher 和 Orchestrator；worker 必须
  显式使用 `open_default_queue(fail_soft=False)`，不能把 queue 不可用伪装为空队列；
  `codev_platform/reindex/executor_bootstrap.py` 是子进程唯一最小启动组合根，只从同一次可信配置加载
  构造并冻结固定白名单 strategy 映射。strategy 实例绝不跨 `exec`、不 pickle、不动态加载。
- 三套构造白名单是静态门禁：isolated 父侧业务对象只允许在 `ops/reindex_queue.py` 构造；子侧
  strategy 与冻结映射只允许在 `executor_bootstrap.py` 构造；File/PG queue 后端只允许由共享
  `reindex.open_default_queue()` 工厂构造。生产包其他模块出现实例化即失败。
- commit subject 使用中文，不写任何 AI 痕迹。

---

## 固定跨计划契约

以下代码块是跨模块公共契约而非单文件堆放要求：核心 spec/result/journal 模型落在
`attempts.py`，completion/digest 落在 `attempt_completion.py`，互斥验证证据与
`ValidatedAttemptResult` 落在 `attempt_validation.py`。所有编解码器只接受 schema version 1 和
规范 JSON 值。

    def _canonicalize_json_object(raw: str, max_bytes: int) -> str:
        encoded = raw.encode("utf-8")
        if len(encoded) > max_bytes:
            raise ValueError("canonical JSON exceeds size limit")

        def reject_duplicate_pairs(pairs: list[tuple[str, object]]) -> dict[str, object]:
            value: dict[str, object] = {}
            for key, item in pairs:
                if key in value:
                    raise ValueError(f"duplicate JSON key: {key}")
                value[key] = item
            return value

        def reject_constant(value: str) -> object:
            raise ValueError(f"non-finite JSON number: {value}")

        parsed = json.loads(
            raw,
            object_pairs_hook=reject_duplicate_pairs,
            parse_constant=reject_constant,
        )
        if not isinstance(parsed, dict):
            raise ValueError("canonical JSON root must be an object")
        canonical = json.dumps(
            parsed,
            ensure_ascii=True,
            allow_nan=False,
            sort_keys=True,
            separators=(",", ":"),
        )
        if len(canonical.encode("utf-8")) > max_bytes:
            raise ValueError("canonical JSON exceeds size limit")
        return canonical


    @dataclass(frozen=True, slots=True, init=False)
    class CanonicalJsonObject:
        text: str

        def __init__(self, raw: str, *, max_bytes: int = 1024 * 1024) -> None:
            object.__setattr__(self, "text", _canonicalize_json_object(raw, max_bytes))

        @classmethod
        def from_text(cls, raw: str, *, max_bytes: int = 1024 * 1024) -> "CanonicalJsonObject":
            return cls(raw, max_bytes=max_bytes)

        @classmethod
        def from_value(cls, value: dict[str, object],
                       *, max_bytes: int = 1024 * 1024) -> "CanonicalJsonObject":
            raw = json.dumps(value, ensure_ascii=True, allow_nan=False, separators=(",", ":"))
            return cls(raw, max_bytes=max_bytes)

        def to_value(self) -> dict[str, object]:
            value = json.loads(self.text)
            if not isinstance(value, dict):
                raise ValueError("canonical JSON root must be an object")
            return value

    class AttemptOutcome(str, Enum):
        SUCCEEDED = "succeeded"
        RETRYABLE = "retryable"
        FAILED = "failed"
        TIMED_OUT = "timed_out"
        QUARANTINED = "quarantined"
        SUPERSEDED = "superseded"


    @dataclass(frozen=True, slots=True)
    class AttemptSpec:
        schema_version: int
        attempt_id: str
        fence: str
        project_id: str
        kind: str
        input_kind: str
        input_payload: CanonicalJsonObject
        target_commit: str
        timeout_sec: float
        runtime_revision: str


    @dataclass(frozen=True, slots=True)
    class AttemptResult:
        schema_version: int
        attempt_id: str
        fence: str
        project_id: str
        kind: str
        input_root: str
        target_commit: str
        input_commits: tuple[tuple[str, str], ...]
        input_trees: tuple[tuple[str, str], ...]
        runtime_revision: str
        outcome: AttemptOutcome
        rc: int | None
        retryable: bool
        note: str
        timing: tuple[tuple[str, float], ...]
        log_ref: str | None
        proof: CanonicalJsonObject


    @dataclass(frozen=True, slots=True)
    class AttemptCompletionReceipt:
        schema_version: int
        attempt_id: str
        fence: str
        spec_digest: str
        result_digest: str
        process_rc: int
        observed_at: float


    class AttemptValidationEvidence(str, Enum):
        COMPLETION_RECEIPT = "completion_receipt"
        DEPENDENCY_BLOCK = "dependency_block"


    @dataclass(frozen=True, slots=True)
    class ValidatedAttemptResult:
        spec: AttemptSpec
        result: AttemptResult
        claim_token: str
        result_digest: str
        process_rc: int | None
        evidence: AttemptValidationEvidence
        validated_at: float


    def validate_attempt_result(
        spec: AttemptSpec,
        result: AttemptResult,
        *,
        claim: ClaimedJob,
        completion: AttemptCompletionReceipt,
        validated_at: float,
    ) -> ValidatedAttemptResult:
        expected_spec_digest = attempt_spec_digest(spec)
        expected_result_digest = attempt_result_digest(result)
        receipt_matches = (
            completion.attempt_id == spec.attempt_id
            and completion.fence == spec.fence
            and completion.spec_digest == expected_spec_digest
            and completion.result_digest == expected_result_digest
        )
        if not receipt_matches:
            raise ValueError("completion receipt 与 spec/result 不匹配")
        identity = (
            (result.schema_version, spec.schema_version, "schema_version"),
            (result.attempt_id, spec.attempt_id, "attempt_id"),
            (result.fence, spec.fence, "fence"),
            (result.project_id, spec.project_id, "project_id"),
            (result.kind, spec.kind, "kind"),
            (result.target_commit, spec.target_commit, "target_commit"),
            (result.runtime_revision, spec.runtime_revision, "runtime_revision"),
        )
        for actual, expected, field_name in identity:
            if actual != expected:
                raise ValueError(f"attempt result {field_name} mismatch")
        if claim.job.project_id != spec.project_id or claim.job.kind != spec.kind:
            raise ValueError("claim 与 attempt 不匹配")
        if type(result.rc) is not int or result.rc != completion.process_rc:
            raise ValueError("result.rc 与 observer 观测退出码不一致")
        if result.outcome is AttemptOutcome.SUCCEEDED:
            proof = result.proof.to_value()
            if completion.process_rc != 0 or result.rc != 0 or proof.get("success") is not True:
                raise ValueError("成功结果缺少独立退出码或严格证明")
        return ValidatedAttemptResult(
            spec=spec,
            result=result,
            claim_token=claim.claim_token,
            result_digest=expected_result_digest,
            process_rc=completion.process_rc,
            evidence=AttemptValidationEvidence.COMPLETION_RECEIPT,
            validated_at=validated_at,
        )

`attempt_spec_digest` 与 `attempt_result_digest` 都对严格模型的规范 JSON UTF-8 字节计算
SHA-256，不对原始文件的空白或键顺序计算摘要。`AttemptCompletionReceipt` 由
`executor_observer` 在独立等待 inner executor 退出后写入；executor、Orchestrator 和恢复逻辑
均不得直接构造 receipt 或用 `result.rc` 替代 `completion.process_rc`。

`ValidatedAttemptResult` 只能由两个互斥边界构造：正常/恢复共用的 receipt-aware
`validate_attempt_result`，以及任务 7 在进程启动前把明确依赖失败转换为
`FAILED` 的 `validate_dependency_block`。后者使用 `DEPENDENCY_BLOCK` 证据且
`process_rc=None`，绝不伪造 completion receipt；除这两个边界外，executor、observer、物化器、
publisher 和 Plan B 均不得直接实例化该类型。任何 `SUCCEEDED` 结果只允许
`COMPLETION_RECEIPT` 证据。

Orchestrator 接缝固定为 `AttemptOrchestrator.run(self, claim: ClaimedJob, spec: AttemptSpec) -> AttemptResult`。

Plan B 绝不在 `run` 之前物化输入。它提供 `input_kind="exact_workspace"` 和经过校验的 `input_payload`；因此物化失败仍是 executor 结果，并由同一个 Orchestrator 处理。

父侧清理契约同样固定：

    @dataclass(frozen=True, slots=True)
    class ConfirmedProcessDeath:
        process_identity: str
        containment_kind: str
        confirmed_at: float
        evidence: str


    @dataclass(frozen=True, slots=True)
    class CleanupReport:
        released: bool
        attempt_id: str
        note: str


    class AttemptCleanupPort(Protocol):
        def release(self, spec: AttemptSpec,
                    death: ConfirmedProcessDeath) -> CleanupReport:
            raise NotImplementedError("cleanup adapter method")

spec 输入选择契约固定为：

    @dataclass(frozen=True, slots=True)
    class AttemptInputSelection:
        input_kind: str
        input_payload: CanonicalJsonObject


    class AttemptInputSelector(Protocol):
        def select(self, claim: ClaimedJob) -> AttemptInputSelection:
            raise NotImplementedError("input selector method")

`AttemptSpecFactory` 是 `AttemptSpec` 的唯一构造器。它接收启动时缓存的 `RuntimeIdentity`、唯一的 `AttemptInputSelector`、按类型取超时的函数以及 attempt/fence 工厂，在不执行 Git 和不修改文件系统的前提下创建 spec：

    class AttemptSpecFactory:
        def __init__(
            self,
            *,
            runtime_identity: RuntimeIdentity,
            input_selector: AttemptInputSelector,
            timeout_for_kind: Callable[[str], float],
            attempt_id_factory: Callable[[], str],
            fence_factory: Callable[[], str],
        ) -> None:
            self._runtime_identity = runtime_identity
            self._input_selector = input_selector
            self._timeout_for_kind = timeout_for_kind
            self._attempt_id_factory = attempt_id_factory
            self._fence_factory = fence_factory

        def create(self, claim: ClaimedJob) -> AttemptSpec:
            target_commit = str(claim.job.meta.target_commit or "").strip()
            if not target_commit:
                raise ValueError("isolated attempt requires target_commit")
            timeout_sec = self._timeout_for_kind(claim.job.kind)
            if timeout_sec <= 0:
                raise ValueError("attempt timeout must be positive")
            selected = self._input_selector.select(claim)
            return AttemptSpec(
                schema_version=1,
                attempt_id=self._attempt_id_factory(),
                fence=self._fence_factory(),
                project_id=claim.job.project_id,
                kind=claim.job.kind,
                input_kind=selected.input_kind,
                input_payload=selected.input_payload,
                target_commit=target_commit,
                timeout_sec=timeout_sec,
                runtime_revision=self._runtime_identity.runtime_revision,
            )


    class ConfiguredInputSelector:
        def select(self, claim: ClaimedJob) -> AttemptInputSelection:
            return AttemptInputSelection(
                input_kind="configured",
                input_payload=CanonicalJsonObject.from_value(
                    {"project_id": claim.job.project_id}
                ),
            )

Plan A 组合一个 `ConfiguredInputSelector`；Plan B 只在唯一组合根中换成能够选择 `exact_workspace` 的 selector。两者都只能由同一个 `AttemptSpecFactory` 调用，任何其他生产模块都不得直接构造 `AttemptSpec`。

`AttemptCleanupRouter` 是注入 Orchestrator 的唯一清理端口。它持有从 `input_kind` 到适配器的显式固定映射，并委派 `release(spec, death)`；未知类型按失败关闭处理：

    class ConfiguredAttemptCleanup:
        def release(self, spec: AttemptSpec,
                    death: ConfirmedProcessDeath) -> CleanupReport:
            if not isinstance(death, ConfirmedProcessDeath):
                raise TypeError("confirmed process death is required")
            return CleanupReport(
                released=True,
                attempt_id=spec.attempt_id,
                note="configured input owns no parent-side resource",
            )


    class AttemptCleanupRouter:
        def __init__(self, adapters: Mapping[str, AttemptCleanupPort]) -> None:
            frozen = dict(adapters)
            if not frozen:
                raise ValueError("cleanup adapter mapping must not be empty")
            self._adapters = MappingProxyType(frozen)

        def release(self, spec: AttemptSpec,
                    death: ConfirmedProcessDeath) -> CleanupReport:
            adapter = self._adapters.get(spec.input_kind)
            if adapter is None:
                raise ValueError(f"no cleanup adapter for {spec.input_kind}")
            return adapter.release(spec, death)

configured 清理是返回 `released=True` 的真实空操作适配器；Plan B 在同一个 router 映射中增加 exact workspace ledger 适配器。Orchestrator 只在结果已冻结为 FINALIZING 意图、进程死亡已确认且 queue token 仍受控时调用 `release`，并保证它先于 ack/retry；`QUARANTINED` 永不调用它。

---

## 文件映射

| 路径 | 职责 |
|---|---|
| `codev_platform/reindex/attempts.py` | 固定的 spec/result/journal/death/cleanup 核心值模型与严格编解码器 |
| `codev_platform/reindex/file_durability.py` | 0600 create-once、durable replace 与 durable unlink 的跨平台耐久叶子 |
| `codev_platform/reindex/attempt_completion.py` | completion receipt 模型、规范 spec/result 摘要和严格 codec |
| `codev_platform/reindex/attempt_artifacts.py` | 按 attempt 摘要派生固定路径、严格读写与最终 artifact 清理的唯一 store |
| `codev_platform/reindex/attempt_finalization.py` | FINALIZING 动作意图、证据组合与 digest 约束的纯模型/codec |
| `codev_platform/reindex/attempt_validation.py` | receipt/dependency 两种互斥证据和 `ValidatedAttemptResult` 的唯一父侧构造边界 |
| `codev_platform/reindex/attempt_inputs.py` | 固定的 `AttemptInputStrategy` 映射和 configured 策略 |
| `codev_platform/reindex/spec_factory.py` | 唯一的 `AttemptSpecFactory`、`AttemptInputSelector` 协议和 configured selector |
| `codev_platform/reindex/queue_ports.py` | `ClaimedJob`、持久 quarantine 和期望版本发布围栏 |
| `codev_platform/reindex/file_queue.py` | 从过大的 `queue.py` 拆出的 FileSpool queue v2 后端 |
| `codev_platform/reindex/file_queue_store.py` | File queue 固定路径、耐久 JSON、旧 marker 与有界逐 key 锁 |
| `codev_platform/reindex/file_queue_view.py` | File queue snapshot/dependency state 的只读轻量聚合 |
| `codev_platform/reindex/file_queue_transitions.py` | File queue token-fenced retry/reject 状态转换 |
| `codev_platform/reindex/pg_queue_sql.py` | PG schema 与状态转换 SQL 单一真值 |
| `codev_platform/reindex/pg_queue_codec.py` | PG queue 领域值/JSON 的无状态严格转换 |
| `codev_platform/reindex/pg_queue_view.py` | PG dependency state 的有限预算只读视图 |
| `codev_platform/reindex/pg_queue_transitions.py` | PG retry/reject 单事务状态转换 |
| `codev_platform/reindex/executor.py` | 单次执行输入物化、runner 和证明生成的进程入口 |
| `codev_platform/reindex/executor_bootstrap.py` | executor 子进程固定白名单 strategy 的唯一最小组合根 |
| `codev_platform/reindex/executor_observer.py` | 在 containment 内启动 inner executor、独立等待退出并耐久写 completion receipt |
| `codev_platform/reindex/health_refresh.py` | project 去重、两阶段健康子进程与 supervisor health operation 恢复的窄适配器 |
| `codev_platform/reindex/attempt_process.py` | 平台无关的 containment 契约和后端选择校验，不构造具体后端 |
| `codev_platform/reindex/windows_job.py` | Windows Job Object 实现 |
| `codev_platform/reindex/posix_process.py` | POSIX 进程组实现 |
| `codev_platform/reindex/cgroup_process.py` | WSL/systemd delegated cgroup v2 的逐 attempt 实现 |
| `codev_platform/reindex/cgroup_bootstrap.py` | 先把自身移入 cgroup 再执行目标的最小 bootstrap |
| `codev_platform/reindex/result_publisher.py` | 把已校验结果写入 manifest，且不访问 queue |
| `codev_platform/reindex/attempt_cleanup.py` | `AttemptCleanupRouter`、父侧清理端口和 configured 空操作适配器 |
| `codev_platform/reindex/orchestrator.py` | 唯一的生命周期、清理和 queue 变更编排器，不构造具体适配器 |
| `codev_platform/reindex/worker.py` | 只委派给 `AttemptOrchestrator` 的兼容 façade |
| `codev_platform/reindex/worker_launcher.py` | 既有短 worker 的 spawn/start-lock 辅助函数 |
| `codev_platform/reindex/supervisor.py` | 既有 worker 状态和当前 attempt journal |
| `codev_platform/ops/reindex_queue.py` | 父进程唯一业务组合根，创建唯一 selector/factory、artifact、dependency、cleanup、health 和 Orchestrator |
| `codev_platform/core/repos.py` | `RepoSpec` 与隔离仓向量叶子模块之间的窄适配，并在常规配置前读取冻结向量 |
| `codev_platform/core/repo_runtime_override.py` | 可信仓向量的进程内状态、严格 codec、配置摘要三方校验与无秘密跨 `exec` 传播 |
| `codev_platform/core/config.py` | 完整配置快照摘要的生成与跨 `exec` 一致性失败关闭 |
| `codev_platform/core/runtime_identity.py` | Plan C 任务 1-2 提供的唯一 runtime revision 生产者，本计划只消费 |
| `codev_platform/core/runtime_interpreter.py` | 从已证明环境固定稳定 venv 逻辑解释器，并在每层 `exec` 复核运行版本 |
| `codev_platform/core/repo_input_guard.py` | 冻结主仓绑定、Git tracked 文件清单与不遍历仓外文件系统的安全 glob |
| `codev_platform/chroma/collection_integrity.py` | Chroma/code_vec 共用的分页精确 ID 集存储后验 |
| `codev_platform/chroma/document_manifest.py` | 文档 manifest 的唯一 schema 与失败关闭 codec |
| `codev_platform/chroma/index_input.py` | proven 文档摘要、写前同 bytes 复核与 legacy 读取兼容 |
| `codev_platform/recall/code_vector_collection.py` | 多仓 codegraph 节点采集与轻量 chunk 聚合 |
| `codev_platform/recall/code_vector_manifest.py` | code_vec manifest 的有界 schema、真实序列化总量校验与失败关闭 codec |
| `codev_platform/reindex/runner_proof.py` | 未截断完整输出流的唯一 marker 与 forbidden policy 扫描 |

---


## 任务子计划索引

实施细节按职责拆分到三个子计划；本文件只维护跨任务唯一契约、文件映射、执行顺序与退出标准。
子计划不得复制或改写本文件的全局约束；发现冲突时以本文件为准，并先修正总览再实施。

| 子计划 | 任务 | 独立交付与提交边界 |
|---|---|---|
| [基础契约实施子计划](./reindex-attempt-isolation-foundation-plan-2026-07-11.md) | 1–4 | 启动器句柄、attempt 严格模型、File/PG queue port、executor/输入边界 |
| [围栏与发布实施子计划](./reindex-attempt-containment-publishing-plan-2026-07-11.md) | 5–6 | Windows Job/cgroup/POSIX containment、共享 Deadline、receipt digest 与幂等 manifest |
| [生产集成与故障验收实施子计划](./reindex-attempt-production-integration-plan-2026-07-11.md) | 7–8 | artifact/finalization、observer、dependency/legacy 门禁、Orchestrator、health、systemd、故障矩阵 |

固定顺序为任务 1 → 8；每个任务仍按子计划内的红灯、最小实现、定向验证、评审和提交边界推进。
任务 7 只能消费任务 5 的严格进程契约与任务 6 的 publisher，不能在编排层复制后端身份判断或
manifest 比较逻辑。Plan B 只通过本文件固定的 selector/router/payload/strategy 接缝扩展。

---

## Plan A 退出标准

- 真实 Windows launcher 不继承调用方管道；任意 runner 后代可以继续持有自身管道，但不得阻塞 Orchestrator。
- 输入物化、Git、runner、证明和 runner 日志清理全部在 attempt containment 边界内执行。
- Orchestrator 的 heartbeat 和 lease 共用一个控制循环；isolated 模式没有后台 heartbeat 或 lease renewer。
- 每条等待和终止路径都有测量过的上界；下一任务在文档规定的上界内启动。
- 死亡未确认时持久化 quarantine；lease 到期、重启或其他 worker 均不能 claim 该 key。
- Journal 恢复在 claim 前完成，并安全处理 result/manifest/ack 的每个崩溃边界。
- completion receipt 的目录耐久同步是 executor 完成线性化点；raw result、outer observer rc 或
  `result.rc=None` 都不能替代独立观测的 inner rc。
- FINALIZING 先续租并按 attempt_id 幂等清理输入，再释放 queue；发布、supersede 和 retry 在 queue
  动作后崩溃时分别有 manifest 或 replacement queue 见证，artifact tombstone 后 journal 最后清除。
- 期望版本在 `QueuePort` 发布围栏中检查；`ResultPublisher` 只接收 `ValidatedAttemptResult`。
- Windows Job Object 和 WSL systemd control group 在父进程崩溃时清理后代。
- 资源 lease 只在死亡确认后、queue 释放前清理，并在 quarantine 期间保持占用。
- `ingest`/`code_vec` 仍只依赖同 project/target 的 codegraph：WAIT 公平移到队尾，BLOCK 通过无进程
  FINALIZING journal 发布确定性失败，不生成 completion receipt。
- isolated worker 以 `open_default_queue(fail_soft=False)` 启动；生产 selector/Delegate/Windows
  capability 任一 readiness 失败时，`recover_owned` 与 `claim` 调用数都为零。
- health refresh 使用独立 0600 handle 子记录和同一严格 containment，有界失败不改写 job 终态，
  死亡未确认则停止新 claim。
- File/PG 契约、CLI、hook、status 和 manifest 兼容性测试集持续通过。
- 生产代码中只有 `AttemptSpecFactory` 构造 `AttemptSpec`，且每个 claim 只调用一次唯一 `AttemptInputSelector`。
- 只有 `codev_platform/ops/reindex_queue.py` 组合 isolated worker 父侧一个 factory、一个 selector、
  一个 artifact store、一个 dependency gate、一个 cleanup router、一个 health refresher 和一个
  Orchestrator；只有 `executor_bootstrap.py` 构造并冻结子侧固定
  `AttemptInputStrategy` 映射；File/PG queue 只由共享 `reindex.open_default_queue()` 提供端口，
  其他业务模块不反向依赖 worker 组合根。
- Plan B 只能通过父侧唯一 selector、同一 cleanup router 映射、`input_payload` schema，以及子侧唯一固定 strategy 映射扩展增加 `exact_workspace`。
