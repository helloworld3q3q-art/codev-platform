# Reindex Attempt 隔离基础契约实施子计划（任务 1–4）

> **供自动化执行代理使用：** 必需子技能：使用 `superpowers:subagent-driven-development`（推荐）或 `superpowers:executing-plans`，逐项实施本计划。各步骤使用复选框（`- [ ]`）跟踪。
>
> 上级总览与唯一全局契约：[Reindex 尝试隔离实施计划](./reindex-attempt-isolation-implementation-plan-2026-07-11.md)

**目标：** 固定启动器、attempt 协议、File/PG queue 围栏和 executor 输入边界，为严格进程 containment 提供无歧义基础。

**架构：** 按叶子能力到组合边界依次交付：先复现句柄泄漏，再固化严格值模型与 queue port，最后把输入物化、runner 与证明迁入 executor。每个任务独立红灯、实现、验证和提交。

**技术栈：** Python 3.10+、`dataclass`/`Protocol`、严格 JSON、pytest，以及上级总览指定的跨平台进程与耐久能力。

## 执行前置

- 必须先读取上级总览的“全局约束”“固定跨计划契约”“文件映射”；这些内容只在上级维护，本子计划不复制第二份真值。
- 本子计划只覆盖标题所列任务；相邻任务的产出通过上级契约中的精确接口消费。
- 所有沟通、注释和文档使用中文；代码标识符与外部协议字段保持英文。

---
### 任务 1：复现并阻断真实启动器的句柄泄漏

**文件：**
- 新建：`tests/fixtures/reindex_process_fixture.py`
- 新建：`tests/test_reindex_launcher_handles.py`
- 修改：`codev_platform/mcp_runtime.py`：detached spawn 辅助函数
- 修改：`codev_platform/chroma/launcher.py`：detached daemon spawn

**接口：** 产出一个真实 launcher 回归测试，以及供 containment 测试复用的三种进程模式。

- [ ] **步骤 1：增加完整进程夹具**

    import argparse
    import signal
    import subprocess
    import sys
    import time


    def main() -> int:
        parser = argparse.ArgumentParser()
        parser.add_argument("mode", choices=("hold-pipe", "ignore-term", "success"))
        parser.add_argument("--seconds", type=float, default=10.0)
        args = parser.parse_args()
        if args.mode == "hold-pipe":
            subprocess.Popen(
                [sys.executable, "-c", f"import time; time.sleep({args.seconds!r})"],
                close_fds=False,
            )
            return 0
        if args.mode == "ignore-term":
            if sys.platform != "win32":
                signal.signal(signal.SIGTERM, signal.SIG_IGN)
            time.sleep(args.seconds)
            return 0
        print("proof: success", flush=True)
        return 0


    if __name__ == "__main__":
        raise SystemExit(main())

- [ ] **步骤 2：针对真实 launcher 编写仅在 Windows 运行的测试**

    @pytest.mark.skipif(os.name != "nt", reason="Windows handle inheritance regression")
    def test_spawn_detached_does_not_hold_caller_stdout_pipe(tmp_path: Path) -> None:
        log_path = tmp_path / "daemon.log"
        child = "import time; time.sleep(2)"
        outer = (
            "import sys; from pathlib import Path; "
            "from codev_platform.mcp_runtime import spawn_detached; "
            f"spawn_detached([sys.executable, '-c', {child!r}], "
            f"{str(tmp_path)!r}, Path({str(log_path)!r})); "
            "print('outer-done', flush=True)"
        )
        completed = subprocess.run(
            [sys.executable, "-c", outer],
            capture_output=True,
            text=True,
            timeout=0.8,
            check=True,
        )
        assert completed.stdout.strip() == "outer-done"

- [ ] **步骤 3：运行红灯测试**

运行：`python -m pytest tests/test_reindex_launcher_handles.py -q`

预期：修复 launcher 之前，Windows 因外层超时而失败；非 Windows 平台跳过这项操作系统专属断言。

- [ ] **步骤 4：实施窄范围的纵深防御修复**

两个真实 detached launcher 都必须传入 `close_fds=True`，同时继续显式传入 stdin/stdout/stderr 句柄。为每个 `Popen` 调用增加单元断言，防止后续重构移除该参数。

- [ ] **步骤 5：运行绿灯测试并提交**

运行：`python -m pytest tests/test_reindex_launcher_handles.py tests/test_mcp_serve.py -q`

