# 运行代际领域与持久化基础实施计划

> **执行要求：** 必须使用 `superpowers:subagent-driven-development` 逐任务实施；每个行为先执行 `superpowers:test-driven-development`，阶段验收前执行 `superpowers:requesting-code-review`。

**目标：** 建立与具体数据库、systemd 和索引实现解耦的运行代际领域模型、持久状态、事务日志、fencing 和自描述入口协议，为后续资源适配器提供稳定接口。

**架构：** 领域对象全部为不可变 dataclass，序列化和校验集中在各自 contract 模块；文件系统安全原语下沉到 runtime 公共层；store 只负责不可变写、CAS 和恢复读取；协调逻辑不进入模型或 store。恢复沿用原 attempt，仅通过单调递增 lease epoch 获得新控制权。

**技术栈：** 正式运行目标 Linux CPython 3.12 x86_64、dataclasses、enum、Protocol、标准库 `os`/`json`/`hashlib`/`fcntl`、pytest。

**全局约束：** 中文注释和文档；单文件优先小于 600 行；不可变对象的身份摘要不包含时间戳和 fencing token；原始 token 不写日志、不持久化，只通过受保护通道短暂传递，持久层只落摘要；生产 baseline 不从 `target^` 推导；所有路径读取拒绝符号链接和越界；每项测试先红后绿。

---

## 任务 1：抽取 descriptor-safe 文件原语

**文件：**

- Create: `codev_platform/runtime_managed_file.py`
- Modify: `codev_platform/ops/reindex_codegraph_resume_managed_path.py`
- Modify: `codev_platform/runtime_storage.py`
- Test: `tests/test_runtime_managed_file.py`
- Modify Test: `tests/test_reindex_codegraph_resume_managed_path.py`

### 步骤

- [ ] 先在 `tests/test_runtime_managed_file.py` 写失败测试，覆盖：目录链含符号链接、文件不是 regular file、mode/owner 不符、原子 replace 后文件与父目录均 fsync、摘要漂移、路径逃逸。
- [ ] 运行并确认失败：

```powershell
python -m pytest tests/test_runtime_managed_file.py -q
```

期望：因 `codev_platform.runtime_managed_file` 不存在而失败。

- [ ] 在 `runtime_managed_file.py` 实现以下最小接口，禁止依赖 `ops/`：

```python
@dataclass(frozen=True, slots=True)
class ManagedFilePolicy:
    mode: int
    require_uid: int | None
    max_bytes: int


@dataclass(frozen=True, slots=True)
class ManagedFileEvidence:
    path: str
    sha256: str
    size: int
    mode: int
    uid: int


def read_managed_bytes(path: Path, *, root: Path, policy: ManagedFilePolicy) -> bytes:
    """通过目录描述符读取受管普通文件并验证边界。"""


def write_managed_bytes_atomic(
    path: Path,
    payload: bytes,
    *,
    root: Path,
    policy: ManagedFilePolicy,
) -> ManagedFileEvidence:
    """同目录临时文件写入、fsync、replace，再 fsync 父目录。"""


def create_managed_bytes_exclusive(
    path: Path,
    payload: bytes,
    *,
    root: Path,
    policy: ManagedFilePolicy,
) -> ManagedFileEvidence:
    """使用 O_CREAT|O_EXCL 冻结不可变对象。"""
```

- [ ] `runtime_storage.py` 复用这些原语，不复制 `_open_regular` 的新变体；保留兼容导出，避免一次性破坏现有调用方。
- [ ] `ops/reindex_codegraph_resume_managed_path.py` 改为从公共模块重导出旧名称，保证现有索引恢复测试无行为变化。
- [ ] 运行：

```powershell
python -m pytest tests/test_runtime_managed_file.py tests/test_reindex_codegraph_resume_managed_path.py tests/test_runtime_storage.py -q
```

期望：全部通过。

- [ ] 执行 `python -m ruff check codev_platform/runtime_managed_file.py tests/test_runtime_managed_file.py`。
- [ ] 提交：`refactor(runtime): 统一受管文件安全读写原语`

## 任务 2：建立 RuntimeGeneration、DeploymentAttempt 与 RollbackBundle

**文件：**

- Create: `codev_platform/runtime_generation_contract.py`
- Create: `codev_platform/runtime_attempt_contract.py`
- Create: `codev_platform/runtime_rollback_contract.py`
- Create: `codev_platform/runtime_generation_acceptance.py`
- Modify: `codev_platform/runtime_deployment_contract.py`
- Test: `tests/test_runtime_generation_contract.py`
- Test: `tests/test_runtime_attempt_contract.py`
- Test: `tests/test_runtime_rollback_contract.py`
- Test: `tests/test_runtime_generation_acceptance.py`
- Test: `tests/test_runtime_deployment_contract.py`

### 步骤

- [ ] 先写失败测试，明确以下不变量：generation ID 为 64 位小写十六进制；attempt ID 为 32 位小写十六进制；同一 revision 可有多个 attempt；`created_at` 不参与 generation ID；token 不参与 generation ID；acceptance 按 attempt 独立且绑定当次 lease；rollback bundle 必须绑定 baseline observation、原 serving generation 和所有受保护载荷摘要。
- [ ] 运行并确认红灯：

```powershell
python -m pytest tests/test_runtime_generation_contract.py tests/test_runtime_attempt_contract.py tests/test_runtime_rollback_contract.py tests/test_runtime_generation_acceptance.py -q
```

- [ ] 实现代际模型：

