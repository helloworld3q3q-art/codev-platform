# 运行代际 Foundation Task 4：持久化与可恢复事务实施计划

> **执行要求：** 按任务逐项使用 TDD；每项先观察真实 RED，再写最小 GREEN；每个任务结束做独立只读复审。
> **状态：** Task 1–7 的代码、测试与 WSL 运行态收口均已完成（2026-07-25）；Task 7 已完成跨域恢复、生命周期、descriptor-bound 隔离、root-fd worker 生命周期闭环和 `code_vec` 跨进程可见性修复。最终服务、队列和四项索引 manifest 均已复核通过；不进入 Task 8。

**目标：** 将已冻结的 Task 3 领域契约安全持久化为不可变对象、CAS 状态和可恢复控制链，覆盖崩溃窗口与 lease 接管/退休竞争。

**架构：** `runtime_root_binding.py` 为一次公开存储操作签发短生命周期 `BoundRuntimeRoot`，文件读取、发布和部署锁均相对同一根目录文件描述符完成；`runtime_managed_file.py` 只保留兼容门面，受绑定输入输出放入独立机械模块。四个分域 store 只处理自己的类型化记录，跨域控制授权通过只在 `with` 内有效的窄作用域 DTO，初始租约通过受信 bootstrap context，退休清理通过只读 tombstone 作用域注入。`ControlLeaseStore` 先冻结不可变 history，再以摘要 CAS 切换 current 指针；transaction store 按 evidence → completed journal → retired lease → clear envelope 的顺序收敛。

**技术栈：** Linux CPython 3.12+、`dataclasses`、`Protocol`、标准库 `fcntl` / `os` / `hashlib`、pytest；开发机 Windows 仅做可移植静态检查，文件描述符、`fsync` 与并发语义以 WSL/Linux 为验收环境。

## 全局约束

- 全部注释、测试名和文档使用中文；单个 Python 文件严格小于 600 行。
- 不重复实现 `O_EXCL`、临时文件、`rename`、文件/父目录 `fsync`、目录文件描述符或符号链接防护；路径兼容门面与 `_at` 原语复用同一受管文件机械实现，store 不得自行拼装文件系统原语。
- 不可变对象只接受严格 decode 后的规范编码；同字节内容幂等，内容漂移拒绝。`reserve_attempt()` 是身份分配，重复调用一律冲突。
- 所有原始 control token 只存在 `ControlLeaseProof`；磁盘只保存 `token_sha256`，公开 JSON 不能成为授权凭据。
- 所有普通控制面写入在同一 mutation 临界区完成：读取 current → 校验 proof → 领域转换 → CAS 写入；退休后的唯一例外是无 token 的精确 tombstone 清理 gate，只允许删除已完全绑定的旧 active envelope。已绑定操作的锁顺序固定为 `BoundRuntimeRoot → deployment_lock_at → activation_lock_at → 受管父目录锁`；禁止在租约内调用会按 pathname 重开 root 的旧锁 API。
- takeover 的 owner 失活授权由 root recovery/systemd 调用方承担；store 只验证 typed 输入和 CAS fencing，不从更高 epoch 推断授权。
- 不暂存、不提交、不推送、不操作 WSL 运行态；只在明确的 WSL 测试步骤运行临时测试目录。

---

## 文件与职责边界

| 文件 | 职责 |
|---|---|
| `codev_platform/runtime_root_binding.py` | 操作期根目录文件描述符租约、可见路径身份复验与跨 binding 拒绝；不处理领域记录或业务锁。 |
| `codev_platform/_runtime_managed_file_contract.py` | 受管文件策略、证据、错误与参数契约；不打开路径或持有 root fd。 |
| `codev_platform/_runtime_managed_file_identity.py` | 普通叶子元数据、稳定身份与精确删除前 inode 证据；不绑定 root 或发布文件。 |
| `codev_platform/_runtime_managed_file_bound.py` | 相对 `BoundRuntimeRoot` 的 read/create/replace/remove/open 机械原语；不含 store 或领域判断。 |
| `codev_platform/runtime_managed_file.py` | 向既有调用方保留路径 API 与类型重导出；兼容调用内部一次绑定后委托 `_at` 原语。 |
| `codev_platform/runtime_storage.py` | runtime root、固定路径、目录骨架，以及描述符绑定业务锁；不放 codec 或领域决策。 |
| `codev_platform/runtime_deployment_lock_capability.py` | deployment flock 持有期的中性短生命周期能力；只负责签发、存活性和同根身份，不处理 lease 或领域记录。 |
| `codev_platform/runtime_store_protocols.py` | policy、通用与 lease 快照 DTO、bootstrap 聚合读取和控制/终态清理窄 Protocol；重导出 root binding 类型但不持有 fd 机械逻辑。 |
| `codev_platform/runtime_fencing_store.py` | current lease 指针、不可变 lease history、control mutation 与精确 tombstone 清理门禁。 |
| `codev_platform/runtime_transaction_store.py` | reservation、attempt、journal、per-attempt envelope、active envelope、terminal evidence。 |
| `codev_platform/runtime_generation_store.py` | generation、fence、acceptance、permit-stage/内容记录的受控不可变写入；门面委托严格 state CAS。 |
| `codev_platform/_runtime_generation_store_state.py` | generation-state 的严格读取、完整证据回读与逐边纯状态机 CAS。 |
| `codev_platform/runtime_control_scope_verifier.py` | 在 gate 授予的同一根租约内复验持久 current/history、lineage 与 proof，不依赖具体 gate 实现。 |
| `codev_platform/runtime_rollback_store.py` | rollback bundle 暂存、封口、读取与受保护载荷完整性编排。 |
| `codev_platform/runtime_rollback_store_factory.py` | rollback store 的唯一生产装配入口，固定 HMAC provider 与密文上下文依赖边界。 |
| `codev_platform/runtime_protected_payload_verifier.py` | 同一根租约内的受保护载荷 `_at` 读取、元数据、公开 SHA、HMAC 与密文上下文校验；不持有 store 或持久化密钥。 |
| `tests/test_runtime_store_layout.py` | 路径与布局契约。 |
| `tests/test_runtime_fencing_store.py` | lease history/current CAS/接管/退休。 |
| `tests/test_runtime_transaction_store.py` | transaction 对象、envelope、terminal evidence 与清理。 |
| `tests/test_runtime_generation_store.py` | generation/acceptance/permit/state CAS。 |
| `tests/test_runtime_generation_store_hardening.py` | scope、根身份、CAS 前置和不可变记录对抗。 |
| `tests/test_runtime_generation_store_evidence.py` | fence、acceptance、permit 与 permit-stage 的严格证据叶子对抗。 |
| `tests/test_runtime_rollback_store.py` | bundle 与受保护载荷边界。 |
| `tests/test_runtime_rollback_store_concrete.py` | production factory、真实 root 能力、HMAC/密文与撤销闭锁集成。 |
| `tests/test_runtime_recovery_store.py` | crash windows、旧 cleaner、takeover/retire barrier 竞争。 |

## 持久化布局

```text
runtime/
  generations/<generation_id>/generation.json
  attempts/<attempt_id>/reservation.json
  attempts/<attempt_id>/attempt.json
  attempts/<attempt_id>/journal.json
  attempts/<attempt_id>/recovery-envelope.json
  attempts/<attempt_id>/terminal-evidence.json
  rollback-bundles/<attempt_id>/bundle.pending.json
  rollback-bundles/<attempt_id>/bundle.json
  acceptances/<attempt_id>.json
  serving-fences/<attempt_id>/<serving_fence_sha256>.json
  serving-permit-stages/<attempt_id>/<staged_generation_state_sha256>.json
  serving-permits/<attempt_id>/<serving_permit_sha256>.json
  generation-state.json
  active-recovery-envelope.json
  control-leases/<attempt_id>/<control_lease_record_sha256>.json
  control-lease.json
```

`control-lease.json` 是唯一可变 current 指针；所有 epoch record（包括 RETIRED tombstone）先写入 history。`serving-permit-stages` 是按维护态 A 摘要唯一寻址的不可变 permit 锚点，内容必须与对应内容寻址 permit 逐字节一致。terminal evidence 即使先落盘也不代表完成，只有 journal 的 `COMPLETED` 才是完成真值。

---

### Task 1（任务 1）：固定布局、窄协议和安全策略

**文件：**

- Modify: `codev_platform/runtime_storage.py`
- Create: `codev_platform/runtime_store_protocols.py`
- Create: `tests/test_runtime_store_layout.py`
- Modify: `tests/test_runtime_storage.py`（仅在既有布局断言处增加回归；超过 600 行时改放新文件）

**接口：**

```python
@dataclass(frozen=True, slots=True)
class RuntimeStorePolicy:
    root: Path
    owner_uid: int
    @property
    def root_binding(self) -> RuntimeRootBinding: ...


class RuntimeRootBinding:
    # Task 3 的描述符绑定修订以此接口为准。
    def bind(self) -> ContextManager[BoundRuntimeRoot]: ...
    def verify(self) -> None: ...


@dataclass(frozen=True, slots=True)
class StoredSnapshot[T]:
    value: T
    sha256: str


class RuntimeControlGate(Protocol):
    def mutation(self, proof: ControlLeaseProof) -> ContextManager[RuntimeMutationScope]: ...


@dataclass(frozen=True, slots=True)
class ActiveBootstrapContext:
    reservation: StoredSnapshot[AttemptReservation]
    # 由严格验证的持久 journal head 的不可变身份派生，不代表当前 head 必为空。
    journal_genesis: StoredSnapshot[TransactionJournal]
    active_envelope: StoredSnapshot[RecoveryEnvelope]


class RuntimeTerminalCleanupGate(Protocol):
    def terminal_cleanup(
        self, tombstone: ControlLeaseSnapshot
    ) -> ContextManager[RuntimeTerminalCleanupScope]: ...


class RuntimeTransactionControlGate(
    RuntimeControlGate,
    RuntimeTerminalCleanupGate,
    Protocol,
): ...


def attempt_terminal_evidence_path(root: Path, attempt_id: str) -> Path: ...
def control_lease_history_path(root: Path, attempt_id: str, record_sha256: str) -> Path: ...
def serving_fence_record_path(root: Path, attempt_id: str, record_sha256: str) -> Path: ...
def serving_permit_record_path(root: Path, attempt_id: str, record_sha256: str) -> Path: ...
def serving_permit_stage_path(root: Path, attempt_id: str, staged_state_sha256: str) -> Path: ...
```

- [x] **步骤 1：写布局 RED 测试。**

```python
def test运行时布局为终态证据与完整lease_lineage提供固定地址(
    tmp_path: Path,
) -> None:
    root = tmp_path / "runtime"
    attempt_id = "a" * 32
    record_sha256 = "b" * 64

    assert attempt_terminal_evidence_path(root, attempt_id) == (
        root / "attempts" / attempt_id / "terminal-evidence.json"
    )
    assert control_lease_history_path(root, attempt_id, record_sha256) == (
        root / "control-leases" / attempt_id / f"{record_sha256}.json"
    )
```

- [x] **步骤 2：运行 RED。**

```powershell
python -B -m pytest -q -p no:cacheprovider tests/test_runtime_store_layout.py
```

期望：因路径函数和 `runtime_store_protocols` 不存在而失败。

- [x] **步骤 3：实现最小布局与协议。** 路径函数必须分别调用既有 `_attempt_id()` / `_generation_id()` / SHA 校验，初始实现的 `initialize_runtime_root()` 新增 `control-leases`、`serving-fences`、`serving-permits` 三个可信目录；Task 5 在同一固定布局规则下追加 `serving-permit-stages`。`RuntimeStorePolicy` 在构造时拒绝相对 root、负 UID；不允许 store 调用点自行传入 `require_uid=None`。

- [x] **步骤 4：运行 GREEN 与边界回归。**

```powershell
python -B -m pytest -q -p no:cacheprovider tests/test_runtime_store_layout.py tests/test_runtime_storage.py
python -m ruff check codev_platform/runtime_storage.py codev_platform/runtime_store_protocols.py tests/test_runtime_store_layout.py
```

期望：路径逃逸、ID 混用、符号链接根和错误摘要均 fail closed。

**完成证据（2026-07-21）：** RED 先后验证缺少 `runtime_store_protocols`、缺少固定路径接口及策略未拒绝符号链接根；回修后 `tests/test_runtime_store_layout.py tests/test_runtime_storage.py` 为 `9 passed, 25 skipped`。Ruff、Ruff format、`git diff --check` 通过；独立复审无 Critical/Important/Minor。

---

### Task 2（任务 2）：预租约 transaction bootstrap 与 active envelope 发布

**文件：**

- Create: `codev_platform/runtime_transaction_store.py`
- Create: `tests/test_runtime_transaction_store.py`

**接口：**

```python
class RuntimeTransactionBootstrapStore:
    def reserve_attempt(
        self, reservation: AttemptReservation
    ) -> StoredSnapshot[AttemptReservation]: ...
    def write_journal_genesis_once(
        self, journal: TransactionJournal
    ) -> StoredSnapshot[TransactionJournal]: ...
    def write_envelope_once(
        self, envelope: RecoveryEnvelope
    ) -> StoredSnapshot[RecoveryEnvelope]: ...
    def publish_active_envelope_if_absent(
        self, envelope: RecoveryEnvelope
    ) -> StoredSnapshot[RecoveryEnvelope]: ...
    def load_active_envelope(self) -> StoredSnapshot[RecoveryEnvelope]: ...
```

- [x] **步骤 1：写 bootstrap RED 测试。**

```python
def test预租约链只能按reservation_journal_envelope_active_envelope发布(
    tmp_path: Path,
) -> None:
    store, reservation, journal, envelope = _bootstrap_input(tmp_path)

    store.reserve_attempt(reservation)
    store.write_journal_genesis_once(journal)
    store.write_envelope_once(envelope)
    active = store.publish_active_envelope_if_absent(envelope)

    assert active.value == envelope
    assert store.load_active_envelope() == active
```

- [x] **步骤 2：运行 RED。**

```powershell
python -B -m pytest -q -p no:cacheprovider tests/test_runtime_transaction_store.py
```

期望：bootstrap class、journal genesis 或 active envelope 接口缺失而失败。

- [x] **步骤 3：实现最小 bootstrap store。** reservation 是无 lease 的 O_EXCL 身份分配，任何已存在叶子都抛 `AttemptReservationConflictError`；journal genesis、per-attempt envelope 使用规范编码和 immutable once，active envelope 在 `deployment_lock()` 内严格 expected-absent 发布。发布前必须复验 reservation、journal genesis、envelope 三者精确绑定，发布后尚不签发 lease。

- [x] **步骤 4：运行 GREEN。**

```powershell
python -B -m pytest -q -p no:cacheprovider tests/test_runtime_transaction_store.py
python -m ruff check codev_platform/runtime_transaction_store.py tests/test_runtime_transaction_store.py
```

期望：active envelope 冲突不覆盖既有文件、不产生 initial lease；截断 JSON、符号链接和绑定漂移均 fail closed。

**完成证据（2026-07-21）：** 已观察到 bootstrap 接口缺失、外层 `deployment_lock()` 内读取自锁、合法推进的 journal head 被误拒绝、根替换错误泄漏，以及首次 envelope 写入错误接受已推进 head 的真实 RED；最小修复后，23 个目标用例可在 Windows 收集，Windows 因 POSIX 门禁为 `23 skipped`，Ruff、Ruff format、工作区与暂存区 diff 检查均通过。WSL `/usr/bin/python3` 缺少 pytest，未安装依赖；改以真实 POSIX 临时目录 smoke 覆盖完整 bootstrap 链、锁组合、推进 head、首写门禁、绑定漂移、符号链接根和外部目标保护。两名独立复审者终审均无 Critical/Important/Minor。正式 WSL pytest 仍列为任务 7 的环境验收缺口。

---

#### Task 3 架构修订（2026-07-21，复审阻断后）

Task 2 的历史完成证据只代表当时已验收的最小 bootstrap 链。Task 3 首轮独立安全复审确认：仅比较 `ActiveBootstrapReader.root` 和 DTO 内部自洽性，不能证明三元组来自受管磁盘；同时将可变 journal head 当作 immutable genesis 读取，会错误拒绝崩溃恢复中的精确重试。两项均属于持久化真值边界，必须在进入 Task 4 前修复。

裁决为新增中性 `PersistedActiveBootstrapLoader`，而不是以具体 `RuntimeTransactionBootstrapStore` 类型作为信任条件，也不是让 fencing 重复解码逻辑：

- loader 只依赖 `RuntimeStorePolicy`、受管文件原语和领域 codec；负责严格读取、规范复编码及 reservation / journal genesis / envelope 绑定；不取得部署锁。
- `RuntimeTransactionBootstrapStore` 保留预租约写入职责，并组合 loader 完成所有严格读取与恢复重试判断。
- `ControlLeaseStore` 只接收 `RuntimeStorePolicy`，在其唯一外层部署锁内直接调用 loader；不再接收任何 caller-provided reader/context，因而无法由同 root 的伪对象绕过磁盘事实。
- `runtime_store_protocols.py` 只保留 DTO 与控制 gate；删除 `ActiveBootstrapReader`、`ActiveEnvelopeReader` 两个会被误当作持久化证明的 Protocol。Task 4 的受控 transaction store 也自行组合同一 loader，不再注入读取器。

#### Task 3 架构修订（二，描述符绑定根）

第二轮独立复审证明，单纯在写前后对 `RuntimeRootBinding.verify()` 做 pathname 身份比较仍不安全：`create_managed_bytes_exclusive(..., root=Path)` 和 `deployment_lock(root=Path)` 会在验证之后重新按 pathname 打开 root。攻击者若在该间隙原子替换目录，系统可能锁住旧根而向替换根发布文件；后置报错不能回收已经落入替换根的副作用。

本轮以操作期 `BoundRuntimeRoot` 根治该错误路径：

- `RuntimeRootBinding.bind()` 从 `/` 逐段以 `O_DIRECTORY | O_NOFOLLOW | O_CLOEXEC` 打开 root，校验目录 `(st_dev, st_ino, st_uid, st_mode)`、`owner_uid` 和无 group/other write 后签发仅本次公开操作存活的 descriptor 租约；同一 policy 首次成功绑定的身份是后续操作必须保持的信任锚。
- `BoundRuntimeRoot.verify_visible()` 只为比较而沿命名路径重新安全打开验证链；它绝不参与真实 I/O。`relative_path()` 只接受无逃逸的绝对 Path 并返回词法相对组件；不同 binding、已关闭或身份不符的 handle 一律拒绝。
- 所有真实 read/create/atomic-write/exact-remove/open-lock-file 都经 `*_at(..., root=bound_root)` 相对持有的 root fd 完成；任何 store 在租约内不得调用旧 Path API，也不得用 `/proc/self/fd/...` 把 fd 伪装回路径。
- `deployment_lock_at()`、`activation_lock_at()`、`id_lock_at()` 也在同一租约内打开 `locks/**`，防止“锁 A、读写 B”。旧 Path 锁 API 仅保留兼容调用；新 store 不得混用。
- loader 的 `bound_root` 为可选关键字参数：无外层租约时自行 bind；外层已持租约时必须由同一 policy `require_bound()` 验证后复用，不重入部署锁或重新打开 root。fencing 与 transaction 的每个公开操作均只持有一个租约并透传。
- 每次 `_at` 写入和锁取得后均复验可见性，写后漂移一律报错且不自动重试。若替换恰发生在最后一次写前可见性复验之后，实际 I/O 最多进入已持有的旧、已脱离命名空间的 fd，绝不会写入替换后的可见 root；退出复验仍必须失败。由于不持有根父目录锁，不能虚假承诺旧脱离目录的零副作用，测试只断言“不成功、替换根无新叶子、后续状态闭锁”。
- 任一次 identity、属主、mode、可见路径或 fd 生命周期漂移都将该 `RuntimeRootBinding` 永久标记为失效；即使旧 inode 随后被人工恢复，同一 policy 也不得重新开放。只有显式构造新的 policy 并重新完成初次信任建立，才可能进入新的操作期租约；测试必须覆盖该永久闭锁语义。
- 初次 `bind()` 前已被错误初始化或被高权限管理员替换、且元数据完全伪装一致的 root 不属于本层可判别威胁；该初始信任仍由受保护的 runtime root 初始化流程保证。

这是一项边界收紧，不改变 Task 4 之后的领域状态机或外部运行态；所有新增读写仍限本地临时目录测试，不启动、重启或改写 WSL 服务和数据库。

---

### Task 3（任务 3）：ControlLease history、current CAS 与 mutation 门禁

**文件：**

- Create: `codev_platform/runtime_root_binding.py`
- Create: `codev_platform/_runtime_managed_file_contract.py`
- Create: `codev_platform/_runtime_managed_file_bound.py`
- Modify: `codev_platform/runtime_managed_file.py`
- Modify: `codev_platform/runtime_storage.py`
- Create: `codev_platform/runtime_control_lease_persistence.py`
- Create: `codev_platform/runtime_control_lease_transition.py`
- Create: `codev_platform/runtime_control_lease_transition_recovery.py`
- Modify: `codev_platform/runtime_fencing_store.py`
- Modify: `codev_platform/runtime_store_protocols.py`
- Create: `codev_platform/runtime_bootstrap_loader.py`
- Modify: `codev_platform/runtime_transaction_store.py`
- Create: `tests/test_runtime_root_binding.py`
- Create: `tests/test_runtime_managed_file_bound.py`
- Create: `tests/test_runtime_fencing_store.py`
- Create: `tests/test_runtime_fencing_store_hardening.py`
- Create: `tests/test_runtime_control_lease_transition.py`
- Create: `tests/test_runtime_fencing_transition_recovery.py`
- Create: `tests/test_runtime_fencing_initial_transition_recovery.py`
- Create: `tests/runtime_store_race_support.py`
- Create: `tests/test_runtime_bootstrap_loader.py`
- Create: `tests/test_runtime_bootstrap_loader_hardening.py`
- Modify: `tests/test_runtime_transaction_store.py`
- Create: `tests/test_runtime_transaction_store_atomic.py`

**接口：**

```python
class RuntimeRootBindingError(ValueError): ...


class BoundRuntimeRoot:
    @property
    def path(self) -> Path: ...
    @property
    def owner_uid(self) -> int: ...
    def verify_visible(self) -> None: ...
    def relative_path(self, path: Path) -> tuple[str, ...]: ...
    def _duplicate_root_fd(self) -> int: ...


class RuntimeRootBinding:
    def __init__(self, root: Path, owner_uid: int) -> None: ...
    def bind(self) -> ContextManager[BoundRuntimeRoot]: ...
    def verify(self) -> None: ...
    def require_bound(self, root: BoundRuntimeRoot) -> None: ...


def read_managed_bytes_at(
    path: Path, *, root: BoundRuntimeRoot, policy: ManagedFilePolicy,
) -> bytes: ...


def read_optional_managed_bytes_at(
    path: Path, *, root: BoundRuntimeRoot, policy: ManagedFilePolicy,
) -> bytes | None: ...


def write_managed_bytes_atomic_at(
    path: Path, payload: bytes, *, root: BoundRuntimeRoot,
    policy: ManagedFilePolicy,
) -> ManagedFileEvidence: ...


def create_managed_bytes_exclusive_at(
    path: Path, payload: bytes, *, root: BoundRuntimeRoot,
    policy: ManagedFilePolicy,
) -> ManagedFileEvidence: ...


def remove_managed_bytes_exact_at(
    path: Path, expected: bytes, *, root: BoundRuntimeRoot,
    policy: ManagedFilePolicy,
) -> bool: ...


def deployment_lock_at(root: BoundRuntimeRoot) -> ContextManager[None]: ...
def activation_lock_at(
    root: BoundRuntimeRoot, *, shared: bool = False,
) -> ContextManager[None]: ...
def id_lock_at(
    root: BoundRuntimeRoot, kind: str, object_id: str, *, shared: bool,
) -> ContextManager[None]: ...


@dataclass(frozen=True, slots=True)
class ControlLeaseSnapshot:
    record: ControlLeaseRecord
    sha256: str


@dataclass(frozen=True, slots=True)
class RuntimeMutationScope:
    snapshot: ControlLeaseSnapshot
    bound_root: BoundRuntimeRoot


@dataclass(frozen=True, slots=True)
class RuntimeTerminalCleanupScope:
    snapshot: ControlLeaseSnapshot
    bound_root: BoundRuntimeRoot


class PersistedActiveBootstrapLoader:
    def __init__(self, policy: RuntimeStorePolicy) -> None: ...
    @property
    def root(self) -> Path: ...
    def load_active_envelope(
        self, *, bound_root: BoundRuntimeRoot | None = None,
    ) -> StoredSnapshot[RecoveryEnvelope]: ...
    def load_active_context(
        self, *, bound_root: BoundRuntimeRoot | None = None,
    ) -> ActiveBootstrapContext: ...


class ControlLeaseStore:
    def __init__(self, policy: RuntimeStorePolicy) -> None: ...
    def load_current(self) -> ControlLeaseSnapshot | None: ...
    def load_active_lineage(
        self, current: ControlLeaseRecord
    ) -> tuple[ControlLeaseRecord, ...]: ...
    def acquire_initial(
        self, *, owner: str, token: bytes, issued_at: str,
    ) -> tuple[ControlLeaseSnapshot, ControlLeaseProof]: ...
    def take_over(
        self, current: ControlLeaseSnapshot, *,
        owner: str, token: bytes, issued_at: str,
    ) -> tuple[ControlLeaseSnapshot, ControlLeaseProof]: ...
    def mutation(self, proof: ControlLeaseProof) -> ContextManager[RuntimeMutationScope]: ...
    def retire_current(
        self, proof: ControlLeaseProof, completed: TransactionJournal,
        evidence: TransactionTerminalEvidence, *, retired_at: str,
    ) -> ControlLeaseSnapshot: ...
    def terminal_cleanup(
        self, tombstone: ControlLeaseSnapshot,
    ) -> ContextManager[RuntimeTerminalCleanupScope]: ...
```

`ControlLeaseSnapshot` 是定义在 `runtime_store_protocols.py` 的跨域 DTO，不定义在具体 fencing store 内；它必须在构造时复验 `sha256 == control_lease_record_sha256(record)`，以便 `RuntimeTerminalCleanupGate` 不反向依赖具体 store。`ActiveBootstrapContext` 的字段名固定为 `reservation`、`journal_genesis`、`active_envelope`，其值均为规范摘要快照；不得为兼容旧名称留下 alias 或双真值。

- [x] **步骤 1：写 lease history 与 current RED 测试。**

```python
def test接管先冻结历史再切换current并可重建完整lineage(tmp_path: Path) -> None:
    store = _lease_store_input(tmp_path)
    initial, _ = store.acquire_initial(**_initial_owner())

    successor, _ = store.take_over(initial, **_recovery_owner())

    assert store.load_current() == successor
    assert store.load_active_lineage(successor.record) == (
        initial.record,
        successor.record,
    )
```

- [x] **步骤 2：运行 RED。**

```powershell
python -B -m pytest -q -p no:cacheprovider tests/test_runtime_fencing_store.py
```

期望：`ControlLeaseStore` 缺少 history/current 操作而失败。


- [x] **步骤 3：先写描述符绑定根与 `_at` 原语的 RED。** 新建 `tests/test_runtime_root_binding.py` 和 `tests/test_runtime_managed_file_bound.py`，先证明当前路径 API 无法满足的行为：同一 binding 仅接受自身未关闭租约；root 的 uid、mode、inode、符号链接或可见路径漂移均拒绝且永久闭锁；`read`、可选读、O_EXCL、原子替换、精确删除、锁文件打开都只从已持有 fd 相对遍历，拒绝路径逃逸、父目录/叶子符号链接、错误属主/权限和非普通文件。每个写入路径必须验证“最后一次写前可见性检查后立即交换 root”时：调用报错、替换后的命名 root 没有新叶子，后续操作闭锁。测试不得通过 `/proc/self/fd`、线程模拟或伪对象绕开真实 fd；使用原子 rename 的两份合法临时根，并把替换注入到真实 `_at` 原语的发布入口。

对旧 Path API 保留兼容回归：其一次公开调用必须临时 bind 后委托相同 `_at` 代码，不能保留第二套发布算法。已有受管文件并发测试若依赖内部 monkeypatch，迁移到真正承载机械逻辑的模块，不得降低现有 `fsync`、临时文件清理、父目录锁和 `RENAME_NOREPLACE` 覆盖。

- [x] **步骤 4：以最小共享机械层实现根租约、`_at` I/O 与绑定锁。** `runtime_root_binding.py` 只实现 `RuntimeRootBinding`、`BoundRuntimeRoot`、identity 生命周期和可见性比较；不得导入 store、codec 或业务锁。`_runtime_managed_file_contract.py` 承担 `ManagedFilePolicy`、`ManagedFileEvidence`、错误和参数校验；`_runtime_managed_file_bound.py` 承担所有 `_at` fd I/O，Path facade 只一次 bind 后委托，二者共享同一发布实现而不复制 `O_EXCL`/rename/fsync 算法。

`BoundRuntimeRoot` 不暴露可供 store 留存或跨进程传递的裸 fd；仅机械层可通过私有复制方法取得带 close-on-exec 的副本。`bind()` 首次建立并冻结同一 policy 的目录身份，后续 bind 只接受同一 identity。每个 `_at` 写入前后、`deployment_lock_at()` 取得锁后与上下文退出前均调用 `verify_visible()`；读操作至少在实际读取前后复验。任何漂移均报边界错误、不重试。`read_optional_managed_bytes_at()` 只对安全的最终叶子缺失返回 `None`，缺父目录、链接、权限或 I/O 异常均报错；`remove_managed_bytes_exact_at()` 必须在同一受信父目录 fd 内读、逐字节比较、unlink、fsync，不能提供无条件删除。所有 store 只能从公开的 `runtime_managed_file.py` 导入 `_at` API，不得直接依赖下划线机械模块。

`runtime_storage.py` 新增 `deployment_lock_at()`、`activation_lock_at()`、`id_lock_at()`，均使用同一 bound root 下的受管锁文件，完成锁文件/父目录 fsync、flock、属主/模式复验和释放；不得从 `_at` 实现回调旧 Path 锁。旧 Path 锁 API 仅做兼容包装。所有新增/改造 Python 文件和拆分后的测试文件严格少于 600 行。

**阶段证据（2026-07-21）：** 描述符绑定根、受绑定文件原语与三类绑定锁已完成 TDD 与独立机械审查；Windows 聚焦集为 `6 passed, 57 skipped`，Ruff、格式、编译和 diff 检查通过。WSL `/usr/bin/python3` 缺 pytest，未安装依赖，改以临时目录标准库 smoke 覆盖最终发布点换根、精确删除、三类锁、链接拒绝与关闭租约。当时发现的 `RuntimeStorePolicy` 旧 pathname binding Critical 已由后续 protocol/fencing 接线修复；完整收敛证据见本任务步骤 7 后的完成记录。

- [x] **步骤 5：接线严格 loader、bootstrap 与 fencing，先 RED 后 GREEN。** `runtime_bootstrap_loader.py` 只承担持久 bootstrap 读取：以同一 `RuntimeStorePolicy` 派生 `0o600 + require_uid=owner_uid` 文件策略，在同一 `BoundRuntimeRoot` 内读取 active envelope → reservation → 当前 journal head → per-attempt envelope，逐项 decode、复编码等字节，并从已验证 head 的 immutable identity 派生空 `journal_genesis`。合法 `ACTIVE` 或 `COMPLETED` head 都必须派生相同 genesis；active 与 per-attempt envelope 必须规范字节完全相同且绑定该 genesis。无外层租约时 loader 自行 bind；有外层租约时只接受同 policy 租约，不取部署锁也不重新打开 root。

