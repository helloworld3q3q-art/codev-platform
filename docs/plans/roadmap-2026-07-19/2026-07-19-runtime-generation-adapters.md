# 运行代际资源适配器实施计划

> **执行要求：** 必须使用 `superpowers:subagent-driven-development` 按独立职责逐任务分派；每项行为使用 `superpowers:test-driven-development`；任务 1→2→5 因 Alembic 和 `web/db/tables.py` 依赖必须串行，禁止并发修改；合并前使用 `superpowers:requesting-code-review`。

**目标：** 让数据库、写者、四套索引、systemd、配置和入口许可都具备统一的 prepare/verify/activate/restore 语义，并能被外层持久事务逐资源恢复。

**架构：** 每类资源提供窄 Protocol 和不可变证据对象；适配器不拥有部署状态机，只执行一个幂等资源动作。数据库采用 expand/backfill/contract 的向前兼容策略；索引采用 immutable generation + CAS pointer；systemd 由 root 固定模板渲染完整 bundle；`serve-permit` 是状态与验收一致后的最后发布物。

**技术栈：** 正式运行目标 Linux CPython 3.12 x86_64、SQLAlchemy/Alembic、PostgreSQL、SQLite、Chroma、CodeGraph、systemd、pytest、Bash。

**全局约束：** 中文注释和文档；资源适配器不得直接推进 `GenerationState`；每个外部动作必须可重复观察与补偿；数据库生产环境永不 downgrade；旧 writer 未静默时禁止发布；四索引必须组成同一 `IndexGenerationSet`；release 不得提供任意 root 目标路径或任意 systemd 文本；permit 缺失即关闭。

---

## 任务 1：数据库向前兼容契约与双版本探针

**文件：**

- Create: `codev_platform/runtime_database_contract.py`
- Create: `codev_platform/runtime_database_compatibility.py`
- Create: `codev_platform/web/db/runtime_compatibility_postgres.py`
- Modify: `codev_platform/web/db/migration_contract.py`
- Modify: `codev_platform/web/db/migration_coordinator.py`
- Modify: `codev_platform/web/db/migration_postgres.py`
- Modify: `codev_platform/runtime_deployment_services.py`
- Test: `tests/test_runtime_database_contract.py`
- Test: `tests/test_runtime_database_compatibility.py`
- Test: `tests/test_runtime_compatibility_postgres.py`
- Modify Test: `tests/test_database_migration_package.py`
- Modify Test: `tests/test_database_migration_coordinator.py`
- Modify Test: `tests/test_database_migration_verification.py`
- Modify Test: `tests/test_runtime_deployment_services.py`

### 步骤

- [ ] 先写失败测试，构造 baseline 和 target 两份契约，覆盖：相同 lineage 的 expand 兼容、目标迁移后 baseline 仍可读写、migration head 不相等但结构兼容、破坏列/约束不兼容、不同 lineage 拒绝、尝试 downgrade 拒绝。
- [ ] 运行：

```powershell
python -m pytest tests/test_runtime_database_contract.py tests/test_runtime_database_compatibility.py tests/test_runtime_compatibility_postgres.py -q
```

期望：新模块不存在导致失败。

- [ ] 实现纯领域契约：

```python
class DatabaseMigrationKind(StrEnum):
    PREPARE_ADDITIVE = "prepare_additive"
    EXPAND = "expand"
    BACKFILL = "backfill"
    CONTRACT = "contract"


@dataclass(frozen=True, slots=True)
class DatabaseColumnRequirement:
    name: str
    data_type: str
    nullable: bool
    default_sql: str | None


@dataclass(frozen=True, slots=True)
class DatabaseObjectRequirement:
    object_kind: str
    qualified_name: str
    columns: tuple[DatabaseColumnRequirement, ...]
    constraints: tuple[str, ...]
    indexes: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class DatabaseCompatibilityContract:
    schema_version: int
    lineage: str
    compatibility_epoch: int
    release_revision: str
    migration_head: str
    supported_revisions: tuple[str, ...]
    allowed_descendant_heads: tuple[str, ...]
    migration_script_sha256: str
    migration_kind: DatabaseMigrationKind
    required_objects: tuple[DatabaseObjectRequirement, ...]
    transition_schema_sha256: str
    final_schema_sha256: str


@dataclass(frozen=True, slots=True)
class DatabaseCompatibilityDecision:
    compatible: bool
    baseline_revision: str
    target_revision: str
    reason_code: str


def require_migration_compatible(
    target: DatabaseCompatibilityContract,
    baseline: DatabaseCompatibilityContract,
) -> DatabaseCompatibilityDecision:
    """只接受同 lineage 且 baseline 能在目标扩展结构上运行。"""
```