```python
class GenerationKind(StrEnum):
    LEGACY = "legacy"
    MANAGED = "managed"


@dataclass(frozen=True, slots=True)
class RuntimeGeneration:
    schema_version: int
    generation_id: str
    revision: str
    release_id: str
    base_id: str
    entrypoint_contract_sha256: str
    systemd_bundle_sha256: str
    configuration_bundle_sha256: str
    database_contract_sha256: str
    index_set_sha256: str
    kind: GenerationKind
    created_at: str


@dataclass(frozen=True, slots=True)
class GenerationReleaseIdentity:
    revision: str
    release_id: str
    base_id: str
    wheel_sha256: str
    interpreter_relative: str


def create_runtime_generation(
    *,
    revision: str,
    release_id: str,
    base_id: str,
    entrypoint_contract_sha256: str,
    systemd_bundle_sha256: str,
    configuration_bundle_sha256: str,
    database_contract_sha256: str,
    index_set_sha256: str,
    kind: GenerationKind,
    created_at: str,
) -> RuntimeGeneration:
    """对不含 created_at 的身份载荷取 canonical SHA-256。"""
```

- [ ] 实现两阶段 attempt：预留记录先持久占位，完整 attempt 在目标 generation 构建完成后冻结；禁止把同 revision 的旧 attempt 当作自动 resume。

```python
class AttemptOperation(StrEnum):
    DEPLOY = "deploy"
    ROLLBACK = "rollback"


@dataclass(frozen=True, slots=True)
class AttemptReservation:
    schema_version: int
    attempt_id: str
    operation: AttemptOperation
    plan_sha256: str
    controller_sha256: str
    created_at: str


@dataclass(frozen=True, slots=True)
class DeploymentAttempt:
    schema_version: int
    attempt_id: str
    operation: AttemptOperation
    target_generation_id: str
    baseline_generation_id: str
    baseline_observation_sha256: str
    plan_sha256: str
    controller_sha256: str
    created_at: str
```

- [ ] 实现 `ProtectedPayloadRef`、`BaselineObservation` 和 `RollbackBundle`；每个路径只保存 runtime root 内相对路径、摘要、mode 和 owner，不接受绝对 destination。
- [ ] 实现 per-attempt 验收对象；不得把“某 generation 曾经成功过”当成当前 attempt 的验收：

```python
@dataclass(frozen=True, slots=True)
class GenerationAcceptance:
    schema_version: int
    attempt_id: str
    generation_id: str
    serving_fence_id: str
    serving_fence_epoch: int
    serving_fence_token_sha256: str
    control_lease_epoch_audit: int
    control_token_sha256_audit: str
    entrypoint_proof_sha256: str
    database_proof_sha256: str
    systemd_proof_sha256: str
    index_set_proof_sha256: str
    health_proof_sha256: str
    accepted_at: str
```
- [ ] acceptance 的有效性只由 attempt/generation、服务探针事实和稳定 `ServingFence` 决定；control lease 字段只作审计，不参与 state/permit 等值判断。recovery 提升 control epoch 后仍可基于同一 O_EXCL acceptance 收敛。
- [ ] 将 `DeploymentPlan` 升到 schema 2：移除生产语义的 `baseline_revision`；增加可选 `legacy_takeover_policy_sha256`。测试中需要 Git 父提交的场景使用测试辅助器，不进入生产 plan。
- [ ] 旧 schema 1 仅允许只读审计，正式新部署拒绝写旧 receipt。
- [ ] 运行：

```powershell
python -m pytest tests/test_runtime_generation_contract.py tests/test_runtime_attempt_contract.py tests/test_runtime_rollback_contract.py tests/test_runtime_generation_acceptance.py tests/test_runtime_deployment_contract.py -q
```

期望：身份摘要稳定、非法字段全部 fail closed。

- [ ] 提交：`refactor(runtime): 建立运行代际与部署尝试契约`

## 任务 3：建立 GenerationState、事务 journal、ControlLease 与 ServingFence

> **状态：🟢 已完成（2026-07-20）。** 原纯领域实现曾错误地要求先冻结完整
> `DeploymentAttempt`，与“reservation → journal → recovery envelope → active envelope →
> initial lease → baseline/candidate/generation → DeploymentAttempt”的批准顺序冲突；同时 journal
> 缺少自身显式终态，lease 可接收调用方任意提供的终态摘要。以下回修已全部闭合，
> 不新增任务编号；后续 Task 4 只消费已冻结的领域契约。

**文件：**

- Create: `codev_platform/runtime_generation_state.py`
- Create: `codev_platform/runtime_transaction_contract.py`
- Create: `codev_platform/runtime_fencing.py`
- Create: `codev_platform/runtime_control_lease_lineage.py`
- Create: `codev_platform/runtime_recovery_contract.py`
- Modify: `codev_platform/runtime_attempt_contract.py`
- Modify: `codev_platform/runtime_generation_acceptance.py`
- Create: `codev_platform/runtime_transaction_terminal.py`
- Create: `codev_platform/runtime_transaction_terminal_evidence.py`
- Create: `codev_platform/runtime_transaction_codec.py`
- Create: `codev_platform/runtime_serving_permit.py`
- Test: `tests/test_runtime_generation_state.py`
- Modify Test: `tests/test_runtime_generation_acceptance.py`
- Test: `tests/test_runtime_transaction_contract.py`
- Test: `tests/test_runtime_fencing.py`
- Create Test: `tests/test_runtime_control_lease_lineage.py`
- Test: `tests/test_runtime_recovery_contract.py`
- Create Test: `tests/test_runtime_reservation_control_chain.py`
- Create Test: `tests/test_runtime_transaction_terminal.py`
- Create Test: `tests/test_runtime_serving_permit.py`
- Modify Test: `tests/test_runtime_transaction_takeover_contract.py`
- Modify Test: `tests/test_runtime_transaction_size_contract.py`