`RuntimeTransactionBootstrapStore` 只保留 reservation / genesis / per-attempt envelope / active envelope 的写入与错误翻译，所有严格读取委托 loader。每个公开操作的最外层只 bind 一次并把相同租约透传到 loader、`*_at` 与锁。journal 首写只允许“reservation 已持久化 + O_EXCL 写入规范空 genesis”，因为首写前本不存在 head；仅在 journal 叶子已经存在时，才由严格当前 head 派生 genesis 并比较传入规范字节。per-attempt envelope 首写和 active 尚不存在时的 `publish_active_envelope_if_absent()` 都必须在 `deployment_lock_at(bound_root)` 内，确认 head 为精确空 genesis、已存在 leaf 条件满足后再 O_EXCL 创建；不得在检查与创建之间留下 head 推进窗口。已存在 immutable record 的恢复重试及已存在 active 的严格读取改为由当前严格 head 派生 genesis 后比较传入规范字节。故合法 head 推进后：相同 genesis 与 per-attempt envelope 返回“由 head 证明的 genesis 快照”，漂移载荷拒绝；缺失 per-attempt 或 active leaf 不得借已推进 head 补写/首次发布。`publish_active_envelope_if_absent()` 保持 expected-absent 冲突语义；崩溃恢复必须显式严格读取 active，而非把重复发布伪装为幂等。

`ControlLeaseStore(policy)` 内建同 policy loader；`load_current()`、`acquire_initial()`、`take_over()`、`mutation()`、`retire_current()` 与 `terminal_cleanup()` 各自最外层只 bind 一次，并在同一 `deployment_lock_at(bound_root)` 内从 loader 实际读取 context、读取 current、冻结 history、CAS current。删除 reader 参数及所有 root 代理校验，不能以同根 fake reader 或内存 DTO 参与授权。immutable history 使用 `encode_control_lease_record()` + `create_managed_bytes_exclusive_at()`；所有 current 指针转换以旧完整记录 SHA 为前置条件严格复读，再用 `write_managed_bytes_atomic_at()` 发布。`ControlLeaseSnapshot` 必须验证摘要与 record 的规范摘要相等，current 还必须存在完全相同的 history 叶子。

`mutation()` 不再只向跨域 store 暴露裸 `ControlLeaseRecord`：它在同一 bind + deployment lock 作用域中校验 current ACTIVE proof 后 yield `RuntimeMutationScope(snapshot, bound_root)`；`terminal_cleanup()` 在同一作用域中严格校验 RETIRED tombstone、history、退休前驱 lineage 与转换后 yield `RuntimeTerminalCleanupScope(snapshot, bound_root)`。两个 DTO 的 `__post_init__` 分别拒绝非 ACTIVE、非 RETIRED snapshot，离开 `with` 后内部 root 已关闭，任何 `_at` 调用必须报错。gate Protocol 不再暴露 `root: Path`，防止后续 store 把路径误当成安全 I/O 能力。`RuntimeTransactionStore(policy, gate)` 必须持有与 `ControlLeaseStore(policy)` **同一个** `RuntimeStorePolicy` 实例，并在进入 scope 后调用 `policy.root_binding.require_bound(scope.bound_root)`；它绝不自行 bind 或拿 deployment lock。

为保持单一职责与 600 行上限，`runtime_control_lease_persistence.py` 专责接收已验证的 `BoundRuntimeRoot` 后执行 control lease 的严格 record/history/current 读取、规范 bytes 复验、不可变 history 冻结与 current CAS；它不验证 proof、lineage、退休转换或签发 scope。`runtime_fencing_store.py` 只保留授权、状态机、lineage、scope 生命周期和错误翻译，不得继续堆积受管文件 I/O。

`acquire_initial()` 仅允许 current 不存在，或为不同 attempt 的完整 RETIRED tombstone；同 attempt 退休后不能重新签发初始 lease。对“不同 attempt 的 RETIRED”也必须先复验 tombstone history、`retired_from_sha256` 对应 ACTIVE history、完整前驱 lineage 与 `verify_retirement_transition()`，不得用损坏旧 tombstone 覆盖为新 ACTIVE。`take_over()` 同样必须复验当前 active bootstrap context，并在持锁重读 current 后从重读记录派生 successor；不可从更高 epoch 推断 owner 失活授权。任何授权路径在 yield / 写 successor 前必须验证完整 active lineage：`take_over()`、`mutation()` 与 `retire_current()` 均拒绝缺失或漂移的前驱 history。`terminal_cleanup()` 除 current/tombstone 同 SHA history 外，还必须复验 `retired_from_sha256` 指向的活动 history、完整前驱 lineage 与 `verify_retirement_transition()`；损坏 tombstone 不得开放清理。`retire_current()` 在同一 bound deployment lock 内严格读取持久 terminal-evidence / journal，逐字节与入参规范编码相等后才允许冻结 RETIRED history 再 CAS。`terminal_cleanup()` 只允许通过 `remove_managed_bytes_exact_at()` 删除精确旧 active envelope，绝不使用无条件 unlink。

- [x] **步骤 6：补 strict-read、negative、终态篡改与真实竞争测试。** `tests/test_runtime_bootstrap_loader.py` 保留聚合语义和合法 `ACTIVE` / `COMPLETED` head 派生 genesis；新增 `tests/test_runtime_bootstrap_loader_hardening.py`，为 reservation、journal head、per-attempt envelope、active envelope 四类叶子分别覆盖缺失、截断、未知字段、空白/字段顺序等非规范 bytes、错误 mode、错误 owner；覆盖 root uid/mode/inode 漂移、不同 binding handle、外层 `deployment_lock_at()` 下 loader 不重入，以及 loader 读取中和 loader 返回后 history/CAS 前的原子 root 替换。任何场景均不得产生 current/history，也不得向替换命名 root 写入。

`tests/test_runtime_transaction_store_atomic.py` 专责 reservation O_EXCL、journal 首写 O_EXCL、per-attempt envelope 首写和 active expected-absent 四条路径；每条都覆盖精确“最后写前检查 → `_at` 原语调用”根替换、head 推进、expected-absent conflict 和既有 active 非规范 bytes。`tests/test_runtime_transaction_store.py` 保持推进后 exact retry、drift 与缺 leaf 回归，不再继续膨胀；不得再用 `terminal_cleanup()` 伪造新 active envelope 发布。

`tests/test_runtime_fencing_store.py` 保留语义测试，并以两个真实独立进程在共同 `multiprocessing.get_context("spawn")` + `Barrier` 后竞争同一 initial snapshot 的 `take_over()` 与 `retire_current()`；父进程只传 root、uid、不可变 snapshot/proof/终态值，worker 内各自新建 policy/store/binding，禁止传递 store、reader、scope 或文件描述符。每轮必须恰有一个成功、另一个只能是预期 CAS/授权冲突；队列中的结构化结果不得含 token 或异常对象，任何解析/路径/锁/绑定异常都必须使测试失败。父进程以单一截止时间收齐结果，并在 `finally` 中 `abort` barrier、join、必要时 terminate/kill 两个子进程并关闭队列，防止卡死和僵尸。最终 current、完整 history 和唯一胜者精确一致，history 叶子集合必须恰为 `{initial, winner}`，不得留下失败方在 CAS 前冻结的孤儿 record。另加 root 替换后两子进程均不能成功、替换 root 无 current/history 的回归。`mutation()` 只能 yield ACTIVE `RuntimeMutationScope`，`terminal_cleanup()` 只能 yield RETIRED `RuntimeTerminalCleanupScope`；两者离开 `with` 后 `_at` I/O 必须拒绝，且不同 policy binding 的 scope 被 Task 4 store 拒绝。两个串行测试只保留为分别验证接管胜、退休胜后的确定性语义，不能替代真实竞争。

为保持测试文件单一职责与 600 行上限，`tests/runtime_store_race_support.py` 只放 control lease 的可 pickle 顶层 worker、无秘密结构化结果、单一截止时间收集及强制回收辅助；语义断言留在各分域测试文件。Task 5 另建同职责但 generation 领域隔离的 `tests/runtime_generation_store_support.py`，Task 7 复用已有通用模式但不得把跨域语义混入同一 support 文件。Windows 保持函数级 POSIX skip，模块导入期不得创建进程、锁或调用 `os.geteuid()`。`tests/test_runtime_fencing_store_hardening.py` 专责覆盖缺 predecessor 时接管/mutation/terminal cleanup 全部闭锁，错误 token、RETIRED proof、历史字节漂移、current 已变化旧 proof，以及 persistent journal/evidence/current tombstone 的缺失、截断 JSON、未知字段、双向摘要/summary 漂移和路径身份漂移。

#### Task 3 架构修订（三，持久 transition 意图与崩溃收敛）

独立并发复审确认，单纯的 `freeze_history() → CAS current` 虽能保证 current 从不指向缺失 history，却在进程于两步之间退出时留下不可达 history。不能以失败后盲删修复：调用方可能在 CAS 成功后未收到结果，盲删会破坏真实 winner 的不可变审计；也不能调换为 current 先行，否则会形成 current 指向缺 history 的授权断链。

裁决为新增单一、无秘密的写前 `pending transition`，而不是引入第二套 history 或将恢复逻辑散落到调用方：

- 新建 `runtime_control_lease_transition.py`，只定义严格规范编码的 `ControlLeaseTransitionIntent`。其字段固定为 schema 版本、`initial` / `takeover` / `retire` kind、完整 `expected_record | None` 与完整 `next_record`；两个摘要都由各自规范 record 派生，禁止重复持久化摘要形成双真值。完整 expected record 不含 token，可在 CAS 已成功后仍按其 attempt_id + 派生摘要精确读取旧 tombstone/history，绝不扫描目录；不得保存 proof、token 或裸路径。
- `runtime_storage.py` 仅增加运行时根下固定单叶 `control_lease_pending_transition_path(root)`（建议文件名 `control-lease-pending-transition.json`）；不得放进需要额外初始化的 staging 子目录，以免现有仅 `root.mkdir()` 的启动路径把父目录缺失误判为安全 optional 缺失。pending 不属于 `control-leases/**` immutable history，也不得由目录扫描选为授权来源。
- `runtime_control_lease_persistence.py` 只在已验证 `BoundRuntimeRoot` 中严格读写、O_EXCL 创建和精确删除 pending，并把 `prepare intent → freeze immutable history → CAS current → exact clear intent` 封装为窄持久化协议。`compare_and_swap_current()` 自身必须复验 next 的 exact history 已存在，不能只信任调用方。
- `runtime_fencing_store.py` 仍只负责授权、状态机、lineage 与错误翻译。以一个不重入的内部 helper 统一 `bind 一次 → deployment_lock_at 一次 → reconcile pending 一次 → 业务动作`；所有 `acquire_initial()`、`take_over()`、`retire_current()`、`mutation()`、`terminal_cleanup()` 与新增显式 `recover_pending_transition()` 都只能走该 helper，persistence 绝不自行重绑或取得部署锁。无锁 `load_current()` / `load_active_lineage()` 必须在同一 bound root 内执行 `检查 pending → 严格读取 → 再检查 pending`；任一次发现（含损坏 pending）均报告“未收敛”，不得返回可继续决策的旧 current。

收敛算法只能在同一 bound root 与部署锁内执行：无 pending 直接返回；若 current 已精确等于 intent.next，仍须按 kind 重验 intent 结构、bootstrap/lineage/terminal 语义、next exact history，并使用 intent.expected_record 的 attempt_id + 派生摘要精确复验旧前驱，才可精确删除 pending；若 current 仍精确等于 intent.expected_record，则按 kind 重验 bootstrap、活动 lineage、recovery edge 或持久 terminal 绑定，再幂等冻结 next history、CAS current、复读并精确删除 pending；`initial` 的 expected 可为 `None`（首个 attempt）或不同 attempt 的完整 RETIRED tombstone，后者也必须复验 tombstone/lineage 且 next 为新的 epoch=1 ACTIVE record。current 既非 expected 亦非 next，或 intent/history/语义任一漂移时，不删除任何记录并 fail closed。写入 intent 即表示已经完成 proof 校验后的可恢复提交决定，因此 `recover_pending_transition()` 不得接受或重建 proof、token、owner、next 或 epoch，只能完成该唯一 candidate，绝不能从目录、孤儿 history 或更高 epoch 推导新的授权。

这保持了不可变 history 先于 current 的顺序。崩溃在 intent 前没有副作用；崩溃在 intent 后的任意窗口都由后续受锁操作或显式恢复收敛为唯一 winner，最终 history 集合不含失败方 orphan，pending 被删除。根替换时旧 fd 下的 staging/history 均留在脱离命名空间的旧根，替换后的命名 root 不产生新叶，旧 binding 永久闭锁。

- [x] **步骤 6a：先写 pending transition 的真实崩溃 RED，再实现收敛。** 使用真实 `spawn` 子进程，在 `prepare_pending_transition()` 成功返回后、`freeze_history()` 成功返回后、以及 CAS 成功但清理 pending 前分别以 `os._exit(0)` 模拟进程消失；三类窗口均覆盖 initial/takeover/retire。父进程必须观察到旧 current 与严格 pending，而新 policy/store 的 `recover_pending_transition()` 必须只完成 intent.next、history 恰为 `{initial, winner}` 并删除 pending。另覆盖 pending 篡改、expected 漂移、不同 binding、根替换，以及“不同 attempt 的 RETIRED tombstone → 新 epoch=1 ACTIVE initial”在全部崩溃窗口均能以 intent.expected_record 精确恢复。恢复后旧 snapshot/proof 只能冲突、绝不产生第三 history 叶。测试通过顶层 worker monkeypatch 真实持久层边界，不引入生产 test hook；父进程不得传递 store、scope、fd、raw proof 或 token，worker 只能以固定公开测试夹具自行重建 capability。根替换 worker 只有异常因果链明确包含 `RuntimeRootBindingError` 或根身份/可见路径漂移时才可报告预期闭锁，任一解析、路径、锁或 transition 异常均必须作为 unexpected 失败。

- [x] **步骤 6b：补 bootstrap 绑定、scope 生命周期与 persistence 自证回归。** `mutation()`、`retire_current()` 在 proof 校验和 yield/退休前，必须从同一 bound root 严格加载 active bootstrap，并确认 current 的 attempt/reservation 精确绑定；active envelope 缺失、不同 attempt 或摘要漂移时，旧 proof 必须闭锁。直接调用 persistence 的 CAS 且未预先冻结 next history 必须拒绝发布 current。ACTIVE 与 RETIRED scope 离开 `with` 后分别尝试真实公开 `*_at` 读取或写入，必须因关闭租约拒绝，不能只断言 `verify_visible()`。

- [x] **步骤 7：运行 GREEN。**

```powershell
python -B -m pytest -q -p no:cacheprovider tests/test_runtime_root_binding.py tests/test_runtime_managed_file.py tests/test_runtime_managed_file_concurrency.py tests/test_runtime_managed_file_bound.py
python -B -m pytest -q -p no:cacheprovider tests/test_runtime_bootstrap_loader.py tests/test_runtime_bootstrap_loader_hardening.py tests/test_runtime_transaction_store.py tests/test_runtime_transaction_store_atomic.py
python -B -m pytest -q -p no:cacheprovider tests/test_runtime_fencing_store.py tests/test_runtime_fencing_store_hardening.py tests/test_runtime_control_lease_transition.py tests/test_runtime_fencing_transition_recovery.py tests/test_runtime_fencing_initial_transition_recovery.py
python -m ruff check codev_platform/runtime_root_binding.py codev_platform/_runtime_managed_file_contract.py codev_platform/_runtime_managed_file_bound.py codev_platform/runtime_managed_file.py codev_platform/runtime_storage.py codev_platform/runtime_bootstrap_loader.py codev_platform/runtime_control_lease_transition.py codev_platform/runtime_control_lease_transition_recovery.py codev_platform/runtime_control_lease_persistence.py codev_platform/runtime_fencing_store.py codev_platform/runtime_store_protocols.py codev_platform/runtime_transaction_store.py tests/test_runtime_root_binding.py tests/test_runtime_managed_file.py tests/test_runtime_managed_file_concurrency.py tests/test_runtime_managed_file_bound.py tests/test_runtime_bootstrap_loader.py tests/test_runtime_bootstrap_loader_hardening.py tests/test_runtime_fencing_store.py tests/test_runtime_fencing_store_hardening.py tests/test_runtime_control_lease_transition.py tests/test_runtime_fencing_transition_recovery.py tests/test_runtime_fencing_initial_transition_recovery.py tests/runtime_store_race_support.py tests/test_runtime_transaction_store.py tests/test_runtime_transaction_store_atomic.py
python -m ruff format --check codev_platform/runtime_root_binding.py codev_platform/_runtime_managed_file_contract.py codev_platform/_runtime_managed_file_bound.py codev_platform/runtime_managed_file.py codev_platform/runtime_storage.py codev_platform/runtime_bootstrap_loader.py codev_platform/runtime_control_lease_transition.py codev_platform/runtime_control_lease_transition_recovery.py codev_platform/runtime_control_lease_persistence.py codev_platform/runtime_fencing_store.py codev_platform/runtime_store_protocols.py codev_platform/runtime_transaction_store.py tests/test_runtime_root_binding.py tests/test_runtime_managed_file_bound.py tests/test_runtime_bootstrap_loader_hardening.py tests/test_runtime_transaction_store_atomic.py tests/test_runtime_control_lease_transition.py tests/test_runtime_fencing_transition_recovery.py tests/test_runtime_fencing_initial_transition_recovery.py
git diff --check
```

期望：同一 ACTIVE record 的 takeover 与 retire 只有一个 CAS 成功；root 被替换时不成功、不写入替换命名 root，所有后续绑定操作 fail closed。

**完成证据（2026-07-21）：** 已先后观察 descriptor-bound 根、bootstrap/fencing 接线、pending transition 收敛及跨目录 history 身份校验的真实 RED，并以最小实现收敛。最终 Windows 聚焦集为 `16 passed, 163 skipped`；Ruff、Ruff format、`compileall`、工作区与暂存区 `diff --check` 均通过，所列 26 个 Python 文件均严格小于 600 行。WSL `/usr/bin/python3` 缺少 pytest，未安装依赖；改以临时目录标准库 fake-pytest wrapper 实际调用真实测试函数，覆盖 initial（空 current 与 tombstone 前驱）、takeover、retire 的 prepare/freeze/CAS 三窗口、history 跨目录身份拒绝、根替换、takeover-vs-retire 竞争与 initial 根替换，全部通过。独立终审先发现首次 initial 的 `expected=None` O_EXCL 分支缺少三窗口覆盖，已拆入专属测试并复审关闭；最终安全/并发复审无 Critical、Important、Minor。未暂存、未提交、未推送，未操作服务、数据库或真实运行态。

---

### Task 4（任务 4）：受控 transaction records、terminal evidence 与精确清理

**文件：**

- Create: `codev_platform/runtime_transaction_controlled_store.py`
- Create: `codev_platform/_runtime_transaction_controlled_validation.py`
- Create: `tests/runtime_transaction_controlled_store_support.py`
- Create: `tests/test_runtime_transaction_store_postlease.py`
- Create: `tests/test_runtime_transaction_store_postlease_hardening.py`

`_runtime_transaction_controlled_validation.py` 仅承载严格 codec、跨记录绑定校验和受控错误边界；store 仅编排 gate、同一 `BoundRuntimeRoot` 与 `_at` I/O。`runtime_transaction_controlled_store_support.py` 仅承载两个测试模块共享的真实 bootstrap / gate 夹具，不从测试模块导入私有函数。为保持测试单一职责和每文件严格小于 600 行，基础正向/流程回归与 hardening 负向回归拆分为两个测试文件。

**接口：**

```python
class RuntimeTransactionStore:
    def __init__(
        self, policy: RuntimeStorePolicy, gate: RuntimeTransactionControlGate,
    ) -> None: ...
    def write_attempt_once(
        self, attempt: DeploymentAttempt, *, proof: ControlLeaseProof
    ) -> StoredSnapshot[DeploymentAttempt]: ...
    def write_journal(
        self, journal: TransactionJournal, *, proof: ControlLeaseProof,
        expected_sha256: str | None,
    ) -> StoredSnapshot[TransactionJournal]: ...
    def write_envelope_once(
        self, envelope: RecoveryEnvelope, *, proof: ControlLeaseProof
    ) -> StoredSnapshot[RecoveryEnvelope]: ...
    def publish_active_envelope_if_absent(
        self, envelope: RecoveryEnvelope, *, proof: ControlLeaseProof
    ) -> StoredSnapshot[RecoveryEnvelope]: ...
    def write_terminal_evidence_once(
        self, evidence: TransactionTerminalEvidence, *, proof: ControlLeaseProof
    ) -> StoredSnapshot[TransactionTerminalEvidence]: ...
    def load_journal(self, attempt_id: str) -> TransactionJournal: ...
    def load_terminal_evidence(
        self, attempt_id: str,
    ) -> TransactionTerminalEvidence: ...
    def clear_terminal_envelope_if_current_tombstone(
        self, retired: ControlLeaseSnapshot,
    ) -> None: ...
```

- [x] **步骤 1：在 post-lease 专属测试文件写 terminal evidence 先行的 RED。**

```python
def test终态证据先于completed_journal持久化且孤儿证据不代表完成(
    tmp_path: Path,
) -> None:
    store, active_journal, evidence, proof = _terminal_store_input(tmp_path)

    store.write_terminal_evidence_once(evidence, proof=proof)

    assert store.load_terminal_evidence(active_journal.attempt_id) == evidence
    assert store.load_journal(active_journal.attempt_id).status is TransactionJournalStatus.ACTIVE
```

- [x] **步骤 2：运行 RED。**

```powershell
python -B -m pytest -q -p no:cacheprovider tests/test_runtime_transaction_store_postlease.py
```

期望：受控 transaction store、terminal evidence、active envelope 和 journal CAS 接口缺失而失败。

- [x] **步骤 3：实现 post-lease transaction store。** 新模块只接收与构造 `ControlLeaseStore` 时**同一个实例**的 `RuntimeStorePolicy` 和 `RuntimeTransactionControlGate`，内部组合 Task 3 的 `PersistedActiveBootstrapLoader` 读取持久 bootstrap；不得注入任意 reader，也不得导入或持有具体 `ControlLeaseStore`。带 proof 的普通受控写入仅在 `with gate.mutation(proof) as scope` 中取得 ACTIVE `RuntimeMutationScope`，清理仅在无 token 的 `with gate.terminal_cleanup(tombstone) as scope` 中取得精确 RETIRED `RuntimeTerminalCleanupScope`。进入每个 scope 后先执行 `policy.root_binding.require_bound(scope.bound_root)`；受控写入/清理路径绝不自行 `bind()`、绝不取得 `deployment_lock()` 或 `deployment_lock_at()`。Task 2 的 reservation/journal genesis/envelope bootstrap 不在本类重复实现；post-lease immutable record 仅规范字节完全相同时幂等。

无 proof 的 `load_journal()`、`load_terminal_evidence()` 与其他单一记录严格读取只可自行 `with policy.root_binding.bind()` 一次，再以 `_at` API 读取、decode、复编码等字节；它们不得获得 scope、授权、CAS、部署锁、跨记录状态决策或写入权限。预租约 bootstrap 继续留在 `runtime_transaction_store.py`，不得与 post-lease 事务类混放。

- [x] **步骤 4：实现 journal/envelope/evidence。** attempt、terminal evidence 均先经 scope 的 `bound_root` 调用 `_at` O_EXCL 发布；journal 仅允许在同一 mutation scope 中按 expected SHA 比较后 `_at` 原子替换。进入 post-lease 后 bootstrap 已是 control gate 的前置事实：`write_envelope_once()` 只能严格确认既有 per-attempt envelope 的规范字节完全相同，`publish_active_envelope_if_absent()` 必须按 expected-absent 冲突拒绝，二者不得在 lease 后首次创建或重写 bootstrap。loader、每个受管文件原语和 gate scope 必须接收对象身份相同的 `BoundRuntimeRoot`，不得从 `scope.bound_root.path` 重建路径能力。读取端必须严格 decode、复编码等字节，并验证 attempt/reservation/journal genesis 绑定。

- [x] **步骤 5：实现精确清理。** `clear_terminal_envelope_if_current_tombstone()` 必须在 `RuntimeTerminalCleanupScope` 临界区内（或进入后再次）从 tombstone attempt 路径以同一 `scope.bound_root` 严格读取 completed journal/evidence，复验二者双向摘要、yielded RETIRED tombstone 的 attempt/reservation/journal/evidence 摘要和 active envelope 绑定；不得把终态读取仅放在进 gate 之前留下 TOCTOU 窗口。随后在同一 scope 内以 `remove_managed_bytes_exact_at()` 比较/删除精确旧 active envelope。已不存在只有 current/history 仍为相同 tombstone 且没有新 envelope 时才幂等；旧 cleaner 绝不能删除新 attempt。

- [x] **步骤 6：补同 scope 回归并运行 GREEN。** 额外验证同一次普通写入中 loader、gate scope 以及 journal / evidence / cleanup 的全部关键 `_at` 原语收到同一 `BoundRuntimeRoot`；受控 transaction store 未调用旧路径锁 API、未自行 bind；不同 binding scope、失效 scope、root 替换和旧 proof 全部拒绝且替换命名 root 无新记录。补齐 journal stale-expected CAS、截断 JSON、符号链接、非普通文件、错误 proof 与受控错误翻译的负向回归。无 proof 的单记录读仅自行创建一次临时绑定且不获取 gate/锁；预租约 bootstrap 测试不因 post-lease 增量膨胀或改语义。

```powershell
python -B -m pytest -q -p no:cacheprovider tests/test_runtime_transaction_store_postlease.py tests/test_runtime_transaction_store_postlease_hardening.py
python -m ruff check codev_platform/runtime_transaction_controlled_store.py codev_platform/_runtime_transaction_controlled_validation.py tests/runtime_transaction_controlled_store_support.py tests/test_runtime_transaction_store_postlease.py tests/test_runtime_transaction_store_postlease_hardening.py
```

期望：缺少 evidence、摘要漂移、截断 JSON、符号链接、非普通文件和错误 proof 全部拒绝。

**完成证据（2026-07-21）：** 已先观察受控 store 接口缺失的真实 RED，并在独立复审发现验收覆盖缺口后，以测试先行补齐 stale CAS、截断 JSON、符号链接、非普通文件、失效 mutation/cleanup scope、旧 proof、根替换，以及 evidence 首写、active/COMPLETED journal、cleanup 三条路径的同一 `BoundRuntimeRoot` 逐调用点观测。为保持低耦合和单一职责，严格校验拆入 `_runtime_transaction_controlled_validation.py`，两个测试模块共享夹具拆入 `tests/runtime_transaction_controlled_store_support.py`；相关 Python 文件行数依次为 525、379、92、493、490，均小于 600。最终 Windows 聚焦回归为 `16 passed, 187 skipped`；WSL `/usr/bin/python3` 未安装 pytest，未安装依赖，改以临时标准库 fake-pytest wrapper 实际调用 24 条 post-lease 测试场景，全部通过。Ruff、Ruff format、`py_compile`、工作区/暂存区及未跟踪文件的空白检查均通过；独立终审为 Critical 0、Important 0、Minor 0。未暂存、未提交、未推送，未操作 WSL 服务、数据库或真实运行态。

#### Task 4 追补安全校正（2026-07-21，Task 5 审查发现同型边界）

Task 5 的伪造 gate/scope 复审表明，Task 4 原先仅验证 scope 的类型、根租约存活和 gate 的调用形状；若依赖注入对象被替换，仍可能传入存活但未来自持久 current/history 的自洽 scope。该风险与具体领域无关，不能只在 generation store 修复。

- [x] **步骤 1：写跨域 RED。** 在 `tests/test_runtime_transaction_store_postlease_hardening.py` 增加存活伪造 mutation scope 和伪造 RETIRED cleanup scope 回归；两者都必须在任何 loader、写入或删除前失败，且原有 terminal evidence / active envelope 字节不变。
- [x] **步骤 2：复用中性 verifier 与活跃锁能力。** 将 `RuntimeMutationScopeVerifier` 扩展为同时复验活动 mutation 与精确 tombstone cleanup：在 gate 已持有的同一 `BoundRuntimeRoot` 内重新读取持久 current/history、pending transition 与 bootstrap/终态绑定，精确比对 scope snapshot、lineage、proof 或 tombstone，并验证 scope 携带的 deployment-lock capability 仍与同一根租约一起存活；不 import 具体 `ControlLeaseStore`，不自行 bind 或重入部署锁。
- [x] **步骤 3：接入 transaction 编排层。** `RuntimeTransactionStore` 只构造该窄 verifier，并在 `_run_mutation()` / `_run_cleanup()` 的 scope 类型与根租约校验后调用；领域校验、文件原语和 transaction 状态机均不改动。
- [x] **步骤 4：运行定向 GREEN。** Windows 收集/静态检查与 WSL 临时 root 实际调用新增回归；同时复跑 generation 聚焦集，确认共享 verifier 未回归 Task 5。

**追补验收约束：** 此项只收紧授权能力边界，不改变历史 Task 4 的业务协议、服务运行态、数据库或索引；相关文件继续严格小于 600 行。

**完成证据（2026-07-21）：** 已先观察伪造 mutation / cleanup scope 的 RED，再以共享 `RuntimeMutationScopeVerifier`、私有 `BoundDeploymentLock` capability 和完整操作期 `hold_active()` 收紧 generation 与 transaction 两域。Windows 聚焦集与后续 Task 5 合并验证为 `18 passed, 63 skipped`；WSL 临时根直接调用 transaction post-lease hardening 与 generation 回归共 40 条场景通过。静态检查、格式检查和编译均通过；独立复审未发现 Critical、Important 或未处理 Minor。未暂存、未提交、未推送，未操作 WSL 服务、数据库、索引或 systemd。

---

### Task 5（任务 5）：generation、acceptance、permit 与 state CAS

**文件：**

- Create: `codev_platform/runtime_generation_store.py`
- Create: `codev_platform/_runtime_store_public_input.py`
- Create: `codev_platform/_runtime_generation_store_validation.py`
- Create: `codev_platform/_runtime_generation_store_state.py`
- Create: `codev_platform/runtime_control_scope_verifier.py`
- Create: `codev_platform/runtime_deployment_lock_capability.py`
- Modify: `codev_platform/runtime_storage.py`
- Modify: `codev_platform/runtime_store_protocols.py`
- Modify: `codev_platform/runtime_fencing_store.py`
- Create: `tests/test_runtime_generation_store.py`
- Create: `tests/test_runtime_generation_store_hardening.py`
- Create: `tests/test_runtime_generation_store_scope_hardening.py`
- Create: `tests/test_runtime_generation_store_evidence.py`
- Create: `tests/test_runtime_store_public_input_errors.py`
- Create: `tests/runtime_generation_store_support.py`
- Modify: `tests/test_runtime_store_layout.py`

**接口：**

```python
@dataclass(frozen=True, slots=True)
class GenerationStateSnapshot:
    state: GenerationState
    sha256: str


class RuntimeGenerationStore:
    def __init__(
        self,
        policy: RuntimeStorePolicy,
        gate: RuntimeControlGate,
    ) -> None: ...
    def write_generation_once(
        self, generation: RuntimeGeneration, *, proof: ControlLeaseProof
    ) -> StoredSnapshot[RuntimeGeneration]: ...
    def load_generation(self, generation_id: str) -> RuntimeGeneration: ...
    def write_serving_fence_once(
        self, fence: ServingFenceRecord, *, proof: ControlLeaseProof
    ) -> StoredSnapshot[ServingFenceRecord]: ...
    def write_acceptance_once(
        self,
        acceptance: GenerationAcceptance,
        *,
        fence: ServingFenceRecord,
        proof: ControlLeaseProof,
    ) -> StoredSnapshot[GenerationAcceptance]: ...
    def write_serving_permit_once(
        self,
        permit: ServingPermitRecord,
        *,
        target_state: GenerationState,
        proof: ControlLeaseProof,
    ) -> StoredSnapshot[ServingPermitRecord]: ...
    def load_state(self) -> GenerationStateSnapshot: ...
    def compare_and_swap_state(
        self,
        expected_sha256: str,
        desired: GenerationState,
        *,
        fence: ServingFenceRecord | None,
        permit: ServingPermitRecord | None,
        proof: ControlLeaseProof,
    ) -> GenerationStateSnapshot: ...
```

#### Task 5 架构修订（2026-07-21，双角色复审）

原接口缺少三个不能由调用方猜测或目录扫描补齐的事实，若继续实现会形成公开服务 fail-open 旁路：

