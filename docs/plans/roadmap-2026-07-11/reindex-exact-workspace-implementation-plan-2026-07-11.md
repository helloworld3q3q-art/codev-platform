# 精确工作区重建索引实施计划

> **供智能体执行者使用：** 必须使用子技能 `superpowers:subagent-driven-development`（推荐）或 `superpowers:executing-plans`，逐任务实施本计划。各步骤使用复选框（`- [ ]`）跟踪状态。

**目标：** 让 webhook 与本地 post-commit 都在 executor 内物化可证明的精确提交 workspace，合法支持非祖先 force-push，并彻底禁止索引 live clone 的旧 HEAD、dirty 内容或错误 extra repo。

**架构：** 本计划只向计划 A 的固定 `AttemptInputStrategy` registry 注册 `exact_workspace`。父进程仅把无秘密、严格 allowlist 的 canonical JSON 写入 `AttemptSpec.input_payload`；executor 在 containment 内完成 materialize → runner → verify，`AttemptResult` 才输出最终 root/commit/tree/proof。父侧只在计划 A 提供 `ConfirmedProcessDeath` 后按 attempt ledger 清理；QUARANTINED 永久保留 workspace，直至持久 quarantine 被带 death proof 清除。

**技术栈：** Python 3.10+、Git plumbing/worktree、dataclass/Protocol、canonical JSON、原子 ledger、pytest 临时 bare remote、ruff。

## 全局约束

- 实施顺序固定为计划 C 任务 1–2 → 计划 A → 计划 B → 计划 C 任务 3–10。
- 本计划只消费计划 C 的 `core.runtime_identity.runtime_identity().runtime_revision`；禁止自行通过 Git、package metadata 或工作目录推导 runtime revision。
- 计划 A 必须先完成；本计划只消费以下固定类型，不修改其字段或 executor CLI 协议：
  - `CanonicalJsonObject.from_text(raw)` / `from_value(mapping)`；直接构造只接受位置参数 `raw`；
  - `AttemptSpec.input_kind: str` 与 `AttemptSpec.input_payload: CanonicalJsonObject`；
  - `AttemptResult.input_root/input_commits/input_trees` 与 `AttemptResult.proof: CanonicalJsonObject`；
  - `MaterializedInput(root, input_commits, input_trees)`；
  - `AttemptInputStrategy.materialize(spec)` 与 `AttemptInputStrategy.verify(spec, materialized)`；
  - `AttemptInputError(outcome, note, retryable)`；
  - `AttemptCleanupPort.release(spec, death: ConfirmedProcessDeath) -> CleanupReport`；
  - `validate_attempt_result(spec, result, *, claim, process_rc, validated_at) -> ValidatedAttemptResult`；
  - `WorkerQueuePort.begin_publish(claim, *, desired_revision, timeout_sec) -> ContextManager[PublishPermit]`；
  - `WorkerQueuePort.quarantine(claim, *, attempt_id, fence, process_identity, containment_kind, native_ref, reason, timeout_sec) -> QuarantineRecord`；
  - `AdminQueuePort.clear_quarantine(record, *, death_proof, timeout_sec) -> bool`。
- 唯一 `AttemptOrchestrator` 独占 executor 生命周期、lease/heartbeat、result validation、desired guard、publisher、ack/retry/quarantine；本计划不新增第二编排层。
- `ValidatedAttemptResult` 只能由计划 A 的 validator 构造；workspace、executor 和 publisher 不得自行构造。
- `PublishPermit` 的锁覆盖 `ResultPublisher.publish(validated)` 与 permit ack；本计划不另建 desired-revision guard。
- 不实现 runtime release、索引 active-generation、多 job 并发或通用 repository framework。
- live clone 只允许 `rev-parse`、`status`、`diff`、`ls-files` 等只读 Git 命令；禁止 pull、reset、checkout、clean、merge、switch、worktree add/remove。
- 所有 checkout 与删除只发生在校验后的 managed cache/workspace root；路径、attempt ID、repo ID 均不能直接采用 payload 文本。
- webhook payload 不能提供 remote URL、remote name、绝对路径、cache path 或 workspace path。
- trusted remote URL/ref 只从服务器配置读取；任何日志、异常、proof、ledger、workspace manifest 都不得持久化含凭据 URL。
- 按 Git `object-format` 只接受完整 40/64 位小写十六进制 OID；拒绝短 SHA、revision expression 和全零非删除目标。
- 不运行 repository hooks、submodule 或 LFS smudge；Git 命令使用参数数组、stdin DEVNULL、固定 timeout 和非交互环境。
- executor 返回前必须完成 post-run input proof；verify 失败作为 attempt 失败，不能发布 manifest。
- workspace lease 在 Git 操作前写入 attempt ledger；只有 `ConfirmedProcessDeath` 才能清理，QUARANTINED 不清理。
- File/PG `JobMeta` 只做 additive 扩展；`source: str = "legacy"` 默认保持不变。
- 所有 `codev_platform/reindex/workspace*.py` 文件均不超过 600 行。

---

## 固定的跨计划数据流

```text
webhook/post-commit 生产端
  -> JobMeta(完整 target OID, source_project_id, target_ref)
  -> AttemptSpec(input_kind="exact_workspace", input_payload=规范化 allowlist JSON)
  -> 唯一 AttemptOrchestrator.run(claim, spec)
  -> 受 containment 约束的 executor
       -> ExactWorkspaceAttemptInputStrategy.materialize(spec)
       -> 现有 runner(materialized.root, process-local 改写后的 cfg)
       -> ExactWorkspaceAttemptInputStrategy.verify(spec, materialized)
       -> AttemptResult(root, commit vector, tree vector, 规范化 input+runner proof)
  -> 计划 A validate_attempt_result(spec, result, claim, process_rc, validated_at)
  -> WorkerQueuePort.begin_publish(claim, desired_revision=spec.target_commit)
  -> ResultPublisher.publish(validated)
  -> permit 执行 ack
  -> AttemptCleanupPort.release(spec, ConfirmedProcessDeath)
```

如果终止流程无法证明进程死亡，计划 A 将写入 `QuarantineRecord`，不进入 publisher、不调用 cleanup，也不会因 lease 到期而使该 key 可再次领取。

## 受管目录布局

```text
<workspace_root>/<attempt_id>/
  ledger.json                 # 模式、状态与清理数据；权限模式为 0600
  workspace.json              # 不含秘密的规范化证明元数据
  repos/
    main/                     # 对应 AttemptResult.input_root
    <controlled_repo_id>/

<cache_root>/<remote_fingerprint>.git/
  refs/codev/fetched/<controlled_ref_id>
  refs/codev/pins/<attempt_id>/<controlled_repo_id>
```

`workspace.json` 只存储相对 repo 路径、commit/tree OID、已配置的 remote name、不含凭据的 remote fingerprint 和已验证 ref。它绝不存储 remote URL、用户名、密码、token、DSN 或 live clone 绝对路径。

## 文件职责表

| 文件 | 单一职责 |
|---|---|
| `codev_platform/reindex/workspace.py` | 不可变 workspace 契约与错误码 |
| `codev_platform/reindex/workspace_manifest.py` | 规范化 payload/manifest schema 与路径安全 codec |
| `codev_platform/reindex/workspace_resolution.py` | 受信配置与 repo 映射到 `WorkspaceSpec` |
| `codev_platform/reindex/workspace_ledger.py` | attempt ledger 状态与原子持久化 |
| `codev_platform/reindex/workspace_gc.py` | 已确认死亡后的清理与路径围栏 |
| `codev_platform/reindex/workspace_content.py` | 运行前后 Git cleanliness proof |
| `codev_platform/reindex/workspace_local.py` | 从本地受信 object DB 建立受管 object cache/worktree |
| `codev_platform/reindex/workspace_git.py` | 从受信 remote 获取精确 OID、建立 pin 与 detached worktree |
| `codev_platform/reindex/workspace_input.py` | `exact_workspace` strategy 与 process-local cfg 改写 |
| `codev_platform/reindex/workspace_spec_factory.py` | 供既有 `AttemptSpecFactory` 使用的唯一 exact/configured selector |
| `codev_platform/core/repo_runtime_override.py` | 计划 A 提供的冻结仓向量状态与严格跨进程 codec，Plan B 只调用窄适配 |
| `codev_platform/core/runtime_interpreter.py` | 计划 A 提供的稳定 venv 解释器选择与逐层运行版本门禁 |
| `codev_platform/core/repo_input_guard.py` | 计划 A 提供的主仓、tracked 输入与安全 glob 门禁，exact workspace 必须继续复用 |
| `tests/fixtures/git_repos.py` | 真实临时 bare/local/force-push fixture |

### 任务 1：定义精确工作区契约

**文件：**
- 新建：`codev_platform/reindex/workspace.py`
- 新建：`tests/test_reindex_workspace_contract.py`

**接口：**
- 使用：stdlib `dataclass`、`Enum`、`Path`、`Protocol`。
- 产出：`WorkspaceErrorCode`、`WorkspaceError`、`TrustedRemote`、`WorkspaceRepoSpec`、`WorkspaceSpec`、`WorkspaceRepoLease`、`WorkspaceLease`、`WorkspaceProvider`。