### 2026-07-20 回修架构决策

1. `AttemptReservation` 是早期控制链唯一身份。增加规范 reservation 摘要；journal genesis、
   `RecoveryEnvelope`、initial/takeover `ControlLeaseRecord` 都持久绑定该摘要，并且工厂只接受精确
   `AttemptReservation`，不接受 `DeploymentAttempt` 或二者 union。完整 attempt 只在
   baseline/candidate/generation 冻结后用于状态切换、动作准备、ServingFence 与回滚包。
2. `TransactionJournal` 增加 `ACTIVE / COMPLETED` 显式生命周期、
   `reservation_sha256`、`terminal_evidence_sha256` 与 `completed_at`。活动 journal 才能追加；
   完成 journal 不再返回 `ADVANCE`，而是明确返回 `TERMINAL`。
3. 新模块 `runtime_transaction_terminal.py` 单独负责终态聚合：构造不可变终态证据、验证当前
   control capability、确认每个动作键的最新记录均为 `COMMITTED`、将证据摘要嵌入 journal，
   并复验完成 journal。journal 的 `COMPLETED` 状态是唯一完成真值；孤立 evidence 不代表完成。
4. 终态证据绑定 reservation、完成前 journal head、结果类型、最终 typed state、acceptance、
   serving fence、serve permit 和 control lease epoch/token 审计；所有摘要只能由类型化对象内部
   计算。公开 steady 结果必须同时绑定相互一致的 state/acceptance/fence/permit；restricted 或
   `safety_unproven` 必须保持维护门禁开启并显式证明 permit 缺失，历史 acceptance/fence 若仍被
   state 引用则必须提供并精确复验，不能用裸 SHA 伪造任何结果。
5. `retire_control_lease()` 不再接受调用方提供的两个任意 SHA；它只接受匹配的 completed journal
   与 terminal evidence，并在内部派生审计摘要。`runtime_fencing.py` 继续只维护 lease/fence 模型
   和 capability 校验，终态聚合逻辑不堆入该文件。
6. 采用干净 schema 纠偏，不保留错误的双签名兼容层；这些模型尚未进入正式 WSL 持久态。
   store 和 coordinator 后续只消费新契约。
7. `ControlLeaseRecord` 与 `ServingFenceRecord` 作为持久格式必须带显式 `schema_version`；
   `commit_serving()` 的领域边界接收 typed acceptance 并复验 attempt/generation/fence 全部绑定，
   不再让任意 acceptance SHA 配合可直接构造的公开 fence 记录进入 steady 候选状态。

### 回修 TDD 顺序

- [ ] RED 1：新增 `test_runtime_reservation_control_chain.py`，证明早期 journal、envelope、初始 lease
  和 takeover 只接受同一 reservation，拒绝 DeploymentAttempt、同 attempt ID 但摘要漂移的 reservation。
- [ ] GREEN 1：实现 reservation 摘要及三类早期对象的精确绑定，更新既有 fixture，运行定向测试。
- [ ] RED 2：新增 `test_runtime_transaction_terminal.py`，证明未 COMMITTED 动作、错误 lease、伪造
  acceptance/最终 state、重复完成、完成后追加和任意摘要退役全部失败。
- [ ] GREEN 2：实现 journal 显式终态、终态证据和基于 completed journal 的 lease retirement。
- [ ] REFACTOR：保持领域对象不可变、模块单一职责、无 ops/IO 反向依赖；避免任一 Task 3 文件
  因回修继续膨胀成聚合模块。
- [ ] 回归：运行本任务全部领域测试、Task 2 attempt/acceptance 测试及当前 Task 4 store 草稿测试；
  store 草稿若因新契约变红，只按已冻结接口调整，不提前实现 Task 4 其余能力。

### 2026-07-20 终审阻断修复

终审用纯内存探针复现以下缺口，全部归入原任务 3，不新增任务编号：

1. `COMPLETED` journal 的构造与严格解码必须自身拒绝空 history、最新动作未全部
   `COMMITTED`、完成时间不晚于动作历史；`transaction_recovery_directive()` 不能把结构无效对象当作
   `TERMINAL`。
2. `DeploymentAttempt` 进入 `begin_switch()` 和 `prepare_transaction_action()` 前，必须复算
   reservation 摘要并与当前 lease 精确绑定；journal/action/state 后续写入口继续持久校验同一
   reservation，不能只比较 `attempt_id`。
3. journal 动作审计、状态更新时间、lease 接管时间与 terminal 完成/退租时间必须严格单调；完成 lease
   必须是 journal 最后动作审计的同一 lease 或具有可验证 predecessor 链的合法后继。
4. `TARGET_COMMITTED` acceptance 的 control 审计必须绑定同一 lease lineage：同 epoch 必须同 token，
   takeover 后允许更高 epoch 与轮换 token，但必须以持久 predecessor 摘要链证明，不接受仅凭
   “epoch 更高且 token 不同”的推断。`commit_serving()` 与 terminal 必须各自复验。
5. evidence 模型/codec 只保留 `runtime_transaction_terminal_evidence.py` 一个公开入口；terminal 聚合
   模块不保留迁移期重导出。