1. `serving_fence_record_path()` 与严格 codec 已存在，但全仓没有 `ServingFenceRecord` 的持久化写入口。`GenerationAcceptance` 不包含 `ServingFenceRecord.issued_at` 或完整 fence record 摘要，不能唯一重建或定位 `serving-fences/<attempt_id>/<sha>.json`；因此必须先以 O_EXCL 写入 fence，再严格读回后写 acceptance。
2. staged permit 必须绑定未来公开状态 B 的完整摘要。原 permit 写入和 CAS 都没有 B 或 permit 的显式事实，无法证明“已持久化的 permit 正是本次 A→B 所需记录”。所以 permit writer 接收 `target_state`，公开 B 的 CAS 强制接收 permit；禁止扫描目录找候选。
3. acceptance 的审计锚点需要完整且已验证的 control lease lineage，但 `RuntimeMutationScope` 原先只含 current snapshot 与 root。`ControlLeaseStore.mutation()` 已在同一 lock 内计算 lineage 却丢弃结果；应把它作为不可变 `control_lease_lineage` 放入 scope。generation store 只消费窄 scope，绝不 import 或构造具体 `ControlLeaseStore`。
4. 仅限制“公开 B 必须有 permit”仍不足：泛化 CAS 可以先构造任意 maintenance A，再通过合法 permit 打开 B，绕过 `commit_serving()` 的旧 fence 单调性与完整转换校验。CAS 必须对每一条边调用既有纯转换函数并要求 `desired` 全值相等；`validating → maintenance steady` 显式接收 fence，以其完整摘要精确读取持久记录，绝不扫描目录。无匹配纯转换的边一律拒绝。
5. 一条维护态 A 只能存在一个 staged permit。`ServingPermitRecord` 的摘要包含 `issued_at`，仅以内容摘要建路径会允许同一 A 写入多个不同 permit；目录扫描既有竞态又违反本任务“不得扫描候选”的约束。因此新增按 `generation_state_sha256(A)` 精确寻址的不可变 stage 锚点，先 O_EXCL 写入完整规范 permit bytes，再写内容寻址记录。公开 CAS 必须同时严格回读 A 锚点与内容记录并要求两者逐字节相同；崩溃后的同值重试可补齐后者，异值重试一律冲突。
6. `RuntimeControlGate` 负责临界区和授权授予，但协议对象可由依赖注入替换，store 不能把未持久化的自洽 scope 当作真实 capability。新增中性 scope verifier：在 gate 已授予的同一 `BoundRuntimeRoot` 内严格回读 current/history，精确比对 scope snapshot、复验完整 lineage 与 proof，并拒绝遗留 pending transition。它不持有或 import 具体 `ControlLeaseStore`，不自行 bind 或重入 deployment lock；锁的所有权仍属于 gate。
7. 终审进一步证明，仅入口回读 current/history 仍无法证明 gate 实际持有 deployment flock：伪 gate 可在同 policy 下构造当前真实 snapshot、lineage 与 proof，随后让 takeover 在 verifier 与最终 `_at` 写入之间穿插。因此 `deployment_lock_at()` 必须在持锁期签发不可复制的 `BoundDeploymentLock`，退出时立即吊销；活动和终态 scope 都携带它，verifier 必须验证 capability 与 `scope.bound_root` 对象身份相同且仍活跃。该 capability 的签发/吊销独立于 lease，放入 `runtime_deployment_lock_capability.py`，由 `runtime_storage` 的 `deployment_lock_at()` 私有闭包在已成功取得真实 flock 的文件描述符后签发；不得导出或提供任何无锁校验的 issuer。私有注册表同时绑定 root 对象、锁描述符与活跃期，普通 DTO 构造、未签发对象、已释放能力或不同根一律闭锁。`ControlLeaseStore` 仅在真实 flock 临界区生成 scope；分域 store 只消费 capability，不 import 具体 gate。拥有真实活跃 capability 即意味着原 gate 的 flock 仍未释放，takeover 不可能穿插。
8. 同一 capability 还必须跨 `fork` fail closed：子进程会复制父进程的内存注册表和锁文件 descriptor，但不会共享父进程随后释放 flock 的事实。因此签发状态绑定签发 PID，`require_active()` 必须拒绝 PID 漂移，并通过 `os.register_at_fork(after_in_child=...)` 将子进程继承的所有能力立即吊销；新增真实 fork 回归，确认父进程释放后子进程不能以继承 scope 写 generation、CAS、terminal evidence 或清理 envelope。
9. `require_active()` 的一次性读取不能替代完整操作期保护：可注入 gate 若在另一线程把仍活跃 scope 交给 store 后提前退出，revoke/解锁可能落在 verifier 与最终 `_at` 写之间。`BoundDeploymentLock` 必须提供覆盖 verifier 与整个领域 operation 的 `hold_active(root)` context；其内部生命周期锁与 `deployment_lock_at()` 退出时的吊销共用，吊销/LOCK_UN 必须等待 hold 释放。generation mutation、transaction mutation 和 terminal cleanup 都在该 guard 内先复验持久事实再执行操作。新增异步 fake-gate 回归，确认提前释放不会让旧 proof 在 takeover 后写入。

10. 审计记录与运行身份有意分离：`RuntimeGeneration.generation_id` 不包含 `created_at`，`serving_binding_sha256(GenerationAcceptance)` 不包含 control 审计字段与 `accepted_at`；这两项由既有领域契约和纯领域测试明确。高层 `write_generation_once()` / `write_acceptance_once()` 仍对同一路径的完整规范 bytes 执行 O_EXCL 同值重试、异值冲突。下游 fence/permit/state 只消费运行身份投影及当前持久 lease，不能凭空推导某次调用的完整审计 bytes；绕过 store、以 runtime owner 身份直接调用底层受管原语属于受信底层写入边界，不是 store API 的授权旁路。未来若需把每个审计字节也写入运行链，必须另起冻结契约变更，将完整 generation record SHA 绑定到 `DeploymentAttempt`、将完整 acceptance record SHA 绑定到 permit/state，禁止在本任务局部改变投影语义。

初始 `generation-state.json` 仍是后续独立 `LegacyGenerationAdapter` 的 root-only、等值幂等职责；本任务的 `load_state()` 与 CAS 在文件缺失时一律闭锁，绝不新增测试夹具专用的生产初始化入口。

- [x] **步骤 1：先写接口和安全链 RED 测试。**

```python
def test围栏验收许可和公开状态必须形成同一条持久化证据链(
    runtime_generation_store: RuntimeGenerationStore,
    accepted_chain: AcceptedGenerationChain,
) -> None:
    fence = runtime_generation_store.write_serving_fence_once(
        accepted_chain.fence,
        proof=accepted_chain.proof,
    )
    acceptance = runtime_generation_store.write_acceptance_once(
        accepted_chain.acceptance,
        fence=fence.value,
        proof=accepted_chain.proof,
    )
    permit = runtime_generation_store.write_serving_permit_once(
        accepted_chain.permit,
        target_state=accepted_chain.public_state,
        proof=accepted_chain.proof,
    )

    published = runtime_generation_store.compare_and_swap_state(
        accepted_chain.staged_state_sha256,
        accepted_chain.public_state,
        fence=None,
        permit=permit.value,
        proof=accepted_chain.proof,
    )

    assert published.state == accepted_chain.public_state


def test状态CAS只允许一个并发控制器使用同一旧摘要写入(tmp_path: Path) -> None:
    root, uid, proof, expected = _generation_race_input(tmp_path)

    results = _run_generation_state_race(
        root=root,
        owner_uid=uid,
        expected_sha256=expected,
        proof=proof,
    )

    assert _success_count(results) == 1
    assert _conflict_count(results) == 1
```

- [x] **步骤 2：运行 RED。**

```powershell
python -B -m pytest -q -p no:cacheprovider tests/test_runtime_generation_store.py
```

期望：generation store 不存在而失败。

- [x] **步骤 3：先补 scope 谱系，再实现最小 generation store。** `RuntimeMutationScope` 增加 `control_lease_lineage: tuple[ControlLeaseRecord, ...]`，在 `__post_init__` 复验其末端与 current ACTIVE snapshot 精确一致；`ControlLeaseStore.mutation()` 在同一 bound deployment lock 内把已验证的 `active_lineage()` 原样放入 scope。该改动只扩展 DTO，不把持久层、loader 或锁能力泄露给调用方。

`RuntimeGenerationStore` 只接受与 `ControlLeaseStore` 共享的**同一个** `RuntimeStorePolicy` 实例和窄 `RuntimeControlGate`。带 proof 的写入只在 `with gate.mutation(proof) as scope` 内执行，先 `policy.root_binding.require_bound(scope.bound_root)`，再由中性 scope verifier 在同一根租约回读持久 current/history、lineage、pending 与 proof；所有 loader、`activation_lock_at()` 和 `_at` 文件原语均传入对象身份相同的 `scope.bound_root`。store 绝不自行 bind、重入 deployment lock、按 `Path` 重开 root 或 import/构造具体 `ControlLeaseStore`。

将跨记录严格读取、规范 bytes 对比、路径身份与领域绑定置于 `_runtime_generation_store_validation.py`，public store 只编排 scope、锁与 O_EXCL/CAS。每个持久对象必须完成“精确路径 → 受管读取 → strict decode → 重编码逐字节相等 → 规范摘要/身份”验证：

- generation 以 `generation_id` 写入/读取，持久 generation id 与路径身份必须一致；
- `write_serving_fence_once()` 先严格读取 scope attempt 和目标 generation，要求 `fence.accepted_attempt_id == scope.snapshot.record.attempt_id`、`fence.generation_id == attempt.target_generation_id`，以 `canonical_sha256(fence)` 为路径摘要 O_EXCL 写入；仅允许相同规范 bytes 的重试；
- `write_acceptance_once()` 必须严格回读传入 fence 的精确持久路径、attempt 与 generation，验证 fence/acceptance 五字段、时间顺序和 `require_acceptance_control_lease_lineage(acceptance, reservation_sha256, scope.snapshot.record, scope.control_lease_lineage)`；随后以 attempt path O_EXCL 写 acceptance，只有完全相同 bytes 可重试；
- `write_serving_permit_once()` 严格读取 target B、acceptance 和 permit 精确指向的 fence；只接受由 `prepare_serving_publication()` 从已持久 state A 得到的 B，并用 `verify_serving_permit()` 复验后，先按 A 摘要 O_EXCL 写 stage 锚点、再 O_EXCL 写内容寻址 permit；
- state CAS 在同一 mutation scope 再进入 `activation_lock_at(scope.bound_root)`，锁内严格读 A、比较 expected SHA、验证 state version/时间单调、原子 replace、严格复读。它不手写状态机，而是调用既有纯函数并要求结果与 `desired` 全值相等：public steady → `begin_switch()`；switching → `begin_validation()`；validating → maintenance steady 只能用已持久 acceptance + 显式 fence 调 `commit_serving()`；maintenance steady → public B 只能用已持久 permit 调 `prepare_serving_publication()`；进入 restricted/safety_unproven 只能分别用 `mark_restricted()` / `mark_safety_unproven()`。每条边强制对应 `fence` / `permit` 为显式 `None` 或精确对象，其他边一律闭锁。这既复用领域单一真值，又不留下任意调用方拼装安全态或公开态的旁路。

无 proof 的 `load_generation()`、`load_state()` 只允许自行短生命周期 bind 后用 `_at` 严格读取单一记录；它们不得获得 gate scope、写入或 CAS 权限。单一 state 文件通过原子替换确保读取完整性，不额外取得 activation lock；禁止隐式回退旧 Path 锁。

- [x] **步骤 4：补边界与真实进程竞争测试。** 在独立 `tests/runtime_generation_store_support.py` 放置本任务可 pickle 的链路夹具、spawn worker、单一截止时间收集和强制回收，不向已有 496 行的 control-lease race support 混入 generation 领域语义。主链语义断言留在 `tests/test_runtime_generation_store.py`，scope/root 对抗断言拆入 `tests/test_runtime_generation_store_hardening.py`，严格 evidence 叶子的截断、链接、非规范与缺失断言拆入 `tests/test_runtime_generation_store_evidence.py`，覆盖：

  - generation、fence、acceptance、permit 的同字节 O_EXCL 幂等和任意内容漂移冲突；
  - 缺持久 fence、错误 attempt/generation、错误 fence 摘要、`accepted_at <= fence.issued_at`、缺/错 lineage、接管时间窗口违反时 acceptance 闭锁；
  - target B 漂移、错误 acceptance/fence、未持久 permit、A 下已有 staged permit、公开 B 未传 permit 或传 A/错误 B permit 时 CAS 闭锁；permit 先写按 A 摘要的 immutable stage 锚点再写内容记录，公开 CAS 同时严格回读两者；仅精确 persisted permit + A→B 成功；
  - 伪造 maintenance A、非法回边、缺显式 fence 的 validating→staged steady、错误 fence/acceptance 的 commit 均闭锁；每个合法状态边只能接受对应纯函数的全值结果；
  - 缺初始 state、旧 SHA、两个真实 spawn writer、takeover 后旧 proof 对 generation/fence/acceptance/permit/state 五类写入均拒绝且无新叶子；
  - 存活但伪造的 gate/scope、伪造 current snapshot、伪造 lineage 或 proof 均在任何 generation 叶子写入前拒绝；
  - 伪 gate 即使携带当前真实 snapshot、完整真实 lineage 与有效旧 proof，只要没有仍活跃且同根的 deployment-lock capability，也必须在写入与 CAS 前闭锁；并用真实竞争回归确认旧 proof 不会在 verifier 后、最终写入前越过 takeover；
  - 未知字段、截断/非规范 JSON、符号链接、非普通文件、过期 scope、不同 policy binding、根替换；替换命名 root 不得出现 state/fence/acceptance/permit 叶子；
  - instrumentation 断言 gate、严格 loader、activation lock 与全部 `_at` 调用接收对象身份相同的 `scope.bound_root`，且 store 不自行 bind；另拆分 scope/root 边界测试至 `tests/test_runtime_generation_store_scope_hardening.py`，保证每个 Python 文件严格小于 600 行。

- [x] **步骤 5：运行 GREEN。**

```powershell
python -B -m pytest -q -p no:cacheprovider tests/test_runtime_generation_store.py
python -m ruff check codev_platform/runtime_store_protocols.py codev_platform/runtime_fencing_store.py codev_platform/runtime_generation_store.py codev_platform/_runtime_generation_store_validation.py tests/runtime_generation_store_support.py tests/test_runtime_generation_store.py
```

期望：每次状态更新均以 current control proof 与 expected SHA 关闭并发窗口；所有公开状态额外以精确 B permit 闭环，缺任何 immutable evidence 时保持维护门禁关闭。

**完成证据（2026-07-21）：** 先按 TDD 观察接口缺失、伪 capability、fork 重放、异步提前释放、内容寻址预占和公共输入错误的 RED，再实现分域 store、窄 scope verifier、私有部署锁 capability 与无状态公共输入映射。最终 Windows 聚焦集为 `18 passed, 63 skipped`；WSL `/usr/bin/python3` 未安装 pytest，未安装依赖，改以临时根直接调用真实用例，generation/transaction 共 40 条场景通过，并额外运行真实 `spawn` 的 generation-state CAS，确认恰有一个成功、一个冲突。`ruff check`、`ruff format --check` 与 `py_compile` 全部通过；本任务涉及的 19 个 Python 文件均为 34–548 行。三轮独立安全/代码复审最终为 Critical 0、Important 0、Minor 0。正式 WSL pytest 及 transaction terminal-evidence/cleanup 的直接 fork、异步提前释放覆盖留给 Task 7，不影响当前共享 verifier 的授权闭锁结论。

#### Task 5 复审边界校正（2026-07-21，公共输入错误类型）

独立终审确认锁、scope 和持久证据链没有 Critical/Important；但部分 public writer 在进入 `_run_mutation()` 前就编码领域对象，错误类型会泄露为领域 `ValueError`，而不是分域 store 的结构化拒绝。该问题不改变授权或写入语义，却会使调用方无法稳定处理无效请求；只在公共入口做窄映射，不引入宽泛 `except Exception`，不吞没受管 I/O、scope 或编排错误。

- [x] **步骤 1：写 RED。** 新建 `tests/test_runtime_store_public_input_errors.py`，覆盖 generation 与 transaction 的预编码对象类型错误必须分别映射为 `RuntimeGenerationStoreError` / `RuntimeTransactionControlledStoreError`，且 fake gate 不得被进入；在 generation hardening 集补真实证据链下 `target_state=object()` 的失败与无 stage 叶子断言。
- [x] **步骤 2：运行 RED。** 先运行新增公共输入测试，预期现状出现领域 `ValueError` 或 `AttributeError`，而不是 store error。
- [x] **步骤 3：实现最小边界映射。** 新增中性 `_runtime_store_public_input.py`，只承载“精确类型 / 领域编码 `ValueError` → 调用方提供的 store error”的无状态辅助；generation 与 transaction store 组合它但不互相 import。`write_serving_permit_once()` 在访问 target 字段前精确校验 `GenerationState`。其他运行期异常仍由既有受控路径映射，禁止扩大捕获范围。
- [x] **步骤 4：运行 GREEN。** Windows 运行新的无 POSIX 测试，WSL 临时根运行 target-state 回归与既有 generation/transaction 聚焦集；Ruff、格式和编译全部通过。

**完成证据（2026-07-21）：** 新增公共入口测试先得到 8 个 RED：领域 `ValueError` / `AttributeError` 直接泄露；最小映射后同一测试为 `8 passed`，并在 WSL 临时根复验 `target_state=object()` 不创建 stage 叶子。映射仅捕获领域编码的 `ValueError`，精确类型检查发生在字段访问与 gate 进入之前，不捕获受管 I/O、scope 或编排异常；独立复审确认该边界不存在 Critical、Important 或 Minor。

---

### Task 6（任务 6）：rollback bundle 与受保护载荷完整性

**文件：**

- Create: `codev_platform/runtime_rollback_store.py`
- Create: `codev_platform/runtime_rollback_store_factory.py`
- Create: `codev_platform/_runtime_rollback_store_validation.py`
- Create: `codev_platform/runtime_protected_payload_verifier.py`
- Modify: `codev_platform/_runtime_managed_file_contract.py`
- Modify: `codev_platform/_runtime_managed_file_bound.py`
- Create: `codev_platform/_runtime_managed_file_identity.py`
- Create: `tests/test_runtime_rollback_store.py`
- Create: `tests/runtime_rollback_store_support.py`
- Create: `tests/test_runtime_protected_payload_verifier.py`
- Create: `tests/test_runtime_rollback_store_concrete.py`
- Modify: `tests/test_runtime_managed_file.py`
- Modify: `tests/test_runtime_managed_file_bound.py`

**接口：**

```python
# 仅白盒测试编排核使用，不是公开生产 API。
class _ProtectedPayloadVerifier(Protocol):
    def verify(
        self, payload: ProtectedPayloadRef, *, root: BoundRuntimeRoot,
    ) -> None: ...


class RuntimeRollbackStore:
    def write_bundle_once(
        self, bundle: RollbackBundle, *, proof: ControlLeaseProof
    ) -> StoredSnapshot[RollbackBundle]: ...
    def recover_pending_bundle(
        self, *, proof: ControlLeaseProof
    ) -> StoredSnapshot[RollbackBundle]: ...
    def load_bundle(self, attempt_id: str) -> RollbackBundle: ...


def create_runtime_rollback_store(
    policy: RuntimeStorePolicy,
    gate: RuntimeControlGate,
    *,
    hmac_key_provider: HmacKeyProvider,
    ciphertext_context_verifier: CiphertextPayloadContextVerifier,
) -> RuntimeRollbackStore: ...
```

#### Task 6 安全绑定细化（2026-07-21，实施前复核）

`RollbackBundle` 的领域 factory 能同时取得 `BaselineObservation`，但当前运行时布局尚未持久化该对象的原文；store 不能从只有摘要的 `DeploymentAttempt` 重建现场事实，更不能把不可重建事实误报为已复验。为保持 fail-closed 与职责边界，Task 6 采用以下精确口径：

- 公共 bundle 先进行“encode → strict decode → 重编码逐字节相等”，在进入 gate 前拒绝伪造冻结 dataclass、越界路径、非规范嵌套 payload 与错误对象类型。
- 写入 scope 内严格读取冻结 `DeploymentAttempt` 与其 `baseline_generation_id` 对应的持久 `RuntimeGeneration`，验证 attempt id、plan、baseline observation SHA、原始 generation id、数据库/index 摘要和 systemd/configuration manifest；缺任一持久记录均闭锁。
- `BaselineObservation` 原文与它的外部现场事实仍是上游 `create_rollback_bundle()` 的已验证前置；本任务只确认 bundle 的摘要等于不可变 attempt 中冻结的摘要。后续若需 store 复算现场事实，必须另起版本化持久契约，禁止在本任务伪造读取来源。
- `ProtectedPayloadVerifier` 只在同一 `BoundRuntimeRoot` 内验证 payload 的真实 mode/owner/SHA/HMAC；store 不读取密钥、不将 root 降级为 `Path`、不自行实现 HMAC。写入和读取都必须调用它，拒绝即不发布或不返回 bundle。
- 具体 fd-safe verifier 独立为 `runtime_protected_payload_verifier.py`：以 `read_managed_bytes_at()` 和 `ManagedFilePolicy(mode, require_uid, require_gid)` 校验叶子，再对 public/ciphertext SHA 使用常量时间比较；HMAC 与密文保护上下文委托窄的外部 identity/context 端口。测试可用临时 HMAC key 验证端口契约，生产 store 与 verifier 均不得保存、打印或派生密钥。
- `ManagedFilePolicy` 新增向后兼容的可选 `require_gid`，并由同一 `_at` 元数据校验与受控写入共同执行；普通 store 保持默认不指定 gid，受保护 payload verifier 才显式要求 `uid=gid=0`。否则 DTO 的 root:root 仅是声明，不能成为真实文件安全边界。

#### Task 6 安全修订（二，独立复审后）

第二轮独立复审确认，原始“注入任意 verifier + final O_EXCL 后再次复验”不能作为完成口径：前者允许调用方传入空实现，后者则可能在拒绝时已经留下不可变最终 bundle。按此修订以下边界与提交协议：

- 公开 `RuntimeRollbackStore` 只能由 `create_runtime_rollback_store()` 经模块私有构造能力创建；它不接受 verifier 实例，也不能被 `verify() -> None` 的对象直接构造绕过。用于白盒单元测试的注入编排核保持私有，禁止作为生产 API。`RuntimeProtectedPayloadVerifier` 只接收 HMAC key provider，并在内部固定创建 HMAC 身份校验器，不能再注入空 HMAC `verify()` 实现。
- verifier 对每个 `ProtectedPayloadRef` 自行执行编码、严格解码、规范重编码闭环，拒绝伪造冻结 DTO；`ManagedFilePolicy` 构造、`_at` 读取以及所有 key/context 端口都处于无秘密文本的异常边界内。端口抛出的普通 `Exception` 一律映射为 `ProtectedPayloadVerificationError` 且不向公开调用方保留原始 cause。
- HMAC 输入必须域分隔并绑定正文、category、role、target_key、relative_path、mode、uid、gid 与 protection context。密文先常量时间比较 ciphertext SHA，再把等价的完整元数据 AAD 交给 context verifier；factory 的受信密文端口必须认证正文和此完整 AAD。没有真实 KMS/AEAD 后端时不得虚构具体 provider；同密文、同 context 但更换角色、目标、路径或元数据必须拒绝。
- bundle 的最终可见叶子采用两阶段协议：先安全探测 final（仅显式允许缺失父目录视为无 final，符号链接及不安全目录仍闭锁），再将完整规范 bytes O_EXCL 写入 `bundle.pending.json`；在同一 mutation scope 内再次核验全部 payload 后才将同 bytes O_EXCL 封口为 `bundle.json`。读取者只认 final leaf。崩溃或后验拒绝只留下可恢复的 pending leaf；`recover_pending_bundle(proof)` 只以 pending 自身的严格规范 bytes 为候选完成封口，不需要重建 `created_at`，绝不把检测到漂移的 bundle 当作 final 成功发布。
- 已有 final leaf 时先严格读取并比较完整规范 bytes；异值预占立即冲突，不调用 payload、HMAC 或密文上下文端口。只有同 bytes final 才重新验证其持久绑定与所有 payload。
- 两阶段协议只能缩小并显式处理 store 自身的提交窗口，不能把独立可变文件与 bundle 做成跨文件原子快照。因此 protected payload 上游发布必须是 root-owned 的 immutable/O_EXCL 叶子；每次 `load_bundle()` 仍重新验证。若未来威胁模型要求对抗任意 root 在最终封口后篡改引用文件，必须另起版本化内容快照/内容寻址契约，不能在本任务用后置校验伪造绝对保证。
- `_file_stability_identity()` 必须纳入 gid；测试补 concrete verifier 与 rollback store 的 root WSL 临时根集成、端口 OSError/TypeError 无泄露、HMAC/密文 metadata 重放、pending 恢复、异值预占零 secret-port 调用及读取期持久绑定漂移。

- [x] **步骤 1：写 rollback RED 测试。**

```python
def test回滚包拒绝越界路径摘要漂移和未验证HMAC载荷(tmp_path: Path) -> None:
    store, bundle, proof = _rollback_store_input(tmp_path)

    with pytest.raises(RuntimeRollbackStoreError):
        store.write_bundle_once(_bundle_with_outside_payload(bundle), proof=proof)
```

- [x] **步骤 2：运行 RED。**

```powershell
python -B -m pytest -q -p no:cacheprovider tests/test_runtime_rollback_store.py
```

期望：rollback store 和 payload verifier 接口不存在而失败。

- [x] **步骤 3：实现最小 rollback store。** bundle 使用 `encode_rollback_bundle()`、严格 decode/重编码与 immutable once；带 proof 的写入只在 mutation scope 内，先严格核对冻结 attempt、持久 baseline generation 与 bundle 绑定，再以 `scope.bound_root` 调用 verifier 和 `_at` O_EXCL，绝不把 scope 路径降级为 Path 后重新打开。公开 store 只能由 production factory 以受信 HMAC provider 和密文上下文端口装配；白盒替身只存在于私有编排核。每个 `ProtectedPayloadRef` 仅接受 runtime root 内相对路径，先经 concrete verifier 检查 mode/owner/SHA/HMAC/密文 AAD，再允许发布。无 proof 的读取自行取得一次短生命周期绑定根后严格读取 bundle、attempt、baseline generation 并复验全部 payload。store 不保存或推导 HMAC 密钥；未持久化的 `BaselineObservation` 原文只以 attempt 中冻结 SHA 作为上游已验证前置，不伪造复算。

- [x] **步骤 4：运行 GREEN。**

```powershell
python -B -m pytest -q -p no:cacheprovider tests/test_runtime_rollback_store.py
python -m ruff check codev_platform/runtime_rollback_store.py tests/test_runtime_rollback_store.py
```

期望：外部路径、符号链接、mode/owner/摘要/HMAC 不匹配全部 fail closed。

**完成证据（2026-07-21）：** 已先后观察缺失 production factory、端口异常泄露、伪造冻结 DTO、gid 漂移、后验失败 final 可见、既存 final 异值污染 pending、无候选 pending 无法恢复及 concrete verifier 空端口绕过等真实 RED；最终实现采用 factory-only 的公开构造链、固定 HMAC provider 身份校验、完整密文 AAD、final-first 安全探测与 `recover_pending_bundle()`。Windows 聚焦集最新为 `13 passed, 50 skipped`；相关 Python 文件均严格小于 600 行，`py_compile`、Ruff、Ruff format 与空白检查通过。WSL `/usr/bin/python3` 缺少 pytest，未安装依赖；改以临时 root 和最小 fake-pytest wrapper 实际调用正式测试函数，覆盖真实 root:root `_at` 读取、gid evidence、公开/HMAC/密文重放、端口无泄露、factory-only、pending→final 后验、final-first 零 pending 写入、无候选恢复、冲突与读取闭锁，全部通过。两名独立终审在最终修订后均为 Critical 0、Important 0、Minor 0。未暂存、未提交、未推送，未操作服务、数据库、索引或真实运行态。

---

### Task 7（任务 7）：恢复顺序、三类崩溃窗口与并发终审

**Task 7 结项检查点（2026-07-22）：** release 预计算、静态复验、跨域恢复、descriptor-bound
隔离和运行期 pidfd 生命周期闭环均已完成。所有跨进程身份字段保持严格 SHA-256；worker 不重新解析命名根。
本次仅执行收口提交、推送和索引，不扩大到历史 activation/service 路径，也不操作真实 WSL 服务、数据库或索引。

**合并回归追加（2026-07-21）：** 长序列 WSL fork 回归揭示 deployment/object capability 吊销后仍
残留在 at-fork 注册表；其已关闭 descriptor 编号被后续 pipe 复用时，子进程会误关无关 fd。先增加
“已吊销 capability 不得在 fork 时关闭复用 descriptor”的真实 fork RED，再让吊销原子移除注册表，
保留仅对仍活跃锁 fd 的 child 关闭语义。

**独立安全审查追加（2026-07-21）：** root-fd worker 还必须闭合两条运行期逃逸边界，不能因现有
336 项聚焦回归通过而省略：一是父进程崩溃或 timeout 后，worker 及其 venv/pip 孙进程不得继续在旧根
inode 写入；二是启动环境不得受 `PYTHONPATH` / user-site 污染而加载影子 worker。实施顺序为：先复用
仓内受限环境与进程组/父死亡机制，以真实父死亡和 timeout 进程树 RED 锁定；再让 base 与 release 的
worker 调度统一为“无论 worker 成功、失败或超时，均先 post-wait 复验根身份，漂移优先”。只治理
Task 7 的 root-fd worker，不借机迁移历史 activation/service 路径。

**运行期闭环设计（2026-07-21，复审后裁决）：** 首轮“独立解释器 + 进程组 + 分离 watchdog”虽然能覆盖
常规 fork 子孙，却不能可靠覆盖子孙主动 `setsid()` / `setpgid()` 后脱离原组；正常成功后若没有明确撤防，
watchdog 还可能在 PID/PGID 复用后误杀无关进程。因此该路径不再作为生产 fallback，也不以 pidfd 扫描伪称
完整 containment。

真实 WSL 探针已证明当前环境为 cgroup v2、普通 CLI 不可直接写 `/sys/fs/cgroup`，但用户级 systemd manager
可创建自动收集的 transient service，且 `KillMode=control-group` 的 `systemctl --user kill --kill-whom=all`
可实际使整个 unit 变为 inactive。终审又用真实 `setsid()` 后代证明：`ExitType=cgroup` 会在 bootstrap 已成功
返回而后代仍留在 cgroup 时继续等待 drain，watchdog 已撤防的窗口内后代可以写入；同一探针改为
`ExitType=main` 后 marker 不再出现。因此根 worker 固定为 `systemd-run --user` 瞬时 service，并显式设定
`KillMode=control-group`、`ExitType=main`、`KillSignal=SIGKILL`、`SendSIGKILL=yes`、`Restart=no`、有限
`RuntimeMaxSec`、`NoNewPrivileges` 与受限环境。bootstrap 主进程结束时 manager 以 SIGKILL 一次性清空整个
cgroup；worker、venv/pip 及其任意 fork/setsid/setpgid 后代均不能留在旧根 inode 中继续执行。

父存活真值改为 Linux pidfd，而不是可被 `fork()` 复制写端的普通 pipe：父端必须在启动 transient unit 前
成功打开自身 pidfd，并把根 fd 与父 pidfd 通过私有 Unix 域 handoff socket 的单次 `SCM_RIGHTS` 交给 scope。
父端核验 `SO_PEERCRED`、同 UID 与该 PID 的 cgroup unit 归属时，同时取得并保留已认证 bootstrap 的 pidfd，
交接前后都确认该 pidfd 未就绪；timeout、异常或中断时先以该 pidfd 终止 bootstrap，再尽力请求 manager 清空
cgroup。该同步 API 的父失联语义精确限定为**原调用进程退出**；它不把同进程并发 `execve()` 另行定义为可恢复
调用场景，因而不保留未治理的 pipe 旁路。