- [ ] PostgreSQL 探针只查询 Alembic head、information_schema、约束和权限，返回不可变 `DatabaseCompatibilityProof`；探针不得执行 DDL。
- [ ] `DatabaseObjectRequirement` 逐表声明必需列、类型、nullable、默认值、索引和约束；`DatabaseMigrationKind` 只允许 `prepare_additive`、`expand`、`backfill`、`contract`。仅比较一个 schema hash 不构成兼容证明。
- [ ] 正式生产前，在生产快照或隔离克隆上使用目标 release 执行真实 migration，再分别用真实 target/baseline wheel 执行回滚所需 CRUD、队列与图谱 smoke probe；proof 绑定快照身份、两份 wheel、migration script 和过渡/最终结构摘要。
- [ ] migration coordinator 在全局 advisory lock 下执行：验证克隆 proof → 目标 preflight → 事务性 expand → 可检查点、按 attempt 幂等的 backfill → 目标结构探针 → baseline 自身解释器结构/CRUD 探针。两份 proof 都通过才返回成功。
- [ ] 将现有“head 必须完全相等”的 `verify_postgres_current()` 从生产验收改为“契约声明的 required schema 满足”；精确 head 校验仅保留开发诊断用途。
- [ ] 应用服务启动路径禁止自行迁移，运行账号无 DDL 凭据；DDL credential 只通过受保护 FD/systemd credential 交给事务控制器。存在 rollback generation 租约时拒绝 `contract` migration。
- [ ] 对当前 migration 建立明确 lineage 和 compatibility epoch；未声明兼容契约的 release 在生产部署中 fail closed。
- [ ] 运行：

```powershell
python -m pytest tests/test_runtime_database_contract.py tests/test_runtime_database_compatibility.py tests/test_runtime_compatibility_postgres.py tests/test_database_migration_package.py tests/test_database_migration_coordinator.py tests/test_database_migration_verification.py tests/test_runtime_deployment_services.py -q
```

期望：兼容扩展通过，破坏性变化与 downgrade 均失败。

- [ ] 提交：`feat(runtime): 增加数据库双版本兼容门禁`

## 任务 2：为队列 claim、索引发布和结果确认增加 writer fencing

**文件：**

- Create: `codev_platform/core/writer_fence.py`
- Create: `codev_platform/reindex/generation_queue.py`
- Create: `codev_platform/reindex/pg_queue_fence_sql.py`
- Create: `codev_platform/web/db/alembic/versions/20260719_0010_reindex_generation_fence.py`
- Modify: `codev_platform/web/db/tables.py`
- Modify: `codev_platform/web/db/runtime_schema.py`
- Modify: `codev_platform/reindex/queue_ports.py`
- Modify: `codev_platform/reindex/pg_queue_sql.py`
- Modify: `codev_platform/reindex/pg_queue_schema_contract.py`
- Modify: `codev_platform/reindex/pg_queue_codec.py`
- Modify: `codev_platform/reindex/file_queue_codec.py`
- Modify: `codev_platform/reindex/attempts.py`
- Modify: `codev_platform/reindex/spec_factory.py`
- Modify: `codev_platform/reindex/attempt_validation.py`
- Modify: `codev_platform/reindex/result_publisher.py`
- Modify: `codev_platform/reindex/orchestrator_finalizer.py`
- Modify: `codev_platform/reindex/isolated_worker_composer.py`
- Test: `tests/test_runtime_writer_fence.py`
- Test: `tests/test_reindex_generation_queue.py`
- Modify Test: `tests/test_reindex_queue_contract.py`
- Modify Test: `tests/test_reindex_queue.py`
- Modify Test: `tests/test_reindex_manifest_proof.py`
- Modify Test: `tests/test_reindex_orchestrator.py`

### 步骤

- [ ] 先写并发失败测试：旧 worker 已 claim，部署提升 epoch 后旧 worker 尝试 manifest publish 和 ack；两步都必须被拒绝，且新 worker 可以接管。
- [ ] 实现 fence 领域对象和 verifier：

```python
@dataclass(frozen=True, slots=True)
class WriterFence:
    schema_version: int
    deployment_attempt_id: str
    generation_id: str
    authority_kind: str
    authority_id: str
    epoch: int
    token_sha256: str


@dataclass(frozen=True, slots=True)
class PublicationPermit:
    project_id: str
    kind: str
    attempt_token: str
    writer_fence: WriterFence
    capability: ControlLeaseProof | ServingFenceProof = field(repr=False)


class WriterFenceVerifier(Protocol):
    @contextmanager
    def publication_permit(
        self,
        expected: WriterFence,
        capability: ControlLeaseProof | ServingFenceProof,
    ) -> Iterator[PublicationPermit]:
        """在同一锁/事务内复验能力、已提交 state 与维护门禁，并保护发布和确认。"""
```