实现边界：`runtime_fencing.py` 只负责单条 lease 的签发、严格 codec 与 capability 校验；新增
`runtime_control_lease_lineage.py` 专责验证不可变 predecessor 摘要链。状态提交和 terminal 只注入
显式 lineage 元组，不从“更高 epoch”推断合法接管；终态证据额外绑定完成时的完整 lease record 摘要。

每个控制面审计对象（journal action、attempt acceptance、控制绑定的 state、terminal evidence）都保存
同一条完整 ACTIVE lease 的摘要锚点，epoch/token 仅作为可读审计字段；lineage 校验以摘要精确定位锚点。
`ACTIVE → RETIRED` 不是 recovery successor：tombstone 保留 issuance predecessor，并以
`retired_from_sha256` 精确绑定被退休的 ACTIVE record，避免终态 evidence 与 tombstone 摘要循环。

本轮继续使用 TDD：先分别为 completed journal 自校验、reservation 边界、时间倒退、lease lineage、
acceptance successor 写失败测试并观察预期红灯；再实现最小共享不变量，最后重跑 Task 2/3 聚焦、
全部 `tests/test_runtime*.py`、Ruff、compileall、行数与三角色复审。

### 2026-07-20 第二轮终审回修（已完成）

第二轮只读终审确认原有时间窗、完整 record、rollback 引用和终态重验已闭合；以下项目仍属于
任务 3 的职责边界和文件纪律，必须在进入 Task 4 前完成：

1. `runtime_fencing.py` 不得继续导入 `GenerationAcceptance` 或公开
   serving-fence 与 acceptance 的跨域语义校验。该校验迁入独立 acceptance validation 单元，
   fencing 仅保留 lease/fence record、proof、codec 与 capability 校验；state、terminal、permit
   只依赖新的窄校验接口。
2. 公开 state 和 terminal 入口虽然要求关键字 `control_lease_lineage`，但显式传入 `None`
   仍不得降级为 singleton。各自的控制边界必须 fail closed；journal 内部的根租约便利语义不变。
3. `tests/test_runtime_generation_state.py`、`tests/test_runtime_transaction_terminal.py` 和
   `tests/test_runtime_fencing.py` 必须按模型/转换/lineage、完成/证据/退租、lease/fence/writer
   职责拆分为不超过 600 行的模块；共享工厂只保留一份 support，禁止复制 fixture。
4. 回归测试名称必须描述真实拒绝原因；原“未提供 lineage”场景改为“显式 lineage 缺少 predecessor”。

回修顺序：先为 `None` 和 fencing 职责边界写失败测试并观察红灯；再抽取 validation；随后只移动测试
代码、不改变断言语义，按每个拆分单元运行回归；最后重新执行运行时全量、Ruff、行数、diff 检查和
三角色终审。

#### 完成证据

- serving-fence 与 acceptance 的跨域绑定已迁入独立
  `runtime_generation_acceptance_validation.py`；`runtime_fencing.py` 不再反向依赖验收模型。
- 所有公开 state/terminal 控制入口均要求显式 `control_lease_lineage`，显式传入 `None` 同样
  fail closed；终态边界会把底层 fencing 与 acceptance 绑定异常统一为
  `TransactionTerminalError`。
- 状态、fencing、terminal 测试已按职责拆分，共享工厂各自唯一；相关生产与测试文件均小于 600 行。
- 定向回归 `240 passed`；全量 `tests/test_runtime*.py` 回归
  `1619 passed, 162 skipped`；Ruff 与 Ruff format 通过。
- 架构、状态/fencing、terminal/recovery 三角色终审均为无 Critical、无 Important；终审发现的
  两处异常映射对调已先 RED 后 GREEN 并复验关闭。

### 步骤

- [ ] 先写状态图表驱动测试，覆盖 `steady → switching → validating → steady`、任意危险分支到 `restricted` / `safety_unproven`，并验证不存在通用 `replace_state(**kwargs)` 绕过状态边。
- [ ] 定义单文件状态：

```python
class GenerationMode(StrEnum):
    STEADY = "steady"
    SWITCHING = "switching"
    VALIDATING = "validating"
    RESTRICTED = "restricted"
    SAFETY_UNPROVEN = "safety_unproven"


@dataclass(frozen=True, slots=True)
class GenerationState:
    schema_version: int
    state_version: int
    mode: GenerationMode
    serving_generation_id: str
    serving_fence_id: str
    serving_fence_epoch: int
    serving_fence_token_sha256: str
    desired_generation_id: str | None
    rollback_generation_id: str | None
    control_attempt_id: str | None
    control_lease_epoch: int | None
    acceptance_sha256: str | None
    maintenance_active: bool
    updated_at: str


def begin_switch(current: GenerationState, attempt: DeploymentAttempt, lease: ControlLeaseProof, *, updated_at: str) -> GenerationState:
    """绑定 desired/rollback/attempt，并进入 switching。"""


def begin_validation(current: GenerationState, lease: ControlLeaseProof, *, updated_at: str) -> GenerationState:
    """资源切换完毕后进入 validating。"""


def commit_serving(
    current: GenerationState,
    acceptance_sha256: str,
    serving_fence: ServingFenceRecord,
    lease: ControlLeaseProof,
    *,
    updated_at: str,
) -> GenerationState:
    """只在完整验收后提交新 serving fence，并保持维护门禁关闭。"""


def prepare_serving_publication(
    current: GenerationState,
    acceptance_sha256: str,
    *,
    updated_at: str,
) -> GenerationState:
    """纯计算维护门禁关闭后的目标状态 B，供 permit 预绑定和后续 CAS。"""


def mark_safety_unproven(current: GenerationState, lease: ControlLeaseProof, *, updated_at: str) -> GenerationState:
    """无法证明补偿结果时关闭服务许可。"""
```