scope bootstrap 在业务 operation 前验证自身是 unit 的 `MainPID`，并验证 `ActiveState`、`KillMode`、
`ExitType`、`KillSignal`、`SendSIGKILL`、`Restart` 等属性精确匹配；随后只把父 pidfd、bootstrap 自身 pidfd、
完成 ACK pipe 交给 watchdog，所有 pidfd 在 operation 前从 bootstrap 关闭。watchdog 在 operation 前就绪，监听
父 pidfd 与 bootstrap 的正常完成 ACK：收到 ACK 才撤防；父 pidfd 就绪、ACK EOF/异常、常规终止或 watchdog
异常都走同一收口路径——先以 bootstrap pidfd 发送 SIGKILL，再尽力请求 systemd 对目标 unit 做
control-group 强杀。这样即使 D-Bus 请求在收口瞬间失败，`ExitType=main` 也能由 manager 在主进程退出时回收
全 cgroup；若 manager 本身完全不可用，有限 `RuntimeMaxSec` 是最后的有界兜底，不伪称即时回收。watchdog
显式解除继承的 `SIGTERM`/`SIGHUP` 屏蔽。

启动命令仍为固定绝对脚本和 `-B -I -S`，以精确环境执行；bootstrap 只插入受信源码根，拒绝
`PYTHONPATH`、user-site 和当前工作目录影子包。Linux/systemd user manager 是该安全契约的必要前置；不可证明
cgroup 或 descriptor handoff 时拒绝执行，不退回普通进程组。该边界处理意外/受限子进程逃离进程组；拥有同一
用户 systemd 管理权的对抗性代码属于操作系统账户隔离范围，不以本模块替代账户级隔离。

**本轮文件边界：** 保留 `runtime_bound_worker.py` 作为父端 API 与注册 operation；新增窄的 systemd scope
命令/身份/descriptor handoff 单元和 scope bootstrap，收敛 `runtime_bound_worker_watchdog.py` 为 cgroup
pidfd/ACK 回收器；删除不再接线的普通进程组 supervisor/guard 路径。更新
`test_runtime_bound_worker.py`、`test_runtime_bound_worker_lifecycle.py` 与 watchdog 单测，新增真实 WSL
`setsid()`、父 fork 后子进程存活、ACK 后父死亡、控制面失败 fallback、timeout、正常撤防、影子导入、无关 fd
与 cgroup 全后代清理回归。仅创建自动 collect 的
临时 user unit；不修改运行中的服务配置、数据库或索引。

**root-fd worker 生命周期闭环完成证据（2026-07-21）：** 先以真实 WSL `setsid()` 后代、父进程
`fork()` 后原父死亡、ACK 后父死亡、completion writer 被 raw `fork()` 保留及 manager 控制面失败为 RED，
再完成 `ExitType=main` + `KillSignal=SIGKILL`、父/Bootstrap 双 pidfd、ACK 优先监听和主进程 pidfd 兜底收口。
bootstrap 在 operation 前逐项校验目标 unit 的 `ActiveState`、`MainPID`、`KillMode=control-group`、
`ExitType=main`、`KillSignal=9`、`SendSIGKILL=yes`、`Restart=no`、`RemainAfterExit=no`；业务 operation
不可继承 pidfd。Windows 聚焦集得到 `15 passed, 23 skipped`；将当前源码和目标测试复制到 WSL `/tmp` 后运行
完整 root-fd 聚焦集得到 `38 passed in 17.61s`，规避 `/mnt/d` 9P 挂载偶发 I/O 阻塞；`ruff check`、
`ruff format --check`、`py_compile` 全部通过，并确认没有遗留 `codev-rootfd-*` user service。独立只读安全
复审最终为 Critical 0、Important 0。该同步 API 的父失联语义仍精确限于原调用进程退出，不承诺同进程
`execve()` 的可恢复语义；拥有同一用户 systemd manager 控制权的对抗者仍属于操作系统账户隔离边界，而非本模块
的安全承诺。root-fd worker 生命周期子项与 Task 7 其余恢复、崩溃窗口和竞争子项均已结项；同进程
`execve()` 及同账户 systemd manager 对抗者仍是明确的契约边界，不作为未处理缺陷。

**文件：**

- Create: `tests/test_runtime_recovery_store.py`
- Create: `tests/runtime_recovery_race_support.py`
- Create: `tests/test_runtime_transaction_store_lifetime.py`
- Modify: `codev_platform/runtime_fencing_store.py`
- Modify: `codev_platform/runtime_transaction_store.py`
- Modify: `codev_platform/_runtime_managed_file_bound.py`
- Modify: `codev_platform/runtime_managed_file.py`
- Modify: `codev_platform/runtime_storage.py`
- Modify: `codev_platform/runtime_isolation.py`
- Modify: `codev_platform/runtime_base.py`
- Modify: `codev_platform/runtime_build.py`
- Modify: `tests/test_runtime_bootstrap_loader.py`
- Modify: `tests/test_runtime_storage.py`
- Modify: `tests/test_runtime_base.py`
- Modify: `tests/test_runtime_build.py`
- Create: `tests/test_runtime_isolation_bound.py`

**执行调整（2026-07-21）：** 已有 `tests/test_runtime_fencing_initial_transition_recovery.py` 与
`tests/test_runtime_fencing_transition_recovery.py` 已覆盖 `initial/takeover/retire ×
after_prepare/after_freeze/after_cas` 的真实 spawn 崩溃收敛，Task 7 不重复实现该层。
本任务仅补跨域恢复状态矩阵、两个接管者/终态请求/清理与新 attempt 的真实竞争，以及
transaction 的 fork 与异步释放生命周期回归。现有 `runtime_store_race_support.py` 已接近
600 行且只承担 control-lease worker；跨域 worker 放入新的窄 `runtime_recovery_race_support.py`，
不得继续向既有 helper 或 bootstrap 测试文件堆叠。

**正式 WSL 基线补充（2026-07-21）：** 以隔离 pytest 环境运行完整聚焦集时，未出现卡死，得到
`221 passed, 20 failed, 1 skipped`。新增恢复/生命周期场景均通过；20 个失败暴露的是同一轮
descriptor-bound 重构尚未迁移完毕的兼容断层，必须在 Task 7 内闭合，不能通过放宽断言掩盖：

- transaction public facade 将严格 bootstrap loader 的结构化失败重新泛化，丢失安全原因；
- bootstrap loader 的两条既有测试仍指向重构前的 private seam，且属主拒绝的断言没有表达新的根租约边界；
- `runtime_isolation` 仍调用已删除的 pathname `_file_lock` / `_open_regular`，并且 lock-path 的
  `ManagedFileError` 被泛化后丢失“符号链接”语义。

处理原则：先保留失败用例作为 RED；transaction 只做受控错误翻译，测试只切到真实的职责归属；
isolation 必须接入已有的 bound root / 受管文件机械层，禁止恢复旧 pathname 锁算法或复制文件 I/O。

**isolation 迁移裁决（2026-07-21）：** 不能只给旧 `_file_lock` / `_open_regular` 加兼容别名：
Path 入口在外层 `id_lock` 已绑定旧根、隔离内部二次绑定新根时会形成“锁 A、写 B”的替换窗口。
改为以下窄链路：

1. `id_lock()` 在不影响既有忽略返回值调用方的前提下 yield 同一 `BoundRuntimeRoot`；`runtime_base` 与
   `runtime_build` 的已持锁分支以 `as bound_root` 将它传给新 `*_locked_at()` 隔离入口。
2. `runtime_isolation.py` 只接受该活跃 root；预检、intent、journal、目录移动和恢复均相对同一 descriptor
   操作。它不重新 bind、不重新取得 ID 锁，也不持有 lease/业务领域状态。
3. 文件 intent 复用 `create/read_optional/remove_exact_managed_bytes_at`；journal 复用扩展后的
   `open_managed_regular_descriptor_at(read_write=True)`；固定 journal lock 由 storage 的 `_at` 锁入口提供。
4. 目录移动复用受管目录 descriptor 遍历与既有 `rename_noreplace_at`，前后 fsync 两个父目录；禁止
   `os.replace` 及“先 exists 再覆盖”的竞态。任何 root 漂移在下一次 descriptor I/O 或返回前都闭锁，
   不返回成功、不触碰替换命名根。
5. 将旧 storage 文件中的 isolation 测试移至独立小文件，补 root 替换、intent 恢复、无覆盖移动和
   符号链接闭锁；保留原有业务可观察结果和崩溃恢复语义。

**Task 7 终审阻断与根治修订（2026-07-21）：** 首轮 isolation 迁移虽然已经把隔离本体收敛到同一
`BoundRuntimeRoot`，但独立复审确认还有三个不能以“写前复验可见路径”修补的边界：

1. `runtime_base.py` / `runtime_build.py` 以及其静态复验路径仍在 ID 锁内使用 `Path` 重开命名根。
   根目录在锁成功后被 rename/replacement 时，锁对应旧 inode，`mkdir`、venv、pip、封存或复验可能
   触及新命名根；一次性 `verify_visible()` 不能消除随后 I/O 的 TOCTOU。
2. `*_locked_at()` 只接收 `BoundRuntimeRoot`，无法机械证明调用者仍持有同一对象的**排他** ID flock；
   shared lock 或任意同根 binding 的误调用可能与构建者并发隔离。
3. 旧版 `runtime-storage.jsonl` 创建 mode 受 umask 影响。安全的既有 `0600` / `0640` journal 被新
   精确 `0644` 策略拒绝，会阻断 pending intent 与半成品恢复。

裁决为引入两个窄职责单元，而不是在调用点叠加路径复验，也不使用 `/proc/self/fd`：

- `runtime_object_lock_capability.py` 仅签发、校验、吊销与 fork 后失效 `BoundRuntimeObjectLock`。
  capability 绑定同一个 `BoundRuntimeRoot`、kind、object_id、共享/排他状态、锁 fd 与签发 PID；
  `runtime_storage` 在真实 `flock` 成功后私有签发，在 `LOCK_UN` 前吊销。隔离入口只接受活跃、同根、
  同身份的排他 capability，不能由 DTO、裸 root、shared lock 或跨进程对象伪造。
- `runtime_bound_worker.py` 只负责以 `pass_fds` 接收根目录副本 fd，进程启动后立即 `os.fchdir(fd)`，
  再以注册的、无任意 import/callback 的 operation 执行现有 base/release 构建或复验内核。worker 仅接收
  安全相对运行时路径；不得 `preexec_fn`、不得将 fd 转成 `/proc` 路径。父进程在启动前和等待后均复验
  `BoundRuntimeRoot`，可见根漂移即使 worker 已完成也必须返回结构化失败，绝不把成功结果交给调用方。
  所有外部 venv/pip 和对象封存也在该 cwd 中执行，故继承子进程仍只面向旧 inode。

本修订的范围边界是 Task 7 已直接触及的 base/release 构建、完整静态复验与隔离恢复链；其他历史
activation/service 路径不在本任务中伪称已迁移。发现的同型调用点将登记为后续独立计划，不能以本次
验收掩盖。所有新增 Python 文件继续严格小于 600 行。

**新增验收矩阵：**

| 场景 | 必须证明的结果 |
|---|---|
| root 在 worker 进入锁后被替换 | 新命名根没有 `bases` / `releases` / `journal` 副作用；调用返回根漂移错误。 |
| worker 正常完成且根未漂移 | 元数据严格 decode，父进程只在 post-wait 可见性复验后返回。 |
| shared lock、伪造 lock、过期 lock 调隔离 | 在 intent、journal、quarantine 前 fail closed。 |
| 排他 lock 调隔离 | 同一 capability 身份贯穿 intent、移动、journal 与精确删除。 |
| journal 既有 `0600`、`0640`、`0644` | 恢复成功且保持原收紧 mode；`0660`、硬链接、uid 错误、符号链接继续拒绝。 |
| intent 已 durable 但未 rename / 目标预存在 / 源 inode 漂移 / move 后根替换 | 不误删 source、不覆盖目标、不在替换根创建 quarantine，intent 只在可证明状态下精确收敛。 |

**验收场景：**

| 持久化观察 | 允许动作 |
|---|---|
| ACTIVE journal + ACTIVE lease + active envelope | root recovery 授权同 attempt takeover，再按 journal directive 恢复。 |
| COMPLETED journal/evidence + ACTIVE lease | 后继 lease 复验 lineage 后退休。 |
| COMPLETED journal/evidence + RETIRED lease + active envelope | 仅精确清 envelope。 |
| RETIRED lease + 无 active envelope | 才允许新 attempt 获取 initial lease。 |
| ACTIVE lease + 无 active envelope，或绑定不一致 | fail closed，不自动开启新 attempt。 |

- [x] **步骤 1：写跨域恢复矩阵、barrier 竞争与生命周期 RED 测试。** 复用 Task 3 的 spawn-safe race 原则；除现有接管/退休覆盖外，补两个接管者、两个终态请求、失败方无 orphan history，以及 tombstone cleanup 与新 attempt active-envelope 发布的跨域竞争。每个 worker 都在子进程内重建 policy/store，单一截止时间收集结果并强制回收进程；不得用线程或串行调用替代真实 flock 竞争。另在独立 transaction 生命周期测试中补 terminal-evidence / cleanup 直接 fork 重放与异步 fake-gate 提前释放回归，验证共享 verifier 在两条 transaction 操作路径上的完整锁生命周期。

```python
def test退休与接管竞争同一active摘要时只有一个CAS成功(tmp_path: Path) -> None:
    store, active, proof = _active_lease_input(tmp_path)

    results = _race_takeover_and_retire(store, active, proof)

    assert _success_count(results) == 1
    assert _cas_conflict_count(results) == 1
```

- [x] **步骤 2：运行 RED。**

```powershell
python -B -m pytest -q -p no:cacheprovider tests/test_runtime_recovery_store.py
```

期望：尚未实现的恢复读取、精确 clear 或竞争 CAS 使测试失败。

- [x] **步骤 3：仅补最小收敛逻辑。** 不引入自动 owner-death 检测；测试显式提供 root recovery 授权。所有恢复分支先严格加载 immutable history/journal/evidence，再执行唯一允许的 store 操作；任何缺记录、摘要漂移、旧 proof 或 envelope 不一致均停止并报结构化错误。

- [x] **步骤 4：执行最终验证。**

```powershell
python -B -m pytest -q -p no:cacheprovider tests/test_runtime_store_layout.py tests/test_runtime_root_binding.py tests/test_runtime_managed_file.py tests/test_runtime_managed_file_bound.py tests/test_runtime_bootstrap_loader.py tests/test_runtime_bootstrap_loader_hardening.py tests/test_runtime_fencing_store.py tests/test_runtime_transaction_store.py tests/test_runtime_transaction_store_postlease.py tests/test_runtime_transaction_store_postlease_hardening.py tests/test_runtime_generation_store.py tests/test_runtime_rollback_store.py tests/test_runtime_recovery_store.py tests/test_runtime_storage.py
python -m ruff check codev_platform/runtime_root_binding.py codev_platform/_runtime_managed_file_contract.py codev_platform/_runtime_managed_file_bound.py codev_platform/runtime_managed_file.py codev_platform/runtime_storage.py codev_platform/runtime_store_protocols.py codev_platform/runtime_bootstrap_loader.py codev_platform/runtime_control_lease_persistence.py codev_platform/runtime_fencing_store.py codev_platform/runtime_transaction_store.py codev_platform/runtime_transaction_controlled_store.py codev_platform/_runtime_transaction_controlled_validation.py codev_platform/runtime_generation_store.py codev_platform/runtime_rollback_store.py tests/test_runtime_store_layout.py tests/test_runtime_root_binding.py tests/test_runtime_managed_file_bound.py tests/test_runtime_bootstrap_loader.py tests/test_runtime_bootstrap_loader_hardening.py tests/test_runtime_fencing_store.py tests/test_runtime_fencing_store_hardening.py tests/runtime_store_race_support.py tests/runtime_transaction_controlled_store_support.py tests/test_runtime_transaction_store.py tests/test_runtime_transaction_store_postlease.py tests/test_runtime_transaction_store_postlease_hardening.py tests/test_runtime_generation_store.py tests/test_runtime_rollback_store.py tests/test_runtime_recovery_store.py
git diff --check
```

- [x] **步骤 5：WSL 验收。** 在不触及真实服务目录的临时 runtime root 下运行同一聚焦 pytest 集合，确认 POSIX `fcntl`、O_EXCL、文件与父目录 fsync、barrier 竞争不被 skip；记录实际通过数和未覆盖风险。当前前置缺口为 WSL `/usr/bin/python3` 尚无 `pytest`，仅可在可用的隔离测试环境中补齐，不得改动实际服务、数据库或索引。

**Task 7 最终验证与收口证据（2026-07-22）：** 本地完整 `tests/test_runtime_*.py` 聚焦集得到
`1676 passed, 458 skipped`；全部 139 个本次变更 Python 文件的 `ruff check`、`ruff format --check` 与
`py_compile` 通过。WSL 临时根已分别验证 root-fd worker 生命周期 `38 passed in 17.61s`，以及恢复、
transaction 生命周期、isolation 与 object-lock capability `41 passed in 8.54s`，且未遗留
`codev-rootfd-*` user service。一次 WSL 完整核心集在 68% 时因宿主重启清空 `/tmp` 测试环境而中断，
输出中未出现 pytest 失败；该环境事件不被误记为全绿结果。用户已明确确认 Task 7 无问题并要求收口，
故 Task 7 的代码计划在不进入 Task 8 的前提下完成；后续运行态收口按下节受控执行。

### Task 7 运行态收口（2026-07-22，用户明确授权）

**目标：** 将 WSL `origin/dev` 上当前已推送的 Task 7 修复提交作为唯一活动 release，通过既有维护状态机恢复 reindex 与 CodeGraph，并使 `chroma`、`codegraph`、`ingest`、`code_vec` 四类 manifest 精确指向该提交。首个收口提交为 `109c8a182c7166809a93f8e29ebaeeb92882b19a`，对象访问适配器修复已推进至其子提交 `e01d5603370ac81d25e12fa64f3c75678b158a9a`；若本节的最小阻断修复产生新的子提交，则目标只前移至该提交，baseline 固定为其父提交。

**等级与范围：** L4 运行态发布/索引恢复；仅触及 WSL systemd 受控 release、reindex 队列和索引产物。正式恢复被 Task 7 实现缺口阻断时，只允许以独立 RED/GREEN 提交修复该阻断点；不进入 Task 8、不推送 GitHub。

**适用规则与约束：** 遵循 `workflow.md`、`ai-tools-mcp.md` 及 `reindex-isolated-maintenance-wsl-runbook-2026-07-13.md`。禁止手工删除维护 marker、hold、runtime mask 或 systemd drop-in；禁止直接 `systemctl start` worker/CodeGraph；禁止把旧 WSL 工作树直接拉到新提交；不初始化 owner，除非 WSL 原生状态机明确证明其为首次 bootstrap。

**有序步骤与回退：**

1. [x] 提交全部用户授权文件并仅推送 WSL `origin/dev`：`109c8a182c7166809a93f8e29ebaeeb92882b19a`。
2. [x] 诊断旧 release 维护态：旧 release 的 CodeGraph 已处于不完整 runtime mask/`Restart=no` 状态，旧状态机无法安全重新 prepare；保持 fail-closed 停机边界。
3. [-] 从 WSL Gitea bare 接收仓建立上述完整 SHA 的独立 release worktree 与独立 runtime venv，执行来源、依赖和 CodeGraph CLI 探针。worktree 已于 2026-07-22 创建、HEAD 精确为目标 SHA 且干净；独立 venv 已创建。发现正式 installer 的 runtime binding 前置失败后，已终止不再能推进收口的候选依赖安装；候选目录保留，未启动服务、未改写索引。
4. [-] 使用新 release 的受控 systemd staged transaction 替换两个固定 unit 的有效载荷，并通过新维护状态机完成 `prepare`、必要的 migration/owner 演练、`restore` 和 `resume-codegraph`；任一步失败均保留维护态并从保留的旧 release 受控回退，绝不手工解除门禁。
5. [-] 等待受控 worker 串行消费队列，验证四类 manifest 的状态均为 `ok`、目标提交均精确等于上述 SHA、队列无 pending/active/failed，且两个 unit 的 `ExecStart` 均指向新 release。
6. [-] 运行健康检查并记录非敏感收口证据；只在四类 manifest、systemd 和健康检查全部通过后，才将本节标为完成。

**验证：** `reindex-queue status`、`wait-for-reindex --commit <完整SHA>`、四类 manifest 精确 SHA、受控 `reindex-maintenance` 证明、`systemctl show` 的 `ExecStart`/运行状态，以及 `codev-platform health`。不以“队列为空”或“服务已启动”代替完整验收。

**2026-07-22 正式 installer 阻塞：** 当前内容寻址 runtime root 的 `current` 存在但以 `-B -I` 进行只读 `runtime verify` 仍失败，故正式 systemd installer 的 release binding 必须 fail-closed；不得拿未验证 runtime 生成/执行完整 manifest。新版 `prepare_reindex_maintenance` 已可幂等接管当前 CodeGraph runtime mask 并补足 guard/hold，但它只修维护边界，不会切换 unit 的 `ExecStart`。仓内也没有“仅更新 reindex 与 CodeGraph 两个 unit”的正式 installer 入口，完整 installer 会处理完整受管 unit 清单。

**2026-07-22 原环境增量恢复结论：** 已按用户授权将原受管 venv 的可编辑源码指针无下载地切至目标 worktree，`pip check` 与 editable revision 探针均通过；但 `configure-resume-codegraph --yes` 在写入前安全拒绝。最小只读复现确认根因：恢复上下文调用 `current_release_interpreter_identity()`，该门禁只接受带完整 release/base/release_id 证据的 `mode=release`；可编辑 venv 必然为 `mode=editable`，因此“复用原 venv + 切源码指针”与 CodeGraph 受控恢复的安全契约不可同时成立。该试验不得继续重试，必须先用同一无依赖命令恢复旧源码指针并复证旧 revision。

**2026-07-22 内容寻址运行时根因与最小恢复裁决：** 对既有内容寻址 runtime 执行只读复验后，当前 release 和其 base 均在对象访问模式证明处失败；release 树 1,956 项中有 1,951 项权限漂移，属于未按受管对象策略封存的历史制品，而非 reindex 重复构建或队列问题。现有精确 freeze、旧 base 内 requirements lock 与目标提交完全一致（均为 143 pins），并且 `/srv/codev-artifacts` 中已有通过 hash/文件集合复验的 root-owned lock 与 wheelhouse；Torch CUDA 探针也已证明 1 张 GPU 可用。故不下载、不升级、不重新解析依赖，改为使用既有离线 wheelhouse 走以下一次性最小修复：

1. 自动隔离损坏的同 ID 历史 base/release 证据；禁止人工 chmod、删除或修改已完成对象。
2. 以正式 `runtime base --wheelhouse` 原语在同一 `base_id` 下离线重建并完整复验共享依赖基座；该步骤创建一次受管 venv，但 pip 固定为 `--no-index`，不会拉取依赖。
3. 从目标提交父提交构建一个薄 baseline release，再从目标提交构建薄 target release；两个 release 共享上一步的 base，依次激活形成可验证的 `current/previous` 回滚对。后续同依赖提交只构建 wheel/薄 release 并复用该 base，不再重新安装 143 个依赖。
4. 仅在 target release `runtime status`、release/base 复验均为 `mode=release` 后，使用 target release 的既有 `configure-resume-codegraph/restore/resume-codegraph` 状态机写入并恢复 reindex 与 CodeGraph 两个固定 unit；仍禁止裸 `systemctl start`、手工 marker/drop-in/hold/mask、全量 retired `runtime deploy` 入口及 Task 8 代码。

**回退与验证：** 任一离线 base、baseline、target 或 unit 配置步骤失败，保持已证明的维护停机态，不删除 quarantine/`.incomplete`/历史对象；只有四类 manifest、队列、两个 unit 的有效身份及健康检查均通过，才标记 Task 7 运行态收口完成。

**2026-07-22 root-fd 对象访问适配器修复（Task 7 范围内）：** 首次离线 base 重建实际暴露一个未覆盖的异常边界：`runtime_bound_worker_object_access.py` 正确保持通用 `RuntimeObjectAccessError`，但 base/release 内核的既有自动隔离分支分别只捕获 `RuntimeBaseIntegrityError` / `RuntimeBuildError`。worker 直接注入通用 CWD 访问函数后，历史完成对象的权限漂移被泛化为 worker 失败，未进入 quarantine，违反“损坏已完成对象自动隔离后重建”的既有契约。

裁决采用两个窄领域适配器：base worker 仅把 CWD seal/verify 的通用访问错误转换为 `RuntimeBaseIntegrityError`，release worker 仅转换为 `RuntimeBuildError`；通用 fd-tree/对象访问层、隔离状态机和内核 catch 条件一律不改。先以真实 worker port 的损坏完成对象写 RED，确认失败因未触发 isolate；再做最小转换并运行 base/release root-fd 聚焦测试、WSL 临时根回归和此次离线 base 重试。该修复不进入 Task 8，不放宽任何权限、对象身份或隔离门禁。

**2026-07-22 root-fd worker 受控依赖加载阻断（Task 7 范围内）：** 对象访问适配器已进入 WSL 根控制器快照后，第二次离线 `runtime base` 仍在约一秒内退出，且没有产生 isolation intent、quarantine 或 incomplete 记录。通过同一 root-owned 控制器、同一 `systemd-run --user`、同一 `env -i`、同一解释器的只读最小导入探针，已稳定复现 `ModuleNotFoundError: No module named 'pip'`。故失败发生在 base/release 隔离状态机之前，而非自动 quarantine、wheelhouse、GPU、队列或服务启动阶段。

根因是 `runtime_worker_scope.build_worker_scope_command()` 对主 worker 同时使用 `-I` 与 `-S`：前者已隔离环境、用户 site 和导入路径，后者额外关闭受控 release venv 的 site-packages；而 wheel tag 解析在干净 venv 的既有设计中会回退到 `pip._vendor.packaging`。两者组合使受信 venv 中的 pip 不可见，违背了该回退的依赖契约。裁决为仅从**主 worker**命令移除 `-S`，保留 `-I`、`env -i`、`PYTHONNOUSERSITE=1`、空 `PYTHONPATH`、root-owned release/base、`NoNewPrivileges` 与全部 systemd cgroup 限制；watchdog 仅用标准库，继续保留 `-S`。已按 TDD 新增命令契约 RED，得到预期的 1 个失败（第四参数仍为 `-S`）；最小 GREEN 后 `tests/test_runtime_worker_scope.py` 为 `7 passed`，worker 聚焦回归为 `13 passed, 22 skipped`，Ruff、格式和 diff 检查均通过；WSL 同受控环境的 `-B -I` 导入探针已输出 `worker_import_probe=ok`。下一步才重建根控制器快照、重跑同一导入探针与离线 base。若验证失败，保持维护停机态，不手工修改历史对象或启动 unit。

**2026-07-22 root-fd release 相对路径闭环阻断（Task 7 范围内，进行中）：** 在上述修复进入根控制器快照并通过同构导入探针后，正式离线 baseline `runtime stage` 运行约 46 秒后失败。共享基座 `1200f0e3edf1ba7205e0f6bda615b1923587bee0ee486fa086f16376c74b37e3` 已由正式只读复验直接通过，故 `runtime_stage_failed kind=base` 只是 stage 外层的错误边界，不表示 base 损坏。失败 release 保留 `.incomplete=after_marker`、已创建 venv、尚无 `release.json`；按既有状态机，下一次同一 formal stage 必须先隔离该 incomplete 对象，禁止手删或重跑同一失败路径。

根因已由真实 worker 语义和两份独立只读审计确认：`runtime_bound_worker_release.stage_release()` 在继承 root fd 后故意 `fchdir()` 并用 `Path(".")` 作为所有受管 I/O 根；`stage_environment()` 却将子 Python 报告的绝对 `purelib` 与相对 `venv` 比较，`purelib.is_relative_to(venv)` 因表示域不同必为假，正好解释 marker 停在 `after_marker`。只修这一个比较仍会依次触发 base purelib、base 相对链接、metadata `relative_to()`、release 静态复验的 base link 与 `.pth` 比较等同类错误；泛用 execution-trust/base 复验若在 worker 中重新绝对化 `Path(".")`，则会把受管 I/O 重新绑定回命名根，违背 Task 7 descriptor-bound 目标。

裁决不采用“把 worker 全部改为绝对路径”（会失去 fd 锚定），也不采用“对两边都 `resolve()`”（只掩盖首个类型错误并重新打开命名根）。选用双表示的窄边界：

1. `io_path` 是以 `Path(".")` 为锚的严格相对受管路径；worker 内所有 mkdir、open、stat、链接验证、目录遍历和子进程 argv 只使用它。直接调用路径继续保留既有绝对 `io_path`。
2. `reference_path` 只由已验证的绝对 runtime payload 词法派生，仅用于比较 `.pth` 的序列化文本；不得用于 runtime-root 文件 I/O。`.pth` 继续保存绝对 base purelib，保证激活后不依赖启动 cwd。
3. venv 子进程只返回 `purelib.relative_to(sys.prefix)` 的严格 POSIX 相对片段；父进程在 `venv` 的 `io_path` 下派生目录并拒绝绝对、空、`.`、`..`、反斜杠或符号链接组件。base link 以精确 `readlink` 文本加 `(st_dev, st_ino)` 身份相等验证，不再以 `resolve()` 混合表示。
4. worker 专用 cwd 门禁先复验 `.`、`bases/`、`releases/` 的直接布局均为 root 所有、不可被 group/other 写入的非链接目录；随后 execution-trust 入口只接受 cwd-relative 对象与路径，并以与绝对入口等价的树级权限/链接证明覆盖即将导入的 release/base 内容。绝对 API 保留给非 worker 直接调用，两个入口不互相把路径转换到另一种表示。
5. 动态构建期不得让子 Python 通过最终命名 runtime reference 导入 base：在 root-fd worker 内，构建、安装和深探针使用只指向继承 cwd 旧 inode 的临时 base reference；只有动态命令均成功且 binding 仍可见时，才原子改写为激活后需要的最终命名 `.pth` reference。根替换时必须在任何错误 base 导入前失败。

**本次文件职责与有序 TDD：**

| 文件 | 责任 |
|---|---|
| `codev_platform/runtime_release_environment.py` | 严格派生 release/base 的相对 I/O 路径；区分动态阶段的 cwd 稳定 reference 与激活后的最终 `.pth` reference，并选择对应 execution-trust 入口。 |
| `codev_platform/runtime_release_paths.py` | release 元数据相对路径、完成标记、绝对 reference 与链接目录身份的纯路径契约；不执行构建、子进程或对象 I/O 编排。 |
| `codev_platform/runtime_build.py` | 在 stage/verify 边界传递 reference 路径；以链接文本和 inode 身份验证 base link，不在 worker 中 `resolve()` 根内路径。 |
| `codev_platform/runtime_cwd_layout.py` | 只证明 root-fd worker 当前目录内的 runtime 直接布局；不构建对象、不持有 binding、不打开命名根。 |
| `codev_platform/runtime_bound_worker_base.py` / `runtime_bound_worker_release.py` | 保持 `Path(".")`，在 base/release 操作前复用 cwd 布局门禁；release 仅注入已绑定的绝对 reference root 与 cwd trust 模式，不得把 root 转为命名 I/O。 |
| `codev_platform/runtime_execution_trust.py`（以及仅在基座 worker 必需时的窄适配器） | 增加 cwd-relative trust 入口与等价树级证明；绝对 API 与其既有调用方保持兼容。 |
| `tests/test_runtime_bound_worker_release.py`、`tests/test_runtime_worker_release_relative_root.py`、`tests/test_runtime_build.py`、对应 execution-trust 测试 | 先写根 fd cwd 下的相对 release stage RED、base link/static verify RED、根替换无新命名根副作用及 `.pth` 绝对文本回归。 |