- [ ] **步骤 1：编写不可变模型测试**

```python
def test_workspace_lease_vectors_are_main_first_and_immutable(tmp_path: Path) -> None:
    extra = WorkspaceRepoLease(
        repo_id="child", source_project_id="child-proj", is_main=False,
        root=tmp_path / "repos" / "child", commit_sha="c" * 40,
        tree_sha="d" * 40, object_format="sha1",
        cache_git_dir=tmp_path / "cache" / "child.git",
        pin_ref="refs/codev/pins/a-1/child",
    )
    main = WorkspaceRepoLease(
        repo_id="main", source_project_id="demo", is_main=True,
        root=tmp_path / "repos" / "main", commit_sha="a" * 40,
        tree_sha="b" * 40, object_format="sha1",
        cache_git_dir=tmp_path / "cache" / "main.git",
        pin_ref="refs/codev/pins/a-1/main",
    )
    lease = WorkspaceLease(
        project_id="demo", attempt_id="a-1", lease_id="l-1",
        bundle_root=tmp_path, cleanup_token="secret-for-test",
        repos=(extra, main), created_at=1.0,
    )
    assert lease.root == main.root
    assert lease.commit_vector == (("main", "a" * 40), ("child", "c" * 40))
    assert lease.tree_vector == (("main", "b" * 40), ("child", "d" * 40))
```

按上述示例使用显式 constructor，并补充以下具名测试：

- `test_workspace_lease_rejects_duplicate_repo_id`
- `test_workspace_lease_requires_exactly_one_main`
- `test_workspace_spec_requires_source_repo_target_match`
- `test_trusted_remote_repr_redacts_url`
- `test_workspace_error_preserves_code_and_retryable`

- [ ] **步骤 2：运行契约红灯测试集**

运行：`python -m pytest tests/test_reindex_workspace_contract.py -q`

预期：以 `ModuleNotFoundError: codev_platform.reindex.workspace` 失败。

- [ ] **步骤 3：实现具体契约**

```python
class WorkspaceErrorCode(str, Enum):
    INVALID_OID = "invalid_oid"
    INVALID_REF = "invalid_ref"
    DIRTY_SOURCE = "dirty_source"
    SOURCE_MISMATCH = "source_mismatch"
    OBJECT_UNAVAILABLE = "object_unavailable"
    SUPERSEDED = "superseded"
    WORKSPACE_MISMATCH = "workspace_mismatch"
    UNSAFE_PATH = "unsafe_path"
    GIT_FAILED = "git_failed"


class WorkspaceError(RuntimeError):
    def __init__(self, code: WorkspaceErrorCode, message: str, *, retryable: bool) -> None:
        super().__init__(message)
        self.code = code
        self.retryable = retryable


@dataclass(frozen=True, slots=True)
class TrustedRemote:
    name: str
    ref: str
    fingerprint: str
    url: str = field(repr=False, compare=False)


@dataclass(frozen=True, slots=True)
class WorkspaceRepoSpec:
    repo_id: str
    source_project_id: str | None
    local_root: Path
    is_main: bool
    target_commit: str | None
    remote: TrustedRemote | None


@dataclass(frozen=True, slots=True)
class WorkspaceSpec:
    project_id: str
    attempt_id: str
    source: str
    source_repo_id: str
    target_commit: str
    target_ref: str | None
    repos: tuple[WorkspaceRepoSpec, ...]


@dataclass(frozen=True, slots=True)
class WorkspaceRepoLease:
    repo_id: str
    source_project_id: str | None
    is_main: bool
    root: Path
    commit_sha: str
    tree_sha: str
    object_format: Literal["sha1", "sha256"]
    cache_git_dir: Path | None
    pin_ref: str | None


@dataclass(frozen=True, slots=True)
class WorkspaceLease:
    project_id: str
    attempt_id: str
    lease_id: str
    bundle_root: Path
    cleanup_token: str = field(repr=False, compare=False)
    repos: tuple[WorkspaceRepoLease, ...]
    created_at: float

    @property
    def root(self) -> Path:
        mains = tuple(repo for repo in self.repos if repo.is_main)
        if len(mains) != 1:
            raise ValueError("workspace lease requires exactly one main repo")
        return mains[0].root

    @property
    def commit_vector(self) -> tuple[tuple[str, str], ...]:
        ordered = sorted(self.repos, key=lambda repo: (not repo.is_main, repo.repo_id))
        return tuple((repo.repo_id, repo.commit_sha) for repo in ordered)

    @property
    def tree_vector(self) -> tuple[tuple[str, str], ...]:
        ordered = sorted(self.repos, key=lambda repo: (not repo.is_main, repo.repo_id))
        return tuple((repo.repo_id, repo.tree_sha) for repo in ordered)


@runtime_checkable
class WorkspaceProvider(Protocol):
    def materialize(self, spec: WorkspaceSpec) -> WorkspaceLease:
        raise NotImplementedError
```

`WorkspaceSpec.__post_init__` 验证受控 repo ID 唯一、恰有一个 main repo、恰有一个 source repo，并要求 `source_repo.target_commit == WorkspaceSpec.target_commit`。

- [ ] **步骤 4：验证并提交**

运行：`python -m pytest tests/test_reindex_workspace_contract.py -q`

```powershell
git add codev_platform/reindex/workspace.py tests/test_reindex_workspace_contract.py
git commit -m "feat(reindex): 定义精确 workspace 契约"
```

### 任务 2：保留生产端版本身份

**文件：**
- 修改：`codev_platform/webhook/providers.py:19-24,73-120`
- 修改：`codev_platform/webhook/server.py:135-156`
- 修改：`codev_platform/reindex/queue.py:28-35,154-184`
- 修改：`codev_platform/reindex/pg_queue.py:44-69`
- 修改：`codev_platform/ops/reindex/dispatch.py:82-91`
- 修改：`tests/test_webhook_body_limit.py`
- 修改：`tests/test_reindex_queue.py`
- 修改：`tests/test_pg_queue.py`
- 修改：`tests/test_reindex_ingest_stage.py`

**接口：**
- 产出：`PushEvent.ref`、`PushEvent.deleted`、以 additive 方式扩展的 `JobMeta.source_project_id/target_ref`，以及 local-hook 的完整 target OID。

- [ ] **步骤 1：编写生产端红灯测试**

```python
def test_job_meta_additive_defaults_remain_legacy() -> None:
    meta = JobMeta()
    assert meta.source == "legacy"
    assert meta.source_project_id is None
    assert meta.target_ref is None


def test_gitea_delete_accepts_sha1_and_sha256_zero_oid() -> None:
    provider = GiteaProvider()
    for zero in ("0" * 40, "0" * 64):
        event = provider.parse(
            {"X-Gitea-Event": "push"},
            {"after": zero, "ref": "refs/heads/dev", "repository": {"full_name": "org/repo"}},
        )
        assert event is not None
        assert event.deleted is True
        assert event.target_commit is None
```

本步骤还需实现以下精确断言：

- `test_force_push_enqueues_full_target_ref_and_source_project_id`
- `test_deleted_ref_never_enqueues_workspace_attempt`
- `test_local_hook_enqueues_resolved_full_oid_not_head`
- `test_local_hook_git_head_failure_is_fail_closed`
- `test_file_and_pg_legacy_meta_without_new_keys_round_trip`

- [ ] **步骤 2：运行生产端红灯测试集**

运行：

```powershell
python -m pytest tests/test_webhook_body_limit.py tests/test_reindex_queue.py tests/test_pg_queue.py tests/test_reindex_ingest_stage.py -q
```

预期：因字段缺失、删除事件行为和本地 `target_commit="HEAD"` 而失败。

- [ ] **步骤 3：保持 `JobMeta` 增量兼容，并在入队时解析本地 OID**

```python
@dataclass(frozen=True)
class JobMeta:
    source: str = "legacy"
    pull_policy: str | None = None
    target_commit: str | None = None
    source_project_id: str | None = None
    target_ref: str | None = None
```

本地 dispatch 路径在 `enqueue` 前调用有界、只读的 `repo_head(repo)`。OID 缺失或不完整时返回失败的 dispatch 结果且不写 queue 记录，绝不写入符号字符串 `HEAD`。

Webhook 删除事件返回明确的 skipped 响应且不 enqueue。非删除 webhook 事件必须先验证 target 是 40/64 位小写十六进制，再执行 enqueue；object-format 匹配仍由 executor provider 负责。

- [ ] **步骤 4：验证并提交**

运行：

```powershell
python -m pytest tests/test_webhook_body_limit.py tests/test_reindex_queue.py tests/test_pg_queue.py tests/test_reindex_ingest_stage.py -q
```

```powershell
git add codev_platform/webhook/providers.py codev_platform/webhook/server.py codev_platform/reindex/queue.py codev_platform/reindex/pg_queue.py codev_platform/ops/reindex/dispatch.py tests/test_webhook_body_limit.py tests/test_reindex_queue.py tests/test_pg_queue.py tests/test_reindex_ingest_stage.py
git commit -m "feat(reindex): 固定生产端目标提交身份"
```