- [ ] 先写 journal 四窗口测试：PREPARED 已 fsync 但资源未变、资源已变但 APPLIED 未 fsync、APPLIED 已 fsync 但 COMMITTED 未 fsync、COMMITTED 已 fsync 但下一步未开始；恢复时不得重复产生非幂等副作用。
- [ ] 实现 `TransactionActionState(PREPARED/APPLIED/COMMITTED)`、`TransactionAction`、`TransactionJournal` 和 `append_transaction_action()`；动作键由 attempt、步骤序号、资源种类和资源 ID 组成。
- [ ] 区分可持久审计记录和不可伪造的调用能力；摘要不得作为授权凭据：

```python
@dataclass(frozen=True, slots=True)
class ControlLeaseRecord:
    attempt_id: str
    epoch: int
    token_sha256: str
    owner: str
    status: ControlLeaseStatus
    issued_at: str
    terminal_journal_sha256: str | None = None
    terminal_evidence_sha256: str | None = None
    retired_at: str | None = None


@dataclass(frozen=True, slots=True)
class ControlLeaseProof:
    attempt_id: str
    epoch: int
    token: bytes = field(repr=False)

    @property
    def token_sha256(self) -> str:
        """仅在校验边界计算审计摘要。"""


@dataclass(frozen=True, slots=True)
class ServingFenceRecord:
    fence_id: str
    generation_id: str
    accepted_attempt_id: str
    epoch: int
    token_sha256: str
    issued_at: str


@dataclass(frozen=True, slots=True)
class ServingFenceProof:
    fence_id: str
    epoch: int
    token: bytes = field(repr=False)
```

- [ ] `ControlLeaseStatus` 只有 `ACTIVE` / `RETIRED`。`ControlLease` 授权 journal/state/acceptance/permit 等控制面写入，recovery 只可在同一活动 attempt 上提升 epoch；`ServingFence` 在一次 serving 提交内稳定，绑定 state/acceptance/permit 和稳态 writer，下一次 deploy/rollback 提交时才换新。两种 proof 不可互换。
- [ ] `begin_switch`、`begin_validation`、`commit_serving` 都保持 `maintenance_active=True`；`switching` / `validating` 状态无条件拒绝所有 `ServingFenceProof`。`prepare_serving_publication()` 只做确定性纯转换，生成 `maintenance_active=False` 的目标状态 B；控制器先让 staged permit 绑定 B 的完整摘要，再用 control lease 执行 A→B CAS。
- [ ] staged permit 写入后、state CAS 前，permit 与当前 A 摘要不匹配，runtime gate 和 serving writer 必须保持关闭；CAS A→B 成功后无需改写 permit，三方才同时一致。CAS 失败或崩溃时 recovery 只能复证后重试同一 CAS，或撤销 staged permit。
- [ ] 先写并发测试证明：旧 serving writer 在 `begin_switch` CAS 后即使仍持有旧 proof 也被拒绝；target 的 provisional proof 在 `commit_serving` 前以及 commit 后但维护门禁尚未关闭时都被拒绝；门禁关闭后只允许当前 generation/current fence。
- [ ] control 原始随机 token 不序列化、不进入 argv/日志/任务 JSON/manifest；只经继承 FD、systemd credential 或受 peer credential 保护的本地 broker 传递。serving capability 由 root broker 或 systemd credential 安全持有，公开持久对象只含摘要。
- [ ] canonical JSON/codec 明确拒绝 `ControlLeaseProof`、`ServingFenceProof` 和任意 bytes capability，禁止对 proof 调用 `dataclasses.asdict()` 后误落盘；测试检查 repr、异常和日志均不含原始 token。
- [ ] 实现稳定外层恢复 envelope；模块名和 argv 不进入 envelope，由 launcher 按固定常量构造：

```python
@dataclass(frozen=True, slots=True)
class RecoveryEnvelope:
    schema_version: int
    attempt_id: str
    controller_root_relative: str
    controller_tree_sha256: str
    interpreter_relative: str
    interpreter_sha256: str
    transaction_store_id: str
    journal_genesis_sha256: str
    created_at: str
```

- [ ] envelope 只绑定不可变 journal genesis/store 与 controller identity，不绑定会变化的 journal head 或 control lease epoch；launcher 通过各自 CAS store读取当前 head/lease并验证 genesis。`controller_root_relative` 和 `interpreter_relative` 必须位于 runtime root 内的不可变 controller tree；禁止绝对命令、shell 文本、环境变量覆盖和 release current 链接。
- [ ] 明确 lease 接管本身不关闭线上服务；撤销 permit 是事务中的第一个入口动作。
- [ ] 运行：

```powershell
python -m pytest tests/test_runtime_generation_state.py tests/test_runtime_transaction_contract.py tests/test_runtime_fencing.py tests/test_runtime_recovery_contract.py -q
```

期望：所有非法状态边、旧 epoch 和错误 token 均被拒绝。

- [ ] 提交：`feat(runtime): 增加代际状态事务日志与围栏`

## 任务 4：实现不可变对象 store、状态 CAS 与恢复 envelope

**文件：**