提交：

    git add tests/fixtures/reindex_process_fixture.py tests/test_reindex_launcher_handles.py codev_platform/mcp_runtime.py codev_platform/chroma/launcher.py docs/plans/roadmap-2026-07-11/reindex-attempt-isolation-foundation-plan-2026-07-11.md
    git commit -m "fix(reindex): 阻断后台进程继承调用方句柄"

---

### 任务 2：实现固定的尝试协议和日志模型

**文件：**
- 新建：`codev_platform/reindex/attempts.py`
- 新建：`tests/test_reindex_attempt_protocol.py`

**接口：** 先实现 `CanonicalJsonObject`、`AttemptOutcome`、`AttemptSpec`、`AttemptResult`、
`ValidatedAttemptResult`、`validate_attempt_result`、`AttemptJournalEntry`、
`ConfirmedProcessDeath`、`CleanupReport` 和原子编解码器。任务 4 才实现 selector/factory/cleanup；
任务 7 按总览文件映射把父侧验证模型无行为变更地抽到 `attempt_validation.py`，避免继续扩张
`attempts.py`，调用方只依赖同一公共契约。

- [ ] **步骤 1：编写编解码与校验红灯测试**

测试文件定义以下 spec 辅助函数：

    def make_spec(tmp_path: Path) -> AttemptSpec:
        return AttemptSpec(
            schema_version=1,
            attempt_id="attempt-1",
            fence="fence-1",
            project_id="demo",
            kind="codegraph",
            input_kind="configured",
            input_payload=CanonicalJsonObject.from_value({"project_id": "demo"}),
            target_commit="a" * 40,
            timeout_sec=30.0,
            runtime_revision="b" * 40,
        )

增加以下具名测试：
- test_canonical_json_rejects_duplicate_keys_non_finite_non_object_and_oversize
- test_canonical_json_sorts_keys_and_has_stable_text
- test_attempt_spec_rejects_unknown_input_keys
- test_attempt_result_atomic_round_trip
- test_attempt_result_rejects_partial_or_oversized_json
- test_validation_rejects_fence_target_runtime_or_process_rc_mismatch
- test_exit_zero_without_success_proof_is_not_validated
- test_claim_token_is_never_serialized

- [ ] **步骤 2：运行红灯测试**

运行：`python -m pytest tests/test_reindex_attempt_protocol.py -q`

预期：由于 `attempts.py` 尚不存在，导入失败。

- [ ] **步骤 3：实现有大小上界的原子 JSON**

    _MAX_ATTEMPT_JSON_BYTES = 1024 * 1024


    def _write_json_atomic(path: Path, payload: dict[str, object]) -> None:
        raw = json.dumps(payload, ensure_ascii=True, separators=(",", ":")).encode("utf-8")
        if len(raw) > _MAX_ATTEMPT_JSON_BYTES:
            raise ValueError("attempt JSON exceeds 1 MiB")
        path.parent.mkdir(parents=True, exist_ok=True)
        temp = path.with_name(f".{path.name}.{uuid4().hex}.tmp")
        try:
            with temp.open("wb") as stream:
                stream.write(raw)
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(temp, path)
        finally:
            temp.unlink(missing_ok=True)


    def _read_json_object(path: Path) -> dict[str, object]:
        if path.stat().st_size > _MAX_ATTEMPT_JSON_BYTES:
            raise ValueError("attempt JSON exceeds 1 MiB")
        value = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(value, dict):
            raise ValueError("attempt JSON root must be an object")
        return value

上面的 `_write_json_atomic` 只描述任务 2 的迁移期红绿基线，不是 Plan A 终态。任务 7 必须删除该
helper 及其所有调用：spec/result/receipt 全部改用共享 `durable_create_once`，journal 单独使用
`durable_replace`；退出门禁要求生产代码中 `_write_json_atomic` 与直接 `os.replace` 写 attempt artifact
的命中数均为零。

`CanonicalJsonObject` 使用 `object_pairs_hook` 解析并拒绝重复键，通过 `parse_constant` 拒绝 `NaN` 和无穷值，要求根节点为对象，在规范化前后都执行字节上限，并以键排序和紧凑分隔符序列化。随后，每个 `input_kind` 应用精确的语义键白名单。`configured` 只接受 `project_id` 和可选的 `repo_targets`；`exact_workspace` 保留给 Plan B，在其固定策略条目存在前一律拒绝。