### 任务 3：定义无秘密输入载荷与工作区清单

**文件：**
- 新建：`codev_platform/reindex/workspace_manifest.py`
- 新建：`codev_platform/reindex/workspace_resolution.py`
- 修改：`codev_platform/core/repos.py:27-45,310-345`
- 修改：`codev_platform/ops/health/_checks.py`
- 修改：`codev_platform/reindex/status.py`
- 新建：`tests/test_reindex_workspace_manifest.py`
- 新建：`tests/test_reindex_workspace_resolution.py`
- 新建：`tests/test_reindex_workspace_config.py`

**接口：**
- 产出：`WorkspaceManifest`、`canonical_json_text(value)`、`canonical_workspace_payload(*, source, source_project_id, target_ref)`、`parse_workspace_payload(payload)`、`write_workspace_manifest(lease)`、`read_workspace_manifest(path) -> WorkspaceManifest`、`resolve_workspace_spec(spec, request, cfg)`、`workspace_config_issues(cfg, project_ids)`。

- [ ] **步骤 1：编写严格的输入载荷与配置测试**

```python
def test_payload_is_canonical_and_contains_no_remote_or_path() -> None:
    payload = canonical_workspace_payload(
        source="webhook",
        source_project_id="child-proj",
        target_ref="refs/heads/dev",
    )
    assert payload.text == (
        '{"schema_version":1,"source":"webhook",'
        '"source_project_id":"child-proj","target_ref":"refs/heads/dev"}'
    )
    assert "remote" not in payload.text
    assert "repo_path" not in payload.text


@pytest.mark.parametrize("extra_key", ["remote_url", "repo_path", "cache_root", "workspace_root"])
def test_payload_rejects_non_allowlisted_key(extra_key: str) -> None:
    text = json.dumps({
        "schema_version": 1,
        "source": "webhook",
        "source_project_id": "demo",
        "target_ref": "refs/heads/dev",
        extra_key: "forbidden",
    }, sort_keys=True, separators=(",", ":"))
    with pytest.raises(WorkspaceError) as exc:
        parse_workspace_payload(CanonicalJsonObject.from_text(text))
    assert exc.value.code is WorkspaceErrorCode.SOURCE_MISMATCH
```

本步骤还需实现以下精确断言：

- `test_webhook_requires_configured_reindex_remote_url_and_ref`
- `test_event_ref_must_equal_configured_ref`
- `test_literal_extra_repo_without_source_project_id_is_rejected_for_webhook`
- `test_main_repo_id_is_main_and_extra_ids_are_stable`
- `test_workspace_manifest_paths_are_relative_and_contained`
- `test_workspace_manifest_contains_remote_fingerprint_not_url`
- `test_workspace_config_issues_reports_missing_remote_url_key_without_value`
- `test_workspace_config_issues_reports_missing_or_invalid_ref`
- `test_workspace_config_issues_never_exposes_userinfo_or_fake_token`
- `test_workspace_config_issues_is_empty_for_complete_exact_config`
- `test_health_and_reindex_status_fail_readiness_before_first_webhook`

- [ ] **步骤 2：运行结构定义红灯测试**

运行：

```powershell
python -m pytest tests/test_reindex_workspace_manifest.py tests/test_reindex_workspace_resolution.py tests/test_reindex_workspace_config.py -q
```

预期：因 manifest 与 resolver 模块尚不存在而失败。

- [ ] **步骤 3：实现精确结构定义与配置契约**

Payload allowlist 固定为：

```python
WORKSPACE_PAYLOAD_KEYS = frozenset({
    "schema_version", "source", "source_project_id", "target_ref",
})
```

受信 server 配置 key 固定为：

```text
projects.<source_project_id>.reindex_remote_url   # webhook 模式必填
projects.<source_project_id>.reindex_ref          # 必填，采用完整 refs/heads/name
projects.<source_project_id>.reindex_remote_name  # 可选，默认值为 origin
```

`reindex_remote_url` 只在受 containment 约束的 executor 内加载。`TrustedRemote.url` 仅以 `repr=False` 留在内存；manifest/ledger 只存储 `remote_name`、不含凭据的 `remote_fingerprint` 和 ref。

配置诊断必须是纯函数且不含秘密：

```python
@dataclass(frozen=True, slots=True)
class WorkspaceConfigIssue:
    project_id: str
    key: str
    reason: str


def valid_heads_ref(ref: str) -> bool:
    suffix = ref.removeprefix("refs/heads/")
    forbidden = ("..", "@{", "\\", " ", "~", "^", ":", "?", "*", "[")
    return (
        ref.startswith("refs/heads/")
        and bool(suffix)
        and not suffix.startswith("/")
        and not suffix.endswith(("/", ".", ".lock"))
        and not any(token in suffix for token in forbidden)
    )


def _validate_project_workspace_config(
    cfg: dict,
    project_id: str,
) -> tuple[WorkspaceConfigIssue, ...]:
    project = (cfg.get("projects") or {}).get(project_id) or {}
    issues: list[WorkspaceConfigIssue] = []
    if not str(project.get("reindex_remote_url") or "").strip():
        issues.append(WorkspaceConfigIssue(project_id, "reindex_remote_url", "missing"))
    ref = str(project.get("reindex_ref") or "").strip()
    if not ref:
        issues.append(WorkspaceConfigIssue(project_id, "reindex_ref", "missing"))
    elif not valid_heads_ref(ref):
        issues.append(WorkspaceConfigIssue(project_id, "reindex_ref", "invalid"))
    return tuple(issues)


def workspace_config_issues(
    cfg: dict,
    project_ids: Collection[str],
) -> tuple[WorkspaceConfigIssue, ...]:
    return tuple(
        issue
        for project_id in project_ids
        for issue in _validate_project_workspace_config(cfg, project_id)
    )
```

Issue 字段只包含 project ID、配置 key 名和固定 reason code，绝不包含配置值。Health 与 reindex status 在检查 queue 是否为空之前调用此函数。Exact server 模式将任一 issue 视为 readiness failure 并拒绝 claim；development 模式仅报告 WARN，不打印 URL。

Workspace manifest schema 固定为：

```json
{"attempt_id":"a-1","lease_id":"l-1","main_repo_id":"main","project_id":"demo","repos":[{"commit_sha":"aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa","is_main":true,"object_format":"sha1","remote_fingerprint":"sha256:example","remote_name":"origin","remote_ref":"refs/heads/dev","repo_id":"main","root":"repos/main","source_project_id":"demo","tree_sha":"bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb"}],"schema_version":1,"source_repo_id":"main","target_commit":"aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa"}
```

生产 writer 从模型字段创建并规范化此对象；上述 literal 只是使用假值的 schema 示例，不是可复用 fixture。

- [ ] **步骤 4：验证并提交**

运行：

```powershell
python -m pytest tests/test_reindex_workspace_manifest.py tests/test_reindex_workspace_resolution.py tests/test_reindex_workspace_config.py -q
```

```powershell
git add codev_platform/reindex/workspace_manifest.py codev_platform/reindex/workspace_resolution.py codev_platform/core/repos.py codev_platform/ops/health/_checks.py codev_platform/reindex/status.py tests/test_reindex_workspace_manifest.py tests/test_reindex_workspace_resolution.py tests/test_reindex_workspace_config.py
git commit -m "feat(reindex): 定义无秘密 workspace 输入协议"
```

### 任务 4：增加尝试账本与已确认死亡后的清理

**文件：**
- 新建：`codev_platform/reindex/workspace_ledger.py`
- 新建：`codev_platform/reindex/workspace_gc.py`
- 新建：`tests/test_reindex_workspace_ledger.py`
- 新建：`tests/test_reindex_workspace_gc.py`

**接口：**
- 产出：`WorkspaceLedgerRecord`、`WorkspaceLedger.begin/update/seal/remove/read/list`、`ExactWorkspaceCleanupPort.release(spec, death)`。
- 使用：计划 A 的 `AttemptCleanupPort`、`ConfirmedProcessDeath`、`CleanupReport`。

- [ ] **步骤 1：编写账本状态与路径围栏测试**

```python
def test_ledger_round_trip_keeps_cleanup_token_out_of_repr(tmp_path: Path) -> None:
    ledger = WorkspaceLedger(tmp_path / "ledger")
    record = WorkspaceLedgerRecord(
        schema_version=1,
        attempt_id="a-1",
        project_id="demo",
        state="materializing",
        bundle_root=tmp_path / "workspaces" / "a-1",
        cleanup_token="secret-for-test",
        repos=(),
        created_at=1.0,
        updated_at=1.0,
    )
    ledger.begin(record)
    loaded = ledger.read("a-1")
    assert loaded == record
    assert "secret-for-test" not in repr(loaded)
```

本步骤还需实现以下精确断言：