1. [x] 先以可移植单测要求相对 I/O 与绝对 `.pth` reference 分离；观察到当前接口缺少 `base_purelib_reference` 的 RED。POSIX 再以真实 `fchdir` 临时根证明 cwd trust 在命名根替换后仍只读取旧 inode。
2. [x] 在不改正式 WSL 对象前运行目标 RED：环境层缺少 reference 接口；构建层缺少 `runtime_root_reference`；cwd trust 入口不存在。三者均不是 wheel、权限、网络或测试搭建错误。
3. [x] 实现双表示路径派生、链接 inode 验证和 cwd trust 分流：新增 `runtime_release_paths.py` 收敛路径契约，release/base worker 只注入 reference/cwd trust；未修改 runtime root binding、对象隔离状态机、wheelhouse、systemd unit 或索引队列。真实 WSL 临时 venv 已覆盖多级解释器链接，输出 `base_cwd_execution_trust=ok`。
4. [-] 已补齐 cwd 内 `bases/`、`releases/` 直接布局门禁、cwd execution-trust 的全树等价证明，以及动态阶段 `.pth` 仅经继承 cwd 导入 base、绑定仍可见后才原子切回最终命名 reference 的窗口闭合。完整 17 文件 release/base root-fd 聚焦集现为 `126 passed, 42 skipped`；Ruff、格式、`py_compile`、`git diff --check` 均通过，`runtime_build.py` 为 598 行。WSL 不安装 pytest，改以临时 root 的标准库实证确认 `wsl_cwd_runtime_proof=ok`、`wsl_stage_dynamic_pth_proof=ok`、`wsl_cwd_layout_proof=ok`、`wsl_rootfd_worker_layout_proof=ok`、`wsl_dynamic_pth_guard_proof=ok`、`wsl_named_reference_guard_proof=ok`。独立复审指出并已修复：5 条 POSIX root-fd 用例补齐 root-owned `bases/`、`releases/` 夹具；`verify_base_locked_from_cwd` 固定内部 `Path(".")`、移除外部 root 参数；父侧 root 请求恢复纯词法 `abspath` 归一化；删除未接线的 `staging_base_purelib_reference` seam，相对 root-fd 模式强制 cwd 动态引用、显式命名基座发布引用、binding checker、受控原子 finalizer，且这些动态前提均在 wheel/venv 子进程前验证；最终激活 reference 拒绝整个 `/proc/self/**`（含 `/proc/self/fd/<n>`）旁路。两轮独立复审均无 Critical/Important/Minor，源码阶段已签核；下一步提交、仅推送 WSL、重建控制器快照、source mirror、同构 worker 探针和 formal stage。只有 baseline/target release 均为 verified release 后，才继续既有 maintenance、队列和四类 manifest 收口。

**2026-07-22 baseline 最终 `.pth` 权限契约阻断（Task 7 范围内，进行中）：** 目标控制器 `9234f3e8263eb0e618314bb0cc1d41966ebf29a6` 下，baseline `441c6c8df3a7f45a64e06aeeebab1cf4b92a74ca` 的正式 thin release `c8422a6eea8661cb793ed86ff6d51bc0b224981015665ad9aaaa7d69e1252435` 保留为 `.incomplete=after_venv`。只读证据显示应用 wheel 已完整安装、cwd execution-trust、应用载荷校验、`pip check`、全部受控模块导入探针与 `pip freeze --all` 均通过，且 `.pth` 仍为动态 `/proc/self/cwd/...` 引用，故失败发生在深探针之后、最终原子切换之前。

根因已通过真实失败对象和最终器同一契约函数双重复现：`write_bytes_exclusive(..., mode=0o640)` 只把 mode 传给 `os.open()`，受 worker 的 `umask=0o077` 裁剪后实际生成 `0600`；`_finalize_base_pth()` 随后以 `ManagedFilePolicy(mode=0o640)` 要求既有叶子精确匹配，因而确定得到“受管文件权限与策略不一致”。这不是 base、wheel、GPU、网络、队列或导入问题，也不能通过放宽最终器策略规避；`0640` 是既有 finalizer 契约和激活期文件策略。

**最小 TDD 修复与验证：**

1. [x] 在 `tests/test_runtime_build.py` 增加 POSIX 回归：临时设定 `umask(0o077)` 后调用 `runtime_release_environment.write_bytes_exclusive(..., mode=0o640)`，断言生成文件精确为 `0640`；WSL 标准库 RED 实际输出 `red_observed_mode=0600` 并以预期断言退出。
2. [x] 仅在 `codev_platform/runtime_release_environment.py` 的该独占创建原语内，于 fd 仍打开时在 POSIX 显式 `fchmod(descriptor, mode)`；不改变 stage 状态机、root binding、managed-file finalizer、base/release ID、wheelhouse、unit 或队列接口。
3. [x] 运行新增用例、release/root-fd 聚焦回归、Ruff/格式/编译；Windows 本地 POSIX 用例按平台条件跳过，WSL 同构绿测输出 `green_observed_mode=0640`，并以临时 root 完整走“独占创建 → root binding finalizer”得到 `wsl_finalizer_umask_chain=ok`。本地扩展发布链回归为 `51 passed, 77 skipped`，Ruff、格式、`py_compile` 与 `git diff --check` 均通过；全量 `test_runtime_*.py` 因 64 秒工具时限中止且未产生断言输出，不以其替代已完成的分批结果。
4. [x] 已完成独立复审、仅推送 `origin/dev`、root-owned controller/source 快照重建；既有 `runtime stage` 已自动隔离历史 incomplete 后成功构建 baseline 与 target thin release，二者均已通过正式 `runtime verify`。未手工 chmod、删除、重跑失败对象或直接启动 unit。

**2026-07-22 权限契约修复提交与正式目标前移：** 修复已提交为
`075b0ece116d71d3e1eead14fe0eddf88a4edf37`（`fix(runtime): 固化独占创建文件权限`），并已由受控 Git hook
仅推送到本地 WSL Gitea 的 `origin/dev`；`git ls-remote origin refs/heads/dev` 已精确返回同一 SHA，未推送 GitHub。
按本节既定“最小阻断修复只前移 target、baseline 固定为 parent”规则，后续正式 target 为该 SHA，baseline 为
`9234f3e8263eb0e618314bb0cc1d41966ebf29a6`。本次 commit hook 仅将 `chroma`、`codegraph`、`ingest`、`code_vec`
入队，未启动 worker/CodeGraph，也未改写正式 runtime 对象；下一步先以 target root-owned controller 快照和同 SHA
bare source mirror 受控重建 baseline，再构建 target thin release。

**2026-07-22 旧引用阻断与显式回滚锚点 CLI 收口（Task 7 范围内，进行中）：** 正式 baseline release
`a1919461b9f64ba7995854b7bfb7417ea9df245110db8b75e537a4070c7975aa` 和 target release
`468d49219fe98c29402daf0aee71e07d087a70b3733d56299130b4016c09f6dd` 已分别通过正式 `runtime verify`，均使用同一
base `1200f0e3edf1ba7205e0f6bda615b1923587bee0ee486fa086f16376c74b37e3`。标准
`runtime activate <target>` 仍 fail-closed；只读取证显示历史 `current` 指向
`f319be62b1201bc65ac505658af657d831642332743c080078070fcf4a65746a`（revision
`9a2d70b5217122bd2ded551bfaedfa019c1d9579`），`previous` 缺失，且该历史 release 的只读
`runtime verify` 精确失败。根因不是新 target/base，而是标准 `activate_release()` 为保护旧回滚链会把
`(target, current, previous)` 全部交给 `_verified_releases()`；历史损坏对象因此阻止新双 release 成为可验证回滚对。

仓内已有 `activate_release_with_rollback_anchor(root, target, rollback)`：它在同一 activation lock 中仅深验
target 与调用方已证明的 rollback anchor、强制二者 base ID 一致，并以双链接原子替换/补偿事务覆盖悬空或不可验证的旧引用。
全仓调用检索确认该函数仅有领域实现与事务测试，尚无 CLI 公共入口。裁决不手工调用领域函数（会绕过正式 CLI 审计边界），不使用
被本任务禁止的全量 `runtime deploy`，而是在既有 `runtime activate` 上增加显式
`--rollback-anchor <完整 release ID>`：

1. 未传该参数时保持现有标准激活语义、端口与 JSON 输出不变。
2. 传入时由 CLI 在同一输入校验边界验证 target/anchor 的完整 ID，并只委派给既有显式锚点事务；不复制链接、锁、验证或补偿逻辑。
3. [x] 已在 `tests/test_runtime_cli.py` 写 RED，先证明该参数被 parser 拒绝、再证明短 anchor 会错误触发 root 解析；随后补窄端口字段、parser 与 dispatch，并将 anchor 完整 ID 校验前移到 root 解析之前。显式路径只委派既有事务，标准路径不变。
4. 仅当源码测试和 review 通过后，使用新 target controller 执行一次正式
`runtime activate <target> --rollback-anchor <baseline>`；随后只读复验 `current=target`、`previous=baseline`、两者
`runtime verify` 均通过。任一步失败，保持维护停机态并保留旧引用，不手改链接。

**2026-07-22 CLI 实现与验证证据：** 第一条 RED 的实际失败为 parser
`unrecognized arguments: --rollback-anchor …`；最小实现后，显式 target/anchor 只调用锚点端口的 GREEN 通过。第二条
RED 实际观察到短 anchor 会触发一次 `runtime_root` 解析；将 `_full_id(rollback_anchor)` 前移后 GREEN 通过，且错误 JSON
只保留已验证 target 的 object ID、不泄露短 anchor。相关回归
`tests/test_runtime_cli.py tests/test_cli_parser.py tests/test_runtime_release_anchor.py tests/test_runtime_release.py tests/test_runtime_release_locking.py`
为 `47 passed, 38 skipped`；默认端口装配探针、Ruff、格式、`py_compile` 与 `git diff --check` 均通过。独立只读审查未发现阻断项；
其建议的“默认路径不得走锚点端口”已由既有 `test_runtime_action_delegates_to_public_port` 与本次显式端口反向断言共同覆盖。

**2026-07-22 锚点 CLI 提交与目标前移：** 锚点 CLI 已提交为
`a8f828bbe308e2bc16bc36699700878495bf78a0`（`fix(runtime): 补齐显式回滚锚点激活入口`），作者为
`helloworld3q3q`，并已自动且仅推送本地 WSL Gitea `origin/dev`；`git ls-remote origin refs/heads/dev`
精确返回同一 SHA。commit hook 只将 `chroma`、`code_vec`、`codegraph`、`ingest` 入本地 worker 串行队列，
未启动 worker/CodeGraph 或改写 runtime 引用。后续正式 target 前移为该 SHA；baseline 仍固定为
`9234f3e8263eb0e618314bb0cc1d41966ebf29a6`，已验证 baseline/target thin release ID 不变，因为它们的 runtime
payload 不含此次仅 CLI 编排层变更。

**2026-07-22 显式锚点激活证据：** 已从同 SHA 的 root-owned controller/source snapshot 运行
`runtime activate <target> --rollback-anchor <baseline>`，返回
`active_release=468d49219fe98c29402daf0aee71e07d087a70b3733d56299130b4016c09f6dd`、
`previous_release=a1919461b9f64ba7995854b7bfb7417ea9df245110db8b75e537a4070c7975aa`。随后只读复验
`current=target`、`previous=baseline`，两份 `runtime verify` 均为 `ok`、共享 base
`1200f0e3edf1ba7205e0f6bda615b1923587bee0ee486fa086f16376c74b37e3`，且 `runtime status` 为
`mode=release`、target revision `075b0ece116d71d3e1eead14fe0eddf88a4edf37`。两个受管 unit 仍是
`inactive`；本步骤未启动服务、未消费索引队列。

**2026-07-22 维护状态机只读预检与恢复顺序：** 使用 target controller 的 `reindex-maintenance status` 已返回
“维护窗口已就绪：门禁、外部 worker、服务与 cgroup 均已证明安全”；以项目 `codev-platform`、完整 target commit
`a8f828bbe308e2bc16bc36699700878495bf78a0` 运行 `configure-resume-codegraph` 与 `resume-codegraph` dry-run
均成功，未写入或启动任何对象。领域状态机的顺序经源码核对如下：

1. `configure-resume-codegraph --yes` 在已证明 maintenance permit 内，以三文件可补偿事务安装两个固定 unit 的同源环境快照/drop-in；
   只写配置并 `daemon-reload`，不启动服务。
2. `reindex-maintenance restore --yes` 只按既有恢复交接启用 reindex 待命实例，使串行 worker 消费已入队的四类索引任务；
   CodeGraph 仍不通过裸启动恢复。
3. 等待四类 manifest 精确达到 a8 target commit 后，才执行 `resume-codegraph --yes`。该状态机自行重新准备 maintenance、
   在转换锁内证明 manifest/有效载荷/健康/稳定性，再受控解除 CodeGraph runtime mask/hold、启动 CodeGraph 并完成 reindex handoff。

**2026-07-22 配置上下文阻断与受控环境裁决：** 首次
`configure-resume-codegraph --yes` 安全返回“恢复配置上下文无法解析”，未进入 unit 写入事务；随后
`reindex-maintenance status` 仍为已就绪。分层只读探针证明 runtime override 正常、唯一失败点是 root 进程缺少
`CODEV_PLATFORM_CONFIG`。现有 `codev-reindex.service` 受管环境文件只包含 MCP token，不是配置路径真值；
配置真值仍是既有 `/home/user/project`。这符合恢复上下文“必须显式指定配置覆盖和数据根”的
fail-closed 契约，不能通过改为 root HOME 回退来绕过。

裁决不修改用户配置、不读取/输出 token、不 source 环境文件：由 root wrapper 仅引用该既有常规配置文件，先在隔离进程内从同一
配置推导 `PLATFORM_DATA_DIR`，校验两者均为现有绝对路径后，以 `env -i` 仅注入
`PATH`、`CODEV_PLATFORM_CONFIG`、`PLATFORM_DATA_DIR` 执行维护 CLI。该环境下
`resolve_codegraph_resume_context(codev-platform, a8…)` 已完整输出 `controlled_resume_context=ok`，并精确证明
当前 release runtime revision 仍为 `075b0ece116d71d3e1eead14fe0eddf88a4edf37`。下一步只在此已验证环境中重试一次
`configure-resume-codegraph --yes`；若失败，不重复写入，直接读取 maintenance/配置证明并保持停机态。

**当前状态与回退：** 权限契约修复、正式 base/thin release 构建及二者 verify、锚点 CLI 源码、验证、提交/WSL 推送和
target/baseline 原子激活均已完成；下一步仅探索并使用 target release 已有的受控 maintenance 状态机配置 reindex/CodeGraph
固定 unit，再恢复队列。若状态机预检、配置或恢复失败，保持 maintenance 停机态以及既有 quarantine/`.incomplete`，不手工
chmod、删除、启动 unit、重建依赖或进入 Task 8。

**2026-07-22 已否决的配置迁移假设：** 初次通用失败曾引出“发布主配置”的候选方案；安全评审已确认，若开放任意来源路径给
root 命令会扩大机密外泄面。后续 WSL 只读证据又证明 root 受管环境文件已正确，而完整用户配置含直接秘密字段；因此该迁移
方案既不安全也非当前故障所需，未实施、没有遗留代码，也不再作为 Task 7 待办。

**2026-07-22 配置发布假设校正与下一次受控验证：** 上述“主配置发布”设计是基于首次失败时的旧摘要提出的安全候选，
不能在新证据出现后直接实施。随后只读核对显示：`/etc/codev-platform/platform.env` 已存在且为 `root:root 0600`，父目录为
`root:root 0700`；它与用户来源环境文件的字节内容精确一致，二者仅含 `CODEV_PLATFORM_MCP_TOKEN` 变量名；用户
`config.json` 的 `systemd.env_file` 已精确声明 `/etc/codev-platform/platform.env`。同时，该用户 config 含多个非空的直接
秘密字段（仅检查字段名、类型和空值状态，未读取或输出值），因此将整份 config 复制到服务组可读位置既非当前恢复所必需，
也会扩大秘密暴露面，明确禁止实施。

当前两个 unit 仍只显示旧的用户 `EnvironmentFile`，是上次可补偿配置事务回滚后的正常状态，不能反推其安装期合并结果。
唯一尚未定位的失败边界为“drop-in 合并后的 systemd 重载或同源证明”，而非 `/etc` 环境文件的所有权或内容。由此替代上述
未执行的发布实现计划：先以现有、已验证的 `/etc` 环境文件和显式用户 config overlay 做**一次**新的
`configure-resume-codegraph --yes` 尝试；该动作只写已有三文件可补偿事务，不启动服务，失败会自动回到维护态。成功即不新增
配置发布代码，直接进入既有 restore 链；失败则停止重复写入，按失败阶段补最小无秘密诊断或修复，重新走 TDD/审查后再试。

**2026-07-22 二次受控尝试与最小诊断边界：** 该一次尝试再次返回“受管恢复配置失败；已回到维护状态”；随后
`reindex-maintenance status` 已重新证明维护窗口就绪，确认无服务启动、无 worker 交接、无数据库/索引写入。静态
`systemd-analyze verify` 的唯一提示是 `codev-mcp-codegraph.service` 被 mask，这是 maintenance 的预期保护，不是新的
unit 语法/路径/环境文件故障，不能据此扩展为 unit 重装或配置迁移。

现有三文件事务曾把写入、`daemon-reload` 与同源证明的底层异常统一收敛为同一无秘密文案，阻止精确修复。现已完成最小
TDD 可观测性补丁：内部只保留 `write` / `reload` / `proof` 三个固定阶段，并通过共享契约映射为中文阶段码；外层仍只输出
无秘密汇总，不输出路径、命令、配置正文、环境变量、token、DSN 或原始异常。三阶段 RED→GREEN 与无泄密回归已覆盖，相关
90 项测试、Ruff、格式、编译和 diff 检查均通过。下一步只提交并推送 `origin/dev`，同步该提交的 controller 源码快照（不构建
runtime/base/release/wheel、不下载依赖），随后最多执行一次既有受控配置尝试；失败阶段码是唯一下一轮最小修复输入，不再猜测
或平行重构。

**2026-07-22 WSL 增量索引硬门禁（用户明确要求，持续生效）：** 日常代码提交并仅推送 `origin/dev` 后，唯一默认动作是
让既有 post-commit hook / WSL 串行 worker 消费已入队 scope，增量更新目标项目的 Chroma、CodeGraph、graph ingest 与
code_vec manifest；Git push 本身不等同数据库完成，worker 停止或 maintenance 时允许 pending 保持，恢复既有状态机后继续消费。
严禁因 pending、索引滞后、MCP 召回旧提交或本次 Task 7 配置诊断而触发 runtime/base/release/wheel 重建、依赖下载、全量
reindex、数据库重置/重建或项目重新部署。仅以下三种可突破门禁：用户本轮明确要求重建；已批准的项目迁移/结构性重构；或已有
可复核证据证明数据库/索引损坏且增量恢复不可行。任何例外必须先更新本计划和 roadmap README，写明证据、最小范围、回退与
为什么普通增量队列不足；不得把“更快”或“省排查”作为理由。

**2026-07-22 阶段诊断提交的受控同步：** 最小补丁已提交为
`67f6098c844b5bf447e4dca00cdbaa8683dacc57`（作者 `helloworld3q3q`），且只推送 `origin/dev`；远端 SHA 已精确核对。
hook 仅登记 `chroma`、`code_vec`、`codegraph`、`ingest` 四类增量任务。只读核对发现 WSL 服务仓仍位于旧的干净 `dev` 提交，
因此下一步仅在既有 runtime deployment lock 内按正式脚本同构子流程执行 `origin/dev` 无交互 fetch/快进，并用
`git archive` 原子物化该 SHA 的 root 只读 controller 快照。该动作不运行正式部署入口，不构建 runtime/base/release/wheel，
不下载依赖，不启动服务，也不写数据库或索引；快照完成后才以当前已验证 release Python 加载该 controller，最多再执行一次
`configure-resume-codegraph --yes`。只有该配置成功，才进入既有 `restore → 四类 manifest → resume-codegraph` 增量链。

**2026-07-22 第一次阶段化实测与最小证据补齐：** WSL 服务仓已安全快进至 `67f6098`，对应 root 只读 controller 快照已原子创建；
两者身份均已复核，未调用 `runtime deploy`、pip、systemd 启动或任何索引写入。随后唯一一次
`configure-resume-codegraph --yes` 返回固定阶段 `同源证明`，且前后 `reindex-maintenance status` 均为成功，故维护态仍安全。
失败后的直接只读 proof 只能看到回滚后的 reindex `EnvironmentFiles` 未引用快照，不能把该回滚态误判为事务内根因；同时，现有
5 个 reindex drop-in 均不含 `EnvironmentFile`，已排除“更高序 drop-in 覆盖恢复快照”的假设。当前唯一允许的后续改动是：
通过 TDD 让 proof 在事务内把固定的 `unit + property` 位置作为无秘密元数据传递给既有阶段错误；不传递原始 systemd 输出、路径、
环境值、token、DSN 或异常正文。该补丁现已完成 RED→GREEN，92 项 Task 7 定向回归、Ruff、格式、编译和 diff 检查均通过；其提交后，
target 只前移至该最小子提交，再按同一受锁 archive 流程同步 controller（不重建 runtime）。届时才允许再做一次受控尝试，并按实际
位置形成单一最小根因假设；仍禁止 restore、worker、数据库重建和运行时构建。

**2026-07-22 proof 位置实测后的最后一项边界证据：** `9fbbb56` 已只推送 `origin/dev`，其 controller 同样已受锁快进并原子
物化；第三次受控尝试仍安全回到 maintenance，现精确报告 `同源证明 / reindex 环境文件`。这排除了写入、重载、CodeGraph unit
及更高序 reindex `EnvironmentFile` 覆盖，但仍无法区分“恢复 drop-in 未被 systemd 采纳”和“已采纳但其环境文件语义未生效”。
不得据此猜测性调整 drop-in 名称、排序或配置文件。唯一剩余诊断补丁是通过 TDD 在该失败点读取一次 `DropInPaths`，只传递
`已生效` / `未生效` / `未知` 三态布尔结果，不传递路径、原始 systemd 输出或任何环境值；该补丁现已完成 RED→GREEN，92 项
Task 7 定向回归、Ruff、格式、编译和 diff 检查均通过。提交后 target 只前移至这一最小子提交，仍按受锁 archive 流程同步
controller；随后才允许最后一次受控配置尝试。

**2026-07-22 活性诊断的精确路径修正：** WSL 报告“恢复 drop-in 已生效”但环境文件仍缺失；本机 systemd 255 的
`systemd.exec(5)` 已确认空 `EnvironmentFile=` 的列表重置语义与当前渲染一致，不能猜测性改写 renderer。复核诊断实现发现
它只比较了 `DropInPaths` 的 basename，可能将更高优先级目录中的同名 shadow 文件误判为当前 `/etc` 受管恢复文件。下一步仅以
TDD 将判断收紧为完整固定路径精确匹配；不改配置事务、不改 drop-in 内容。该补丁现已完成 RED→GREEN，94 项 Task 7 定向回归、
Ruff、格式、编译和 diff 检查均通过。提交/同步后，最终尝试的 `已生效` 才可作为系统根因证据；若显示未生效，则修复对象是
shadow/优先级，而不是环境文件语义。

**2026-07-22 精确活性实测后的最小修复假设：** `d921ef3` controller 已按既有受锁 archive 流程同步，且唯一一次
`configure-resume-codegraph --yes` 前后维护窗口均为就绪；实测为“同源证明 / reindex 环境文件 / 恢复 drop-in 已生效”。
失败后的只读分类还证明：当前 reindex 生效的 5 个 drop-in 中，恢复文件名之后虽有 2 个文件，但均不含
`EnvironmentFile=` 指令。因此 shadow、后续 reset 与数据库/worker 均不是本轮根因；保持 maintenance 停机态，未启动服务或写入索引。

下一步只对 `render_codegraph_resume_dropin_content()` 做 TDD 最小改动：不再在 drop-in 中输出空
`EnvironmentFile=` 列表重置，而是保留既有辅助环境文件并把受管恢复快照放在最后。现有同源证明会读取并拒绝所有辅助文件中对受保护
变量的覆盖，因此该策略不放宽安全边界；不新增配置键、状态机、unit 写入入口或数据库操作。先使精确渲染断言 RED，再最小实现并跑
Task 7 定向回归；仅在源码验证、提交、`origin/dev` 同源 controller 同步都通过后，才允许一次新的受控 configure 尝试。

**2026-07-22 最小渲染修复的 TDD 证据：** 先把“最后追加快照且不重置既有环境文件”断言改为新契约，RED 精确失败于旧输出中的空
`EnvironmentFile=`；随后只删除该渲染叶子的空列表项，并将同一 release payload proof 的硬编码期望收敛为新唯一输出。GREEN 为
Task 7 五个测试文件 `94 passed`；Ruff check、Ruff format check、`py_compile` 与 `git diff --check` 均通过。现有 proof 还覆盖
“辅助 MCP token 环境文件 + 受管快照”可通过，及任何辅助文件覆盖受保护变量必失败，故安全边界未放宽。此时尚未再次触及 WSL unit、
worker、数据库或索引；下一步仅提交、只推送 `origin/dev`、同源同步 controller，再执行一次受控 configure 尝试。

**2026-07-22 渲染修复后的受控反证与下一项诊断：** 提交
`c9f6069a08f42119a5783a7b526bfcc7a23065c5` 已只推送 `origin/dev`，四类增量任务仅入队；对应 controller 已同源同步。
随后唯一一次 configure 尝试仍在“同源证明 / reindex 环境文件 / 恢复 drop-in 已生效”失败，且前后 maintenance status 均就绪，
故“空列表重置”不是充分根因，不能继续猜测性改 renderer。只读 `systemctl` 分类还证明 reindex unit 为 `loaded + enabled + inactive`，
不是 maintenance mask 导致的配置解析缺失。下一步只通过 TDD 将 `EnvironmentFiles` proof 的固定内部失败边界细分为“列表格式、
受管快照引用、受管快照内容、辅助环境文件”四种无秘密原因，并沿既有 proof 元数据链传递；不输出路径、环境值、token、DSN、
原始 systemd 输出或异常正文。完成源码验证、提交和 controller 同步前，不再重试 configure。

**2026-07-22 环境文件原因码的 TDD 证据：** 四类场景的 RED 先证明旧实现没有原因字段；最小实现将环境文件 proof 拆为
列表解析、受管引用选择、受管快照校验和辅助来源校验四个单一职责步骤，并只沿既有 `proof → unit config → maintenance`
元数据链传递白名单原因码。外层只显示固定中文标签，未知值和原始错误均被丢弃。GREEN 为 Task 7 五个测试文件 `98 passed`；
Ruff check、Ruff format check、`py_compile` 与 `git diff --check` 均通过。此改动没有触及 WSL 配置、服务、worker、数据库或
索引；下一步仅提交、只推送 `origin/dev`、同源同步 controller，再执行一次受控 configure 以取得单一根因证据。

**2026-07-22 辅助来源根因与 systemd 语义探针计划：** `7b460d2` 的唯一 configure 尝试已返回“辅助环境文件”；只读分类证明
reindex 当前唯一辅助来源位于用户目录、未含受保护变量名，但经同一可信读取边界判定为非 root 受管。这说明恢复必须排除该旧来源，
不能放宽可信文件契约。当前 release 不具备把源 unit 重渲染为配置声明的环境文件能力，不能借此触发 runtime 部署或全量重建。

在修改 recovery drop-in 结构前，先执行一次隔离的 systemd 解析探针：只在 `/run` 下创建唯一临时 unit 与三份无秘密 probe
环境文件，且**不 enable、不 start、不 reload 任何生产 unit**；分别验证“同一 drop-in 的空列表 reset 后追加两个文件”与
“前一 drop-in reset、后一 drop-in 追加两个文件”的实际 `EnvironmentFiles` 解析结果。探针无条件删除精确临时文件、`rmdir`
临时目录并再次 `daemon-reload`；输出只为三个布尔存在性与清理状态。只有该证据确定后，才以 TDD 修改固定受管 drop-in 结构，
仍禁止数据库/索引重建、服务启动或绕过维护状态机。

**2026-07-22 根因最终裁决与最小修复设计：** 隔离 probe 已证明 WSL systemd 255 对“同一 drop-in 先
`EnvironmentFile=` reset、后追加 root 平台环境与恢复快照”的解析完全正确，且临时 unit 从未启动并已清理。进一步只读验证显示：
当前 context 精确选择 `/etc/codev-platform/platform.env`；该文件 root 可信、其变量名经共享
`parse_systemd_environment_keys()` 契约验证有效且不含受保护/仓覆盖变量，但它的 MCP token 值不符合 recovery 快照专用的
严格值语法。该严格解析器本应只用于需要比较 config/data/digest 的恢复快照，不应被用于只需检查变量名的辅助环境文件。

故放弃“保留用户来源”与“拆分 drop-in”两条错误方向：恢复单个 drop-in 的 reset 行，继续追加 root 平台环境与恢复快照；同时将
辅助文件校验改为复用既有的 root-trusted 读取 + 变量名公共契约，仍拒绝保留变量、受保护变量与仓覆盖变量。恢复快照自身继续用
严格值解析和摘要比对，安全边界不放宽。按 TDD 先覆盖带合法引号 token 的辅助文件可通过、带受保护名称仍失败、reset 渲染与
stage receipt 一致；源码验证、提交、同源 controller 同步后，才允许一次新的 configure。

**2026-07-22 最终修复的 TDD 与验证证据：** 已先观察两个精确 RED：带合法引号 token 的 root 可信辅助文件被快照专用
严格值解析器错误拒绝，以及 renderer 缺少 `EnvironmentFile=` reset 行。最小实现只改两个职责单元：
`_require_auxiliary_environment_files_safe()` 继续通过既有 root-trusted 读取边界取得文件，但复用
`core.systemd_environment_file.parse_systemd_environment_keys()` 只校验变量名；恢复快照仍保留原有严格值、路径和摘要比较。
`render_codegraph_resume_dropin_content()` 恢复同一 drop-in 内的空列表 reset，之后只追加 root 平台环境与恢复快照。没有新增
配置键、状态机、服务入口、数据库操作或依赖。

两个新增 RED 均转为 GREEN；恢复上下文、配置证明、unit 配置、恢复状态机和 systemd stage receipt 共 `134 passed`。
改动文件的 Ruff check、Ruff format、`py_compile` 与 `git diff --check` 均通过。此时 WSL 仍保持 maintenance ready，未启动
worker、未写数据库/索引。下一步仅提交并推送 `origin/dev`，同步同 SHA 的 controller 快照后执行一次既有受控 configure；成功后
才走 `restore → 四类增量 manifest → resume-codegraph`，不进入 Task 8。

**2026-07-22 configure 成功后的 worker 失败与根因：** `4aa3923` 已只推送 `origin/dev`，controller 源码快照同 SHA 后，
`configure-resume-codegraph` 前后 maintenance 均成功，并按既有 `restore` 状态机恢复 worker。restore 事务本身成功；其后
maintenance status 非零是已离开维护窗口的预期状态，不是回滚。只读 queue/systemd 证据显示 worker 以 exit 1 退出、8 项历史与
本次增量待办仍 pending，日志只给出“reindex 配置在 attempt 执行期间发生变化”。这不是 owner 缺失（专用退出码为 77），也不是
数据库或 CUDA 故障。

进一步无秘密摘要对比证明：恢复快照与 controller、`runtime/current` 的有效配置摘要一致，但现有 `codev-reindex.service`
仍绑定更早的 immutable release，该 release 的默认配置集合不同，故其有效摘要必不一致。根因是 systemd 主 unit 的 release
绑定漂移，而不是用户配置在运行中被改写。严禁通过删除摘要、篡改快照、手工启动 unit、重建数据库或重新构建 runtime 绕过该门禁。

**下一步最小受控修复：** 首次按通常服务用户生成 manifest 的前置被现有 root-only `runtime/current` 解释器权限拒绝（exit 126），
未生成 manifest、未改写 unit。不得为此放宽 release 权限。改由同一个 root controller 在显式只读用户配置覆盖下调用现有
`serve-mcp install-systemd --no-restart`，把仅绑定既有 `runtime/current` 的受限 manifest 物化到 root 可信目录；该步骤不复制
配置、不启动、停止或重启任何服务。随后重新进入 maintenance，使用现有 root
`mcp_systemd_install_transaction --maintenance-stage` 事务安装 manifest，保留其原像补偿与延迟激活语义；该事务不构建 release、
不下载依赖、不写数据库/索引、不手工编辑 unit。仅在有效 payload 证明 reindex 已绑定 `runtime/current` 后，才重新 configure、
restore，并继续既有四类增量 manifest → CodeGraph 受控恢复链。若 manifest 生成、安装或 payload 证明失败，保持 maintenance
停机态并按固定错误类别停止，不进行裸 `systemctl` 操作。