外层 spec/result 编解码器把 `input_payload` 和 `proof` 嵌入为 JSON 对象，而不是带引号的 JSON 字符串：

    def _encode_spec(spec: AttemptSpec) -> dict[str, object]:
        payload = asdict(spec)
        payload["input_payload"] = spec.input_payload.to_value()
        return payload


    def _encode_result(result: AttemptResult) -> dict[str, object]:
        payload = asdict(result)
        payload["outcome"] = result.outcome.value
        payload["proof"] = result.proof.to_value()
        return payload

- [ ] **步骤 4：在不增加状态存储的前提下定义 journal 数据**

    @dataclass(frozen=True, slots=True)
    class AttemptJournalEntry:
        schema_version: int
        owner_token: str
        claim_token: str
        attempt_id: str
        fence: str
        project_id: str
        kind: str
        spec_path: str
        result_path: str
        pid: int | None
        process_identity: str | None
        containment_kind: str | None
        native_ref: str | None
        state: str
        started_at: float
        timeout_sec: float

Supervisor 把该对象存入 `reindex-worker-state.json`，并在状态输出中排除 `claim_token`。

- [ ] **步骤 5：运行绿灯测试并提交**

运行：`python -m pytest tests/test_reindex_attempt_protocol.py -q`

提交：

    git add codev_platform/reindex/attempts.py tests/test_reindex_attempt_protocol.py
    git commit -m "feat(reindex): 定义 attempt 协议与恢复日志"

---

### 任务 3：增加显式队列围栏、持久隔离和发布围栏

**文件：**
- 新建：`codev_platform/reindex/queue_ports.py`
- 新建：`codev_platform/reindex/file_queue.py`
- 修改：`codev_platform/reindex/queue.py`：模型和兼容性重新导出
- 修改：`codev_platform/reindex/pg_queue.py`：新增 quarantine 列和有界调用
- 新建：`tests/test_reindex_queue_contract.py`
- 修改：`tests/test_reindex_queue.py`
- 修改：`tests/test_pg_queue.py`

**接口：** File 和 PG 后端直接实现以下精确方法；旧的 `pending`/`complete`/`release`/`renew` 在一个发布周期内保留为薄包装。

    @dataclass(frozen=True, slots=True)
    class ClaimedJob:
        job: Job
        claim_token: str
        owner_token: str
        lease_expires_at: float


    @dataclass(frozen=True, slots=True)
    class QuarantineRecord:
        project_id: str
        kind: str
        claim_token: str
        attempt_id: str
        fence: str
        process_identity: str
        containment_kind: str
        native_ref: str
        reason: str
        quarantined_at: float


    class PublishPermit(Protocol):
        @property
        def superseded(self) -> bool:
            raise NotImplementedError("publish permit property")
        def ack(self) -> bool:
            raise NotImplementedError("publish permit method")


    class WorkerQueuePort(Protocol):
        def claim(self, *, owner_token: str, projects: set[str] | None, limit: int,
                  timeout_sec: float) -> list[ClaimedJob]:
            raise NotImplementedError("queue adapter method")
        def renew(self, claim: ClaimedJob, *, ttl_sec: float,
                  timeout_sec: float) -> bool:
            raise NotImplementedError("queue adapter method")
        def recover_owned(self, *, owner_token: str,
                          timeout_sec: float) -> list[ClaimedJob]:
            raise NotImplementedError("queue adapter method")
        def retry(self, claim: ClaimedJob, *, reason: str,
                  timeout_sec: float) -> bool:
            raise NotImplementedError("queue adapter method")
        def reject(self, claim: ClaimedJob, *, reason: str,
                   timeout_sec: float) -> bool:
            raise NotImplementedError("queue adapter method")
        def quarantine(self, claim: ClaimedJob, *, attempt_id: str, fence: str,
                       process_identity: str, containment_kind: str,
                       native_ref: str, reason: str,
                       timeout_sec: float) -> QuarantineRecord:
            raise NotImplementedError("queue adapter method")
        def begin_publish(self, claim: ClaimedJob, *, desired_revision: str,
                          timeout_sec: float) -> ContextManager[PublishPermit]:
            raise NotImplementedError("queue adapter method")


    class AdminQueuePort(Protocol):
        def clear_quarantine(self, record: QuarantineRecord, *,
                             death_proof: ConfirmedProcessDeath,
                             timeout_sec: float) -> bool:
            raise NotImplementedError("queue admin method")

`retry` 的最终契约不是把原记录留在队首：成功时必须在同一个 token-fenced 事务中更新重入时间与
排序序号到当前队尾。`reject` 是任务 7 接入 isolated 迁移时补充的 additive 方法；旧薄包装不得用
ack 或 quarantine 冒充 reject。File 与 PG 的契约夹具必须共享同一断言。