- Create: `codev_platform/runtime_generation_store.py`
- Create: `codev_platform/runtime_transaction_store.py`
- Create: `codev_platform/runtime_rollback_store.py`
- Create: `codev_platform/runtime_fencing_store.py`
- Modify: `codev_platform/runtime_storage.py`
- Test: `tests/test_runtime_generation_store.py`
- Test: `tests/test_runtime_transaction_store.py`
- Test: `tests/test_runtime_rollback_store.py`
- Test: `tests/test_runtime_fencing_store.py`
- Modify Test: `tests/test_runtime_storage.py`

### 步骤

- [ ] 先写失败测试，覆盖 O_EXCL 冲突、重复写相同内容幂等、重复写不同内容拒绝、CAS 旧摘要拒绝、临时文件崩溃隔离、符号链接、截断 JSON、父目录 fsync、并发两个控制器只有一个成功。
- [ ] 扩展 runtime root，但不要把 64 位 generation ID 和 32 位 attempt ID混入旧 `_KINDS` 的同一校验器：

```text
runtime/
  generations/<generation_id>/generation.json
  attempts/<attempt_id>/reservation.json
  attempts/<attempt_id>/attempt.json
  attempts/<attempt_id>/journal.json
  attempts/<attempt_id>/recovery-envelope.json
  rollback-bundles/<attempt_id>/bundle.json
  acceptances/<attempt_id>.json
  generation-state.json
  active-recovery-envelope.json
```

- [ ] 实现 generation store 的原子边界：

```python
@dataclass(frozen=True, slots=True)
class GenerationStateSnapshot:
    state: GenerationState
    sha256: str


class RuntimeGenerationStore:
    def write_generation_once(self, generation: RuntimeGeneration, *, lease: ControlLeaseProof) -> str:
        """冻结 generation.json 并返回摘要。"""

    def load_generation(self, generation_id: str) -> RuntimeGeneration:
        """加载并复算 generation_id。"""

    def load_state(self) -> GenerationStateSnapshot:
        """读取单一 generation-state.json。"""

    def compare_and_swap_state(
        self,
        expected_sha256: str,
        desired: GenerationState,
        *,
        lease: ControlLeaseProof,
    ) -> GenerationStateSnapshot:
        """持锁复读、比较摘要、原子替换并 fsync。"""

    def write_acceptance_once(
        self,
        attempt_id: str,
        payload: bytes,
        *,
        lease: ControlLeaseProof,
    ) -> str:
        """验收记录按 attempt 不可变保存。"""
```

- [ ] `RuntimeTransactionStore.reserve_attempt()` 使用 O_EXCL；随后冻结 journal genesis、fsync per-attempt envelope，再以 expected-absent CAS 发布固定 active 副本，最后获取初始 control lease。envelope 发布前 reservation 无外部副作用，可按 TTL 和 controller identity 安全取消；envelope 发布后统一由 recover 收敛。
- [ ] `ControlLeaseStore` 在持有全局部署锁时 CAS 唯一当前 `(attempt_id, epoch, token_sha256, owner, status)`；`acquire_initial()` 只接受 active envelope 指向的未获 lease reservation，且当前 lease 必须不存在或是前一 attempt 的 `RETIRED` tombstone；`take_over()` 只允许同一 `ACTIVE` attempt 且 epoch 单调增加，`require_current(ControlLeaseProof)` 在与 journal/state/index/permit 相同的文件锁或数据库事务内复验原始 token。
- [ ] lease store 额外按完整 record 摘要保留不可变 active epoch 历史，current 指针只做 CAS；takeover 先 fsync 新历史节点，再切换指针。后续 terminal/recovery 读取 lineage 时不得依赖已被 current 指针覆盖的旧文件。
- [ ] 增加 `retire_current(proof, terminal_journal_sha256, terminal_evidence_sha256)`：只接受已 fsync 的 complete journal 和一致终态证据，以 CAS 把 `ACTIVE` 变为不可逆 `RETIRED` tombstone，立即拒绝旧 proof。`clear_terminal_envelope()` 只在 active envelope、retired attempt、journal 和 evidence 四者精确匹配时幂等清理，不再授权任何资源写入。
- [ ] 固定终态顺序为 complete journal → retire current lease → clear active envelope。覆盖三个崩溃窗口：complete 后未 retire 由同 attempt takeover 收敛；retire 后未 clear 只能幂等清 envelope；clear 后新 attempt 才能从 retired tombstone CAS 为新的 active lease。并发测试证明两个新 attempt 只有一个成功，旧 proof 和旧 terminal cleaner 都不能影响新 lease。
- [ ] 用 barrier 写 `retire_current()` 与 recovery `take_over()` 竞争同一 `ACTIVE` record 的确定性测试：只能一个 CAS 成功；retire 胜出时 takeover 被拒绝且 recovery 只能清 envelope，takeover 胜出时旧 proof 的 retire 被拒绝并由新 epoch proof 完成退休，不得出现 ACTIVE/RETIRED 双成功或旧 epoch 覆盖新 epoch。
- [ ] 所有控制面 store 写接口显式接收 `ControlLeaseProof`，不接受调用者自行提供的 token 摘要；测试证明读取 lease JSON 的低权限进程不能伪造写权限。
- [ ] `RuntimeRollbackStore` 冻结 rollback bundle 并逐项验证 payload 摘要、mode、owner 和相对路径。
- [ ] 旧 `runtime_deployment_receipt.py` 保留只读解析器供审计，不再作为新事务真值。
- [ ] 运行：

```powershell
python -m pytest tests/test_runtime_generation_store.py tests/test_runtime_transaction_store.py tests/test_runtime_rollback_store.py tests/test_runtime_fencing_store.py tests/test_runtime_storage.py -q
```