- [ ] Alembic 0010 在 `reindex_jobs` 添加 pending/active/result 三段的 generation ID、fence epoch 和 token digest；迁移只前向增加 nullable 列并回填 legacy 标识，不删除旧列。
- [ ] 维护窗口前的候选构建只使用 attempt-scoped isolated queue/worker 和候选目录，legacy worker 无法发现或 claim；共享生产队列只在 permit 已撤销、legacy writer 已静默且新 fence 生效后接管。测试用真实 legacy codec 证明它不会领取 generation candidate。
- [ ] 更新 `QUEUE_COLUMN_CONTRACT`、codec 和 file queue schema，使 PG/file 两种后端语义一致；任务载荷不得包含原始 fencing token。
- [ ] 新增的 fence/CAS SQL 放入 `pg_queue_fence_sql.py`，`pg_queue_sql.py` 只重导出兼容入口，避免在现有 436 行模块继续聚合新职责。
- [ ] 固定锁顺序并写入模块注释：generation/maintenance lock → queue claim → manifest publication permit → result ack。任何反序调用在测试端通过 recording lock 拒绝。
- [ ] `AttemptSpec` 和 `AttemptResult` 增加 typed `WriterFence`；runner 输出增加结构化 `ArtifactGenerationProof`，不能从自由文本日志猜生成物。
- [ ] `WriterFence` 只携带审计身份；attempt-scoped candidate 的真正授权来自 `ControlLeaseProof`，已提交 serving writer 来自稳定 `ServingFenceProof`，两者不可互换。worker 通过继承 FD、systemd credential 或 peer-credential 本地 socket 获得能力，读取任务 JSON 中的摘要不能获得发布权限。
- [ ] `ServingFenceProof` 的校验必须在覆盖前置复验、pointer/manifest replace 与 result ack 的同一临界区读取 `GenerationState`，并与 `begin_switch` 的 state CAS 共用锁或数据库事务锁：只接受 `mode ∈ {steady, restricted}`、`maintenance_active=False`、generation 等于当前 serving generation，且 fence id/epoch/token 摘要精确等于 state 中已提交 fence。provisional fence 在 serving CAS 前不具备生产发布权限；`switching` / `validating` 连旧 serving proof 也一律拒绝。
- [ ] `ControlLeaseProof` 只允许写 attempt-scoped candidate namespace/queue，不能借控制能力直接替换生产 pointer。增加两个确定性并发测试：旧 writer 在 `begin_switch` 后发布和 ack 均失败；target writer 在 provisional fence 签发后、`commit_serving` 前以及维护门禁关闭前均失败。
- [ ] 增加 barrier 并发测试：旧 writer 已通过前置校验并持锁、但尚未 replace 时，`begin_switch` 不得提交；释放后只允许“pointer/manifest/ack 全部完成，再切换”或“切换先完成，发布在复验处被拒绝”，禁止 switching 后仍落盘或只发布未 ack 的半结果。
- [ ] `result_publisher` 在 pointer/manifest replace 前后、同一锁/数据库事务内复验 lease authority；`orchestrator_finalizer` 在 ack 前复验同一 capability。
- [ ] 增加旧 controller 测试：recovery 提升 epoch 后，即使 pointer 和任务摘要未变化，旧 controller 也不能提交 journal、index、ack 或 permit。
- [ ] 运行：

```powershell
python -m pytest tests/test_runtime_writer_fence.py tests/test_reindex_generation_queue.py tests/test_reindex_queue_contract.py tests/test_reindex_queue.py tests/test_reindex_manifest_proof.py tests/test_reindex_orchestrator.py -q
```

期望：旧 epoch 在任何发布窗口都不能写成 current。

- [ ] 提交：`feat(runtime): 为重建写者增加代际围栏`

## 任务 3：建立四索引代际统一契约和集合 manifest

**文件：**

- Create: `codev_platform/core/index_generation_contract.py`
- Create: `codev_platform/runtime_index_generation.py`
- Create: `codev_platform/runtime_index_manifest_adapter.py`
- Modify: `codev_platform/core/index_handoff.py`
- Modify: `codev_platform/ops/reindex_manifest_proof.py`
- Test: `tests/test_index_generation_contract.py`
- Test: `tests/test_runtime_index_generation.py`
- Test: `tests/test_runtime_index_manifest_adapter.py`
- Modify Test: `tests/test_index_handoff.py`
- Modify Test: `tests/test_reindex_manifest_proof.py`

### 步骤

- [ ] 先写失败测试，固定四种 kind 恰为 `chroma`、`codegraph`、`ingest`、`code_vec`；集合缺一、多一、重复 project、revision 不一致、artifact 摘要漂移均失败。
- [ ] 实现统一契约：

```python
class IndexKind(StrEnum):
    CHROMA = "chroma"
    CODEGRAPH = "codegraph"
    INGEST = "ingest"
    CODE_VEC = "code_vec"


@dataclass(frozen=True, slots=True)
class IndexGeneration:
    schema_version: int
    kind: IndexKind
    generation_id: str
    target_revision: str
    projects_sha256: str
    artifact_sha256: str
    created_at: str


@dataclass(frozen=True, slots=True)
class IndexGenerationSet:
    schema_version: int
    target_revision: str
    generations: tuple[IndexGeneration, ...]
    created_at: str


@dataclass(frozen=True, slots=True)
class IndexPointerSnapshot:
    kind: IndexKind
    generation_id: str | None
    pointer_sha256: str


@dataclass(frozen=True, slots=True)
class IndexPrepareRequest:
    target_revision: str
    deployment_attempt_id: str
    projects: tuple[str, ...]
    writer_fence: WriterFence


@dataclass(frozen=True, slots=True)
class IndexVerification:
    generation_id: str
    artifact_sha256: str
    projects_sha256: str
    verified: bool


@dataclass(frozen=True, slots=True)
class IndexActivationPermit:
    kind: IndexKind
    generation_id: str
    control_lease: ControlLeaseProof = field(repr=False)


class IndexGenerationAdapter(Protocol):
    def prepare(self, request: IndexPrepareRequest) -> IndexGeneration:
        """在不可见候选位置构建。"""

    def verify(self, generation: IndexGeneration) -> IndexVerification:
        """只读验证候选内容、revision 和完整性。"""

    def activate(
        self,
        generation: IndexGeneration,
        expected: IndexPointerSnapshot,
        permit: IndexActivationPermit,
    ) -> IndexPointerSnapshot:
        """比较交换 active pointer。"""

    def restore(
        self,
        snapshot: IndexPointerSnapshot,
        expected_active: IndexPointerSnapshot,
        permit: IndexActivationPermit,
    ) -> IndexPointerSnapshot:
        """比较交换恢复旧 pointer。"""

    def inspect(self) -> IndexPointerSnapshot:
        """读取当前 active 身份和摘要。"""
```