- [ ] **步骤 1：编写后端无关的状态测试**

为 File 和 PG 两类契约夹具增加：
- test_claim_binds_worker_incarnation_and_non_optional_token
- test_recover_owned_lists_active_claim_without_mutation
- test_renew_and_retry_require_matching_claim_token
- test_retry_atomically_moves_item_to_tail_and_updates_reentry_time
- test_reject_retires_only_matching_unstarted_claim_with_failure_audit
- test_quarantine_remains_non_claimable_after_lease_expiry
- test_clear_quarantine_requires_matching_confirmed_process_death
- test_dirty_pending_is_superseded_inside_publish_guard
- test_publish_guard_ack_preserves_enqueue_after_guard
- test_queue_operation_exceeding_timeout_fails_closed

File 后端的核心断言如下：

    def test_quarantine_requires_confirmed_death(tmp_path: Path) -> None:
        port = FileSpoolQueue(tmp_path / "queue")
        admin = port
        port.enqueue(
            "demo",
            "codegraph",
            JobMeta(source="test", target_commit="a" * 40),
        )
        claim = port.claim(
            owner_token="worker-1",
            projects=None,
            limit=1,
            timeout_sec=0.2,
        )[0]
        record = port.quarantine(
            claim,
            attempt_id="attempt-1",
            fence="fence-1",
            process_identity="pid:10:start:20",
            containment_kind="test",
            native_ref="fixture:10",
            reason="tree death unconfirmed",
            timeout_sec=0.2,
        )
        assert port.claim(
            owner_token="worker-2",
            projects=None,
            limit=1,
            timeout_sec=0.2,
        ) == []
        proof = ConfirmedProcessDeath(
            "pid:10:start:20",
            "test",
            time.time(),
            "fixture exited",
        )
        assert admin.clear_quarantine(record, death_proof=proof, timeout_sec=0.2)

- [ ] **步骤 2：运行红灯测试**

运行：`python -m pytest tests/test_reindex_queue_contract.py -q`

预期：缺少 queue port 和 quarantine API。

- [ ] **步骤 3：以增量方式实现 queue v2 存储**

File 后端使用受现有逐 key 锁保护的 `quarantined` 目录，并为 `QueueSnapshot` 增加 `quarantined` 分桶。PG 增加 `quarantine_reason`、`quarantine_attempt_id`、`quarantine_fence`、`quarantine_process_identity`、`quarantine_containment_kind`、`quarantine_native_ref` 和 `quarantine_at` 列；claim SQL 排除已 quarantine 的行。PG 对每次 worker 变更设置小于 `timeout_sec` 的 `statement_timeout` 和 `lock_timeout`。File 锁获取在同一 deadline 停止。

`begin_publish` 在 manifest 发布及 `permit.ack()` 全程持有 File key 锁或 PG 行事务。它原子检查活跃 `claim_token` 和待处理的期望版本。待处理版本与 `desired_revision` 不同表示 `permit.superseded=True`，且不得写 manifest；相同版本的重复待处理项继续留在 queue 中，但不使当前证明失效。

- [ ] **步骤 4：移除不安全的 isolated 恢复路径**

Isolated 启动不得调用 `reclaim_stale_own`。该旧 API 只保留在 `execution_mode=legacy` 后。`recover_owned` 只枚举精确匹配上一代 worker 的活跃 claim，不回收也不改变 lease；任务 7 在任何新 claim 之前把它们与 journal 对账。

- [ ] **步骤 5：验证并提交**

运行：`python -m pytest tests/test_reindex_queue_contract.py tests/test_reindex_queue.py tests/test_pg_queue.py -q`

提交：

    git add codev_platform/reindex/queue_ports.py codev_platform/reindex/file_queue.py codev_platform/reindex/queue.py codev_platform/reindex/pg_queue.py tests/test_reindex_queue_contract.py tests/test_reindex_queue.py tests/test_pg_queue.py
    git commit -m "feat(reindex): 增加持久隔离与发布围栏"

---

### 任务 4：把输入物化、Git、运行器和证明迁入执行器