- `test_cleanup_requires_confirmed_process_death`
- `test_quarantined_attempt_keeps_sealed_ledger_without_cleanup`
- `test_ledger_begin_precedes_first_git_mutation`
- `test_ledger_atomic_write_survives_partial_temp_file`
- `test_cleanup_rejects_workspace_path_escape`
- `test_cleanup_rejects_cache_path_escape`
- `test_cleanup_removes_worktree_before_pin_then_prunes`
- `test_cleanup_is_idempotent_after_confirmed_death`
- `test_cleanup_does_not_delete_ledger_when_git_deletion_is_uncertain`

- [ ] **步骤 2：运行账本红灯测试集**

运行：

```powershell
python -m pytest tests/test_reindex_workspace_ledger.py tests/test_reindex_workspace_gc.py -q
```

预期：因 ledger 与 cleanup 模块尚不存在而失败。

- [ ] **步骤 3：实现精确的账本状态与 API**

```python
@dataclass(frozen=True, slots=True)
class WorkspaceLedgerRecord:
    schema_version: int
    attempt_id: str
    project_id: str
    state: Literal["materializing", "active", "sealed", "release_pending"]
    bundle_root: Path
    cleanup_token: str = field(repr=False, compare=False)
    repos: tuple[WorkspaceRepoLease, ...]
    created_at: float
    updated_at: float


class WorkspaceLedger:
    def begin(self, record: WorkspaceLedgerRecord) -> None:
        self._write_atomic(record, require_absent=True)

    def read(self, attempt_id: str) -> WorkspaceLedgerRecord:
        return self._read_validated(attempt_id)

    def update(self, record: WorkspaceLedgerRecord) -> None:
        self._write_atomic(record, require_absent=False)

    def seal(self, attempt_id: str, repos: tuple[WorkspaceRepoLease, ...]) -> None:
        self._seal_validated(attempt_id, repos)

    def list(self) -> tuple[WorkspaceLedgerRecord, ...]:
        return self._list_validated()

    def remove(self, attempt_id: str, cleanup_token: str) -> None:
        self._remove_validated(attempt_id, cleanup_token)
```

在支持的平台上，ledger 文件权限模式为 0600。它只存储 remote name/fingerprint/ref，不存储 remote URL。写入顺序固定为同目录 temp → flush → fsync → `os.replace`；第一条 ledger 记录必须在 clone/fetch/pin/worktree 命令之前持久化。

Cleanup 顺序固定。对每个 repo，只有通过 containment 与 token 检查后才能构造破坏性命令数组：

```python
remove_worktree = [
    "git", "--git-dir", str(repo.cache_git_dir),
    "worktree", "remove", "--force", str(repo.root),
]
delete_pin = [
    "git", "--git-dir", str(repo.cache_git_dir),
    "update-ref", "-d", str(repo.pin_ref), repo.commit_sha,
]
prune = [
    "git", "--git-dir", str(repo.cache_git_dir),
    "worktree", "prune", "--expire", "now",
]
```

1. 要求存在 `ConfirmedProcessDeath` 且 attempt ID 匹配；
2. 验证 ledger schema、cleanup token 与解析后的 root；
3. 运行 `remove_worktree`；
4. 运行 `delete_pin`；
5. 运行 `prune`；
6. 删除空 bundle 目录；
7. 仅在确认所有必需步骤后删除 ledger。

- [ ] **步骤 4：验证并提交**

运行：

```powershell
python -m pytest tests/test_reindex_workspace_ledger.py tests/test_reindex_workspace_gc.py -q
```

```powershell
git add codev_platform/reindex/workspace_ledger.py codev_platform/reindex/workspace_gc.py tests/test_reindex_workspace_ledger.py tests/test_reindex_workspace_gc.py
git commit -m "feat(reindex): 持久化并安全清理 workspace 租约"
```

### 任务 5：不读取运行中脏内容并物化本地提交对象

**文件：**
- 新建：`codev_platform/reindex/workspace_content.py`
- 新建：`codev_platform/reindex/workspace_local.py`
- 新建：`tests/fixtures/__init__.py`
- 新建：`tests/fixtures/git_repos.py`
- 新建：`tests/test_reindex_workspace_content.py`
- 新建：`tests/test_reindex_workspace_local.py`

**接口：**
- 产出：`LocalWorkspaceProvider(cache_root, workspace_root, hooks_root, ledger, git_runner, timeout_sec)`、`materialize(spec)`、`verify_workspace_content(lease) -> dict[str, object]`。

- [ ] **步骤 1：编写精确本地对象测试**

`tests/fixtures/git_repos.py` 首先定义以下完整且仅用于本地的 helper：

```python
def git(cwd: Path, *args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["git", *args], cwd=cwd, check=True, capture_output=True,
        text=True, encoding="utf-8", errors="replace", timeout=20,
    )


def git_text(cwd: Path, *args: str) -> str:
    return git(cwd, *args).stdout.strip()


def init_repo(path: Path, branch: str = "dev") -> None:
    path.mkdir(parents=True)
    git(path, "init", "-b", branch)
    git(path, "config", "user.name", "Workspace Test")
    git(path, "config", "user.email", "workspace@example.invalid")


def commit_text(repo: Path, relative: str, text: str) -> str:
    (repo / relative).write_text(text, encoding="utf-8")
    git(repo, "add", "--", relative)
    git(repo, "commit", "-m", f"write {relative}")
    return git_text(repo, "rev-parse", "HEAD")


@pytest.fixture
def clean_git_repo(tmp_path: Path) -> Path:
    repo = tmp_path / "source"
    init_repo(repo)
    commit_text(repo, "tracked.txt", "committed")
    return repo


def dirty_live_repo(repo: Path, mode: str) -> None:
    if mode == "tracked":
        (repo / "tracked.txt").write_text("tracked dirty", encoding="utf-8")
    elif mode == "staged":
        (repo / "tracked.txt").write_text("staged dirty", encoding="utf-8")
        git(repo, "add", "--", "tracked.txt")
    elif mode == "untracked":
        (repo / "untracked.txt").write_text("untracked dirty", encoding="utf-8")
    else:
        raise ValueError(f"unsupported dirty mode: {mode}")
```

```python
@pytest.mark.parametrize("dirty_mode", ["tracked", "staged", "untracked"])
def test_local_provider_ignores_live_dirty_content(clean_git_repo: Path, dirty_mode: str) -> None:
    target = git_text(clean_git_repo, "rev-parse", "HEAD")
    dirty_live_repo(clean_git_repo, dirty_mode)
    before_status = git_text(clean_git_repo, "status", "--porcelain=v1", "--untracked-files=all")
    repo = WorkspaceRepoSpec(
        repo_id="main", source_project_id="demo", local_root=clean_git_repo,
        is_main=True, target_commit=target, remote=None,
    )
    spec = WorkspaceSpec(
        project_id="demo", attempt_id="a-1", source="local_hook",
        source_repo_id="main", target_commit=target, target_ref=None,
        repos=(repo,),
    )
    provider = LocalWorkspaceProvider(
        cache_root=clean_git_repo.parent / "cache",
        workspace_root=clean_git_repo.parent / "workspaces",
        hooks_root=clean_git_repo.parent / "empty-hooks",
        ledger=WorkspaceLedger(clean_git_repo.parent / "ledger"),
        git_runner=run_tree,
        timeout_sec=20,
    )
    lease = provider.materialize(spec)
    assert (lease.root / "tracked.txt").read_text(encoding="utf-8") == "committed"
    assert git_text(clean_git_repo, "status", "--porcelain=v1", "--untracked-files=all") == before_status
```

`tests/fixtures/git_repos.py` 定义 `git`、`git_text`、`init_repo`、`commit_text`、`dirty_live_repo` 和 `clean_git_repo`；任务 6 在同一文件中补充 force-push graph。

本步骤还需实现以下精确断言：

- `test_local_provider_materializes_target_even_when_live_head_advanced_to_descendant`
- `test_local_provider_rejects_missing_or_noncommit_target_object`
- `test_local_provider_uses_bare_clone_not_live_worktree_add`
- `test_local_provider_leaves_live_head_status_and_file_hashes_unchanged`
- `test_post_run_tracked_diff_fails_content_proof`
- `test_post_run_staged_diff_fails_content_proof`
- `test_post_run_untracked_file_outside_codegraph_allowlist_fails_proof`
- `test_codegraph_metadata_is_the_only_untracked_allowlist`

- [ ] **步骤 2：运行本地红灯测试集**

运行：

```powershell
python -m pytest tests/test_reindex_workspace_content.py tests/test_reindex_workspace_local.py -q
```

预期：因 content 与 local provider 模块尚不存在而失败。

- [ ] **步骤 3：只解析已入队对象，并建立受管本地缓存**

允许对 live clone 执行的命令仅有：

```text
git -C <live> rev-parse --show-object-format
git -C <live> rev-parse --verify <queued-full-oid>^{commit}
git -C <live> rev-parse --verify <queued-full-oid>^{tree}
git -C <live> cat-file -e <queued-full-oid>^{commit}
```

Provider 绝不检查或复制 working-tree 文件，允许存在 tracked、staged 和 untracked 的 live 变更。已证明入队完整 OID 是受信本地 object DB 中的 commit 后，provider 使用以下命令创建受管 bare cache：

```text
git clone --bare --no-hardlinks -- <trusted-local-path> <managed-cache>
```