- [ ] `runtime_index_generation.py` 只聚合四类 adapter，按 kind 排序生成不可变 `index_set.json`；不把新职责继续塞入已 574 行的 `index_manifest.py`。
- [ ] `index_handoff.py` 增加 root-bound、拒绝 symlink、父目录 fsync 和 expected-current CAS；旧 `commit_build()` 保留为兼容门面并调用新 CAS。
- [ ] manifest proof 读取结构化 generation proof，不解析 runner 日志；`BuildRecord` 只增加对 index set 摘要的窄引用，如导致文件超过 600 行，先把 schema/codec 拆到 `index_manifest_contract.py`。
- [ ] 运行：

```powershell
python -m pytest tests/test_index_generation_contract.py tests/test_runtime_index_generation.py tests/test_runtime_index_manifest_adapter.py tests/test_index_handoff.py tests/test_reindex_manifest_proof.py -q
```

期望：集合不完整时不能进入 activate。

- [ ] 提交：`feat(runtime): 建立四索引代际集合契约`

## 任务 4：实现 Chroma 与 code_vec 文件型代际适配器

**文件：**

- Create: `codev_platform/chroma/runtime_generation.py`
- Create: `codev_platform/recall/code_vector_generation.py`
- Modify: `codev_platform/chroma/_models.py`
- Modify: `codev_platform/chroma/_project_state.py`
- Modify: `codev_platform/recall/code_vector_paths.py`
- Modify: `codev_platform/recall/code_vector_build.py`
- Modify: `codev_platform/recall/code_vector_query.py`
- Test: `tests/test_chroma_runtime_generation.py`
- Test: `tests/test_code_vector_generation.py`
- Modify Test: `tests/test_health_chroma_handoff.py`
- Test: `tests/test_code_vector_build.py`

### 步骤

- [ ] 先写失败测试：在候选 build 完成前 reader 始终看到旧 pointer；候选验证失败不改 pointer；第一个 kind 激活后第二个失败能恢复第一个；旧 client cache 在 pointer 切换后被逐出。
- [ ] Chroma adapter 复用每项目 `builds/<id>` 和 current pointer，把 collection、BM25、`.last_build.json`、输入 revision 和项目列表纳入 artifact digest。
- [ ] code_vec adapter 复用现有 checkpoint/build/manifest，generation identity 必须绑定 embedding 模型、维度、chunk 规则、目标 revision 和项目列表。
- [ ] reader 只能通过 pointer 解析 active build；legacy 无 pointer 仅在首次接管 adapter 中允许，正式 managed generation 禁止 fallback 到 base 目录。
- [ ] activate/restore 都要求 expected pointer 摘要和不可序列化的 `IndexActivationPermit`；适配器在 pointer CAS 的同一临界区内复验 lease authority。任何 cache eviction 失败只允许入口保持关闭，不得继续签发 permit。
- [ ] 运行：

```powershell
python -m pytest tests/test_chroma_runtime_generation.py tests/test_code_vector_generation.py tests/test_health_chroma_handoff.py tests/test_code_vector_build.py -q
```

期望：两种文件型索引具备对称切换和恢复。

- [ ] 提交：`feat(runtime): 代际化文档与代码向量索引`

## 任务 5：实现 CodeGraph 与统一图谱代际适配器

**文件：**

- Create: `codev_platform/codegraph/generation.py`
- Create: `codev_platform/graph/generation.py`
- Create: `codev_platform/graph/sqlite_store_schema.py`
- Create: `codev_platform/graph/sqlite_store_generation.py`
- Create: `codev_platform/web/db/alembic/versions/20260719_0011_graph_generation_shadow.py`
- Modify: `codev_platform/codegraph/backend_runtime.py`
- Modify: `codev_platform/codegraph/backend_startup.py`
- Modify: `codev_platform/graph/schema.py`
- Modify: `codev_platform/graph/store.py`
- Modify: `codev_platform/graph/pg_store.py`
- Modify: `codev_platform/web/db/tables.py`
- Test: `tests/test_codegraph_generation.py`
- Test: `tests/test_graph_generation.py`
- Modify Test: `tests/test_graph_store.py`
- Test: `tests/test_graph_pg_store.py`
- Test: `tests/test_codegraph_backend_runtime.py`

### 步骤