**文件：**
- 新建：`codev_platform/reindex/spec_factory.py`
- 新建：`codev_platform/reindex/attempt_inputs.py`
- 新建：`codev_platform/reindex/attempt_cleanup.py`
- 新建：`codev_platform/reindex/executor.py`
- 新建：`codev_platform/reindex/executor_bootstrap.py`
- 新建：`tests/test_reindex_spec_factory.py`
- 新建：`tests/test_reindex_executor_strategy.py tests/test_reindex_executor_failures.py tests/test_reindex_executor_cli.py`
- 新建：`tests/test_reindex_attempt_cleanup.py`
- 修改：`codev_platform/reindex/worker.py`：迁出 `prepare_project_repos` 和依赖检查
- 修改：`codev_platform/reindex/git_sync.py`：保留由 executor 输入策略调用的窄 Git 同步入口
- 修改：`codev_platform/reindex/runners.py`：保留注册表和证明行为
- 修改：`codev_platform/reindex/runner_logs.py`：把受控子进程环境显式传给真实 runner
- 修改：`codev_platform/reindex/__init__.py`：lazy façade，避免 executor 间接加载 queue/worker
- 修改：`codev_platform/core/repos.py`：只保留 `RepoSpec` 与隔离仓向量的窄适配
- 新建：`codev_platform/core/repo_runtime_override.py`：冻结并跨 `exec` 传播无秘密仓向量
- 修改：`codev_platform/core/config.py`：只传播完整配置 SHA-256，并在子进程加载时验证
- 新建：`codev_platform/core/runtime_interpreter.py`：固定不可随 `current` 切换漂移的 venv 逻辑解释器，并提供跨 `exec` 版本复核
- 新建：`codev_platform/core/repo_input_guard.py`：绑定冻结主仓并只从 Git tracked 清单匹配索引文件
- 修改：`codev_platform/ops/reindex/commands.py`：在任何 stage 前复核项目、版本、配置、仓向量和 `--repo`，并拒绝为不完整 ingest 输出成功证明
- 修改：`codev_platform/graph/ingest.py`：聚合不含异常正文的组件失败事实，同时保持组件间失败隔离
- 修改：`codev_platform/chroma/_discover.py`：proven 模式禁绝绝对 external 路径且不执行文件系统 glob
- 新建：`codev_platform/chroma/collection_integrity.py`：分页精确证明 collection ID 集
- 新建：`codev_platform/chroma/document_manifest.py`、`codev_platform/chroma/index_input.py`：严格 manifest codec 与写前摘要复核
- 新建：`codev_platform/recall/code_vector_collection.py`：多仓采集叶子，proven 任一登记仓失败关闭
- 新建：`codev_platform/recall/code_vector_manifest.py`：统一限制 manifest 文件大小、条目数、ID 长度与实际 UTF-8 JSON 总字节数
- 新建：`codev_platform/reindex/runner_proof.py`：流式扫描未截断完整输出，不把截断日志当证明真值
- 修改：`codev_platform/chroma/indexer.py`、`codev_platform/recall/code_vector_store.py`：成功 marker 只能来自存储后验
- 新建：`tests/test_reindex_runtime_context.py`、`tests/test_reindex_proven_input_guard.py`
- 新建：`tests/test_collection_integrity.py`、`tests/test_chroma_indexer_proof.py`、`tests/test_code_vec_storage_proof.py`
- 新建：`tests/test_reindex_repo_override.py`、`tests/test_runner_proof.py`
- 修改：`tests/test_chroma_doctypes.py`：延迟导入 indexer，确保宿主配置隔离先建立
- 修改：`tests/test_code_vec_chunking.py`：覆盖多仓 ID 本地化与 proven 失败关闭
- 修改：`tests/test_reindex_pull.py`、`tests/test_reindex_runner_logs.py`、`tests/test_reindex_runner_timeout.py`：覆盖 executor 内 Git、完整流证明与超时映射
- 修改：`tests/test_graph_ingest.py`、`tests/test_call_resolvers.py`、`tests/test_graph_analyzers.py`、`tests/test_graph_api_usage.py`、`tests/test_reindex_ingest_stage.py`：覆盖结构化失败与 marker/返回码门禁