**2026-07-22 已完成 runtime 访问投影漂移的最终诊断与最小修复计划：** root controller 生成 manifest 同样在
`snapshot_current_release()` 处 fail-closed；固定原因码为 `release_access_mode`。对 `current` 所指 release 及其精确 base
做了只读全树审计：release 共 2058 项、base 共 34995 项，均为 root 所有、无非 root 可写、无特殊文件/硬链接/跨设备、
无 ACL/xattr；唯一模式差异是 release 10 个目录和 base 8 个目录仍为 `0755`，而 current 自身声明的策略要求 `0750`。
此外，两棵对象树的 GID 全为 root，目标服务组尚未发布任何对象。故这是历史访问投影未收敛，不是数据库、CUDA、配置、内容
摘要或索引损坏；也不是 controller 单方面收紧了契约。

现有入口均不能安全修复该状态：`seal_runtime_object_access()` 只允许带 `.incomplete` 的未完成对象；已完成对象会在修改前拒绝。
`publish_runtime_service_objects()` 只发布已规范对象的 GID，其严格 base/release 校验会先因本次 `0755` 漂移失败。`runtime stage`
会走隔离后重建，`runtime deploy` 已退役；它们都不符合本任务“不重建、不手工权限操作”的约束。previous 也依赖同一存在
mode 漂移的 base，不能作为回滚旁路。

因此本任务新增且仅新增一个窄的、root-only、可重试的 `runtime access-repair-current` 恢复事务，范围只限 activation lock
固定时 `current` 指向的一对 schema 3 / `root-service-group-read-v1` base 与 release，拒绝 previous、任意历史对象、legacy
schema、标记文件、内容身份不一致或任何未列出的权限差异。实现按以下顺序 TDD：

1. 在 `runtime_fd_tree` 增加封闭的“已完成对象访问投影预检/收敛”操作：仅允许目录 `0755 → 0750` 或已规范 `0750`，
   普通文件必须已规范；全树双遍 no-follow 身份快照、root 所有者、无 ACL/xattr、无写权限、无硬链接/特殊文件/跨设备后，
   才以叶子优先 `fchmod`、`fsync`、复验收敛。任何中断只留下更严格的 `0750` 或原状，重试可安全继续。
2. 以现有 base/release 内容、wheel、metadata、link、`.pth` 与摘要校验为唯一完整性真值；仅把上述有限 mode 容差注入
   修复前的对象访问 verifier，绝不复制一套内容校验器或靠解析异常正文判断。
3. mode 严格复验成功后，复用既有 `converge_runtime_service_namespace()` 与
   `publish_runtime_service_objects()` 将精确 base/release 发布到已验证服务主组，最后执行
   `verify_runtime_service_access()`、服务用户真实探针与 `current` 复读。该过程不修改内容、ID、`current/previous`、
   systemd、数据库、队列或索引。
4. CLI 只负责编排 root/维护窗口前置和无秘密 JSON 结果；dry-run 零写。真实操作仅在既有 reindex maintenance
   管理许可中执行，禁止直接 Python 调叶子或裸 `chmod/chown`。
5. 通过后才重新生成 manifest、走既有 maintenance-stage systemd 安装事务、有效载荷证明，再 configure → restore →
   四类增量 manifest → CodeGraph 受控恢复；任何一步失败都保持维护安全态，不触发 rebuild。

新增测试至少覆盖：dry-run 零写；只接受 current schema 3 对；18 个 `0755` 目录收敛；普通文件或其他目录 mode、owner、
ACL/xattr、内容/metadata/link/wheel/`.pth` 漂移均在首次写前失败；中断后幂等重试；base/release ID 与
`current/previous` 不变；最终服务用户探针通过；CLI 不触发 systemd、数据库或 reindex 写入。完成后运行新增定向测试、
现有 runtime service-access / release / Task 7 回归、Ruff、格式、编译和 `git diff --check`。

**2026-07-22 访问修复 dry-run 的内容门禁补充：** 以正确 release Python 和只读阶段诊断后确认，base 的访问预检本身
通过；完整 base 验证被 `purelib` 静态清单拒绝。无秘密差异统计为“未知 56、缺失 0”；56 项全部是直接位于
`__pycache__` 的 `.pyc`，均为 root 所有、单链接、非 group/other 可写、当前 CPython magic，且可逆映射到已被
RECORD 声明的 `.py` 源文件。故此为历史 root 进程生成的字节码缓存污染，不是锁、wheel、依赖文件、数据库或索引损坏。
此前任何未带 `-B` 的 root 当前解释器调用都可能扩大该集合；后续诊断已改为 `-B` 或进程内禁用字节码写入。

为在不 rebuild 的前提下恢复原对象，访问修复事务新增一个窄的“已证明生成字节码清理”子步骤，仍只针对 current 的
精确 base：先以既有 lock/RECORD 解析得到完整声明集，拒绝任意缺失声明、非 `__pycache__/*.pyc`、无法映射到声明源、
非 root 单链接、可写、链接、非当前 magic 或预检快照漂移的条目；只有全量候选预检成功后，才通过 root fd-relative
删除候选、持久化父目录、删除空缓存目录并立即复用现有静态清单 verifier。它绝不忽略未知文件、不放宽 inventory 规则、
不删除源文件或登记文件，也不接触 release、`current/previous`、systemd、队列、数据库或索引。失败或中断只会留下
“已删部分可信缓存 + 其余仍可再次验证”的可重试状态。

为避免本事务再次污染 base，CLI 与领域入口会在任何动态加载前显式禁止 Python 写字节码；生产 systemd 和安装控制器
已有 `PYTHONDONTWRITEBYTECODE=1` / `-B` 约束，本次补齐修复入口自身的同等保护。新增测试覆盖候选全量预检零写、
恶意/非缓存未知文件拒绝、缓存 source/magic/owner/link/写权限检查、fd 删除中断重试、静态清单恢复以及禁写 pyc。

**2026-07-22 release 同类缓存的只读补充与收敛范围：** base 的虚拟清单预检已在真实 WSL dry-run 通过；但 release 的
wheel 静态载荷校验随后仍 fail-closed。只读统计证明 release purelib 有 151 个未知项，全部是直接位于
`__pycache__/*.pyc` 的 root 所有、单链接、非 group/other 可写、当前 CPython magic 文件，且全部能映射到现有 wheel
应用载荷、受控 bootstrap RECORD 或受控 `.pth` 已允许的 `.py` 源文件；没有其他未知形态。故它与 base 的历史 root
字节码缓存属于同一污染类别，但其唯一完整性真值是现有 `runtime_wheel.verify_installed_application()`，不能错误复用
base 的依赖 RECORD 语义。

实施改为一个共享的、fd-relative 生成字节码机械清理核心，两个窄适配器各自只负责其原有完整性真值的“虚拟删除后预检”与
“物理删除后严格复验”：base 继续委托 distribution inventory，release 委托 wheel 安装载荷 verifier。禁止复制删除循环、
放宽任一 verifier、删除源/RECORD/wheel/.pth、或将 release/base 以外对象纳入范围。dry-run 仍只产生证明；真实删除仍仅
发生在 current 精确对象对已锁定、maintenance 许可的 `--yes` 事务内，失败/中断仅留下可再次证明的剩余缓存。

**2026-07-22 共享清理核心实现与真实 dry-run 证据：** 已将生成缓存的 no-follow、root-fd-relative 删除、身份复验、
`fsync`、空 `__pycache__` 删除和中断重试收敛到 `runtime_generated_bytecode_core.py`；base/release 仅提供各自的
文件来源快照、虚拟验证和严格验证回调。wheel verifier 同时抽出“已证明安装清单上下文 / 快照复验”两个私有职责单元，默认
`verify_installed_application()` 仍调用同一规则，release 环境只通过显式注入点选择同语义 verifier，正常构建/验证路径不变。

新增 base/release 缓存候选、未知空目录、错误 magic/source、fd 删除与中断重试测试；相关 runtime、wheel、CLI、parser
共 `187 passed, 22 skipped`，Ruff、格式、编译和 diff 检查通过。真实写入前还先对 base 与 release 同时执行虚拟预检；任一
release 虚拟预检失败都会在任何缓存删除、mode 收敛或服务发布前停止。WSL current 解释器以 `-B` 和禁写字节码运行的临时目录实测，
base 与 release 两个 fd 删除器均完成严格复验；对正式 runtime 的 `runtime access-repair-current` dry-run 已成功，证明为
dry-run 且对象统计为正。至此未执行 `--yes`，未修改正式 base/release、systemd、worker、数据库、队列或索引。

**2026-07-22 部署前置已完成与维护门禁缺失：** 修复已提交为
`3e96c7672ed354a21f80b8f418b25c2fa997771b`，作者为 `helloworld3q3q`，且仅推送 `origin/dev`；服务仓和 root
controller 均已受控同步到同一提交，未向 GitHub 推送。已部署 controller 的正式 current dry-run 通过，仍是零写。随后
`reindex-maintenance status` 只读失败为“drop-in 不受信任”；对固定路径的无内容分类证明为：文件缺失、非链接、父目录可信。
这不是索引、数据库或 runtime 内容故障，也没有触发任何写入。既有 `prepare --yes` 的职责正是通过可信 dirfd 原子创建
`Restart=no` drop-in、reload、启用维护 marker 并收敛 reindex/CodeGraph；禁止手工写文件、直接 systemctl 或绕过门禁。

下一步顺序固定为：执行既有 `reindex-maintenance prepare --yes` 并用 status 复验 → 在维护许可内执行一次
`runtime access-repair-current --yes` → 严格复验/服务用户探针 → 既有 manifest 事务、configure/restore 和原有 8 项增量队列。

**2026-07-22 首次 access repair 的 fail-closed 根因与最小回修：** 已部署的 `--yes` 在维护许可内完成两类生成缓存
清理、current 对象 mode 收敛及 strict base/release 复验；随后父命名空间仍保持未发布状态。无秘密阶段追踪证明
`_publish_service_access()` 复用同一 `arguments` 字典调用两个不同职责：内容发布需要 `base_ids`/`release_ids`，而
`converge_runtime_service_namespace()` 只接受服务 UID/GID；多余关键字在命名空间函数调用边界被正确拒绝，因而没有发生
父目录或服务内容的半发布。直接预检、同文件系统 WSL 临时收敛和维护许可均通过，排除权限、ACL、服务账号和文件系统原因。

回修必须保持单一职责：在 `_publish_service_access()` 内分别构造不可变的命名空间参数与内容对象参数，不改变下层函数
契约、不新增 flag、不放宽验证。先新增严格命名空间函数签名的 RED 回归，GREEN 后运行 current-access-repair、CLI、
service-access、release/wheel 与 parser 定向测试及 Ruff/格式检查；部署后仍仅重试已有 `runtime access-repair-current --yes`，
再继续原 manifest/增量队列流程，禁止 runtime/数据库/索引重建。

**2026-07-22 回修验证：** 严格签名回归已先观察到预期 RED（多余内容对象参数在命名空间边界被转换为
`RuntimeCurrentAccessRepairError`）；最小 GREEN 仅将 `_publish_service_access()` 的局部参数拆为
`namespace_arguments` 与 `content_arguments`，现有下层接口和所有其他调用点未变。定向回归
`tests/test_runtime_current_access_repair.py`、access-repair CLI、service-access、fd-tree、release binding、generated bytecode、
wheel、runtime CLI 与 parser 共 `160 passed, 17 skipped`；Ruff check、Ruff format、`compileall`、`git diff --check` 通过。
下一步仅提交并只推送 `origin/dev`、同 SHA 同源同步 controller，然后在已保持的 maintenance 许可内重试官方 CLI，不启动
worker、不中断队列以外的服务。

**2026-07-23 访问修复后的目标用户探针根因与最小修复计划：** 提交 `5641609d62dfbb75bd2165dafe0712e345df7ca3`
已仅推送 `origin/dev`，服务仓与 root controller 已同源同步；既有官方访问修复已完成 current base/release 的严格服务访问
复验，只有目标用户探针以退出码 70 fail-closed。相同 `systemd-run + setpriv + env -i + -I -B` 链的只读分段证据表明：
导入 `codev_platform.chroma.server` 后，`setuptools` 仅追加一个 base purelib 下的 `_vendor` 目录，导致旧的“导入后
`sys.path` 字节级完全相同”断言失败。该目录已证明为 root 所有、无组/其他写权限、位于已严格验证的 base 内、位于 app/base
主路径之后，且不含 `codev_platform` 或 `torch`，故不能抢占应用或 torch 的导入；这不是权限、ACL、数据库、索引或 CUDA
故障。

回修不删除导入后路径校验，而是在内嵌子进程协议中把它收紧为窄白名单不变量：基线路径必须完整保留且相对顺序不变；新增路径只可
位于 base purelib 内、排在 app/base 主路径之后、root 所有且不可被组/其他写入，并且不得提供 `codev_platform` 或 `torch`。
任何外部、可写、抢占优先级或可影子化关键包的新增路径仍必须退出 70。先为“安全 `_vendor` 通过”与四类越界拒绝写 Linux root
真实 RED，再以单一 `validate_post_import_paths()` 协议辅助完成最小 GREEN；随后运行目标探针、access-repair 和相关 runtime
定向回归。部署后只重试既有访问修复，严格证明通过后才继续 manifest、restore 与原有增量队列；始终禁止 rebuild、依赖下载、
数据库/索引重置或手工权限修改。

**2026-07-23 目标用户协议回修验证：** 新增 `tests/test_runtime_target_user_protocol_paths.py`，将路径变更的测试职责与
原有探针编排测试隔离。先以旧的完全相等语义在 WSL root、`setpriv + env -i + -I -B` 的真实子进程中观察 RED：安全 `_vendor`
仍返回 70；随后只在 `_PROBE_SCRIPT` 内增加 `require_trusted_base_addition()` 与
`validate_post_import_paths()` 两个内聚辅助，保留环境、身份、capability、应用模块来源、torch 来源和末次 runtime identity 的
所有原有门禁。GREEN 在相同链路下确认 root 只读且后置的 base `_vendor` 返回唯一成功行；外部路径、组可写路径、非 root 所有路径、
位于 app 之前、位于 app/base 之间、影子 `codev_platform`、影子 `torch` 共七类均返回 70，标准输出和错误均为空。

WSL 当前运行时 venv 未安装 pytest，按“不下载依赖”约束未安装；上述使用新源码构造的精确协议矩阵是其 Linux root 运行证据。
Windows 定向回归为 `179 passed, 31 skipped`，并通过 Ruff check、Ruff format、`compileall` 与 `git diff --check`。下一步仅提交、
只推送 `origin/dev`、同步同 SHA root controller，并在既有 maintenance 许可内重试官方 `runtime access-repair-current --yes`；
官方成功证明前不恢复 worker、不生成 manifest、不操作数据库或索引。

**2026-07-23 官方运行时二次诊断与下一项最小回修：** `f9739db` 已提交、仅推送 `origin/dev`，服务仓和 root controller
同源同步；维护状态曾因 reindex/CodeGraph 停机证明漂移而 fail-closed，已仅通过既有 `reindex-maintenance prepare --yes` 收敛，
随后 status 与显式 `--runtime-root` 的访问修复 dry-run 均通过。真实 `access-repair-current --yes` 没有残留进程但仍返回泛化失败；
同一 systemd 目标用户链的无敏感诊断证明 `_PROBE_SCRIPT` 已产生唯一成功 stdout，路径、身份和模块来源均通过，失败仅因 stderr
非空。

逐导入字节计数将全部 1799 字节定位到 `codev_platform.chroma._config`：该配置叶子在探针固定 cwd `/` 无法解析默认 project 时，
虽然正确降级 `PROJECT_ID=None`，却在**导入期**打印含路径/异常正文的多租户提示。不得放宽探针 stderr 零输出门禁，也不得在探针中
吞掉任意 stderr；应把“可选默认项目解析”收敛为 `_resolve_optional_project_id()` 纯辅助：成功返回 project_id，`ProjectIdError`
返回 `None` 且不输出。既有 daemon/SSE 的显式 project 路由和 `PROJECT_ID=None` 语义保持不变。先写“解析失败静默且仍为 None”的
RED 测试，再作最小 GREEN，之后重跑 chroma 相关回归、精确 WSL probe、官方 access repair；仍不 rebuild、下载依赖或动数据库/索引。

**2026-07-23 不可变 current release 的兼容裁决：** 配置叶子的 GREEN 只会随下一次正常应用 release 生效，controller 同源同步
不能也不得覆写 current release 内的 wheel 载荷；为该条导入日志重建 base/release 会违反本任务的增量恢复边界。故探针还需在**自身
短生命周期子进程内**建立确定的导入上下文：仅在加载 `MANAGED_IMPORTS` 前设置合法常量 `PLATFORM_PROJECT_ID=codev-target-probe`，
无论导入成功或失败均在 `finally` 删除；加载后既有 `process_state()` 仍要求环境精确等于原五项，任何残留、覆盖或其他环境变更仍
fail-closed。该变量只消除 `resolve_local()` 对 cwd `/` 的非业务歧义，不读取/写入项目数据、不改变真实 daemon 环境，也不捕获或
丢弃 stderr。先写真实目标用户“导入时可见、结束后环境已恢复”的 RED，再最小接线到现有模块来源检查；旧 release 与未来静默
release 均由同一协议证明。

**2026-07-23 配置静默与 probe 上下文回修验证：** `tests/test_chroma_config_import.py` 先以缺少
`_resolve_optional_project_id()` 得到 RED；最小 GREEN 移除 `_config` 的 import 期打印，异常仍精确降级为 `None`，stdout/stderr
均为空。真实 target 用户回归再以一个模拟旧 release 的 `chroma.server` 要求 `PLATFORM_PROJECT_ID`：旧协议返回 70，接入
`load_managed_modules()` 的 `try/finally` 后返回唯一成功行且 stderr 为空，因导入后的 `process_state()` 仍通过同时证明变量已回收。
Chroma 配置/服务、目标用户、访问修复和 runtime CLI 定向回归合计 `195 passed, 32 skipped`；Ruff check、`compileall`、
`git diff --check` 均通过。下一步仅提交、只推送 `origin/dev`、同步同 SHA controller，再在保持的 maintenance 许可内先跑
官方 dry-run、后跑一次 `access-repair-current --yes`；成功前不 restore worker 或开始增量索引。

**2026-07-23 current base 第三方 warning 裁决：** 新 controller 的真实探针已消除应用配置输出，但当前不可变 base 的 `jieba`
首次导入仍发出 3 条 `SyntaxWarning` 和 1 条 `UserWarning`（共 1220 字节）。无敏感 origin 证明四条均来自
`base_purelib/jieba`；同一 base 外、其他类别或其他数量均未观察到。不能使用全局 `ignore`、`PYTHONWARNINGS` 或吞掉 stderr，
也不能为第三方库告警重建 base。协议应只在 `MANAGED_IMPORTS` 的导入临界区用 `warnings.catch_warnings(record=True)` 收集**默认会
输出**的告警，并接受两种严格状态：零条（测试/未来库已静默）或恰好 3 条 `SyntaxWarning` + 1 条 `UserWarning`，且每条源文件
严格位于已验证的 `base_purelib/jieba`；任何额外、缺失组合、app/external 来源或不同类别均退出 70。导入后仍不允许实际 stderr。
先以 base 内四条预期告警的真实 target RED/GREEN 和 app 内 `RuntimeWarning` 拒绝回归覆盖，再重跑官方 probe/repair。

**2026-07-23 第三方 warning 门禁验证：** 预期 base warning 的 RED 证明旧协议虽有成功 stdout 但 stderr 非空；GREEN 仅在
`load_managed_modules()` 内增加告警记录与窄验证，不修改 Python 全局 warning filter、不设置 `PYTHONWARNINGS`。真实 root target
夹具中，base `jieba` 的 3+1 精确告警返回唯一成功行且 stderr 为空；app `chroma.server` 发出的 `RuntimeWarning` 返回 70 且
stdout/stderr 均为空。该闭环连同现有 Chroma/runtime 定向套件、Ruff check、`compileall` 和 diff 检查完成后，才允许第三次
controller 同源同步与官方 access repair 重试。

**2026-07-23 官方访问修复已成功收口：** 最新 controller `f5557c8bdecc4912f5a8d71dcc9a289f28ff61dd` 在既有 maintenance
许可内完成一次 `runtime access-repair-current --yes`，命令退出为 0，最终无秘密 JSON 状态为 `ok`。随后以同一 controller
复验 `reindex-maintenance status` 和显式 runtime root 的 access-repair dry-run，二者均退出 0；独立受限服务用户 probe 亦返回
成功且证据摘要长度有效。此次事务仅处理 current 精确 base/release 的既有访问投影与证明，不构建 runtime/base/release/wheel，
不下载依赖，不改数据库、索引、队列内容或 worker 状态。

后续顺序固定为：先由当前 root controller 生成仅绑定 `runtime/current` 的 manifest，再通过既有
`mcp_systemd_install_transaction --maintenance-stage` 安装并复验 payload；只有该事务成功后，才依次执行
`configure-resume-codegraph --yes`、`reindex-maintenance restore --yes`、消费既有四类增量 scope 并以精确目标提交验证 manifest，
最后执行 `resume-codegraph --yes`。任一步失败均保持维护安全态，禁止重建、裸 `systemctl`、手工改 unit 或重置数据库/索引。

**2026-07-23 maintenance-stage 的入口停机缺口与修复裁决：** 上述 manifest 已在显式用户配置覆盖下生成并通过静态输入审计；
正式 maintenance-stage 在任何 unit 写入前正确拒绝，唯一稳定原因是 `codev-webhook.service` 仍在运行。现有
`reindex-maintenance prepare` 只维护 reindex/CodeGraph，而安装策略已把 Webhook 声明为第三个 `ingress_state_machine`
延迟激活 unit，二者契约不一致。status 仍成功，证明没有发生半安装或服务漂移。

不得用裸 `systemctl stop`、改用 `install-only`、删除 manifest 中 Webhook，或进入完整 runtime deploy/数据库停写流程：前两者绕过
maintenance-stage 的原子证明，后两者分别破坏受管 unit 集合或扩大到本任务禁止的代际/数据库范围。最小修复是在既有
reindex maintenance 状态机中增加独立的 Webhook 生命周期域：固定条件 guard 与 root-only hold 防止跨重启重开；prepare/inspect/
失败补偿共同证明 Webhook 的 cgroup 已停；restore 保持入口关闭；`resume-codegraph` 仅在 CodeGraph、reindex handoff、目标 manifest
和稳定性均成功后，仍在同一受控转换锁内解除 hold、启动并健康验收 Webhook。若入口恢复失败，则重新收敛整个 maintenance 安全态。

实现保持职责分离：通用 root-only hold 机械逻辑独立，Webhook guard/hold/生命周期各自单一职责；reindex maintenance 只注入
prepare/proof，CodeGraph 恢复只在最终提交点调用入口恢复端口。先为持久 hold、条件证明、prepare/restore/恢复顺序及失败补偿写
RED，再最小 GREEN；随后运行相关 runtime、maintenance、CodeGraph 恢复与 systemd 安装定向回归，才重新生成 manifest 并重试
同一 maintenance-stage 事务。

**2026-07-23 Webhook maintenance 实现与验证：** 已新增通用 `systemd_maintenance_hold` 机械层，并把既有
CodeGraph hold 收敛为该层的窄适配器；Webhook 使用独立 guard、hold 与 lifecycle 模块，避免让 reindex 或 CodeGraph 直接持有
入口文件细节。`prepare_reindex_maintenance` 在同一 systemd 转换锁中依次收敛 CodeGraph 与 Webhook，`inspect`、prepare/restore
补偿和锁内组合补偿均把入口关闭纳入最终证明；`resume-codegraph` 仅在 reindex handoff 完成后、转换锁仍持有时调用 Webhook 恢复，
恢复包含 guard/hold 复证、enable、start、稳定窗口与 loopback 健康验收，失败会先重新关闭入口再回到完整 maintenance。

新增入口 guard/lifecycle、maintenance 接线和 CodeGraph 最终顺序 RED→GREEN；相关 maintenance、CodeGraph 恢复、systemd 安装、
stage receipt、ingress/runtime 验收共 `410 passed, 1 skipped`。当前尚未重试正式 installer，下一步仅提交、只推送
`origin/dev`、同步同 SHA root controller，再从现有 maintenance 状态执行一次新的 `prepare --yes`，然后重新生成 manifest
并重试 maintenance-stage；仍不重建 runtime、数据库或索引。

**2026-07-23 Webhook 部署后的目标用户预检兼容性裁决：** Webhook 修复已提交为
`ca32abfff71b20ef80a5ca15a13b328fd39be252`、仅推送 `origin/dev`，服务仓与 root controller 已同源同步；新的
`reindex-maintenance prepare --yes`、status、访问修复 dry-run、绑定 manifest 生成和静态输入审计均通过。正式
maintenance-stage 仍在任何 unit 写入前 fail-closed，但稳定原因已从 Webhook 活动态变为“systemd 目标用户预检执行失败”；
失败后 maintenance status 仍通过，证明没有半安装、服务启动或数据库/索引写入。

无敏感的同命令分段实测已精确定位：WSL 当前 systemd 对 transient `systemd-run --uid=<target-user>` 的
`WorkingDirectory=%h` 不能解析，单独加入该属性即返回失败；将其替换为该目标账号的绝对主目录后，同一不可变解释器、环境文件、
运行时绑定和证明模块返回 0，且生成 12 个受管 unit 的完整 proof。因此问题不在 target user、release、环境文件、配置、
payload、数据库或索引，也不是 Webhook 修复回归。

最小永久修复只触及 `mcp_systemd_target_preflight` 的 transient argv 构造：复用既有服务账号解析作为唯一身份/主目录真值，
以经过该解析器验证的绝对 home 替代 `%h`，账号无法解析或 home 无效时继续在启动 transient unit 前 fail-closed。不得手工写
unit、裸 `systemctl`、删除环境文件、放宽 proof 或为此重建 runtime/base/release/wheel。先增加“精确 literal home”与
“账号解析失败时不启动 systemd-run”的 RED 回归，再最小 GREEN；随后运行 target-preflight、systemd 安装/maintenance 相关回归、
Ruff、格式、编译和 diff 检查。提交后仍只推送 `origin/dev`、同步 root controller，并从已保持的 maintenance 状态重新生成
manifest、重试 maintenance-stage；只有成功后才继续 configure → restore → 既有四类增量队列 → `resume-codegraph`。

**2026-07-23 target preflight 回修验证：** RED 先证明旧模块不存在目标 home 解析职责；GREEN 只增加
`_target_user_working_directory()`，委托既有 `resolve_service_account()`，不复制 passwd/path 校验逻辑。命令契约断言 literal
home 且明确拒绝 `%h`；账号解析失败时 `_execute_transient` 不得被调用，正常路径仍使用同一不可变解释器、环境文件、超时和 proof
比较。目标单测 `10 passed`；加上 systemd 安装 transaction/maintenance-stage/systemd 端口、Webhook maintenance、CodeGraph
resume、runtime proof 与服务账号回归为 `199 passed, 2 skipped`。后续只需完成 Ruff/格式/编译/diff 检查、提交和 WSL 同源 controller
实机复验；维护态、worker、数据库和四类增量队列保持不变。

**2026-07-23 新 controller 实机 stage receipt 根因与最小裁决：** `1a4fe25` 已仅推送 `origin/dev`，服务源码与 root
controller 同源同步。新的 manifest 通过完整目标用户 transient proof（12 个 unit），证明 `%h` 回修已生效；随后 maintenance-stage
在 `_capture_stage_receipt()` 的首次写入前 fail-closed，固定错误为“stage 受保护 unit 未绑定当前发布解释器”。失败后
maintenance status 仍通过。无敏感载荷审计同时证明 `codev-reindex.service` 与 `codev-mcp-codegraph.service` 两者的 ExecStart
均是 `runtime/current/...` alias、与绑定 release 的 immutable interpreter 都不相等；其它 unit、配置、环境文件、目标用户、
Webhook、数据库、索引和 worker 均不在本次失败路径中。

不能让 stage receipt 接受 `current` 软指针、手改 unit 或跳过 receipt：前者会把 release 切换窗口重新引入受保护写服务的
stage 证明，后两者绕过原子事务。最小修复是在 manifest 专用渲染入口把 `SystemdRuntimeBinding` 作为唯一绑定真值传入，仅令
CodeGraph 与 reindex 两个受保护 unit 的 ExecStart 使用 `binding.immutable_python`；普通 MCP、Webhook、agent、web、clock 与
memory unit 继续使用原有 `runtime/current` 语义。渲染器的无 binding 公共调用保持不变，避免把 stage 特殊策略扩散到普通服务。
先写生成 manifest 的两个 protected unit 精确 immutable、其它 Python unit 仍为 current、以及真实 stage payload identity 的
RED 回归，再最小 GREEN；随后运行 systemd renderer/install/stage receipt/transaction、maintenance/CodeGraph 恢复回归与静态
检查。提交后仍只推送 `origin/dev`、同步 controller，再从现有 maintenance 重新生成 manifest、preflight、maintenance-stage；
成功前不 configure/restore、不恢复 worker、不消费或重建索引。

**2026-07-23 不重建 current release 的预检兼容性约束：** 目标用户 proof 由当前 immutable release 内的
`runtime_preflight_proof` 执行，controller 同源同步不能覆写该已发布模块。该旧模块仍按 `current` 渲染两个 protected unit；若只
修改 manifest 渲染为 immutable，旧 proof 会在 payload 摘要比较处错误拒绝，重建 release 又违反本任务边界。不能因此让 stage
receipt 接受可变 alias，也不能把 controller 源码注入目标用户进程。

因此保留严格 stage receipt，同时在 controller 的 target-preflight 适配器增加一个封闭的 legacy proof 兼容候选：只有
`codev-reindex.service` 与 `codev-mcp-codegraph.service` 两项，且 manifest payload 中绑定解释器字节串精确出现一次时，才以同一
受信 release root 的固定 `current/venv/bin/python` 字节替换后重算这两项摘要；其余 10 项、release/revision/target user/schema
仍逐项精确相等。实际 proof 仅可等于“新 immutable 全量摘要”或这一唯一 legacy 候选；任一普通 unit 漂移、缺少/多次出现绑定解释器
或不可变解释器不在 protected ExecStart 首位都继续 fail-closed。未来正常 release 带上新 proof 后自动走全量 immutable 分支，
无需保留额外运行态。
先为 legacy 恰当通过、普通 unit 漂移拒绝、绑定字节次数异常拒绝写 RED，再最小 GREEN；依然不重建 runtime/base/release/wheel。

**2026-07-23 stage binding 与旧 proof 兼容实现验证：** manifest renderer 新增可选的 `runtime_binding`，只有
`render_systemd_units()` 内的 CodeGraph 与 `render_reindex_unit()` 消费该 binding 的 immutable interpreter；普通渲染调用、
Webhook、agent、web、clock、memory 仍未接收该策略。`install_systemd()` 使用它生成 manifest；未来 release 内的
`runtime_preflight_proof` 也携带同一 binding。controller 的 `mcp_systemd_target_preflight` 则在 exact proof 不匹配后，才构造
封闭的 legacy current-alias 候选，绝不改变 stage receipt 的 immutable 验证。RED 已观察到 protected unit current alias、旧 proof
不匹配；GREEN 覆盖 protected immutable / 普通 current 分离、proof binding、legacy 恰当通过、普通 unit 漂移拒绝和一次性字节替换。
systemd renderer、target preflight、stage receipt、transaction、Webhook maintenance、CodeGraph resume、runtime systemd/service
回归共 `294 passed, 3 skipped`；Ruff、格式、编译和 diff 检查均通过，待提交、仅推送 WSL 并实机重试，当前 maintenance、worker、数据库和
四类增量队列仍未改变。

**2026-07-23 maintenance-stage 有效载荷证明的 WSL 序列化根因：** 新 manifest、legacy/exact target proof 和 stage identity
均已在 WSL 通过；事务写入后按补偿路径回滚，maintenance status 每次均复验通过。阶段标签将失败收敛到 effective payload，逐 unit
再定位为 `codev-clock-resync.service`，逐字段定位为 ExecStart。只读 `systemctl show` 证实 WSL systemd 把该 unit 的
`/bin/sh -c '<含空格和 || 的单一脚本参数>'` 展开为无引号的 `argv[]` 文本，丢失了原本不可逆的参数边界；这不是 unit 文件、
FragmentPath、drop-in、reindex、CodeGraph、Webhook、服务健康或数据库/索引问题。