- [ ] 先写失败测试，证明当前 in-place delete/upsert 不满足代际语义：构建失败不能破坏 active，切换失败能恢复，reader 不混读两个 generation。
- [ ] 在增加代际逻辑前，先把已 599 行的 `graph/store.py` 中 SQLite DDL、旧库迁移和 generation 文件定位分别抽到 `sqlite_store_schema.py`、`sqlite_store_generation.py`；先运行既有 graph store 测试证明纯重构无行为变化，再实施后续红灯测试。
- [ ] CodeGraph 将每项目 `.codegraph` 候选构建到 runtime 管理的 generation 根，验证数据库文件集合、目标 commit 和工具版本，再通过每项目 pointer CAS 激活；backend startup 从 pointer 解析路径。
- [ ] SQLite graph 每项目使用 generation 独立 `.sqlite` 文件和 pointer，不在 active 文件上执行 delete/upsert；candidate 完成 `PRAGMA integrity_check`、项目隔离 audit 和 revision proof 后才能激活。
- [ ] PostgreSQL graph 的 0011 是纯 additive 准备迁移：创建 legacy wheel 不会查询的 `graph_v2_nodes/edges/evidences/findings/ingest_meta` 和 `graph_v2_active_generations(project_id, generation_id, deployment_attempt_id, epoch, token_sha256)`；绝不修改 legacy 五表的列、主键、索引和既有数据。
- [ ] 0011 必须先在克隆库由真实 legacy wheel 证明可忽略，再由事务控制器以 `prepare_additive` 类型安装；只有 shadow 表存在后才允许在维护窗口前预构建 managed 候选。任务 5 串行依赖任务 1、任务 2 和 Alembic 0010，不与共同修改 `web/db/tables.py` 的任务并发。
- [ ] managed `PgGraphStore` 所有读查询只访问 v2 表，并在同一事务读取 active generation 后附加过滤；写入只写 candidate generation，禁止删除 active generation；activate 使用 `UPDATE ... WHERE generation_id = expected AND epoch = expected_epoch` CAS。legacy rollback 继续由旧 wheel 读取未改变的旧五表。
- [ ] 双真实版本测试必须在 managed 候选数据已经写入 shadow 表时运行 legacy 完整读写探针，证明旧版仍只看到 legacy 数据。
- [ ] cleanup 只删除既非 active、非 rollback、非任何未完成 attempt 引用且超出保留期的 generation。
- [ ] 运行：

```powershell
python -m pytest tests/test_codegraph_generation.py tests/test_graph_generation.py tests/test_graph_store.py tests/test_graph_pg_store.py tests/test_codegraph_backend_runtime.py -q
```

期望：CodeGraph、SQLite graph、PostgreSQL graph 均不再原地覆盖 active 数据。

- [ ] 提交：`feat(runtime): 代际化代码图谱与统一图谱`

## 任务 6：冻结配置 bundle 与 root 渲染 systemd bundle

**文件：**

- Create: `codev_platform/runtime_configuration_bundle.py`
- Create: `codev_platform/runtime_systemd_bundle_contract.py`
- Create: `codev_platform/runtime_systemd_bundle_renderer.py`
- Create: `codev_platform/runtime_systemd_bundle_store.py`
- Modify: `codev_platform/mcp_systemd_unit_registry.py`
- Modify: `codev_platform/mcp_systemd.py`
- Test: `tests/test_runtime_configuration_bundle.py`
- Test: `tests/test_runtime_systemd_bundle_contract.py`
- Test: `tests/test_runtime_systemd_bundle_renderer.py`
- Test: `tests/test_runtime_systemd_bundle_store.py`
- Modify Test: `tests/test_runtime_systemd.py`

### 步骤

- [ ] 先写失败测试：release 尝试声明绝对 destination、未知 unit、任意 root 命令、越权 environment file、符号链接 payload、bundle digest 循环依赖，均必须拒绝。
- [ ] 配置 bundle 只保存 root 读取的受保护快照和身份；敏感文件 mode 0600。公开 generation/证据不保存秘密正文的直接 SHA-256，秘密使用 root-keyed HMAC 或加密载荷身份。
- [ ] 秘密优先通过 `LoadCredentialEncrypted` 或受保护 FD 注入，不进入 Environment、argv 和日志；回滚前复验 credential version，已撤销凭据不得恢复并进入 `safety_unproven`。
- [ ] 配置 bundle 的身份由排序后的角色引用计算，时间戳不参与 bundle ID：

```python
@dataclass(frozen=True, slots=True)
class ConfigurationPayloadRef:
    role: str
    payload_relative_path: str
    public_sha256: str | None
    identity_hmac: str | None
    credential_version: str | None
    mode: int
    uid: int


@dataclass(frozen=True, slots=True)
class ConfigurationBundleIdentity:
    schema_version: int
    bundle_id: str
    payloads: tuple[ConfigurationPayloadRef, ...]
    created_at: str
```
- [ ] 非秘密 payload 必须且只能设置 `public_sha256`，秘密 payload 必须且只能设置 `identity_hmac` 和 `credential_version`；codec 对两者同时存在或同时缺失 fail closed。
- [ ] 定义受限 systemd 载荷：