后续所有 pin/worktree 操作都以受管 cache 为目标，绝不作用于 live clone。Detached workspace 从入队 OID 创建，而不是从当前 HEAD 创建。运行前后 proof 只检查受管 workspace：HEAD/tree 必须精确、tracked/index diff 必须为空，untracked path 仅允许 `.codegraph/`。

- [ ] **步骤 4：验证并提交**

运行：

```powershell
python -m pytest tests/test_reindex_workspace_content.py tests/test_reindex_workspace_local.py -q
```

```powershell
git add codev_platform/reindex/workspace_content.py codev_platform/reindex/workspace_local.py tests/fixtures/__init__.py tests/fixtures/git_repos.py tests/test_reindex_workspace_content.py tests/test_reindex_workspace_local.py
git commit -m "feat(reindex): 隔离本地精确提交输入"
```

### 任务 6：物化受信远端工作树并脱敏凭据

**文件：**
- 新建：`codev_platform/reindex/workspace_git.py`
- 修改：`tests/fixtures/git_repos.py`
- 新建：`tests/test_reindex_workspace_git.py`

**接口：**
- 产出：`GitWorktreeProvider.materialize(spec)`、`redact_remote_url(text)`、`remote_fingerprint(url)`。

- [ ] **步骤 1：创建真实的非祖先强制推送测试夹具**

```python
@dataclass(frozen=True, slots=True)
class ForcePushRepo:
    remote: Path
    live: Path
    old_sha: str
    new_sha: str
    new_tree: str


def force_push_repo(tmp_path: Path) -> ForcePushRepo:
    remote = tmp_path / "remote.git"
    old_src = tmp_path / "old-src"
    rewrite = tmp_path / "rewrite"
    live = tmp_path / "live"
    git(tmp_path, "init", "--bare", str(remote))
    init_repo(old_src, branch="dev")
    commit_text(old_src, "version.txt", "A1")
    commit_text(old_src, "version.txt", "A2")
    git(old_src, "remote", "add", "origin", str(remote))
    git(old_src, "push", "-u", "origin", "dev")
    git(tmp_path, "clone", "--branch", "dev", str(remote), str(live))
    old_sha = git_text(live, "rev-parse", "HEAD")
    (live / "version.txt").write_text("local dirty", encoding="utf-8")
    init_repo(rewrite, branch="dev")
    commit_text(rewrite, "version.txt", "B1")
    commit_text(rewrite, "version.txt", "B2")
    git(rewrite, "remote", "add", "origin", str(remote))
    git(rewrite, "push", "--force", "origin", "dev")
    new_sha = git_text(rewrite, "rev-parse", "HEAD")
    new_tree = git_text(rewrite, "rev-parse", "HEAD^{tree}")
    return ForcePushRepo(remote, live, old_sha, new_sha, new_tree)
```

同一 fixture 模块使用参数数组和仅供测试的路径定义 `git`、`git_text`、`init_repo` 与 `commit_text`，绝不接触已配置或真实的 remote。

- [ ] **步骤 2：编写安全、脱敏与精确性测试**

```python
def test_remote_userinfo_is_redacted_from_error_log_and_workspace_json(tmp_path: Path, caplog) -> None:
    fake_url = "https://example.invalid/reference"
    redacted = redact_remote_url(f"fetch failed: {fake_url}")
    assert redacted == "fetch failed: https://example.invalid/reference"
    manifest = {
        "remote_name": "origin",
        "remote_fingerprint": remote_fingerprint(fake_url),
        "remote_ref": "refs/heads/dev",
    }
    encoded = canonical_json_text(manifest)
    assert "fake-token" not in encoded
    assert "alice:fake-token" not in encoded
```

本步骤还需实现以下精确断言：

- `test_nonancestor_force_push_materializes_exact_new_sha_without_mutating_live_clone`
- `test_oid_length_39_41_63_65_and_revision_expression_are_rejected_before_git`
- `test_sha1_and_sha256_oid_must_match_object_format`
- `test_invalid_or_non_heads_ref_is_rejected_before_fetch`
- `test_existing_cache_remote_mismatch_is_fail_closed_and_redacted`
- `test_fetched_tip_different_from_target_is_superseded`
- `test_missing_object_or_network_failure_is_retryable_without_head_fallback`
- `test_pin_survives_remote_ref_deletion_until_confirmed_cleanup`
- `test_post_checkout_hook_submodule_and_lfs_smudge_are_not_executed`
- `test_workspace_and_ledger_contain_fingerprint_but_no_remote_url`

- [ ] **步骤 3：运行 Git 红灯测试集**

运行：`python -m pytest tests/test_reindex_workspace_git.py -q`

预期：因 Git provider 与 fixture 尚不存在而失败。

- [ ] **步骤 4：实现精确参数数组并拆分 Git 环境**

凭据脱敏与持久化身份使用不同函数：

```python
_URL_USERINFO_RE = re.compile(r"(?P<scheme>[A-Za-z][A-Za-z0-9+.-]*://)[^/@\\s]+@")


def redact_remote_url(text: str) -> str:
    return _URL_USERINFO_RE.sub(r"\g<scheme>***@", text)


def remote_fingerprint(url: str) -> str:
    identity = _URL_USERINFO_RE.sub(r"\g<scheme>", url.strip())
    return "sha256:" + hashlib.sha256(identity.encode("utf-8")).hexdigest()
```

原始 URL 只用于 subprocess 参数与内存中的相等性检查。构造 `WorkspaceError` 前，必须脱敏异常消息和有界 stderr。

Fetch 环境保留已配置的 credential helper，但禁止交互：

```python
def fetch_env() -> dict[str, str]:
    env = os.environ.copy()
    env.update({
        "GIT_TERMINAL_PROMPT": "0",
        "GCM_INTERACTIVE": "never",
        "SSH_ASKPASS_REQUIRE": "never",
    })
    return env
```

Checkout 环境不得加载 system/global filter 或 LFS smudge：

```python
def checkout_env() -> dict[str, str]:
    env = fetch_env()
    env.update({
        "GIT_CONFIG_NOSYSTEM": "1",
        "GIT_CONFIG_GLOBAL": os.devnull,
        "GIT_LFS_SKIP_SMUDGE": "1",
    })
    return env
```

新 cache 使用以下精确命令数组：

```python
fetched_ref = f"refs/codev/fetched/{controlled_ref_id}"
pin_ref = f"refs/codev/pins/{controlled_attempt_id}/{controlled_repo_id}"
commands = (
    ["git", "clone", "--bare", "--no-tags", "--", trusted.url, str(cache)],
    ["git", "--git-dir", str(cache), "remote", "get-url", "--all", trusted.name],
    ["git", "check-ref-format", trusted.ref],
    ["git", "--git-dir", str(cache), "fetch", "--force", "--no-tags",
     "--no-recurse-submodules", trusted.name, f"+{trusted.ref}:{fetched_ref}"],
    ["git", "--git-dir", str(cache), "rev-parse", "--show-object-format"],
    ["git", "--git-dir", str(cache), "rev-parse", "--verify", f"{fetched_ref}^{{commit}}"],
    ["git", "--git-dir", str(cache), "cat-file", "-e", f"{target_oid}^{{commit}}"],
    ["git", "--git-dir", str(cache), "update-ref", pin_ref, target_oid, "0" * len(target_oid)],
    ["git", "-c", f"core.hooksPath={hooks_root}", "--git-dir", str(cache),
     "worktree", "add", "--detach", str(worktree), pin_ref],
    ["git", "-C", str(worktree), "rev-parse", "--verify", "HEAD"],
    ["git", "-C", str(worktree), "rev-parse", "--verify", "HEAD^{tree}"],
)
```

`--force` 只能更新计算得到的 `fetched_ref`。`repo_id`、attempt ID 和 ref ID 都是经过验证或 hash 的受控值。绝不记录原始命令数组与原始 stderr；所有异常 note 都必须经过凭据脱敏与有界裁剪。

对 source repo，fetched tip 必须等于 `AttemptSpec.target_commit`；tip 不同则返回 `SUPERSEDED`。其他 project repo 将其已配置 ref tip 固定到同一 lease vector。

- [ ] **步骤 5：验证并提交**

运行：`python -m pytest tests/test_reindex_workspace_git.py -q`

```powershell
git add codev_platform/reindex/workspace_git.py tests/fixtures/git_repos.py tests/test_reindex_workspace_git.py
git commit -m "feat(reindex): 从可信远端物化精确 worktree"
```

### 任务 7：注册精确输入策略并改写执行器配置

**文件：**
- 新建：`codev_platform/reindex/workspace_input.py`
- 使用但不修改：计划 A 位于 `codev_platform/reindex/attempt_inputs.py` 的 `freeze_attempt_input_strategies(strategies)`
- 修改：`codev_platform/core/repos.py`
- 新建：`tests/test_reindex_workspace_input.py`
- 新建：`tests/test_reindex_workspace_multirepo.py`
- 修改：`tests/test_reindex_executor_strategy.py tests/test_reindex_executor_failures.py tests/test_reindex_executor_cli.py`