期望：并发与崩溃窗口全部通过。

- [ ] 提交：`feat(runtime): 持久化运行代际与可恢复事务`

## 任务 5：让每个 release 保持独立 base，并自描述、自校验入口契约

**文件：**

- Create: `codev_platform/runtime_entrypoint_models.py`
- Create: `codev_platform/runtime_entrypoint_selftest.py`
- Create: `codev_platform/runtime_entrypoint_probe.py`
- Create: `codev_platform/runtime_release_generation.py`
- Modify: `codev_platform/runtime_target_user_protocol.py`
- Modify: `codev_platform/runtime_release_environment.py`
- Modify: `codev_platform/runtime_release.py`
- Test: `tests/test_runtime_entrypoint_models.py`
- Test: `tests/test_runtime_entrypoint_selftest.py`
- Test: `tests/test_runtime_entrypoint_probe.py`
- Test: `tests/test_runtime_release_generation.py`
- Modify Test: `tests/test_runtime_target_user_probe.py`

### 步骤

- [ ] 先写真实的两个临时虚拟 release 测试：基线只认识旧模块，目标新增模块；目标控制器必须调用各 release 自己的解释器和 `runtime_entrypoint_selftest`，不得把目标 `MANAGED_IMPORTS` 注入基线。
- [ ] 将稳定外层 envelope、opaque contract 正文和稳定 proof 分开；controller 只解析 envelope/proof：

```python
@dataclass(frozen=True, slots=True)
class RuntimeEntrypointEnvelope:
    schema_version: int
    protocol_min: int
    protocol_max: int
    contract_sha256: str
    self_test_argv: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class OpaqueEntrypointContract:
    sha256: str
    payload: bytes = field(repr=False)


@dataclass(frozen=True, slots=True)
class RootEntrypointDeclaration:
    service_role: str
    module: str
    fixed_argv: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class TargetEntrypointDeclarations:
    declarations: tuple[RootEntrypointDeclaration, ...]
    sha256: str


@dataclass(frozen=True, slots=True)
class RuntimeEntrypointProof:
    schema_version: int
    contract_sha256: str
    release_id: str
    root_declarations_sha256: str
    checks_sha256: str
    observed_at: str
```

- [ ] `self_test_argv` 只接受固定语法 `-m codev_platform.runtime_entrypoint_selftest verify --contract-sha256 <sha256>`；opaque contract 上限 64 KiB 且不得含配置/凭据；`describe` 输出 envelope 和 contract bytes，`verify` 只在该 release 内解析 contract、导入并检查自己的入口，再在稳定 proof 中输出 root declaration 摘要。controller 不解析 entrypoint 列表或内部 schema。
- [ ] 冻结的目标 controller 从自己版本的 registry 构造受限 `TargetEntrypointDeclarations`，并要求其摘要等于目标 release proof；root renderer 只接收这份 controller-owned 声明。baseline/legacy systemd 只从 rollback bundle 原样恢复，因此新 controller 不需要理解其内部 contract。
- [ ] controller-side `runtime_entrypoint_probe.py` 只负责无 shell 启动指定 release Python、限制环境/输出/超时、解析稳定 envelope，不导入被测 release 的新模块。
- [ ] 为不含 selftest 模块的 legacy 版本保留独立 legacy adapter；不得通过捕获任意 ImportError 自动信任。
- [ ] `runtime_release_generation.py` 从 serving generation/rollback bundle 分别加载 target 与 baseline 自己的 `release_id/base_id/python`；移除正式事务对 `_ReleasePair.base_id` 相同的要求。managed A/B base 不同仍可往返；legacy external interpreter 不进入 managed `current/previous` 链。
- [ ] 所有 target/baseline/legacy verifier 都以对应服务 UID、只读文件和只读数据库凭据在 systemd sandbox 中执行，不继承 root 控制器环境或 DDL 权限。
- [ ] 从 generation 发布路径删除 `runtime_target_user_protocol.py` 中硬编码 `MANAGED_IMPORTS` 的跨版本注入；不得复活 schema 1 staging 写链，旧 helper 只保留兼容测试门面。
- [ ] 运行：

```powershell
python -m pytest tests/test_runtime_entrypoint_models.py tests/test_runtime_entrypoint_selftest.py tests/test_runtime_entrypoint_probe.py tests/test_runtime_release_generation.py tests/test_runtime_target_user_probe.py -q
```

期望：旧/新入口集合不同仍能分别自检；篡改 Python、release ID 或契约摘要均失败。

- [ ] 提交：`fix(runtime): 改为发布版本自描述入口契约`

## 任务 6：实现首次遗留环境接管契约

**文件：**

- Create: `codev_platform/runtime_legacy_takeover_contract.py`
- Create: `codev_platform/runtime_legacy_policy.py`
- Create: `codev_platform/runtime_legacy_audit.py`
- Create: `codev_platform/runtime_legacy_takeover.py`
- Test: `tests/test_runtime_legacy_takeover_contract.py`
- Test: `tests/test_runtime_legacy_policy.py`
- Test: `tests/test_runtime_legacy_audit.py`
- Test: `tests/test_runtime_legacy_takeover.py`

### 步骤

- [ ] 先写测试证明：批准策略不能由现场观察自动生成；观察清单只能使用一次；MainPID、current release、systemd 有效载荷、配置、数据库、四索引任一漂移都使接管失败。
- [ ] 实现独立信任输入：