```python
@dataclass(frozen=True, slots=True)
class SystemdBundleFile:
    role: str
    unit_name: str
    drop_in_name: str | None
    payload_relative_path: str
    sha256: str
    mode: int


@dataclass(frozen=True, slots=True)
class PreparedSystemdBundle:
    descriptor: SystemdPayloadBundle
    payloads: tuple[SystemdBundlePayload, ...]


@dataclass(frozen=True, slots=True)
class SystemdPayloadBundle:
    schema_version: int
    bundle_id: str
    files: tuple[SystemdBundleFile, ...]
    units: tuple[SystemdBundleUnit, ...]
    stage_receipt_sha256: str
    created_at: str


def render_systemd_payloads(
    release: GenerationReleaseIdentity,
    entrypoints: TargetEntrypointDeclarations,
    configuration: ConfigurationBundleIdentity,
    *,
    runtime_root: Path,
    service_user: str,
) -> tuple[SystemdBundlePayload, ...]:
    """root 固定模板先渲染 unit/drop-in，不接收 stage receipt。"""


def build_systemd_stage_receipt(
    payloads: tuple[SystemdBundlePayload, ...],
    *,
    release: GenerationReleaseIdentity,
    configuration: ConfigurationBundleIdentity,
) -> bytes:
    """根据最终 payload 摘要构建本代 receipt。"""


def prepare_systemd_bundle(
    payloads: tuple[SystemdBundlePayload, ...],
    stage_receipt: bytes,
) -> PreparedSystemdBundle:
    """校验 receipt 与 payload 后形成可冻结 bundle。"""
```

- [ ] renderer 不接收完整 `RuntimeGeneration`，避免 generation ID 与 bundle digest 循环；目标绝对路径由 root 固定映射推导，release 只声明 registry 中的逻辑入口。
- [ ] 固定顺序为“渲染 unit/drop-in → 根据最终 payload 摘要生成 receipt → 冻结完整 bundle”；legacy raw receipt 只从 rollback bundle 原样恢复，不使用当前 renderer 重建。
- [ ] registry 增加 `start_group`、`serve_gate`、writer 属性，成为 unit 顺序和 gate 的唯一真值；禁止在多个脚本复制 unit 列表。
- [ ] `freeze_systemd_bundle()` O_EXCL 保存完整 unit/drop-in/stage receipt/enablement；`load_systemd_bundle()` 复算每个 payload 和总摘要。
- [ ] 现有 `mcp_systemd.install_systemd()` 保留兼容门面，但正式生产事务不再调用它。
- [ ] 运行：

```powershell
python -m pytest tests/test_runtime_configuration_bundle.py tests/test_runtime_systemd_bundle_contract.py tests/test_runtime_systemd_bundle_renderer.py tests/test_runtime_systemd_bundle_store.py tests/test_runtime_systemd.py -q
```

期望：bundle 可复现且任何 release 提供的 root 自由文本被拒绝。

- [ ] 提交：`feat(runtime): 冻结配置与 systemd 代际载荷`

## 任务 7：将 systemd 安装拆成可 journal 的单资源动作

**文件：**

- Create: `codev_platform/runtime_systemd_bundle_activation.py`
- Create: `codev_platform/runtime_systemd_lifecycle.py`
- Modify: `codev_platform/runtime_systemd_handoff.py`
- Modify: `codev_platform/runtime_systemd_payload_acceptance.py`
- Modify: `codev_platform/runtime_deployment_quiesce.py`
- Test: `tests/test_runtime_systemd_bundle_activation.py`
- Test: `tests/test_runtime_systemd_lifecycle.py`
- Modify Test: `tests/test_runtime_systemd_handoff.py`
- Modify Test: `tests/test_runtime_systemd_payload_acceptance.py`
- Modify Test: `tests/test_runtime_deployment_quiesce.py`

### 步骤

- [ ] 先用 recording ports 写第 N 个文件 replace、daemon-reload、stop、mask、unmask、enable、start 失败测试；每个动作必须能由“观察当前事实 + journal expected_before”确定重做还是补偿。
- [ ] 实现文件叶子动作：

```python
def observe_systemd_file(file: SystemdBundleFile, *, ports: SystemdBundlePorts) -> SystemdFileObservation:
    """观察目标文件或缺失状态。"""


def apply_systemd_file(
    file: SystemdBundleFile,
    payload: bytes,
    expected_before: SystemdFileObservation,
    *,
    ports: SystemdBundlePorts,
) -> SystemdFileEvidence:
    """仅应用一个文件并验证摘要。"""


def restore_systemd_file(
    file: SystemdBundleFile,
    original: SystemdFileObservation,
    expected_current: SystemdFileObservation,
    *,
    ports: SystemdBundlePorts,
) -> SystemdFileEvidence:
    """仅恢复一个文件并拒绝第三方漂移。"""
```

- [ ] 实现 lifecycle 叶子：`stop_and_prove_unit()`、`mask_and_prove_unit()`、`unmask_owned_unit()`、`restore_enablement()`、`start_and_prove_unit()`；每个函数只处理一个 unit。
- [ ] runtime mask 必须绑定 attempt，并精确验证 `/run/systemd/system/<unit> -> /dev/null` 归属；persistent 或第三方 mask 不得擅自删除。
- [ ] quiesce 覆盖 registry 中所有 writer，验证 cgroup 为空和 MainPID 退出；不再只停止普通 writer 或只 mask CodeGraph。
- [ ] 外层事务负责逐动作 journal；现有 `install_systemd_transaction()` 的进程内 originals/actions 仅作为兼容工具，不用于跨重启生产事务。
- [ ] 运行：