**接口：**
- 使用：计划 A 的 `AttemptInputStrategy`、`MaterializedInput`、`CanonicalJsonObject`、`freeze_attempt_input_strategies(strategies)`。
- 产出：`ExactWorkspaceAttemptInputStrategy.materialize/verify`、registry key `exact_workspace`、process-local 精确 repo override。

- [ ] **步骤 1：编写输入载荷、执行顺序与多 repo 测试**

```python
def test_exact_strategy_returns_plan_a_materialized_input(strategy, exact_spec) -> None:
    materialized = strategy.materialize(exact_spec)
    assert isinstance(materialized, MaterializedInput)
    assert materialized.root.endswith("/repos/main")
    assert materialized.input_commits == (("main", "a" * 40),)
    assert materialized.input_trees == (("main", "b" * 40),)


def test_executor_order_is_materialize_runner_verify(executor_components, exact_spec) -> None:
    executor_components.execute(exact_spec)
    assert executor_components.events == ["materialize", "runner", "verify"]
```

本步骤还需实现以下精确断言：

- `test_payload_unknown_key_fails_before_provider_or_git`
- `test_payload_cannot_override_remote_ref_url_or_paths`
- `test_verify_failure_returns_failed_attempt_and_no_success_proof`
- `test_verify_timeout_remains_inside_executor_deadline`
- `test_extra_repo_runner_uses_managed_child_not_config_or_meta_live_child`
- `test_runtime_exact_repo_override_suppresses_literal_meta_extra_repo`
- `test_result_proof_contains_canonical_input_and_runner_objects`
- `test_exact_strategy_does_not_probe_runtime_git_or_package_metadata`
- `test_source_tip_mismatch_maps_to_superseded_attempt_result`
- `test_network_or_missing_object_maps_to_retryable_attempt_result`
- `test_dirty_or_unsafe_workspace_maps_to_failed_attempt_result`
- `test_executor_does_not_import_workspace_error`

- [ ] **步骤 2：运行策略红灯测试集**

运行：

```powershell
python -m pytest tests/test_reindex_workspace_input.py tests/test_reindex_workspace_multirepo.py -q
```

预期：因 exact strategy 尚不存在且唯一 composition root 尚未向冻结映射加入 `exact_workspace` 而失败。

- [ ] **步骤 3：实现唯一固定策略与进程内 cfg 改写**

```python
class ExactWorkspaceAttemptInputStrategy:
    def __init__(self, cfg: dict, local: WorkspaceProvider, remote: WorkspaceProvider) -> None:
        self._cfg = cfg
        self._local = local
        self._remote = remote
        self._leases: dict[str, WorkspaceLease] = {}

    def materialize(self, spec: AttemptSpec) -> MaterializedInput:
        try:
            request = parse_workspace_payload(spec.input_payload)
            workspace_spec = resolve_workspace_spec(spec, request, self._cfg)
            provider = self._remote if request.source == "webhook" else self._local
            lease = provider.materialize(workspace_spec)
            rewrite_process_cfg(self._cfg, lease)
            write_workspace_manifest(lease)
        except WorkspaceError as exc:
            raise map_workspace_error(exc) from None
        self._leases[spec.attempt_id] = lease
        return MaterializedInput(
            root=str(lease.root),
            input_commits=lease.commit_vector,
            input_trees=lease.tree_vector,
        )

    def verify(self, spec: AttemptSpec, materialized: MaterializedInput) -> CanonicalJsonObject:
        try:
            lease = self._leases[spec.attempt_id]
            proof = verify_workspace_content(lease)
        except WorkspaceError as exc:
            raise map_workspace_error(exc) from None
        return CanonicalJsonObject.from_text(canonical_json_text(proof))
```

Adapter 映射固定且穷尽：

```python
def map_workspace_error(exc: WorkspaceError) -> AttemptInputError:
    if exc.code is WorkspaceErrorCode.SUPERSEDED:
        return AttemptInputError(AttemptOutcome.SUPERSEDED, str(exc), False)
    if exc.code is WorkspaceErrorCode.OBJECT_UNAVAILABLE:
        return AttemptInputError(AttemptOutcome.RETRYABLE, str(exc), True)
    return AttemptInputError(AttemptOutcome.FAILED, str(exc), False)
```

`workspace_input.py` 导入计划 A 的 `AttemptInputError`；executor 只导入并捕获 `AttemptInputError`，绝不接触计划 B 的 `WorkspaceError`。

`attempt_inputs.py` 保持计划 A 定义，不构造任何具体 strategy；它只用 `freeze_attempt_input_strategies(strategies)` 验证 key 并返回不可变映射。父侧 `codev_platform/ops/reindex_queue.py` 仍只组合 selector、cleanup 与 Orchestrator；子侧唯一 `codev_platform/reindex/executor_bootstrap.py` 在 executor 进程内实例化 `ConfiguredAttemptInputStrategy`、`ExactWorkspaceAttemptInputStrategy`、provider 与 ledger，再把两个实例交给冻结函数。Executor 核心只消费冻结映射，不包含 strategy 类型分支。禁止跨 `exec` 传 Python 实例、pickle、entry point、import string、由 payload 选择的 class name 或动态 plugin。

`rewrite_process_cfg` 必须原地、事务式更新 bootstrap 注入的同一个 executor-local cfg 对象，并通过计划 A 由 `core.repos` 窄适配的 `install_runtime_repo_override` 写入包含所有已租用 repo root/tag/source project 的私有 runtime override；不得重新绑定只有 strategy 可见的新字典。状态与严格 codec 的真值位于 `core.repo_runtime_override`。`core.repos.project_repo_specs()` 在读取 machine config 或 committed meta extra 之前返回该精确 override。真实 runner 复用计划 A 从 `RuntimeIdentity.environment_prefix` 固定的稳定 venv 逻辑解释器，并把同一无秘密仓向量通过严格 schema 的受控环境载荷传给下一层 CLI；子进程无效载荷必须失败关闭，绝不回退 live machine config。每层 `exec` 继续复核运行版本和 project，`--repo` 必须精确等于 workspace 主仓；Chroma 只从 workspace 仓向量的 Git tracked 清单匹配安全 glob，禁止绝对 external 路径和仓外遍历。完整 cfg、DSN 和 token 不跨 `exec` 复制；只传播规范配置 SHA-256，下一层 `load_config()` 必须验证机器配置仍与 executor 快照相同。

计划 B 继续复用计划 A 的 ingest 完整性契约：组件失败只以不含异常正文的 phase/component/code 进入 `IngestReport.failures`；任一 failure 都禁止成功 marker，proven isolated 映射为确定性 rc=1，rc=2 仍只保留给锁忙或数据库 busy。exact workspace 不得另建 failure codec、marker 规则或重试分支。

计划 A 的 executor 顺序保持固定：

```python
materialized = strategy.materialize(spec)
runner_result = run_runner(spec, materialized, cfg)
input_proof = strategy.verify(spec, materialized)
proof = canonical_proof(input=input_proof, runner=runner_result.proof)
```

任何 materialize/runner/verify 异常或 timeout 都转换为 `AttemptResult`，绝不逃逸到外层 queue terminal path。

- [ ] **步骤 4：验证并提交**

运行：

```powershell
python -m pytest tests/test_reindex_workspace_input.py tests/test_reindex_workspace_multirepo.py tests/test_reindex_executor_strategy.py tests/test_reindex_executor_failures.py tests/test_reindex_executor_cli.py -q
```

```powershell
git add codev_platform/reindex/workspace_input.py codev_platform/core/repos.py tests/test_reindex_workspace_input.py tests/test_reindex_workspace_multirepo.py tests/reindex_executor_support.py tests/test_reindex_executor_strategy.py tests/test_reindex_executor_failures.py tests/test_reindex_executor_cli.py
git commit -m "feat(reindex): 注册精确 workspace 输入策略"
```

### 任务 8：接入清理、期望版本围栏与工作区发布上下文

**文件：**
- 新建：`codev_platform/reindex/workspace_spec_factory.py`
- 修改：计划 A 位于 `codev_platform/reindex/attempt_cleanup.py` 的 cleanup router
- 修改：唯一 composition root `codev_platform/ops/reindex_queue.py`
- 修改：`codev_platform/reindex/result_publisher.py`
- 修改：`codev_platform/reindex/workspace_gc.py`
- 新建：`tests/test_reindex_workspace_lifecycle.py`
- 修改：`tests/test_reindex_result_publisher.py`
- 修改：`tests/test_reindex_orchestrator.py`
- 修改：`tests/test_reindex_spec_factory.py`
- 修改：`tests/test_reindex_attempt_cleanup.py`
- 修改：`tests/test_reindex_composition_root.py`

**接口：**
- 使用：`AttemptOrchestrator.run(claim, spec)`、`ValidatedAttemptResult`、`WorkerQueuePort.begin_publish`、持久化 quarantine、`ConfirmedProcessDeath`。
- 产出：`ExactWorkspaceCleanupPort`、`WorkspacePublishContext`、publisher context loader；不新增 Attempt 字段，也不新增外层 materialize/terminal handler。