不得放宽所有 unit 的 ExecStart 比较，也不应猜测性把扁平文本重新分词。注册表已有 `runtime_bound=False` 真值，适用于时钟 service/
timer；对这一类非运行时 unit，最小安全策略是继续精确验证 root 原像、canonical FragmentPath，并将有效 drop-in 集合钉死为唯一
部署总 guard 的固定路径与 root 原像，但不请求/解析无损格式不存在的 ExecStart 属性。所有 runtime-bound Python unit 和两个受保护
写服务继续请求并逐 argv 精确验证 ExecStart；任何额外/缺失/篡改的非运行时 drop-in、原像或 FragmentPath 漂移仍 fail-closed。先为
无 ExecStart 属性的时钟 unit 通过与未知 drop-in 拒绝写 RED，再最小 GREEN，随后重跑 effective payload、install/stage transaction、
maintenance 回归；无需构建 runtime 或下载依赖。

**2026-07-23 非运行时 drop-in 契约补充：** 第一轮 controller 实机重试证明时钟 ExecStart 扁平化已不再是前置门禁，但
effective payload 随后精确停在 `codev-memory-maintenance.timer`。只读 `systemctl show` 显示其 canonical FragmentPath 正确，且
唯一 DropInPaths 是所有受管 unit 必有的 `10-codev-deployment-guard.conf`。因此原计划中的“空集合”会错误拒绝平台自身 guard；
这不是外来 shadow。实现必须复用 `deployment_guard_drop_in_path()` 与固定 guard 内容作为非运行时 unit 的唯一 expected snapshot，
并继续拒绝任何多余/缺失/内容不符的路径。

**2026-07-23 非运行时 effective payload 校正与验证：** `mcp_systemd_effective_payload` 直接复用注册表
`managed_systemd_unit(...).runtime_bound`，没有新增配置或第二份 unit 分类。runtime-bound unit 保持原来的 `parse_unit_exec_start`
与 systemd effective argv 精确比较；非运行时 unit 不请求 ExecStart，并在同一 FragmentPath/主 unit 原像校验后，精确要求唯一
`deployment_guard_drop_in_path(unit)` 及固定 guard 的 root 原像。默认安装证明显式注入 drop-in 原像读取器；缺失、额外或内容篡改
的 guard 都 fail-closed。RED/GREEN 覆盖时钟 unit 的无 ExecStart 成功、foreign drop-in 拒绝、guard 缺失和 guard 篡改拒绝。
定向回归 `73 passed`；与此前 renderer、target preflight、stage receipt、transaction、Webhook maintenance、CodeGraph resume、
runtime systemd/service 的完整受影响回归为 `297 passed, 3 skipped`。Ruff、格式、编译和 diff 检查均通过；下一步提交、仅推送
WSL、同步 controller 并实机重试 maintenance-stage；worker、数据库和四类增量队列仍关闭且不重建。

**2026-07-23 提交与同步前状态：** 修复已以 `c1e20ca` 提交，并仅推送 `origin/dev`；提交作者已复核为既定服务账号。
post-commit 只把 `chroma`、`code_vec`、`codegraph`、`ingest` 四个既有增量 scope 入队，未触发全量重建。维护门禁仍关闭 worker，
下一步仅同步同 revision 的服务源码与 root controller 快照，并以官方 maintenance-stage 事务重试；任何失败继续保持维护态。

**2026-07-23 controller 与事务前证明：** 服务源码已由服务账号快进到 `c1e20ca` 且 tracked-clean；root controller 快照以同一
revision 归档并完成 root 所有权/非可写位校验，未改动 runtime release。官方 maintenance status 通过；`--no-restart` manifest
生成后，manifest root 原像、运行时绑定、固定受管集合、目标用户 transient proof 和两个受保护 unit 的不可变解释器身份均通过。
下一步才允许执行官方 `--maintenance-stage` 事务；其失败必须由现有补偿路径结算并维持维护态。

**2026-07-23 maintenance-stage 第二个精确根因与修正边界：** 新事务与一次按 unit 标记的受控诊断均在有效载荷阶段失败后
成功补偿，maintenance status 均通过。失败 unit 是 `codev-clock-resync.service`，只读 `systemctl show` 证明其 canonical
FragmentPath 正确、DropInPaths 为空。此前把全部 `runtime_bound=False` unit 都要求部署 guard，是把“ExecStart 解析豁免”错误
推导为“部署守卫成员”；它错误要求时钟 unit 拥有本不应存在的 drop-in。

真正的部署守卫成员清单当前只存在于 `runtime_deployment_guard.GUARDED_SYSTEMD_UNITS`，其中包含
`codev-memory-maintenance.timer`、排除两个 clock unit。为消除这一跨模块重复分类，最小修正把该不可变成员列表下沉到
`mcp_systemd_unit_registry`，部署守卫模块仅兼容性复用；effective payload 只对“非运行时且部署守卫成员”要求唯一固定 guard，
对 clock service/timer 精确要求空集合。运行时 unit 的 ExecStart 和既有 protected/resume drop-in 证明均不放宽。先补
clock 无 drop-in 成功、memory timer guard 成功、guard 缺失/篡改和外来路径拒绝的回归，再跑完整受影响测试与静态检查，提交后
仅推送 WSL、同步 controller 并重试同一 maintenance-stage 事务。

**2026-07-23 分类真值回修验证：** `DEPLOYMENT_GUARDED_SYSTEMD_UNITS` 已归入 unit 注册表，部署模块只保留同一对象的
兼容导出；导入期拒绝重复或未受管成员。effective payload 只按该注册表选择非运行时 guard，clock service/timer 继续精确要求
空集合，memory timer 继续精确要求 guard 原像。定向回归 `83 passed`；包含安装事务、maintenance、target preflight、stage receipt、
Webhook/CodeGraph 恢复、runtime service 与部署守卫的完整受影响回归为 `315 passed, 3 skipped`。Ruff、格式、编译和 diff 检查
均通过；下一步提交、仅推送 WSL、同步 controller 并重试 maintenance-stage，仍不重建 runtime、数据库或索引。

**2026-07-23 maintenance-stage 第三个精确根因与接口裁决：** 分类回修后有效 drop-in 门禁已通过；事务随后在启用态复证阶段
补偿退出，maintenance status 每次均通过。只读预冻结的 7 个常规 unit 均为 `enabled,active`；带阶段追踪的同一事务证明
`codev-mcp-platform-docs.service` 在 `systemctl enable` 后短暂为 `enabled,activating`。持久启用链接已经正确，稍后自动回到
`active`；这不是服务失败，也不是可由补偿安全复原的活动态。

根因是 `verify_enabled_units()` 为了验证“链接已启用”而复用了 `SystemdUnitState`，后者的职责是冻结可逆活动态供补偿，必然拒绝
`activating`。不得把 `activating` 并入可逆状态、不得睡眠/重试猜测稳定窗口、不得跳过启用态证明。最小永久修复是在
`SystemdInstallPorts` 增加窄的 `read_unit_enablement_state` 注入：默认实现直接复用既有
`read_persistent_unit_enablement_state()` 的 root-owned symlink 证明；`verify_enabled_units()` 仅消费该窄接口并精确要求
`enabled`。完整 `read_unit_state` 仍仅用于事务前状态冻结和补偿 proof，语义不变。补齐“活动态 transient 但持久链接 enabled
仍能完成启用验证”、非 enabled 拒绝，以及既有 transaction 端口构造回归后，跑完整受影响验证、提交并重试同一事务。

**2026-07-23 启用态/活动态职责拆分验证：** `SystemdInstallPorts` 已增加窄的
`read_unit_enablement_state`，默认端口精确复用既有 persistent enablement reader；事务启用复证不再读取活动态，完整
`SystemdUnitState` 仍未改变其冻结和补偿职责。回归覆盖 transient 活动态不阻断已落地启用链接、disabled 链接拒绝、默认端口接线和
全部既有端口构造。定向回归 `87 passed`；包含 installation、maintenance、target preflight、stage receipt、Webhook/CodeGraph
恢复、runtime service 与部署守卫的完整受影响回归为 `318 passed, 3 skipped`。Ruff、格式、编译和 diff 检查均通过；下一步提交、
仅推送 WSL、同步 controller 并重试官方 maintenance-stage，仍不重建 runtime、数据库或索引。

**2026-07-23 maintenance-stage 第四个精确根因与 readiness 裁决：** 启用态拆分后，事务越过启用复证，但 restart 后的
`verify_running_units()` 仍立即读取 `is-active`；同一受控事务的内层错误为“运行状态未证明”。WSL `systemctl --help` 明确
`--wait` 对 (re)start 的语义是等待服务停止，不能用于本任务。结合先前实测，platform-docs 在 restart 返回时可短暂处于
`activating`，随后自动回到 `active`；这次活动态是交付目标，不能忽略或提前成功。

最小永久修复只在 systemctl Linux 适配器：将单次立即断言改为共享 15 秒 deadline 的 readiness 证明。仅
`activating`/`deactivating`/`reloading` 允许以短间隔继续观测；所有其他非 `active` 终态立即失败，超时也失败。多个重启 unit 共享同一
deadline，避免逐 unit 累积等待；不使用 `systemctl --wait`、不把过渡态写入状态模型、不改变事务/补偿边界。先写 transient
收敛成功、终态立即拒绝、共享 deadline 超时拒绝的 deterministic 回归，完成完整验证后提交、仅推送 WSL 并重试事务。

**2026-07-23 restart readiness 验证：** systemctl 适配器已以单个 shared deadline 轮询所有 pending unit，过渡态只允许继续
观测、从不作为成功结果；每轮仅睡眠剩余时间与短间隔的较小值。回归覆盖三类过渡态收敛、failed 终态零等待拒绝与两个 unit 共享
deadline 的超时拒绝。定向回归 `70 passed`；包含 systemctl、installation、maintenance、target preflight、stage receipt、
Webhook/CodeGraph 恢复、runtime service 与部署守卫的完整受影响回归为 `333 passed, 3 skipped`。Ruff、格式、编译和 diff
检查均通过；下一步提交、仅推送 WSL、同步 controller 并重试官方 maintenance-stage，仍不重建 runtime、数据库或索引。

**2026-07-23 readiness 期限与 systemd 合同复核：** 新 controller 的实机事务按 15 秒 shared deadline 运行后，platform-docs、
graph、agent-memory 已转 `active`，agent 与 web 仍为 `activating`，随后补偿后均恢复 `active`。只读 `systemctl show` 证明五个
重启 service 的 `TimeoutStartUSec` 均为 `1min 30s`；两个 timer 没有该 service 启动期限。故 15 秒是错误复用了单次 systemctl
命令 I/O timeout，而不是受管服务的启动合同。

最小修复将两个时间边界拆开：保留每次 systemctl 子进程 15 秒 I/O timeout，新增唯一的 90 秒 readiness shared deadline（与当前
受管 service 的 systemd start 合同一致）。成功仍立即返回；终态立即拒绝；所有 pending unit 仍共享总期限，绝不让 N 个 unit
累积为 N×90 秒。回归将该 deadline 作为独立真值验证后，重新跑完整验证、提交、仅推送 WSL 并重试。

**2026-07-23 readiness 期限拆分验证：** 15 秒 systemctl 子进程 timeout 保持不变；90 秒 shared readiness deadline 已由
deterministic 超时回归直接引用，防止两类期限再被混用。过渡态收敛、终态零等待拒绝与共享 deadline 均持续通过。完整受影响回归为
`333 passed, 3 skipped`，Ruff、格式、编译和 diff 检查均通过；下一步提交、仅推送 WSL、同步 controller 并重试官方
maintenance-stage，仍不重建 runtime、数据库或索引。

**2026-07-23 maintenance-stage 收口成功：** controller `61b3be6` 与服务源码同 revision；维护状态、manifest、目标用户
proof、runtime binding 和 stage identity 均通过后，官方 maintenance-stage 事务退出 0，事务后 maintenance status 再次通过。
普通服务完成收敛证明，受保护 reindex/CodeGraph/Webhook 仍按维护策略未被提前恢复；runtime release、数据库、索引和既有四类
pending queue 均未重建或手改。下一步严格按现有状态机执行：以明确 `project_id=codev-platform` 与当前服务源码完整提交配置
CodeGraph 恢复 → `reindex-maintenance restore --yes` 交接 worker 待命 → 仅消费既有增量 scope → `resume-codegraph --yes`。

**2026-07-23 restore 启动根因与渲染边界：** configure-resume-codegraph 已成功，但 restore 的受控异常链显示
`codev-reindex` 待命实例启动后 failed。脱敏 journal 的本轮行精确为 `status=200/CHDIR`、requested WorkingDirectory
permission denied；只读 systemctl show 证明已安装 unit 是 `User=helloworld`、`WorkingDirectory=/root`。这是 systemd
system unit 中 `%h` 展开为 manager home 的语义，不是 worker、数据库、索引、队列或待命状态机错误。

最小永久修复只改渲染数据流：生产 `install_systemd()` 以既有 `resolve_service_account()` 解析目标服务账号的受信 home，作为
显式 `working_directory` 传给受管 unit 聚合与运行时环境渲染；渲染器验证绝对 POSIX 路径并拒绝换行/NUL/回退。单独调用的兼容
渲染 API 保留显式 `%h` 默认，只有生产 manifest 禁止它。补齐“root 生成时 reindex/MCP unit 使用目标 home、不出现 `%h`”和
账号解析失败拒绝生成的回归，再跑完整验证、提交、仅推送 WSL、同步 controller、重新 stage/configure/restore。

**2026-07-23 WSL 服务源码 Git 元数据漂移的受控修复：** 提交 `efa1125` 已仅推送 `origin/dev`；同步服务源码时，目标用户
fetch 在 `.git/objects` 写入处被拒绝。只读审计确认工作区 tracked clean、`git fsck --no-dangling` 通过；恰有 45 个
`.git/objects` 条目为 root 所有，零个其他 UID、零个链接/特殊文件、零个组/其他可写项。这是历史 root Git 写入留下的元数据
所有权漂移，不是源码、远端、runtime、数据库或索引损坏。

允许的最小修复仅在服务仓 `.git/objects` 内：再次逐项 no-follow / 同设备 / regular-or-directory / root-owned / 无不安全写位
预检成功后，将这 45 个对象条目的 UID/GID 恢复为服务账号主身份；禁止递归 chown 工作区、删除/重打包对象、清理 refs 或改写 Git
历史。修复后必须以服务账号重新运行 `git fsck`、fetch、`merge --ff-only`、tracked-clean 复验，才构造 root controller 快照；仍不运行
runtime deploy、不改数据库/索引或 worker。

**2026-07-23 systemd 服务账号工作目录修复验证：** 生产 `install_systemd()` 已在生成 manifest 前唯一解析目标
服务账号，并将其绝对 POSIX home 显式传入所有 Python unit 的运行时环境渲染；因此 system manager 不再有机会把 `%h`
解释为 root 的 home。独立兼容渲染 API 仍可显式使用 `%h`，但生产 manifest 不会使用该默认值。路径校验拒绝相对路径、
回退段、换行和 NUL；账号解析失败时在任何输出目录或 manifest 写入前失败关闭。回归覆盖 root 生成时 reindex/MCP
unit 绑定目标 home、不出现 `%h`、解析失败零输出和不安全路径拒绝；测试夹具同时隔离宿主账号解析，避免掩盖部署语义。

定向回归 `97 passed, 1 skipped`；覆盖 systemd、maintenance、事务、stage receipt、Webhook/CodeGraph 恢复、
runtime 服务和部署守卫的完整受影响回归为 `338 passed, 3 skipped`。Ruff、格式、编译和 diff 检查均通过。下一步提交、
仅推送 `origin/dev`、同步 root controller，重新执行官方 maintenance-stage → configure → restore；继续只消费既有
增量队列，不重建 runtime、数据库或索引。

**2026-07-23 冻结预检 release 的双历史差异：** `2958ccf` 的 controller 与服务源码已安全快进并建立 root 只读快照，
新 manifest 的 12 个 unit 静态证明通过：九个 Python service 都是目标账号真实 home，生产内容无 `%h`。官方
maintenance-stage 随后在首次写 unit 前以“目标用户预检证明不一致”安全拒绝，事务后 maintenance status 仍通过。

同构的短生命周期目标用户探针只输出摘要布尔结果：exact、仅旧解释器 alias、仅旧 `%h` 均为 false，只有二者同时还原为
冻结 runtime 的旧语义时为 true。根因是不可变 current release 内的 `runtime_preflight_proof` 仍渲染所有运行时 service
为 `%h`，并对 CodeGraph/reindex 仍使用 `runtime/current`；它不能因 controller 更新而改变。现有兼容函数只白名单后者，
故正确拒绝了新 manifest。

下一项最小修复在 controller 的 target-preflight 兼容层：从受管注册表导出运行时 service 名单；仅当每个指定 payload 恰有一处
显式目标 home、两个受保护 payload 恰有一处 immutable interpreter 时，构造唯一 combined legacy proof。actual 必须与该完整、
规范摘要逐字相等才允许继续；缺失、重复、非受管 unit、其他字段或任何额外差异均继续失败关闭。先以组合成功和每种篡改拒绝的
TDD 覆盖，完成验证、提交、仅推送 WSL、同步 controller 后才重试 stage。

**2026-07-23 冻结预检联合兼容验证：** 注册表已导出唯一 `RUNTIME_BOUND_SYSTEMD_UNIT_NAMES`，并要求其非空、为受管
service 的真子集。target-preflight 在一次账号 home 解析后复用于 transient command 与兼容候选；候选顺序固定为 exact、
仅旧 current alias、旧 current alias + 旧 `%h`。联合候选先验证 payload 名称集合精确等于全部受管 unit，随后只把每个
运行时 service 中唯一的 `WorkingDirectory=<解析 home>` 替换为 `%h`，并复用既有受保护 ExecStart 的唯一 immutable→current
校验；每个中间 payload 都重算摘要。任何重复/缺失 home、非运行时 unit 的 home 或 `%h`、解释器位置/次数异常、普通摘要
漂移及其他 JSON 字段差异都不能命中候选。

新回归覆盖联合旧语义唯一成功、普通 unit 摘要漂移、运行时 service 重复 home 与 clock 非运行时 home 拒绝；既有 alias-only
兼容与所有 fail-closed 协议回归持续通过。target-preflight 定向为 `18 passed`；完整受影响回归为 `342 passed, 3 skipped`，
Ruff、格式、编译和 diff 检查均通过。下一步提交、仅推送 `origin/dev`、安全同步 controller 后重试同一官方 maintenance-stage；
仍不重建 runtime、数据库或索引。

**2026-07-23 旧错误 unit 的一次性稳定化：** 联合兼容生效后，官方 stage 已越过 target-user preflight，随后在首次写入前
冻结普通 unit 原像时拒绝“运行状态无法读取”。只读矩阵证明五个立即重启 service（platform-docs、agent-memory、graph、
agent、web）均为 `ActiveState=activating`、`SubState=auto-restart`、`ExecMainStatus=200`；其余受保护 worker/CodeGraph/
Webhook 仍 inactive/masked 并由 maintenance status 证明安全。该五项仍安装旧 `WorkingDirectory=/root`，故与本轮已定位的
CHDIR 根因相同，不是新的服务、数据库或队列故障。

不得把 `activating` 放宽进可逆 `SystemdUnitState`：补偿无法精确恢复 retry 倒计时，反而削弱事务回滚。维护门禁已持有且这五项
正在失败循环，故采用最小一次性运行态修复：只对这五个精确 unit 执行一次 `systemctl stop`，只读证明全为 `inactive/dead`，
不触及 reindex、CodeGraph、Webhook、timer、数据库或队列；随后立即重试同一官方 stage。stage 成功时才由既有策略重启普通
service，使用已验证的 `/home/user/project` unit；若任何 stop/proof 失败则停止并保持 maintenance，不手动启动 worker。

**2026-07-23 CodeGraph 恢复的冻结 worker 锁竞争根因与收口计划：** stage、configure、官方 restore 与既有四类增量队列已
按状态机完成；队列最终为 `pending=0, active=0, results=8`。最后的 `resume-codegraph` 在 controller 的全局
`maintenance_systemd_transition_lock()` 内恢复 reindex 基线并执行五秒稳定性证明。当前 immutable runtime 中的 worker 在
marker 存在时会申请 reader SH 以证明待命身份；EX 持有超过其锁等待期限后，旧 worker fail-closed 退出，导致结算阶段稳定性
证明失败并自动回维护态。该行为是锁协议与恢复编排的时序冲突，不是数据库、队列、CodeGraph 载荷或 service 配置问题。

不能仅修改 `maintenance_gate_state`：运行中的 worker 由 immutable current release 加载，controller 同步不会覆盖它；为日常
增量恢复重建 release 又违反本任务边界。选定的永久方案是调整 controller 编排，而不放宽旧 worker 的身份校验：先在长时
transition EX 内完成 CodeGraph 与 Webhook 的启动、健康和稳定证明，此时 reindex 仍由 `Restart=no` 且 marker 停止；释放 EX 后，
复用既有 reindex 恢复状态机完成待命启动、基线 `Restart=always`、稳定性和依赖复证；最后仅以短 EX 删除 marker 作为唯一写权限
提交点。这样旧 worker 从不跨越长 EX，锁繁忙、身份异常和 marker 失效仍维持 fail-closed。

本次影响面为 L4 controller 状态机：`reindex_codegraph_resume`、其窄适配器、`reindex_maintenance_restore` 的依赖边界及
对应单元回归；不修改 systemd unit、frozen runtime、runtime/base/release/wheel、依赖、数据库、索引或队列内容。实现前先写
“长 EX 内 worker 必须停止”、“入口在 marker 删除前已完成验收”、“短最终 EX 后才允许 worker 写入”、“任一阶段失败仍完整回维护”
的 RED 回归；实现后运行 CodeGraph resume、reindex restore、maintenance、Webhook、gate 和 systemd transaction 受影响测试，
再执行 Ruff、格式、编译与 diff 检查。通过后仅提交并推送 `origin/dev`，安全同步 controller 快照，在既有 maintenance 上重试
官方 `resume-codegraph`；成功后只观察既有增量 worker，不触发任何全量重建。

**2026-07-23 锁时序方案复核与正式分相契约：** 只把原有 EX 缩短而不保留全程管理员串行会让 installer、迁移或 prepare
插入 CodeGraph 已就绪与 marker 删除之间，故不足以根治。新增的 controller 会话锁只被管理员状态机获取，固定顺序为
`仓租约 → 会话 EX → intent EX → gate EX`；冻结 worker 仍只取 `intent SH → gate SH`，因此会话锁可以覆盖整次恢复而不阻塞
其待命证明。既有 intent/gate 转换入口自动纳入同一会话锁，避免旧管理操作绕过新会话。

恢复状态明确拆为：M0=`marker + reindex stopped/Restart=no + CodeGraph/Webhook hold`；M1=长 gate EX 内完成
CodeGraph 启动、身份/health/stability/enable，并建立“服务已就绪、reindex 仍封闭”的显式相位；M2=仅持会话锁启动同一
reindex standby、绑定 InvocationID、恢复 `Restart=always` 并在无长 EX 下稳定复证；M3=Webhook 已完成受控开放、marker
仍存在，生产者至多追加 pending、worker 仍无索引写许可；M4=短 gate EX 内快速复核 handoff 后耐久删除 marker，随后只释放锁
和租约。M3 是对旧“入口仅在 marker 后开放”时序的有意替代：维护期控制面 enqueue 已有明确允许，且它保证 marker 删除后没有
可能失败的外部动作、worker 不会先于入口验收写入。

实现不把 ready proof 伪装成 maintenance proof：正向 M1/M2/M3 使用 CodeGraph/Webhook ready 或 held 的独立窄端口；任何失败
补偿仍只使用 M0 maintenance proof，重建 marker、`Restart=no`、CodeGraph/Webhook hold 并停止 worker。reindex handoff 将按
“session 内准备待命 → session 内基线结算/长稳定证明 → transition 内短提交”拆分；短提交内禁止健康等待、稳定窗口、启动或
`daemon-reload`。补充会话锁可信预置/重入/顺序、旧 worker 不跨长 EX、marker 删除前的 M3 证明、各阶段失败回 M0、以及无
marker 后不再执行可失败动作的回归；同步当前运行手册的 supersession 说明。仍不发布 runtime，也不触碰数据库、索引和队列。

## 计划自检

- 覆盖性：路径、不可变对象、lease lineage、terminal evidence、state CAS、rollback、崩溃窗口和并发竞争均对应至少一个任务。
- 职责：根绑定只在 `runtime_root_binding.py`；受管文件机械实现只在 `_runtime_managed_file_contract.py` / `_runtime_managed_file_bound.py`，`runtime_managed_file.py` 只做兼容 facade；路径/锁只在 `runtime_storage.py`；四个 store 不互相持有具体实现。
- 一致性：普通控制写入口只接收 `ControlLeaseProof` 并由 `RuntimeControlGate.mutation()` 在同一 bind + lock 中验证后授予 ACTIVE `RuntimeMutationScope`；退休后精确清理只能经无 token 的 `RuntimeTerminalCleanupGate` 授予同一 RETIRED tombstone scope。scope 内所有 I/O 复用相同 `BoundRuntimeRoot`，reservation 是唯一的重复冲突例外。
- 范围：实现阶段未实现 Task 5 release 自描述、Task 6 legacy takeover；真实 systemd/索引写入与 WSL 服务部署仅限上节的 Task 7 受控收口，不扩展任何运行时代际功能。

**2026-07-23 会话锁分相恢复实现与验证：** controller 已实现全程管理员会话锁，并把原来的长转换拆为“worker
停止时的 CodeGraph 长 gate EX”“无长 EX 的 reindex 待命/稳定交接”“marker 仍在时的 Webhook 入口验收”“仅删除 marker 的短
gate EX 提交”四段；冻结 worker 只继续取得原有 reader 锁，因此不会再跨越五秒 gate EX 而被误判失败。维护 systemd 命令、状态解析和
稳定窗口已下沉到 `reindex_maintenance_systemd.py` 叶子，原维护模块仅保留稳定兼容门面，职责边界不变且文件预算从 660 行降为
505 行；门禁模块为 600 行。

新增和更新回归覆盖会话锁重入/顺序、旧 worker 在会话内可安全待命、管理员事务不能插队、M1→M4 相位顺序、入口失败不删除 marker、
CodeGraph 身份漂移拒绝及最终短提交失败回维护。定向回归为 `208 passed, 23 skipped`；Ruff、格式、编译和 `git diff --check`
通过。全仓回归为 `5954 passed, 632 skipped, 1 warning`，唯一失败是本次未触及、且在基线提交中已超预算的
`codev_platform/mcp_systemd.py`（634 行，最近提交 `2958ccf`）与 `codev_platform/runtime_fd_tree.py`（718 行，最近提交
`3e96c76`）。不为掩盖该存量债务扩大本次 WSL 恢复范围。下一步仅提交、推送 `origin/dev`、同步 root controller，并从当前
maintenance 状态执行官方 `resume-codegraph`；仍不重建 runtime、数据库、索引或依赖。

**2026-07-23 WSL 实机 effective payload 根因与最小修正：** `ee80118` 已仅推送 WSL，服务源码与 root controller 快照同 SHA，
维护 status 通过。官方 `resume-codegraph` 三次受控诊断均在 `remove_runtime_mask` 后的 effective payload 阶段失败，并均由
既有补偿回到 maintenance；无 worker、CodeGraph、Webhook 或索引写入遗留。受控错误链最终为“有效 drop-in 路径集合未证明”。

只读清单证明 CodeGraph 的有效集合正好是 deployment guard、恢复 drop-in 与永久 maintenance guard；reindex 除这类受管项外，
还保留 root 受信的 `20-codev-cpu-embedding.conf`（仅两个设备环境变量）和 `30-codev-start-limit.conf`，且 M1 本就必须保留
`Restart=no` 的 maintenance drop-in。原 verifier 错把旧流程“先删除 maintenance drop-in”的时序假设带入 M1，要求 reindex
只剩 deployment/resume 两项，因而拒绝了正确的分相状态。

修正不删除、不重写或宽松接受 CodeGraph drop-in：将 staged effective verifier 拆为独立叶子并新增显式“保留 reindex 本地
override”模式，只在 M1 使用。该模式仍逐字证明 reindex 主 unit、FragmentPath、有效 ExecStart 与已验证的恢复配置，且先单独
证明 `Restart=no` maintenance drop-in；仅不把 root 受信的既有 reindex 运行参数当作未知 CodeGraph 载荷。CodeGraph 的三项
drop-in、主 unit、条件、ExecStart 继续精确集合/原像校验，活动 `90-codev-release.conf` 仍拒绝。补充 strict/兼容双向回归、
M1 boundary 回归和不允许 CodeGraph 额外 drop-in 的回归；再同步 controller 并重试一次官方恢复。

**2026-07-23 M1 local override 修正验证：** `mcp_systemd_staged_effective_payload.py` 现只负责 staged
effective 组合；`mcp_systemd_install_systemd.py` 保留兼容门面与既有依赖注入入口，文件降至 463 行。默认 strict 模式保持原有
reindex/CodeGraph drop-in 精确集合；仅 `default_codegraph_effective_payload_proof()` 在 M1 先调用窄
`prove_reindex_maintenance_dropin()`，再显式启用 `allow_reindex_local_dropins=True`。因此 reindex 的 unit 原像、
FragmentPath、ExecStart、恢复配置和 `Restart=no` 均仍被证明，CodeGraph 额外 drop-in 仍被拒绝；无参数的其他安装/恢复调用不改变。

定向 systemd/stage receipt/维护恢复/分相回归为 `303 passed, 23 skipped`，Ruff、格式、编译和 diff 检查通过。全仓回归为
`5956 passed, 632 skipped, 1 warning`，唯一失败仍是基线的 `mcp_systemd.py`（634 行）与 `runtime_fd_tree.py`（718 行）预算，
本次修改未触及二者。下一步提交本最小 controller 修正、仅推送 `origin/dev`、同源同步服务源码和 controller，随后从已验证的
maintenance 状态执行一次官方 `resume-codegraph`；成功后只观察 4 项 pending 的增量 worker 消费，不手工 rebuild。

**2026-07-23 CodeGraph 健康端口与 stage 载荷漂移收口（进行中）：** 两次官方恢复均在健康阶段自动补偿回 M0；第二次受控诊断证明
`codev-mcp-codegraph.service` 在 `active/running`，但配置快照要求的公开健康端口为 `19091`，有效启动命令只有 `--http`、未带
`--port`，25 秒内 101 次 loopback 请求均为连接拒绝。当前源码的端点构造器会显式生成 `--http --port <ep.port>`，故问题是旧
stage receipt/已安装 unit 与当前配置的端口契约漂移，而非 CodeGraph 进程、队列、数据库或 10 秒等待预算。

本轮只补两项窄门禁：一是把 CodeGraph 健康地址解析为共享的受控端口输入；二是在 `prove_staged_systemd_payload()` 读取已验证的
两个受保护 unit 后，无条件复证其 release 启动入口，并在恢复适配器传入的端口存在时要求 CodeGraph `--port` 与该端口精确一致。
因此旧的无端口/错端口回执会在 M0、解除 runtime mask 之前失败关闭；不会再先启动服务再等待超时。该验证只读取现有 receipt 和
canonical unit，不新增配置键、不改变 frozen runtime 或 worker 编排。完成 TDD、定向回归和提交后，仅推送 `origin/dev`、同步
controller；随后由服务账号用当前 controller 生成受限 manifest，再由 root 走既有 `maintenance-stage` 事务刷新受管 unit，最后
重新 configure/resume 并观察既有四项增量队列，不重建 runtime、依赖、数据库或索引。

实现与验证已完成：健康证明叶子公开唯一的 loopback 端口解析；stage receipt 在 canonical payload 读取后总是复证两个受保护
启动入口，并可接收恢复上下文已验证的 CodeGraph 端口作精确比对。适配器在 M0 staged proof 与 M1 effective proof 均传入该端口，
所以旧 unit 的无 `--port` 形态和任意错端口形态均在 `remove_runtime_mask()` 之前失败。回归新增“旧 stage 缺显式端口”和“端口与
健康地址不一致”两类证据；受影响的 systemd、stage receipt、CodeGraph resume、maintenance 与 runtime handoff 集合为
`425 passed, 2 skipped`，Ruff、格式、编译和 `git diff --check` 均通过。下一步只提交、推送 `origin/dev`、同步 controller，按
既有受控 maintenance-stage 刷新 manifest 后再执行 configure/resume。