**接口：** 消费任务 2 的 `AttemptSpec`/`AttemptResult`、任务 3 的 `ClaimedJob` 以及 Plan C 任务 1-2 的 `RuntimeIdentity`。产出固定契约章节定义的唯一 `AttemptSpecFactory`、唯一 `AttemptInputSelector`、唯一 `AttemptCleanupRouter`，以及以下 executor 输入策略：

    @dataclass(frozen=True, slots=True)
    class MaterializedInput:
        root: str
        input_commits: tuple[tuple[str, str], ...]
        input_trees: tuple[tuple[str, str], ...]


    class AttemptInputStrategy(Protocol):
        def materialize(self, spec: AttemptSpec) -> MaterializedInput:
            raise NotImplementedError("input strategy method")
        def verify(self, spec: AttemptSpec,
                   materialized: MaterializedInput) -> CanonicalJsonObject:
            raise NotImplementedError("input strategy method")


    class AttemptInputError(RuntimeError):
        def __init__(self, outcome: AttemptOutcome, note: str,
                     retryable: bool) -> None:
            super().__init__(note)
            self.outcome = outcome
            self.note = note
            self.retryable = retryable


    def freeze_attempt_input_strategies(
        strategies: Mapping[str, AttemptInputStrategy],
    ) -> Mapping[str, AttemptInputStrategy]:
        allowed = {"configured", "exact_workspace"}
        if "configured" not in strategies or not set(strategies) <= allowed:
            raise ValueError("invalid fixed attempt input strategy mapping")
        return MappingProxyType(dict(strategies))


executor 函数签名固定为 `execute_attempt(spec: AttemptSpec, strategies: Mapping[str, AttemptInputStrategy]) -> AttemptResult`。`AttemptSpecFactory.create` 是生产代码中唯一允许调用 `AttemptSpec(...)` 的位置；原子解码器只负责重建已持久化对象，不是新的业务构造入口。

- [ ] **步骤 1：编写 factory、router、隔离和策略测试**

增加以下测试：
- test_attempt_spec_factory_is_only_production_constructor
- test_factory_uses_one_selector_and_cached_runtime_identity
- test_factory_rejects_missing_target_commit_or_nonpositive_timeout
- test_configured_selector_emits_only_allowlisted_payload_without_git_or_io
- test_cleanup_router_routes_by_input_kind_after_confirmed_death
- test_cleanup_router_unknown_input_kind_fails_closed
- test_configured_strategy_rejects_payload_remote_or_absolute_path
- test_materialize_git_runner_and_proof_execute_in_executor_pid
- test_materialize_timeout_is_reported_as_attempt_failure
- test_typed_input_error_maps_to_structured_attempt_result
- test_unexpected_input_exception_is_failed_and_non_retryable
- test_executor_has_no_queue_supervisor_manifest_or_health_import
- test_executor_result_uses_core_runtime_identity
- test_rc_two_is_retryable_and_rc_zero_requires_success_proof
- test_result_contains_actual_root_commit_and_tree_vectors
- test_executor_orders_materialize_runner_then_verify
- test_proof_contains_canonical_input_and_runner_subobjects
- test_configured_cleanup_is_noop_but_requires_confirmed_death

- [ ] **步骤 2：运行红灯测试**

运行：`python -m pytest tests/test_reindex_spec_factory.py tests/test_reindex_executor_strategy.py tests/test_reindex_executor_failures.py tests/test_reindex_executor_cli.py tests/test_reindex_attempt_cleanup.py -q`

预期：缺少 `spec_factory`、`attempt_inputs`、`attempt_cleanup` 和 `executor` 模块。

- [ ] **步骤 3：实现唯一 factory、selector 和 cleanup router**

在 `spec_factory.py` 中逐字实现固定契约章节的 `AttemptInputSelection`、`AttemptInputSelector`、`ConfiguredInputSelector` 和 `AttemptSpecFactory`。factory 只复制缓存的 `RuntimeIdentity.runtime_revision`，不得自行调用 `runtime_identity()`，也不得访问 Git、网络或文件系统。`tests/test_reindex_spec_factory.py` 以 AST 扫描 `codev_platform`：除 `spec_factory.py` 的 `AttemptSpecFactory.create` 和 `attempts.py` 的解码路径外，不得出现 `AttemptSpec(...)` 调用；并用记录型 selector 断言每次 `create` 恰好调用一次同一个 selector。

在 `attempt_cleanup.py` 中逐字实现固定契约章节的 `ConfiguredAttemptCleanup` 和 `AttemptCleanupRouter`。构造时复制传入映射并用 `MappingProxyType` 固定；空映射或未知 `input_kind` 按失败关闭处理。Router 不检查 queue、不查找 workspace，也不接受 `ConfirmedProcessDeath` 之外的替代证明。

- [ ] **步骤 4：实现 configured 策略和 executor**