```powershell
python -m pytest tests/test_runtime_systemd_bundle_activation.py tests/test_runtime_systemd_lifecycle.py tests/test_runtime_systemd_handoff.py tests/test_runtime_systemd_payload_acceptance.py tests/test_runtime_deployment_quiesce.py -q
```

期望：所有第 N 步失败均可确定性恢复，第三方状态不被覆盖。

- [ ] 提交：`refactor(runtime): 拆分 systemd 可恢复资源动作`

## 任务 8：实现 serve-permit 和稳定 recovery launcher/service

**文件：**

- Create: `codev_platform/runtime_serve_permit.py`
- Create: `codev_platform/core/runtime_serve_gate.py`
- Create: `codev_platform/gateway/runtime_gate_middleware.py`
- Create: `codev_platform/runtime_recovery_launcher.py`
- Create: `codev_platform/runtime_recovery_service.py`
- Create: `codev_platform/runtime_controller_service.py`
- Create: `codev_platform/runtime_controller_trust.py`
- Create: `codev_platform/runtime_systemd_start_condition.py`
- Modify: `codev_platform/runtime_systemd_gate_contract.py`
- Modify: `codev_platform/runtime_deployment_ingress.py`
- Modify: `codev_platform/web/app.py`
- Modify: `codev_platform/webhook/server.py`
- Modify: `codev_platform/chroma/server.py`
- Modify: `codev_platform/codegraph/server.py`
- Modify: `codev_platform/graph/mcp_server.py`
- Modify: `codev_platform/agent/memory_mcp.py`
- Modify: `scripts/install-wsl-runtime.sh`
- Test: `tests/test_runtime_serve_permit.py`
- Test: `tests/test_runtime_serve_gate.py`
- Test: `tests/test_runtime_gate_middleware.py`
- Test: `tests/test_runtime_recovery_launcher.py`
- Test: `tests/test_runtime_recovery_service.py`
- Test: `tests/test_runtime_controller_service.py`
- Test: `tests/test_runtime_controller_trust.py`
- Test: `tests/test_runtime_systemd_start_condition.py`
- Modify Test: `tests/test_runtime_systemd_gate_contract.py`
- Modify Test: `tests/test_web_foundation.py`
- Modify Test: `tests/test_runtime_webhook_acceptance.py`
- Modify Test: `tests/test_chroma_search_schema.py`
- Modify Test: `tests/test_codegraph_server_drain.py`
- Modify Test: `tests/test_graph_mcp_authz.py`
- Modify Test: `tests/test_agent_memory_mcp.py`
- Modify Test: `tests/test_install_wsl_runtime_scripts.py`

### 步骤

- [ ] 先写 permit 失败测试：缺失、截断、符号链接、错误 mode/owner、state 摘要漂移、epoch/token 漂移、acceptance 漂移、先写 permit 后 state 变化，入口均关闭。
- [ ] permit 内容只含非秘密绑定：

```python
@dataclass(frozen=True, slots=True)
class ServePermit:
    schema_version: int
    generation_id: str
    serving_fence_id: str
    serving_fence_epoch: int
    serving_fence_token_sha256: str
    acceptance_sha256: str
    generation_state_sha256: str


def stage_serve_permit(
    target_state: GenerationStateSnapshot,
    acceptance: GenerationAcceptance,
    *,
    store: ServePermitStore,
    lease: ControlLeaseProof,
) -> ServePermitEvidence:
    """预签发绑定目标状态 B 摘要的许可；B 提交前 gate 必须因摘要失配关闭。"""


def revoke_serve_permit(
    *,
    store: ServePermitStore,
    lease: ControlLeaseProof,
) -> ServePermitEvidence:
    """原子撤销许可并 fsync 父目录。"""
```

- [ ] `ServePermitStore` 在 stage/revoke 的同一文件锁内调用 `ControlLeaseStore.require_current(lease)`；旧 controller 在 recovery 提升 control epoch 后不能 stage 或 revoke。gate 等值比较使用稳定 serving fence，不使用会轮换的 control lease。
- [ ] `stage_serve_permit()` 把绑定目标状态 B 完整摘要的许可原子写入活动许可路径，但不修改 state。当前状态仍为 A 时，gate 因 `generation_state_sha256` 不相等保持关闭；随后 A→B CAS 成功即三方一致。禁止先绑定 A 再通过第二次 permit 改写追赶 B。