**2026-07-23 WSL manifest 生成身份边界：** 实机预检证明服务账号读取用户配置时可正确得到 `19091`，但它不能绑定
`/var/lib/codev-platform/runtime` 的 root-owned release；这在 `install_systemd()` 首次写入前即被 descriptor-bound 根校验拒绝。
不修改用户配置，也不放宽 runtime 权限。刷新阶段改为由 root 在 root-only 临时输入目录中、带服务账号的显式配置覆盖和显式
`SystemdRuntime(/var/lib/codev-platform/runtime)` 生成 manifest；manifest 的 `target_user` 仍为服务账号，后续 maintenance-stage
仍执行既有 target-user preflight。事务消费完成后删除该受限临时目录。此路径不新增持久环境、不生成 wheel、不改 runtime/base/release，
且消除了用户可写 manifest 输入与 root runtime 读取之间的身份冲突。

**2026-07-23 maintenance-stage 刷新已完成：** root-only 临时输入目录中生成的 manifest 已明确证明 CodeGraph argv 包含配置端口，
并由同一 controller 的既有 maintenance-stage 事务成功消费。事务按 policy 仅延迟保留 `codev-reindex.service`、
`codev-mcp-codegraph.service` 与 `codev-webhook.service`，没有手工启动或停止任何服务；临时输入已由受限路径清理。下一步从维护态
执行官方 `configure-resume-codegraph` 与 `resume-codegraph`，恢复仍以现有已成功的 CodeGraph manifest 目标 `d2c7b7f0f6a372496968718737c26dfbb5089e82`
为前置，随后只观察本次 `cf45fff` 入队的四类增量任务。

**2026-07-23 恢复配置已完成：** maintenance status 在配置前后均通过；`configure-resume-codegraph` 已由当前 controller 成功写入并
复证受管恢复配置。下一步只运行标准 `resume-codegraph` 状态机，禁止手工启动 CodeGraph/reindex/Webhook；成功后核对队列由
`pending` 转为受控消费，并等待四类 manifest 目标提交对齐。

**2026-07-23 刷新后的首次 resume：** 标准 `resume-codegraph` 约 14 秒后以受控领域错误退出，状态机已按既有补偿回到维护态；
没有手工 service 操作、数据库或索引写入。该公开错误刻意不携带内部阶段，不能据此推断根因。下一步只在同一官方状态机进程内包裹
既有端口，记录完成的固定阶段标签和异常类型白名单；失败仍让原状态机完成补偿。诊断不输出配置、journal 正文、HTTP body 或异常文本，
定位后再做最小修复或继续恢复。

阶段诊断已证明 `staged-payload → remove-runtime-mask → effective-payload → remove-codegraph-hold → start → running → identity`
均成功，唯一失败类别为 health。只读配置策略计算为 allow；维护期间 runtime mask 使 `systemctl show` 解析到遮蔽层，不能以此否定
已通过的 canonical/effective receipt 证明。下一次仍只走官方状态机，在 health 端口记录 MainPID 参数形状、NRestarts、目标/默认
loopback 端口监听布尔值及 journal 固定异常类别；不得输出原始命令、日志、异常文本或任何配置字段。

**2026-07-23 M1 冻结服务启动门禁冲突与收口方案：** 运行时诊断证明真实进程已携带 `--http --port 19091`，但从未监听并以
exit 1 自动重启；脱敏 traceback 定位到 frozen release 的 `codegraph/maintenance_gate`。该 gate 将全局 reindex marker 作为
CodeGraph 服务启动条件，而分相恢复的 M1 正确要求 marker 继续存在以封闭 worker。controller 源码无法覆盖 immutable release，
故不能仅修改该源码或删除 marker。

选定最小兼容方案是 root-owned、仅 M1 生效的 CodeGraph 一次性启动桥。bridge 仍由 frozen interpreter 执行并只加载 frozen
`codev_platform` 包；当且仅当全局 marker 活跃、CodeGraph 专属 hold 已删除、runtime mask 已解除、当前进程属于固定
`codev-mcp-codegraph.service` cgroup 时，临时放行**第一次** `require_codegraph_service_start_permitted()` 调用后立即恢复原函数。
因此 HTTP server 能完成 health 启动，但请求与后端启动仍使用原 marker gate 而拒绝，worker 写入许可也从未提前打开。

bridge 通过 root-controlled `15-...` drop-in 精确覆盖 M1 的 ExecStart；effective payload 需同时证明该 drop-in 原像和唯一 bridge
argv。初始 health/stability 后，状态机删除 bridge、daemon-reload、复证原始 ExecStart，并重新证明同一 InvocationID、health 和
稳定窗口，才 enable CodeGraph 并进入 worker/Webhook handoff。任一步失败仍回 M0。实现不改 frozen runtime、marker、数据库、索引、
依赖或 release；补齐 bridge、配置事务、effective payload、状态机时序和补偿回归后再提交、仅推送 WSL 并重试。

**bridge 残留收口约束（实施中）：** M0 不是只靠 marker/mask 的逻辑状态；状态机进入 M0 前和锁内失败补偿时，必须以当前受信
controller 的精确规格清除该临时 drop-in，并 daemon-reload 后证明不存在。不存在可幂等通过，内容或元数据不匹配一律拒绝，不覆盖未知
文件。这样崩溃后遗留的 bridge 不会被下一次正常启动继承；bridge 的存在窗口严格收敛为一次 M1 事务。验证增加 bridge 首次放行、
非 M1 拒绝、drop-in 写删恢复、effective argv 对应、正常 ExecStart 回切以及失败回 M0 清理顺序；仍不重建 runtime、依赖、数据库或索引。

**2026-07-23 bridge 收口实现完成，等待 WSL 官方恢复：** 新增 `codegraph_startup_bridge.py`，在冻结解释器内只替换首次服务
启动门禁；marker 非活跃、hold 未删、mask 未解或 cgroup 不匹配均失败关闭，首次调用后立即恢复 frozen 原函数。新增独立
`reindex_codegraph_startup_bridge_config.py` 管理 root-only `15-...` drop-in：源脚本、现有 drop-in、删除后的缺失状态均用
root-owned 原像复证；未知内容拒绝覆盖，删除/重载失败精确回滚。M0 进入、锁内补偿和下一次重入都清理残留 bridge。

有效载荷证明新增显式 temporary ExecStart 覆盖契约；bridge 存在时精确验证 drop-in 与 argv，删除后再次验证 canonical ExecStart。
状态机顺序固定为 `M0清理 → stage → 安装bridge → unmask → bridge有效载荷 → 启动/身份/health/stable → 删除bridge → canonical
有效载荷 → 同实例二次health/stable → enable`；任何失败仍由原锁内收敛回 M0。相关 `mcp_systemd`、CodeGraph 恢复、维护、handoff
全集回归 `508 passed, 1 skipped`；Ruff、格式、编译与 `git diff --check` 通过。下一步仅 commit/push `origin/dev`、安全同步 root
controller，并走既有 `configure-resume-codegraph → resume-codegraph` 官方路径，观察既有增量队列；不重建 runtime、依赖、数据库或索引。

**2026-07-23 WSL 实机转义兼容修复（进行中）：** 官方状态机已在 M1 完成 bridge 安装、runtime mask 解除与有效 drop-in 顺序证明，
但 systemd 将 bridge 的 Python `-c` 文本拆成多个 argv，导致有效 `ExecStart` 精确证明拒绝并自动回到 M0。该问题不涉及数据库、
索引、manifest 或 frozen runtime。修复改为冻结解释器直接执行 root-only bridge 脚本，彻底移除含空格和分号的 `-c` 参数；补充
生成命令回归、完整受影响测试、提交与 WSL 官方恢复验证后再宣告完成。

**2026-07-23 direct bridge 本地验证完成：** `CodegraphStartupBridgeSpec` 已固定为
`<frozen-python> -I -B <root-only-bridge.py> --http --port <port>`，没有 shell 或 Python `-c` 文本；源路径与解释器路径继续
受规格校验。新增回归锁定 argv 与 drop-in 内容，定向 `100 passed`、完整受影响集合 `509 passed, 1 skipped`，Ruff、格式、
编译与 diff 检查均通过。下一步仅提交、推送 `origin/dev`、刷新 root controller，再运行既有官方恢复状态机；不重建 runtime、
依赖、数据库或索引。

**2026-07-23 WSL 最终索引验收与最小自愈（执行中）：** `9328507` 已仅推送 `origin/dev`，服务仓和 root controller 均已同源；
官方 `resume-codegraph` 成功，worker 为 `running/idle`，原四类任务队列已消费完毕。最终 manifest 中 `codegraph` 与 `ingest`
均为 `ok` 且已对齐目标提交和 runtime；仅 `chroma` 与 `code_vec` 记录失败。只读健康检查同时确认 worker 正常、队列为零、
CodeGraph 数据库和 graph store 完整；Chroma 当前 build 缺少可验收元数据。失败分类是 Chroma compaction 清理失败与 code_vec
metadata segment 读取 I/O 错误，数据卷容量正常，因而判定为这两个旧 vector build 的局部存储损坏，而非 worker 卡死或代码提交未同步。

不执行手工删除、`--force`、全量 reindex、runtime/release 重建或依赖下载。现有普通增量实现已具备最小自愈：Chroma 在 manifest
缺失或失效时自动创建干净 side-build，成功校验后才原子切换 `current`；code_vec 在当前 build 探活失败时自动创建 side-build，
成功校验后才发布。旧 `current` 在新 build 完整通过前持续供读；失败 side-build 不会覆盖读库。下一步只向既有 WSL 串行队列按
`9328507` 重入 `chroma`、`code_vec` 两个 scope，保留健康的 `codegraph`、`ingest` 不动；随后复核队列清空、四类 manifest
同时为 `ok`，且目标提交/runtime 全部匹配。若任一 side-build 再次失败，停止在失败证据处，不扩大为数据库重置。

**2026-07-23 code_vec compaction 根因与永久最小回修（本地实现与验证完成，待 WSL 发布验收）：** Chroma 已由上述普通重入成功发布；code_vec 的新 side-build
仍在第 2 次 `upsert`（checkpoint `128/6521`）失败。当前 runtime 为 Chroma `1.5.9`，后端单请求上限为 `5461`，而 code_vec
固定每 `64` 条写入；collection 使用默认 HNSW `batch_size=100`，第二个 64 条请求正好跨过内部 compaction 门槛。SQLite
`quick_check` 仍为 `ok`，因此该故障不能由底层 SQLite 健康探针提前识别，且不是磁盘空间、worker、并发或队列租约问题。

已在自动清理的 WSL 临时目录以相同版本、相同 `6521` 条和相同 `102` 次 64 条写入复现并验证：创建 collection 时将官方支持的
HNSW `batch_size` 和 `sync_threshold` 同时设为 `50000` 后，写入、重开和计数均成功。选定实现为独立的 code_vec Chroma
collection 配置叶子，保持 64 条嵌入/checkpoint 批量不变，仅在新 collection 创建时写入这两个安全阈值；同时把该存储策略版本
纳入 checkpoint 指纹，使旧的默认阈值 build 不会被续跑误用，而是走既有 side-build 原子迁移。不会修改 runtime 依赖、手工删除
数据库、重置健康索引或扩大到其他项目。验证包括配置叶子单测、旧 checkpoint 触发 full side-build 的回归、现有 code_vec 存储/队列
回归、Ruff、格式、编译，以及 WSL 上的最终 manifest 验收。本地定向/扩展回归为 `258 passed, 1 skipped`，配置实证为
`6521` 条、`102` 次 64 条写入、重开计数均成功；不会把 HNSW 内部阈值变更误做成嵌入批量或模型变更。

**2026-07-23 冻结 worker 的薄发布与最终验收边界：** 只读证明当前 `codev-reindex` 进程固定加载当前 immutable release，
服务源码仓快进或 controller 同步均不会覆盖已运行 worker；因此仅 push 后重入 code_vec 仍会执行旧的默认 HNSW 策略。当前
release/base 均通过只读身份复验，且构建工具链可用。本轮允许且仅允许构建一个应用层 thin release：从本次精确提交生成本地
wheel，复用现有已验证 base，wheel 构建为 `--no-deps --no-build-isolation`，release 安装为 `--no-index --no-deps --no-compile`。
禁止 `runtime base`、依赖下载、依赖解析、手工修改 runtime 对象或数据库目录；activation 必须使用当前 release 作为显式 rollback
anchor，失败只执行既有原子回滚入口。

发布后不把新提交误当成“只需 code_vec”的索引状态：该提交同时包含运行时代码与受管计划文档，四类 manifest 的目标提交都必须
前移。按既有串行队列正常投递 `chroma`、`codegraph`、`ingest`、`code_vec` 四个增量 scope；前三项仅做常规增量，只有
code_vec 因 storage policy 从旧默认阈值迁移而进行一次隔离 side-build，旧读库在新 build 验收前持续保留。worker 更新只经既有
maintenance 状态机完成，不使用裸 `systemctl`。最终仅在队列无 pending/active/failed、worker 稳定、四类 manifest 全为 `ok`，
且每项 `head_match/runtime_match` 均为真时收口；否则保留已验证 current/previous 回滚对并停止，不扩大为重建。

**2026-07-23 thin release 候选访问门禁实测：** 首个服务账号候选 wheel 已按无依赖方式成功生成，但 root-fd worker 的
`release-prepare` 在读取该账号 `0700` 候选目录前失败。相同 root user transient scope 的最小只读复现证明：该 scope 保留
`NoNewPrivileges`，不能依赖 root 的 DAC override 穿透服务账号私有目录；直接取消该门禁或放宽服务账号目录均不可接受。故候选生成
改为由 root controller 从已核对、干净的服务源码精确提交构建到 root-owned `0700` candidate root；之后仍由既有 root-fd worker
读取、stage、verify。服务账号候选保留供审计，不手工删除；新候选不包含依赖、不写 base、数据库或索引。此调整只解决受控发布的
输入可读性，不改变运行时对象访问策略。

**2026-07-23 root 候选 Git 身份桥（本地实现与验证完成，待 WSL bootstrap）：** root candidate 的第一次实测又证明当前 `runtime build` 会以 root 直接读取
服务账号 Git 工作树，Git 的 dubious-ownership 门禁正确拒绝。禁止写入全局 `safe.directory`、禁止改服务仓属主/权限、禁止 root 直接
信任任意用户路径。新增的窄桥只服务于 explicit `runtime build --source-user <账号>`：仅 Linux root 可调用；先严格证明该账号存在、
仓根为该账号拥有的非链接目录且无 group/other 写位，再以固定 `runuser --user <账号> -- git -C <受检仓>` 运行本地
`rev-parse`、`status`（如适用）和精确 `archive`。默认 build 路径与非 root 调用保持原行为，不隐式降权或扩大信任。候选输出仍必须
为 root `0700`，Git 子进程环境固定为无网络、无交互、空 `PYTHONPATH`，只覆盖该账号 home/PATH；wheel 与 release 安装的无依赖
约束不变；候选 wheel 也固定增加 `--no-index`，与既有 `--no-deps --no-build-isolation` 共同阻断任何依赖下载。

实现按单一职责拆为候选构建参数/CLI 接线和 Git archive 受限执行两个边界；测试至少覆盖：CLI 显式参数、root-only 拒绝、账号/仓
属主与权限拒绝、固定 runuser argv、无 source-user 的兼容行为以及 archive 仍只接收精确提交。实现后运行 runtime candidate、Git
snapshot、CLI parser、runtime build/stage 相关回归和静态检查；通过后才以 root-only controller snapshot 构建本次候选。该一次性
bootstrap 不调用 retired `runtime deploy`，不构建 base、不下载依赖、不写索引。

实现已新增显式 `--source-user` CLI 接线；默认候选构建保持原调用方式，指定服务账号而未指定 revision 会失败关闭。Git snapshot
边界集中持有账号、仓属主/权限、固定 `runuser` argv 与无网络环境，不向 release/stage 层泄漏账号逻辑。新增回归覆盖 CLI、精确
revision、候选→snapshot 传递、固定 `runuser`、非特权拒绝和离线 wheel 参数；扩展 runtime build/stage、release binding、
worker scope、Git snapshot 与 CLI parser 集为 `100 passed, 27 skipped`，Ruff、格式、编译与 diff 检查通过。下一步只提交并仅推送 WSL，物化同 SHA 的 root controller snapshot，
再用该 controller 对 root `0700` 候选目录执行一次 thin release build/stage。

**2026-07-23 systemd 目标用户预检的命名空间契约冲突（执行中）：** `fbb5e81` 的薄 release 已复用既有 base 完成
activation，且 maintenance 窗口、manifest 运行时绑定和目标服务账号的解释器执行权限均已证明。随后既有
`runtime access-repair-current` 按其既定安全策略，把 runtime、`bases`、`releases` 三个父命名空间精确发布为
`root:服务组 0710`：服务账号可穿越至受控对象，但不能列举父目录。这是访问投影的设计目标，不是数据库、索引或依赖问题。

实际 systemd 瞬时预检返回固定的 `runtime_root_traverse` 失败。根因是
`runtime_preflight_paths.permission_requirements()` 将该语义为“仅穿越”的检查错误映射为 `directory_rx`，而该探针要求
`R_OK | X_OK`；因此每次 access repair 后，`runtime_preflight_proof` 都会以 `preflight_failed` 退出，维护态安装事务安全拒绝。

最小永久修复是把该单一检查引入明确的 `directory_traverse` 探针类型，只验证目录存在、为目录且目标用户具备 `X_OK`；其余
`directory_rx`、读写、原子替换、队列和导入来源检查保持不变。实现前先补契约/路径/文件系统单测：0710 的服务组目录应通过
`runtime_root_traverse`，缺少 execute 位必须失败，其他 `directory_rx` 仍必须要求 read+execute；再补 target-user preflight
与 access-repair 集成回归，证明 0710 namespace 能通过完整 systemd 同源预检。通过 Ruff、格式、编译、diff 和受影响回归后，
仅提交并推送 `origin/dev`；再按已证明的 root controller → root candidate → 复用 base 的 thin release → access repair →
maintenance-stage 事务流程发布。禁止 runtime base、依赖下载、数据库重建、手工 chmod/chown、裸 systemctl 和伪造 manifest。
新提交改变运行时和计划文档，最终仍只向既有串行队列投递四个 scope 的正常增量；仅 code_vec 依存既有 storage-policy side-build，
其余 scope 不重建。

**2026-07-23 仅穿越预检实现与本地验证完成：** 已将 `runtime_root_traverse` 的固定 kind 改为
`directory_traverse`，并在 `runtime_preflight_filesystem.py` 中集中实现只校验目录存在、目录类型和 `X_OK` 的叶子探针；
`runtime_preflight.py` 只增加该 kind 的分派。`directory_rx` 仍原样要求 `R_OK | X_OK`，没有放宽 release、base、数据目录、
导入来源或任何可写路径。新增测试明确锁定“仅有 execute 时 traverse 通过而 rx 失败”与“缺 execute 仍失败”，并更新固定契约断言。

本地预检/证明/target-user/systemd/访问修复定向集为 `54 passed, 12 skipped`；扩展 systemd 安装事务与运行时服务访问集为
`163 passed, 16 skipped`；Ruff、格式、编译与 `git diff --check` 均通过。下一步仅提交并推送 `origin/dev`，再从该精确提交构建
无依赖 thin release，复用已验证 base；发布后先运行访问投影修复和 maintenance-stage 事务，再由官方恢复状态机启动服务并投递
四类正常增量任务。不会把这项运行时契约修复误当作数据库重建。

**2026-07-23 root manifest 配置上下文收口（执行中）：** `2aee9ff` 已完成 thin release、访问投影和目标用户权限证明；
`runtime_root_traverse` 已不再失败。maintenance-stage 随后安全拒绝于 unit 摘要不一致：九个依赖配置的 Python 服务 unit 与
目标用户实时渲染不同，三个纯 systemd unit 一致。无服务启动、无数据库或索引写入，维护状态仍通过。

只读对照证明：root 按自身 HOME 载入配置时九项摘要不同；root 仅以服务账号 HOME 作为配置默认上下文重新渲染时，十二项摘要全部
与 systemd target-user proof 一致。因此根因不是 manifest、runtime binding 或服务账号权限，而是根进程静态 `Path.home()` 默认值
混入了用户态 manifest。

永久修复不靠调用方手工设置 `HOME`，也不让服务账号绑定 root runtime：为配置读取增加显式、可选的 service-home 上下文；
`serve-mcp install-systemd --config-user <服务账号>` 仅允许 Linux root 使用，解析该账号的受信 home，并要求它与目标 `--user`
一致。它只用该 home 计算配置路径和动态默认值，manifest 输出仍由 root 写入 root-owned 目录；普通用户默认路径、既有 CLI 语义和
目标用户预检不变。补充配置默认值/环境覆盖、root-only/mismatch 拒绝、CLI 传递及 root 生成内容与目标 home 一致的回归；通过后提交、
仅推送 WSL，再复用同一 base 做一次无依赖 thin release。仍禁止手工 HOME、数据库重建、依赖下载和裸 systemctl；最终四 scope 目标
以前移后的最新提交为准。

**2026-07-23 root manifest 配置上下文实现与验证完成：** `load_config()`/`config_path()` 新增可选 `home`，只在显式提供
绝对 `Path` 时用该 home 计算动态模型默认值和默认配置位置；`CODEV_PLATFORM_CONFIG` 环境覆盖、默认调用语义和进程 HOME 均保持不变。
`serve-mcp install-systemd` 新增 `--config-user`：仅 POSIX root 可用、必须同时显式给出相同的 `--user`，并通过既有服务账号解析器
取得受信 home；root 仍写自己的 root-owned manifest 目录，因此不会向服务账号目录写入或让服务账号绑定 root runtime。

新增回归覆盖显式 home 默认值、环境配置优先级、相对 home 拒绝、CLI 参数、root 传递、非 root 拒绝、账号不一致和缺显式目标用户拒绝。
定向集为 `100 passed, 1 skipped`；CLI/config/preflight/systemd transaction/访问修复交叉集为 `232 passed, 23 skipped`；Ruff、
新增测试格式、编译与 diff 检查通过。下一步仅提交、推送 `origin/dev`，重做应用层 thin release（复用同一 base），再以
`--user helloworld --config-user helloworld` 生成 root manifest；只有 target-user proof 和 maintenance-stage 都通过后才恢复服务与
既有增量队列。

**2026-07-23 target-user proof 工作目录一致性收口（执行中）：** 已部署 `fc8dcb9` 的配置上下文修复后，新的 root manifest
确实绑定了新 release、12 个固定 unit，并且 root 显式 service-home 配置摘要与真实 target-user 的配置摘要完全一致；因此此前
“root 默认 HOME 混入配置”的根因已被消除。受控的只读 target-user proof 仍只在九个 Python unit 上发生哈希差异，release、
runtime revision、target user 和 unit 集合均一致，纯 systemd unit 仍全部一致。

进一步的无写入逐项复现证明差异唯一来自工作目录：生产 `install_systemd()` 已按受信服务账号记录写入绝对
`WorkingDirectory=/home/user/project`，而 `runtime_preflight_proof.create_target_user_systemd_proof()` 调用同一渲染器时遗漏该参数，
使其回退为历史 `%h`。以绝对目录渲染时 12 项均与冻结 manifest 一致；以 `%h` 渲染时恰好是相同的九项 Python unit 不一致。
这不是配置、权限、数据库或索引问题。

**2026-07-24 CodeGraph 启动桥来源信任与发布访问投影冲突（执行中）：** `eb331230` 的 thin release、maintenance-stage、
恢复配置、四类既有增量任务和 manifest 验收均已完成；`chroma`、`codegraph`、`ingest`、`code_vec` 均为 `ok` 且对齐当前
runtime revision。最终官方 `resume-codegraph` 在安装 M1 一次性启动桥前安全回维护，未启动未受控服务、未写入数据库或索引。

受控阶段探针只记录边界名称，确认失败固定发生在 `install_codegraph_startup_bridge`。只读原像证明 bridge 源是 root 所有、
常规文件、无组/其他写位，但 thin release 的既有服务访问投影为 `root:服务组 0640`；bridge 专用校验却错误硬编码
`root:root 0644`，与已经验证的 release 访问契约矛盾。该文件已经由通用受管文件读取器验证 root 所有、单链接、常规文件及
无不安全写位，因此最小修复只收窄为复用该通用真值，不手工 `chmod/chown`、不放宽写权限、不改 bridge argv、drop-in、
maintenance 状态机或数据库/索引。

后续顺序：先补 root:服务组 `0640` 的允许回归，以及非 root 所有、组/其他可写、空来源的拒绝回归；随后以单一来源信任 helper
替换 bridge 的重复 mode/gid 判断，运行 bridge/CodeGraph 恢复定向测试和 Ruff；仅提交并推送 `origin/dev`，复用既有 base
进行一次无依赖 thin release，再走现有 `maintenance-stage → configure → restore → 增量队列 → resume-codegraph` 状态机。
最终仍要求全部服务为 OK、worker 稳定、队列 `pending=0/active=0`，四类 manifest 同时对齐新 runtime revision；任何失败
继续自动回维护而不扩大为重建。

**2026-07-24 来源信任回修与本地验收完成：** `_require_trusted_bridge_source()` 已改为只接受
`RootOwnedRegularFileSnapshot` 与非空载荷；该值对象和对应受管 reader 已集中保证 root 所有、常规单链接与无组/其他写位，
因此保留 `root:服务组 0640` 的安全只读发布投影，不接受伪造字段对象或空源。新增回归锁定服务组只读源可安装 bridge、
非受管/空源在写 drop-in 前拒绝。先后得到预期 RED（旧实现拒绝 `0640` 并错误接受伪造对象），随后 GREEN 为 bridge 定向
`8 passed`；CodeGraph、systemd transaction 与受管文件交叉集为 `487 passed, 29 skipped`，Ruff、格式、编译和 diff 检查通过。
下一步仅提交、仅推送 `origin/dev`，从精确提交复用当前 base 生成无依赖 thin release，重新执行既有维护收口链；不进入 Task 8。

永久修复集中在 proof 自身的目标账号边界：新增 fail-closed 的服务账号工作目录解析，仅接受既有解析器确认的同名、绝对 home，
并把该目录显式传入 `render_managed_systemd_units()`；不修改清单生成器、事务、服务启停或数据面。回归测试锁定 proof 渲染必须接收
该绝对目录；与 target preflight、systemd install、运行时访问路径的交叉集为 `96 passed, 13 skipped`。后续仅提交并推送
`origin/dev`，再复用已验证 base 做一次无依赖 thin release；重新生成 manifest 后必须先通过无写入 proof，再执行 maintenance-stage
事务和既有恢复状态机。四项索引仍只做正常增量投递，禁止重建或手工改数据库。

## 2026-07-25 Task 7 收口补充：code_vec 跨进程可见性

**状态：已完成（仅处理这一项，未进入 Task 8）。**

**目标：** 修复 WSL 正常增量 `code_vec` 在写入成功后、独立完整性探针过早读取时误判失败的问题，使既有队列可以稳定完成单项增量验收。

**已复核事实：** 本轮 `4d363a6` 的 `chroma`、`codegraph`、`ingest` 均已成功并对齐目标提交。`code_vec` 在 18 秒内完成 27 个变更节点的 upsert，随后独立探针返回完整性失败；只读复核当前 collection 后，期望 ID、实际 ID、数量和分页均为 `17184`，缺失和意外 ID 都为 `0`。因此不是 Git、CUDA、CodeGraph、队列卡死或数据库损坏，而是写入 client 尚未释放时跨进程可见性的短窗口造成的假阴性。

**范围与约束：**

- 仅调整 `codev_platform/recall/code_vector_*` 中写端资源释放与独立探针的衔接，并补定向回归；不改变嵌入模型、批次、HNSW 策略、索引 schema 或公共查询接口。
- 不重建 runtime/base/release/wheel，不下载依赖，不删除或手工修改 Chroma/SQLite/manifest/队列数据，不裸启停 systemd 服务。
- 保持 fail-closed：只有释放写端后独立进程完整验证通过才写 manifest 或发布 side-build；关闭失败、最终 ID 不一致或探针不可用均仍失败，不得假绿。
- 复用既有 WSL 串行 worker；本次源码与计划提交会由既有 hook 正常投递四项增量 scope，前三项只做普通增量，不手工重投或重建。

**有序步骤：**

1. 先补 RED：断言构建编排在跨进程完整性验证前释放本次写入 client，且关闭失败阻止验证与发布。
2. 最小 GREEN：由 build target 提供窄的写端关闭能力；默认实现只关闭本次 `PersistentClient`，验证后不再访问该 collection。
3. 运行 code_vec 定向测试、Ruff、格式和编译检查；复核不影响既有完整性失败闭锁与 side-build 原子切换。
4. 提交后仅推送 `origin/dev`，复用当前 base 做无依赖 thin release；按既有受控状态机刷新 immutable worker，不重建数据。
5. 由既有 hook 正常投递四项增量 scope，等待队列清空并复核四项 manifest；重点验收 `code_vec` 成功，最后运行官方 `resume-codegraph`，验收全部服务、worker 与队列。

**验证口径：** `tests/test_code_vec_storage_proof.py`、`tests/test_code_vector_probe_process.py`、相关 code_vec 队列回归、Ruff/format/compile；WSL 只读 ID 集证明、单 scope 正常增量、四项 manifest 均为 `ok` 且目标提交精确为 `4d363a6a0ebf0c83e2d3687d8f620584d58103d7`。

**本地实现与验证完成：** 将 build target 与写端生命周期拆入 `code_vector_build_target.py`，编排层只在跨进程证明前调用其窄关闭能力；实际 `PersistentClient` 缺少关闭接口也会 fail-closed。新增“关闭先于证明”“关闭失败不发布”“客户端缺少关闭接口拒绝构建目标”三条回归。定向测试为 `66 passed, 1 skipped`，Ruff、格式、编译与 `git diff --check` 均通过。下一步仅提交、推送 `origin/dev`，再按既有受控发布链消费 hook 自动投递的四项正常增量任务，重点验收 `code_vec`。

**2026-07-25 运行时版本收口诊断与最小恢复（进行中）：** 当前 release 的源码版本为
`ee39226ad2d1ea94006c95c626d101ca039becd8`，维护窗口、配置同源和当前 release 身份均已只读证明通过。
队列已清空且四项 `target_commit` 均对齐该提交，但只读 manifest 显示 `chroma`、`codegraph`、`ingest`
的 `runtime_revision` 仍为旧 release `4d363a6a0ebf0c83e2d3687d8f620584d58103d7`；仅 `code_vec`
已使用当前 release。故 `resume-codegraph` 严格拒绝恢复是正确的 fail-closed 行为，不是数据库损坏、索引卡死或
需要重建。

恢复仅在现有维护窗口内执行：先证明 stage canonical payload 仍绑定当前 release；随后通过当前 release 的既有
`reindex-queue enqueue` 入口，以同一 target commit 仅重新投递 `chroma`、`codegraph`、`ingest` 三项正常增量任务，
不触碰已匹配的 `code_vec`，不删除任何数据或历史结果。然后使用既有 `restore` 让绑定当前 immutable interpreter 的
worker 消费队列；四项 manifest 的 target/runtime 均精确匹配后，才再次运行官方 `resume-codegraph`。若任一项仍不匹配，
保持维护态并继续只读定位，不放宽 manifest 门禁。

**2026-07-25 最终验收完成：** stage canonical payload 已证明绑定 `ee39226`；仅补投的 `chroma`、`codegraph`、`ingest`
均由当前 immutable worker 正常完成，`code_vec` 保持此前已验收的当前 release 结果。四项 manifest 均为 `ok`，其
`target_commit` 与 `runtime_revision` 均精确等于 `ee39226ad2d1ea94006c95c626d101ca039becd8`；队列
`pending=0/active=0`，worker 为 `running/idle`。官方 `resume-codegraph` 成功完成，`platform-docs`、`codegraph`、
`agent-memory`、`graph` 四个 MCP endpoint 全部为 `OK`。全过程未重建 runtime/base、未下载依赖、未重建或手工修改数据库/索引，
未推送 GitHub。