把当前仓库准备、pull policy、Git 目标覆盖检查和依赖检查迁出 `worker.py`。`ConfiguredAttemptInputStrategy` 在构造函数中接收 `cfg`，只根据服务端配置和 `project_id` 解析项目仓库，并拒绝 payload 覆盖 remote 或任意 root 的尝试。所有 Git 命令都在 executor 进程内执行。`attempt_inputs.py` 只定义具体策略和 `freeze_attempt_input_strategies`，不得在模块内部实例化策略；子侧唯一 `executor_bootstrap.py` 创建 `ConfiguredAttemptInputStrategy(cfg)` 后，把 `{"configured": configured_strategy}` 交给冻结函数。父侧业务组合根不构造、也不尝试跨进程传递 strategy 实例。

configured strategy 在首次解析后把 root/tag/source-project 向量冻结到同一个 executor-local `cfg`。真实 runner 必须从已验证 `RuntimeIdentity.environment_prefix` 固定稳定 venv 逻辑解释器：解析父目录但保留 POSIX `bin/python` 最后一跳符号链接，禁止在 identity 通过后重新解析可切换的 `current`，也禁止解引用到丢失 venv 语义的系统 Python。runner 把该无秘密、限长、严格 schema 的仓向量显式传给下一层 CLI；下一层 `core.repos.project_repo_specs()` 优先读取它且遇到无效载荷失败关闭，禁止回退机器实时配置。每层 `exec` 都必须再次比较完整 runtime revision；隔离 CLI 在任何 stage 前要求 expected project、`PLATFORM_PROJECT_ID`、仓声明和冻结向量一致，并要求 `--repo` 精确等于冻结主仓。隔离 Chroma 只从冻结仓的 Git tracked 清单匹配 doc glob，禁止绝对 external 路径，禁止先遍历仓外文件系统再事后过滤。完整配置不跨 `exec` 复制；runner 只传规范配置的 SHA-256，下一层每次 `load_config()` 都要求摘要一致，机器配置中途变化立即失败关闭。不得把完整配置、DSN 或 token 写入 spec、result、临时文件或环境载荷。

executor 顺序固定为：读取并校验 spec；调用 `core.runtime_identity.runtime_identity` 并要求其 `runtime_revision` 等于 `spec.runtime_revision`；执行 `strategy.materialize`；执行已注册 runner；执行 `strategy.verify`；合并规范的 input 和 runner 证明子对象；原子写入 `AttemptResult`。它捕获 `AttemptInputError` 并复制其 `outcome`/`note`/`retryable` 字段；物化器的意外异常转换为 `FAILED`、不可重试并带有长度受限且已脱敏的 note。它绝不导入 Plan B 的 `WorkspaceError`，不写 manifest，也不接触 queue 状态。父进程使用以下命令启动：

runner 的成功 marker 必须来自存储后验而不是“命令返回 0”：Chroma/code_vec 复用分页完整性校验，只有 collection 的实际 ID 集与 manifest 期望 ID 集完全一致（数量相同但含 stale/missing ID 也失败）才算通过；full Chroma build 先切换 `current`，再原子落 reload stamp，最后输出唯一 `proof: chroma ok`。code_vec 删除 stale 节点失败或 ID 集后验失败都会撤销成功 manifest，只有后验通过后才允许既有 `proof: code_vec ok`。graph ingest 用 `IngestReport.failures` 聚合 plugin analyze、calls resolver、frontend API 归因源码/执行、analyzer context/analyze 的稳定 phase/component/code，禁止异常正文进入报告；`NOT_APPLICABLE` 不是失败。任一 failure 都禁止 `proof: ingest ok`，proven isolated 返回确定性 rc=1；legacy 直调为兼容可返回 rc=0，但必须显示“部分完成”且 runner 因 marker 缺失提升为失败。rc=2 只表示锁忙或数据库 busy，禁止用它重试确定性组件失败，以免重新形成无限 pending。runner 缺少对应 marker 时把 rc=0 提升为失败；Chroma 子进程还会在拿锁与索引前复核隔离运行版本，防止二次 exec 漂移。

Chroma 删除、打开、manifest codec、proven 摘要/读取/解码或扫描后文件漂移任一失败都撤销 manifest；full 空项目也必须创建并发布空 collection。code_vec 的 manifest schema、多仓图谱采集和 stale 删除同样失败关闭，proven 模式不允许跳过任一登记 extra 仓；持久化旧清单与内存中新清单都受 64 MiB、条目数、单 ID 长度和真实序列化总量约束，总量校验按 JSON 编码器分块计数，不复制整份清单。`runner_proof` 从子进程未截断原始字节流内存有界扫描，marker 必须是唯一独立完整行；forbidden pattern 跨块匹配，未知 policy、observer/reader 异常或 reader 未结束一律失败。截断脱敏日志只供 failure note，不再充当证明真值。

    python -m codev_platform.reindex.executor --spec SPEC_PATH --result RESULT_PATH