`workspace_spec_factory.py` 产出纯函数式的 `ExactWorkspaceInputSelector.select(claim) -> AttemptInputSelection`。计划 A 现有 spec factory 仍是唯一的 `AttemptSpec` constructor。Selector 将 `JobMeta.source in {"webhook", "post_commit"}` 映射为 `input_kind="exact_workspace"` 与 canonical payload `{schema_version, source, source_project_id, target_ref}`；legacy/manual source 仍映射为 `configured`。它绝不执行 Git 或文件系统 mutation。

- [ ] **步骤 1：编写单一 `AttemptOrchestrator` 生命周期测试**

生命周期测试模块定义唯一 `LifecycleHarness` dataclass，包含具体的 recording queue、process、publisher、quarantine 与 cleanup port。其成功断言固定为：

```python
assert harness.events == [
    "materialize", "runner", "verify", "confirmed-dead",
    "validate-result", "begin-publish", "publish", "permit-ack", "cleanup",
]
```

未确认死亡的断言要求：`QuarantineRecord` 已持久化、sealed ledger 仍存在，且没有 `publish`、`permit-ack` 与 `cleanup` 事件。

本步骤还需实现以下精确断言：

- `test_materialize_unavailable_is_orchestrator_retry_not_outer_exception`
- `test_materialize_superseded_is_orchestrator_superseded_without_publish`
- `test_new_pending_revision_blocks_old_publish_under_publish_permit`
- `test_publish_permit_holds_file_key_lock_through_publish_and_ack`
- `test_publish_permit_holds_pg_row_transaction_through_publish_and_ack`
- `test_clear_quarantine_with_death_proof_then_runs_cleanup_once`
- `test_clear_quarantine_without_death_proof_keeps_workspace`
- `test_webhook_job_reaches_exact_workspace_spec_through_production_composition`
- `test_post_commit_job_reaches_exact_workspace_spec_with_full_oid`
- `test_legacy_manual_job_remains_configured`
- `test_unmaterialized_failed_exact_result_publishes_without_context_and_acks`
- `test_exact_selector_feeds_existing_attempt_spec_factory_once`
- `test_only_ops_reindex_queue_instantiates_strategies_and_freezes_mapping`

- [ ] **步骤 2：编写不扩展 Attempt 类型的发布上下文测试**

```python
@dataclass(frozen=True, slots=True)
class WorkspacePublishContext:
    attempt_id: str
    lease_id: str
    target_commit: str
    input_commits: tuple[tuple[str, str], ...]
    input_trees: tuple[tuple[str, str], ...]
    manifest_sha256: str


def workspace_context_from_validated(
    validated: ValidatedAttemptResult,
) -> WorkspacePublishContext | None:
    if validated.result.outcome is not AttemptOutcome.SUCCEEDED:
        return None
    if not validated.result.input_root:
        raise WorkspaceError(
            WorkspaceErrorCode.WORKSPACE_MISMATCH,
            "successful exact attempt has no input root",
            retryable=False,
        )
    manifest = read_manifest_adjacent_to_input_root(validated.result.input_root)
    require_manifest_matches_validated(manifest, validated)
    return WorkspacePublishContext(
        attempt_id=validated.spec.attempt_id,
        lease_id=manifest.lease_id,
        target_commit=validated.spec.target_commit,
        input_commits=validated.result.input_commits,
        input_trees=validated.result.input_trees,
        manifest_sha256=manifest.sha256,
    )


def read_manifest_adjacent_to_input_root(input_root: str) -> WorkspaceManifest:
    root = Path(input_root).resolve()
    manifest_path = root.parent.parent / "workspace.json"
    manifest = read_workspace_manifest(manifest_path)
    if (manifest_path.parent / "repos" / "main").resolve() != root:
        raise WorkspaceError(WorkspaceErrorCode.UNSAFE_PATH, "input root is not managed main", retryable=False)
    return manifest


def require_manifest_matches_validated(
    manifest: WorkspaceManifest,
    validated: ValidatedAttemptResult,
) -> None:
    if manifest.attempt_id != validated.spec.attempt_id:
        raise WorkspaceError(WorkspaceErrorCode.WORKSPACE_MISMATCH, "attempt mismatch", retryable=False)
    if manifest.target_commit != validated.spec.target_commit:
        raise WorkspaceError(WorkspaceErrorCode.WORKSPACE_MISMATCH, "target mismatch", retryable=False)
    if manifest.commit_vector != validated.result.input_commits:
        raise WorkspaceError(WorkspaceErrorCode.WORKSPACE_MISMATCH, "commit vector mismatch", retryable=False)
    if manifest.tree_vector != validated.result.input_trees:
        raise WorkspaceError(WorkspaceErrorCode.WORKSPACE_MISMATCH, "tree vector mismatch", retryable=False)
```

`ResultPublisher.publish(validated)` 保持计划 A 的 signature。Publisher 通过 constructor injection 接收 `workspace_context_from_validated`，且只在 `validated.spec.input_kind == "exact_workspace"` 时调用。物化前产生的 non-retryable FAILED result 具有空 `input_root/vector`，不返回 workspace context，写入常规有界 failure proof 后可以 ack；它不会仅因 `workspace.json` 尚不存在而循环。SUCCEEDED 的 exact result 必须具有 context，manifest 缺失或不匹配时禁止 publish/ack。Publisher 不查询 queue，也不构造 `ValidatedAttemptResult`。

- [ ] **步骤 3：运行生命周期红灯测试集**

运行：

```powershell
python -m pytest tests/test_reindex_workspace_lifecycle.py tests/test_reindex_result_publisher.py tests/test_reindex_orchestrator.py tests/test_reindex_spec_factory.py tests/test_reindex_attempt_cleanup.py tests/test_reindex_composition_root.py -q
```

预期：因 exact strategy 的 cleanup/context 接线缺失而失败。

- [ ] **步骤 4：只接入计划 A 的固定端口**

两个按进程分离的 composition root 负责构造：

- 子侧 `executor_bootstrap.py` 中的 `ConfiguredAttemptInputStrategy` 与 `ExactWorkspaceAttemptInputStrategy` 两个具体 strategy 实例；
- 子侧由 `freeze_attempt_input_strategies` 返回的固定 `configured`/`exact_workspace` 映射；
- 唯一计划 A `AttemptCleanupRouter`，显式包含 `configured` 与 `exact_workspace` route；
- 注入 `ExactWorkspaceInputSelector` 的计划 A spec factory；
- 唯一 Orchestrator，使用计划 A 的 queue、process backend、validator、publisher 与 quarantine store。

子侧唯一启动 composition root 中的 strategy 接线固定为：

```python
configured_strategy = ConfiguredAttemptInputStrategy(cfg)
exact_strategy = ExactWorkspaceAttemptInputStrategy(
    cfg,
    local_provider,
    git_provider,
)
strategies = freeze_attempt_input_strategies({
    "configured": configured_strategy,
    "exact_workspace": exact_strategy,
})
```

生产代码 AST 测试要求两个具体 strategy 的实例化与 `freeze_attempt_input_strategies(...)` 调用都只出现在 `codev_platform/reindex/executor_bootstrap.py`；父侧业务对象仍只在 `codev_platform/ops/reindex_queue.py` 组合。`attempt_inputs.py` 只保留定义与冻结函数，executor 核心只接收映射。

`AttemptCleanupRouter` 只在计划 A 的 `attempt_cleanup.py` 定义一次，并作为唯一 `AttemptCleanupPort` 注入 Orchestrator。`ExactWorkspaceCleanupPort` 只位于 `workspace_gc.py`；`workspace_input.py` 不持有 cleanup 行为。

不存在外层 provider 调用。Workspace materialization error 都转换为 executor result，不存在外层 ack/retry/publish/quarantine 分支。只有 Orchestrator 在取得 `ConfirmedProcessDeath` 后才能调用 cleanup；quarantined attempt 保留其 sealed ledger。计划 A 的 `AdminQueuePort.clear_quarantine(record, death_proof=proof, timeout_sec=timeout)` 成功后，同一 cleanup port 携带已确认 proof 执行一次。

Desired revision 固定为 `spec.target_commit`。计划 A 的 `begin_publish` 原子检查 active claim token 与较新的 pending desired revision，并在 publisher 与 permit ack 全程持有 File key lock 或 PG row transaction。

- [ ] **步骤 5：验证并提交**

运行：

```powershell
python -m pytest tests/test_reindex_workspace_lifecycle.py tests/test_reindex_result_publisher.py tests/test_reindex_orchestrator.py tests/test_reindex_spec_factory.py tests/test_reindex_attempt_cleanup.py tests/test_reindex_composition_root.py -q
```

```powershell
git add codev_platform/reindex/workspace_spec_factory.py codev_platform/reindex/attempt_cleanup.py codev_platform/ops/reindex_queue.py codev_platform/reindex/result_publisher.py codev_platform/reindex/workspace_gc.py tests/test_reindex_workspace_lifecycle.py tests/test_reindex_result_publisher.py tests/test_reindex_orchestrator.py tests/test_reindex_spec_factory.py tests/test_reindex_attempt_cleanup.py tests/test_reindex_composition_root.py
git commit -m "refactor(reindex): 接入单一编排器 workspace 生命周期"
```