- [ ] permit 为 root-owned 0644，服务用户只读；MCP/Web 在应用/网关层检查 permit，因为它们需先启动做内部验收，不能被启动条件永久挡住。Webhook/外部入口使用固定 `ExecCondition`。
- [ ] `ExecCondition` 只是启动期第二道门禁。Web、MCP、Webhook、SSE/WebSocket 与网关的每个新请求/连接都经共享 runtime gate：在 generation state 锁内读取 state/acceptance/permit 后复读 state 摘要，要求 `maintenance_active=False` 且三者一致，否则立即拒绝。内部验收只允许 loopback + 受验证服务身份在 `validating` 访问固定只读探针，不形成外部绕过。
- [ ] 长连接注册 generation/epoch 并周期复验 gate；`revoke_serve_permit()` 必须使 gate cache 失效、摘除外部 listener/route、主动关闭或排空既有 SSE/WebSocket/HTTP keepalive 并复证外部不可达。进程保持运行时撤销 permit 后，新的 HTTP/WebSocket/Webhook 请求均须被拒绝。
- [ ] recovery launcher 仅用标准库，可复制到 release 外稳定路径；只读取 expected-absent CAS 发布的固定 `active-recovery-envelope.json`。envelope 引用的 controller 必须同时匹配 root-owned 0600 `controller-trust-manifest.json` allowlist，不能用自带摘要给自己授权。
- [ ] launcher 使用固定解释器参数 `-I -s`、固定模块/argv/cwd、清洁环境，清除 `PYTHONPATH`、`PYTHONHOME`、`LD_*`，关闭非标准 FD；拒绝 `current` 链接和任何非 root 可写的 controller/interpreter 路径。
- [ ] controller trust manifest 在存在未完成 envelope 时只允许追加，不得移除其引用版本；launcher/trust 更新自身也使用原像、摘要、fsync 和补偿 journal，并至少保留能解析全部未完成 envelope 的版本。
- [ ] 拆分开机 barrier 与重复 worker：`codev-runtime-recovery-barrier.service` 使用 `Type=notify`、`RemainAfterExit=yes`，只在 boot 时等待 active envelope 收敛后 READY；`codev-runtime-recovery-worker.service` 不使用 `RemainAfterExit`，每次读取 active envelope 并可重复启动。全部业务 unit 具有 `Requires/After=...barrier.service` 和固定 recovery-boundary `ExecCondition`，Webhook 另加 serve-permit condition。
- [ ] systemd 契约测试核对完整 `Requires/After`、失败联动、root/runtime 目录权限、`NoNewPrivileges`、最小 capability 集和可写路径白名单，不只检查 unit 名称存在。
- [ ] 生产 deploy/rollback/recover 只在 `codev-runtime-controller@<attempt>.service`（或等价 transient unit）中执行；CLI 仅提交并跟踪。controller `OnFailure=codev-runtime-recovery-worker.service`，root-owned path unit 监视 active envelope 并触发同一 worker；测试证明同一 boot 前台 CLI 退出后仍实际产生新的 recovery 进程。无 active envelope 且 state 为 steady/restricted 时 barrier 可 READY，无 envelope但 state 非稳态时 fail closed。
- [ ] 安装器原子安装稳定 launcher、helper 和 recovery unit，先 daemon-reload 再 enable；重跑验证摘要相同且不覆盖未知文件。
- [ ] 运行：

```powershell
python -m pytest tests/test_runtime_serve_permit.py tests/test_runtime_serve_gate.py tests/test_runtime_gate_middleware.py tests/test_runtime_recovery_launcher.py tests/test_runtime_recovery_service.py tests/test_runtime_controller_service.py tests/test_runtime_controller_trust.py tests/test_runtime_systemd_start_condition.py tests/test_runtime_systemd_gate_contract.py tests/test_web_foundation.py tests/test_runtime_webhook_acceptance.py tests/test_chroma_search_schema.py tests/test_codegraph_server_drain.py tests/test_graph_mcp_authz.py tests/test_agent_memory_mcp.py tests/test_install_wsl_runtime_scripts.py -q
$repoWsl = (wsl.exe -d Ubuntu -- wslpath -a (Get-Location).Path).Trim()
wsl.exe -d Ubuntu -- bash -n "$repoWsl/scripts/install-wsl-runtime.sh"
```

期望：launcher 篡改、envelope 多义和 recovery 失败全部 fail closed。

- [ ] 提交：`feat(runtime): 增加启动许可与稳定恢复服务`

## 任务 9：资源适配器阶段整体验收与评审

**文件：**

- Review: 本计划所有 Create/Modify 文件
- Update: `docs/plans/roadmap-2026-07-19/2026-07-19-runtime-generation-rollback.md`（只勾选有命令证据的 M2 项）

### 步骤

- [ ] 按任务 1 至 8 的命令重新执行全部适配器测试。
- [ ] 执行既有受影响面回归：

```powershell
python -m pytest tests/test_database_migration_coordinator.py tests/test_reindex_queue.py tests/test_reindex_orchestrator.py tests/test_index_manifest.py tests/test_index_handoff.py tests/test_graph_store.py tests/test_graph_pg_store.py tests/test_runtime_systemd.py tests/test_mcp_systemd_install.py tests/test_install_wsl_runtime_scripts.py -q
python -m compileall -q codev_platform
```

期望：全部通过。

- [ ] 检查层级耦合和文件规模：

```powershell
rg -n "runtime_transaction_coordinator|runtime_production" codev_platform/chroma codev_platform/codegraph codev_platform/graph codev_platform/recall codev_platform/web/db
Get-ChildItem codev_platform -Recurse -Filter '*.py' | Where-Object { $_.Name -match 'generation|compatibility|fence|serve_permit|recovery_launcher|systemd_bundle' } | ForEach-Object { [PSCustomObject]@{ File = $_.FullName; Lines = (Get-Content -Encoding UTF8 $_.FullName).Count } } | Sort-Object Lines -Descending
```

期望：资源适配器不依赖生产协调器；新文件小于 600 行。

- [ ] 请求数据库、安全、systemd/恢复、索引一致性四类评审，逐项验证后处理。
- [ ] 执行 `git diff --check`；M2 全部满足后进入事务接线阶段。