- [ ] **步骤 5：保持 Plan B 接缝收敛**

Plan B 只在父侧 `codev_platform/ops/reindex_queue.py` 把唯一 selector 换成能够选择 `exact_workspace` 的实现，并在子侧 `executor_bootstrap.py` 向同一个固定映射增加 `exact_workspace` 条目。它不得增加第二个注册表，也不得在 executor 核心或 Orchestrator 中增加类型分支。`ExactWorkspaceAttemptInputStrategy` 返回前以 `attempt_id` 记录 workspace lease；其 `verify` 方法在 containment 内完成 diff/cache/status 清洁度证明。在适配器边界，source-tip 不匹配通过 `AttemptInputError` 映射为 `SUPERSEDED`/不可重试，网络错误或缺少对象映射为 `RETRYABLE`/可重试，脏输入或不安全输入映射为 `FAILED`/不可重试。Plan B 向同一个 `AttemptCleanupRouter` 增加按 attempt-id ledger 清理的适配器，不替换 router，也不把清理逻辑注入 Orchestrator。

- [ ] **步骤 6：验证并提交**

运行：`python -m pytest tests/test_reindex_spec_factory.py tests/test_reindex_executor_strategy.py tests/test_reindex_executor_failures.py tests/test_reindex_executor_cli.py tests/test_reindex_repo_override.py tests/test_reindex_attempt_cleanup.py tests/test_reindex_runner_timeout.py tests/test_reindex_runner_logs.py tests/test_runner_proof.py tests/test_reindex_worker_affinity.py tests/test_reindex_pull.py tests/test_reindex_ingest_stage.py tests/test_reindex_runtime_context.py tests/test_reindex_proven_input_guard.py tests/test_collection_integrity.py tests/test_chroma_indexer_proof.py tests/test_chroma_doctypes.py tests/test_code_vec_chunking.py tests/test_code_vec_storage_proof.py tests/test_graph_ingest.py tests/test_call_resolvers.py tests/test_graph_analyzers.py tests/test_graph_api_usage.py -q`

提交：

    git add codev_platform/core/config.py codev_platform/core/repos.py codev_platform/core/repo_runtime_override.py codev_platform/core/runtime_interpreter.py codev_platform/core/repo_input_guard.py codev_platform/ops/reindex/commands.py codev_platform/graph/ingest.py codev_platform/chroma/_discover.py codev_platform/chroma/collection_integrity.py codev_platform/chroma/document_manifest.py codev_platform/chroma/index_input.py codev_platform/chroma/indexer.py codev_platform/recall/code_vector_collection.py codev_platform/recall/code_vector_manifest.py codev_platform/recall/code_vector_store.py codev_platform/reindex/__init__.py codev_platform/reindex/spec_factory.py codev_platform/reindex/attempt_inputs.py codev_platform/reindex/attempt_cleanup.py codev_platform/reindex/executor.py codev_platform/reindex/executor_bootstrap.py codev_platform/reindex/git_sync.py codev_platform/reindex/runner_logs.py codev_platform/reindex/runner_proof.py codev_platform/reindex/worker.py codev_platform/reindex/runners.py tests/test_reindex_spec_factory.py tests/reindex_executor_support.py tests/test_reindex_executor_strategy.py tests/test_reindex_executor_failures.py tests/test_reindex_executor_cli.py tests/test_reindex_repo_override.py tests/test_reindex_attempt_cleanup.py tests/test_reindex_pull.py tests/test_reindex_runner_logs.py tests/test_reindex_runner_timeout.py tests/test_reindex_ingest_stage.py tests/test_reindex_runtime_context.py tests/test_reindex_proven_input_guard.py tests/test_collection_integrity.py tests/test_chroma_indexer_proof.py tests/test_chroma_doctypes.py tests/test_code_vec_chunking.py tests/test_code_vec_storage_proof.py tests/test_runner_proof.py tests/test_graph_ingest.py tests/test_call_resolvers.py tests/test_graph_analyzers.py tests/test_graph_api_usage.py docs/plans/roadmap-2026-07-11/reindex-attempt-isolation-implementation-plan-2026-07-11.md docs/plans/roadmap-2026-07-11/reindex-exact-workspace-implementation-plan-2026-07-11.md
    git commit -m "refactor(reindex): 在隔离进程内完成输入与索引"

---