### 任务 9：持久化精确清单证明并运行强制推送端到端测试

**文件：**
- 修改：`codev_platform/index_manifest.py:30-80,137-154,204-270`
- 修改：`tests/test_index_manifest.py`
- 新建：`tests/test_reindex_workspace_e2e.py`
- 修改：`tests/test_reindex_pull.py`
- 修改：`docs/plans/roadmap-2026-07-11/README.md`

**接口：**
- 使用：`ValidatedAttemptResult` 与 `WorkspacePublishContext`。
- 产出：additive 精确 workspace manifest schema 与显式精确 dependency check。

- [ ] **步骤 1：编写精确清单结构测试**

```python
def test_exact_workspace_record_requires_commit_and_tree_vectors() -> None:
    record = BuildRecord(
        project_id="demo", kind="code_vec", git_commit="a" * 40,
        target_commit="a" * 40,
        repo_commits_json='{"main":"aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa"}',
        input_trees_json='{"main":"bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb"}',
        attempt_id="a-1", workspace_lease_id="l-1",
        workspace_manifest_sha256="c" * 64, workspace_proof_version=1,
    )
    assert exact_workspace_record_valid(record)
```

本步骤还需实现以下精确断言：

- `test_exact_dependency_rejects_descendant_commit_vector`
- `test_exact_dependency_rejects_same_commit_different_tree_vector`
- `test_legacy_record_keeps_ancestor_freshness_without_exact_mode`
- `test_workspace_manifest_failure_prevents_publish_and_ack`
- `test_publisher_rejects_validated_result_when_workspace_manifest_mismatches`
- `test_manifest_and_logs_never_contain_remote_userinfo_or_fake_token`

- [ ] **步骤 2：运行清单红灯测试集**

运行：`python -m pytest tests/test_index_manifest.py tests/test_reindex_workspace_e2e.py -q`

预期：因精确 proof column 与 E2E case 尚不存在而失败。

- [ ] **步骤 3：增加显式增量兼容结构**

复用计划 A 现有的 `attempt_id` 与 `input_trees_json` column 作为唯一真值。只新增以下 nullable column 与对应 `BuildRecord` 字段：

```text
workspace_lease_id             TEXT
workspace_manifest_sha256      TEXT
workspace_proof_version        INTEGER
```

增加 migration/codec 测试，证明 exact row 读写计划 A 引入的同一组 `attempt_id/input_trees_json` 字段；schema、model、SQL 或 compatibility alias 中都不得出现 `workspace_attempt_id` 与 `repo_trees_json`。

Exact mode 的判定条件是 `workspace_proof_version == 1`，不能仅凭字段存在就选中。Exact dependency readiness 要求 canonical commit vector 与 tree vector 相等。`workspace_proof_version IS NULL` 的 legacy row 保持现有 ancestor freshness 行为。

Publisher 只能从 `ValidatedAttemptResult` 与已验证的相邻 `WorkspacePublishContext` 取值，不持久化 input root、remote URL、credential、cleanup token 或 live path 绝对路径。

- [ ] **步骤 4：增加真实端到端验收用例**

`tests/test_reindex_workspace_e2e.py` 只使用临时本地 Git remote，并包含以下测试：

- `test_nonancestor_force_push_indexes_new_tree_and_keeps_old_live_clone_unchanged`
- `test_extra_repo_force_push_indexes_managed_child_not_live_child`
- `test_new_push_during_attempt_supersedes_old_result_without_manifest_write`
- `test_out_of_order_old_webhook_is_superseded_by_current_remote_tip`
- `test_deleted_ref_never_materializes_or_falls_back_to_head`
- `test_missing_target_object_retries_without_runner_or_manifest`
- `test_verify_detects_runner_source_mutation_and_blocks_publish`
- `test_unconfirmed_process_death_keeps_claim_quarantine_and_workspace`
- `test_confirmed_death_cleanup_removes_worktree_pin_then_ledger`
- `test_fake_userinfo_remote_is_redacted_from_logs_manifest_and_ledger`

- [ ] **步骤 5：运行计划 B 的目标、兼容性与静态门禁**

```powershell
python -m pytest tests/test_reindex_workspace_contract.py tests/test_reindex_workspace_manifest.py tests/test_reindex_workspace_resolution.py tests/test_reindex_workspace_config.py tests/test_reindex_workspace_ledger.py tests/test_reindex_workspace_gc.py tests/test_reindex_workspace_content.py tests/test_reindex_workspace_local.py tests/test_reindex_workspace_git.py tests/test_reindex_workspace_input.py tests/test_reindex_workspace_multirepo.py tests/test_reindex_workspace_lifecycle.py tests/test_reindex_workspace_e2e.py -q
python -m pytest tests/test_reindex_executor_strategy.py tests/test_reindex_executor_failures.py tests/test_reindex_executor_cli.py tests/test_reindex_orchestrator.py tests/test_reindex_result_publisher.py tests/test_reindex_queue.py tests/test_pg_queue.py tests/test_webhook_body_limit.py tests/test_reindex_ingest_stage.py tests/test_index_manifest.py tests/test_reindex_pull.py -q
python -m ruff check codev_platform/reindex codev_platform/webhook codev_platform/ops/reindex/dispatch.py codev_platform/index_manifest.py tests/test_reindex_workspace_contract.py tests/test_reindex_workspace_manifest.py tests/test_reindex_workspace_resolution.py tests/test_reindex_workspace_config.py tests/test_reindex_workspace_ledger.py tests/test_reindex_workspace_gc.py tests/test_reindex_workspace_content.py tests/test_reindex_workspace_local.py tests/test_reindex_workspace_git.py tests/test_reindex_workspace_input.py tests/test_reindex_workspace_multirepo.py tests/test_reindex_workspace_lifecycle.py tests/test_reindex_workspace_e2e.py
codev-platform health
codev-platform reindex-queue status
```

Workspace 源文件行数门禁：

```powershell
Get-ChildItem codev_platform/reindex/workspace*.py | ForEach-Object {
  $lines=(Get-Content -LiteralPath $_.FullName -Encoding UTF8).Count
  if ($lines -gt 600) { throw "$($_.Name) has $lines lines" }
}
```

计划 A 字段消费门禁：

```powershell
@'
from dataclasses import fields
from codev_platform.reindex.attempts import AttemptSpec, AttemptResult

spec_fields = {field.name for field in fields(AttemptSpec)}
result_fields = {field.name for field in fields(AttemptResult)}
assert {"input_kind", "input_payload"} <= spec_fields
assert "input_root" not in spec_fields
assert {"input_root", "input_commits", "input_trees", "proof"} <= result_fields
'@ | python -
```

预期：所有 pytest 命令 PASS，ruff 退出码为 0，每个 workspace 源文件不超过 600 行，计划 A seam 断言通过，且两个运维命令在 cutover 前均报告 exact-workspace 配置 issue 为零。

- [ ] **步骤 6：提交**

```powershell
git add codev_platform/index_manifest.py tests/test_index_manifest.py tests/test_reindex_workspace_e2e.py tests/test_reindex_pull.py docs/plans/roadmap-2026-07-11/README.md
git commit -m "fix(reindex): 发布精确 workspace 提交证明"
```

## 计划 B 退出条件

- 非祖先 force-push 物化并索引精确新 SHA；旧 WSL live clone 的 HEAD、dirty 状态和文件 hash 完全不变。
- webhook/local producer 均在 enqueue 时固定完整 OID；local provider 从受信 object DB 物化该 OID，允许 live HEAD 后移及 tracked/staged/untracked 修改，但绝不读取这些 working-tree 内容。
- executor 内 materialize/runner/verify 全受计划 A 的 deadline/containment 管理；父进程没有 pre-materialize failure seam。
- runtime revision 只来自计划 C 的 `runtime_identity()`；identity 不可证明时 readiness fail 且零 claim，本计划不实现第二套探测。
- 单仓与多仓 runner 都只解析 process-local managed repo override，不能回落 config/meta live extra repo。
- 当前 ref tip、source target、workspace HEAD/tree、AttemptResult vector、publisher context 和 manifest exact schema 完全一致。
- 新 pending desired revision 在线性化 publish permit 下阻止旧结果写 manifest；乱序旧 webhook 被 current remote tip 判为 SUPERSEDED。
- tracked/index/untracked 内容证明失败会阻止发布；唯一 untracked allowlist 是 `.codegraph/`。
- executor 未确认死亡时持久 quarantine 与 workspace ledger 均保留；只有 `ConfirmedProcessDeath` 才清 worktree、pin 和 ledger。
- remote 凭据仅在受信配置与 Git 子进程内存中出现；日志、错误、proof、workspace.json、ledger、manifest 均通过假 userinfo 测试证明无泄漏。
- health/reindex status 在队列为空时仍检查 remote/ref；exact server mode 有配置 issue 时 readiness fail 且零 claim，cutover 前必须先跑绿该门禁。
- 本计划不改变计划 A 类型，不引入 runtime/deploy 或索引 active-generation。