```python
@dataclass(frozen=True, slots=True)
class LegacyIndexIdentity:
    kind: str
    generation_id: str
    manifest_sha256: str
    pointer_sha256: str


@dataclass(frozen=True, slots=True)
class LegacyTakeoverPolicy:
    schema_version: int
    approved_revision: str
    approved_release_id: str
    approved_interpreter_path: str
    approved_interpreter_sha256: str
    approved_receipt_schema: int
    approved_receipt_sha256: str
    approved_systemd_payloads: tuple[ProtectedPayloadRef, ...]
    approved_configuration_payloads: tuple[ProtectedPayloadRef, ...]
    approved_database_contract_sha256: str
    approved_index_identities: tuple[LegacyIndexIdentity, ...]
    nonce: str
    expires_at: str
    approved_by: str
    approved_at: str


@dataclass(frozen=True, slots=True)
class LegacyTakeoverManifest:
    schema_version: int
    policy_sha256: str
    observed_release_sha256: str
    observed_systemd_sha256: str
    observed_configuration_sha256: str
    observed_database_sha256: str
    observed_index_set_sha256: str
    observed_at: str
```

- [ ] `runtime_legacy_policy.py` 是 release 外稳定 root 工具：把 policy 以 root-owned 0600、O_EXCL 写入，授权来自 root 管理通道或外部签名；`approved_by` 只用于显示。policy 独立绑定精确批准事实、nonce、过期时间和单次消费记录，目标 release 无写权限；随后 `DeploymentPlan` 单向引用 policy SHA，policy 不回指最终 plan，避免摘要循环。
- [ ] `audit_legacy_generation(policy, ports)` 只做只读观察并冻结 manifest；`bootstrap_legacy_generation(policy, manifest, plan_sha256, attempt_id, store)` 复验全部绑定后生成 `GenerationKind.LEGACY`。
- [ ] 禁止用“当前看起来可用”反推批准值；策略文件必须作为部署 plan 的独立摘要输入。
- [ ] 运行：

```powershell
python -m pytest tests/test_runtime_legacy_takeover_contract.py tests/test_runtime_legacy_policy.py tests/test_runtime_legacy_audit.py tests/test_runtime_legacy_takeover.py -q
```

期望：单次接管成功；重放、漂移、策略与观察同源均失败。

- [ ] 提交：`feat(runtime): 增加遗留环境审计接管契约`

## 任务 7：基础阶段整体验收与评审

**文件：**

- Review: `codev_platform/runtime_*.py`
- Review: `tests/test_runtime_*.py`
- Update: `docs/plans/roadmap-2026-07-19/2026-07-19-runtime-generation-rollback.md`（只勾选有命令证据的 M1 项）

### 步骤

- [ ] 执行基础阶段全集：

```powershell
python -m pytest tests/test_runtime_managed_file.py tests/test_runtime_generation_contract.py tests/test_runtime_attempt_contract.py tests/test_runtime_rollback_contract.py tests/test_runtime_generation_acceptance.py tests/test_runtime_generation_state.py tests/test_runtime_transaction_contract.py tests/test_runtime_fencing.py tests/test_runtime_recovery_contract.py tests/test_runtime_generation_store.py tests/test_runtime_transaction_store.py tests/test_runtime_rollback_store.py tests/test_runtime_fencing_store.py tests/test_runtime_entrypoint_models.py tests/test_runtime_entrypoint_selftest.py tests/test_runtime_entrypoint_probe.py tests/test_runtime_release_generation.py tests/test_runtime_legacy_takeover_contract.py tests/test_runtime_legacy_policy.py tests/test_runtime_legacy_audit.py tests/test_runtime_legacy_takeover.py -q
python -m pytest tests/test_runtime_storage.py tests/test_runtime_deployment_contract.py tests/test_runtime_target_user_probe.py -q
python -m ruff check codev_platform/runtime_managed_file.py codev_platform/runtime_generation_contract.py codev_platform/runtime_attempt_contract.py codev_platform/runtime_rollback_contract.py codev_platform/runtime_generation_acceptance.py codev_platform/runtime_generation_state.py codev_platform/runtime_transaction_contract.py codev_platform/runtime_fencing.py codev_platform/runtime_recovery_contract.py codev_platform/runtime_generation_store.py codev_platform/runtime_transaction_store.py codev_platform/runtime_rollback_store.py codev_platform/runtime_fencing_store.py codev_platform/runtime_entrypoint_models.py codev_platform/runtime_entrypoint_selftest.py codev_platform/runtime_entrypoint_probe.py codev_platform/runtime_release_generation.py codev_platform/runtime_legacy_takeover_contract.py codev_platform/runtime_legacy_policy.py codev_platform/runtime_legacy_audit.py codev_platform/runtime_legacy_takeover.py
```

期望：全部通过。

- [ ] 检查文件规模和反向依赖：

```powershell
Get-ChildItem codev_platform/runtime_*.py | ForEach-Object { [PSCustomObject]@{ File = $_.Name; Lines = (Get-Content -Encoding UTF8 $_.FullName).Count } } | Sort-Object Lines -Descending
rg -n "codev_platform\.ops|ops\.reindex" codev_platform/runtime_*.py
```

期望：新文件均小于 600 行；runtime 领域层不依赖 `ops`。

- [ ] 请求架构、安全和测试三类代码评审；逐条验证意见，不做无证据修改。
- [ ] 执行 `git diff --check`，确认无尾随空格和冲突标记。
- [ ] M1 全部满足后再开始资源适配器阶段。
