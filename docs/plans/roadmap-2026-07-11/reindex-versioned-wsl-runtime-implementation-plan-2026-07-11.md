# WSL 版本化运行时实施计划

> **面向自动化实施者：** 必须使用 `superpowers:subagent-driven-development`（推荐）或 `superpowers:executing-plans`，逐任务实施本计划。步骤使用复选框（`- [ ]`）跟踪。

**目标：** 将 WSL 上所有 Python `systemd` 服务从实时可编辑检出迁到可证明、可原子切换、可回滚的版本化运行时，并以内容寻址依赖基座复用数 GB 的 Torch/CUDA 依赖。

**架构：** 受审摘要锁与当前 Python ABI 共同生成 `base_id`，在 `bases/<base_id>/venv` 构建不可变重依赖基座；源码修订、应用 wheel、`base_id` 与基座元数据共同生成 `release_id`，每个版本只在最终目录创建薄 venv，并用受控纯路径 `.pth` 追加基座的 `site-packages`。`current/previous` 只切换版本符号链接；`systemd` 使用 `current/venv/bin/python` 与 `current/base/venv/bin`，服务启动时只捕获一次 `RuntimeIdentity.runtime_revision`。

**技术栈：** Python 3.10+、标准库 `venv/hashlib/json/pathlib/fcntl/subprocess/sysconfig`、setuptools wheel、pip 摘要校验、pytest、`systemd` 系统单元、现有网关认证。

## 全局约束

- Plan C 不修改 `codev_platform/reindex/**`、QueuePort、Attempt 状态、InputMaterializer、WorkspaceProvider、ResultPublisher 或 manifest 语义。
- 唯一跨计划字段为 `AttemptSpec.runtime_revision == RuntimeIdentity.runtime_revision`。
- 跨计划顺序固定为：Plan C 任务 1–2 → Plan A → Plan B → Plan C 任务 3–10。
- Plan A 在 worker 就绪检查/组合根直接调用并缓存 `runtime_identity()`，把其 `runtime_revision` 注入 `AttemptSpec`；不得另造修订提供器，也不得在 `attempt/claim` 热路径执行 Git。
- Plan A 落地 reindex 围栏后，Plan C 任务 8 必须保留 `Delegate=yes` 与 `KillMode=control-group`。
- Plan B 落地 webhook `exact_workspace` 后，Plan C 任务 9 对 `webhook/server.py` 只改受保护状态/认证组合，不改入队/物化器。
- 首版保持单 worker、单写者，不引入作业并发、DAG 或索引活动代际。
- 依赖基座和版本 venv 都直接在最终路径创建，以 `.incomplete` 门禁完成度；禁止对已创建的 venv 执行 `rename`。
- 基座一经写入 `base.json` 即不可变；版本一经写入 `release.json` 即不可变。
- 每个薄版本必须绑定一个通过 ABI、freeze、锁和元数据摘要校验的基座。
- `base_id` 与 `release_id` 只接受完整 64 位小写 SHA-256；目录名、符号链接和 CLI 均不得使用缩写。复用前必须重算 ID 并逐字段比较身份输入，任何同 ID 异内容都按碰撞失败处理。
- 基座/版本构建分别持有对应 ID 的 POSIX `flock`；不同 ID 可并行，同一 ID 只能有一个写者。激活另持有全局激活锁，固定锁顺序，禁止反向获取。
- `.incomplete` 不能通过删除标记“修好”；持有每 ID 锁后只能把整个未完成目录原子隔离到 `quarantine/incomplete/**`，写入无秘密审计记录，再从最终路径重建。隔离目录永不复用或激活。
- `.pth` 只能有一行规范化绝对基座 `purelib` 路径；禁止空行、相对路径、第二行和任何 `import` 语句。
- 版本 `site-packages` 必须位于基座 `purelib` 之前；`codev_platform` 必须只来自版本。
- 公共健康接口不暴露修订、路径或摘要；详情只进入已有受保护状态接口。
- CLI 不调用 `sudo/systemctl`；`--no-restart` 只生成安装脚本，不改变运行服务状态。
- 版本化单元必须保留现有 `User=`、可选 `EnvironmentFile=` 和服务写目录权限；安装脚本在复制/重启前必须以目标 `User` 运行真实解释器、导入、目录、quarantine、journal 和 run-lock 探针，任一失败即终止。
- 首次切换必须先从 `plan-c-main-base` 指向的提交构建、验证并激活基线版本，再构建并激活新版本；切换后 `previous` 必须指向可完整验证的基线版本，禁止以空 `previous` 进入生产。
- requirements 只冻结当前批准版本，不升级 Torch、CUDA、Chroma 或其他依赖。
- 所有新增/修改 Python 文件≤600 行；新模块遵守“文件映射”的更低目标。
- 每任务先红灯、再最小实现、再绿灯、最后独立中文提交。

## 开工检查与跨计划顺序

在独立 `worktree` 中分两个窗口执行。窗口一先记录 C1–2 的基线：

```powershell
git status --short
$leafBasePath = (git rev-parse --git-path plan-c-leaf-base).Trim()
git rev-parse HEAD | Set-Content -LiteralPath $leafBasePath -Encoding ASCII -NoNewline
```

任务 2 提交后记录叶子窗口终点：

```powershell
$leafHeadPath = (git rev-parse --git-path plan-c-leaf-head).Trim()
git rev-parse HEAD | Set-Content -LiteralPath $leafHeadPath -Encoding ASCII -NoNewline
```

随后依次实施 Plan A、Plan B。两者合入且工作树为空后，再记录 C3–10 基线：

```powershell
$mainBasePath = (git rev-parse --git-path plan-c-main-base).Trim()
git rev-parse HEAD | Set-Content -LiteralPath $mainBasePath -Encoding ASCII -NoNewline
```

预期：三个锚点均由 `git rev-parse --git-path` 解析到当前普通仓库或关联 `worktree` 自己的 Git 管理目录，且不加入 Git；最终审计分别检查 C1–2 与 C3–10 两段，排除中间 A/B 提交。

## 架构决策：共享依赖基座

| 方案 | 磁盘成本 | 隔离/回滚 | 结论 |
|---|---:|---|---|
| 每个版本完整 venv | 每版重复 Torch/CUDA 数 GB | 最直观，但成本与轻量聚合冲突 | 拒绝 |
| 共享可变全局 venv | 最小 | 依赖漂移，旧版本不可证明 | 拒绝 |
| 内容寻址不可变基座 + 薄版本 | 每个锁+ABI 一份重依赖；每版仅应用 | 版本 `sys.prefix` 独立；基座摘要固定；回滚可验证 | 采用 |

风险与控制：

- `.pth` 可执行 Python：生成器和校验器仅接受单一绝对路径，拒绝 `import`、多行和根目录逃逸。
- 基座污染应用优先级：基座禁止安装 `codev-platform`；探针断言版本 `purelib` 先于基座 `purelib`。
- 原生 ABI 不兼容：`base.json` 记录实现、Python 版本、缓存标签、SOABI、平台和机器架构；不一致时拒绝复用。
- `pip check` 跨层漏检：必须由版本 Python 在 `.pth` 生效后执行，并另测应用发行包依赖可见。
- 回滚指向已丢失基座：`verify_release()` 先验证基座链接、基座元数据摘要、ABI 与 freeze，再允许切换。
- 基座内容漂移：复用和激活时重算 freeze SHA；不一致即失败，不修补原基座。
- 摘要碰撞或半成品残留：完整 ID 仍必须逐字段校验；半成品只允许在每 ID 锁内隔离并写审计记录，不得就地补全。

---

## 文件映射

| 文件 | 动作 | 单一职责 | 目标行数 |
|---|---|---|---:|
| `codev_platform/core/runtime_models.py` | 新建 | dataclass、公共编解码委托、规范 ID 与 hash/OID/ABI 值校验 | ≤300 |
| `codev_platform/core/runtime_metadata_io.py` | 新建 | 严格类型化 JSON 解码与同目录原子文件 I/O；不承载业务模型 | ≤180 |
| `codev_platform/core/runtime_identity.py` | 新建 | 环境→前缀→可编辑→已安装的来源选择、Git/RECORD 身份与进程缓存 | ≤180 |
| `codev_platform/core/runtime_release_identity.py` | 新建 | 版本、基座、requirements 锁、ABI 与受管路径的完整证明 | ≤180 |
| `codev_platform/runtime_lock.py` | 新建 | 批准索引、原始 freeze、摘要锁生成/验证 | ≤350 |
| `codev_platform/runtime_storage.py` | 新建 | 每 ID `flock`、路径围栏与 `.incomplete` 隔离 | ≤280 |
| `codev_platform/runtime_base.py` | 新建 | 内容寻址依赖基座构建/验证 | ≤400 |
| `codev_platform/runtime_build.py` | 新建 | 干净应用 wheel 候选、薄版本暂存/验证 | ≤420 |
| `codev_platform/runtime_release.py` | 新建 | 根目录解析、激活锁、`current/previous` 原子切换 | ≤320 |
| `codev_platform/runtime_preflight.py` | 新建 | `systemd` 目标用户解释器、导入和路径权限探针 | ≤280 |
| `codev_platform/ops/runtime.py` | 新建 | 运行时 CLI 组合与结构化输出 | ≤300 |
| `codev_platform/ops/memory_maintenance.py` | 新建于 C1–2 | wheel 内 maintenance 兼容入口，保证基线版本可启动 | ≤120 |
| `tests/runtime_support.py` | 新建 | 本计划测试共用的具体构造函数/认证器 | ≤260 |
| `tests/test_runtime_*.py` | 新建 | 分层测试 | 每文件≤600 |
| `requirements/wsl-runtime.lock` | 新建 | 全量 exact pins + hashes + 唯一批准 CUDA index | 生成文件 |
| `codev_platform/core/config.py` | 修改 | `runtime.release_root` 默认值 | 保持≤600 |
| `codev_platform/mcp_systemd.py` | 修改 | 受管单元注册表、argv 改写、权限探针、禁止重启模式 | 保持≤600 |
| `codev_platform/mcp_serve.py` | 修改 | 版本子进程固定当前 Python | 保持≤600 |
| `codev_platform/cli.py`、`cli_cmds/mcp.py`、`ops/__init__.py` | 修改 | parser 和组合接线 | 各≤600 |
| 服务状态文件 | 修改 | 启动时身份快照和受保护详情 | 各≤600 |
| `docs/plans/roadmap-2026-07-11/reindex-runtime-release-runbook-2026-07-11.md` | 新建 | 切换/回滚操作规程 | ≤300 |

## 精确数据模型

所有 dataclass 放在 `codev_platform/core/runtime_models.py`；其他模块只 import，不重复定义。

```python
RuntimeMode = Literal["release", "editable", "installed"]

@dataclass(frozen=True)
class RuntimeAbi:
    implementation: str
    python_version: str
    cache_tag: str
    soabi: str
    platform_tag: str
    machine: str

@dataclass(frozen=True)
class RequirementsLockInfo:
    requirements_sha256: str
    approved_index_url: str
    pin_count: int
    artifact_manifest_sha256: str
    cuda_tags: frozenset[str]

@dataclass(frozen=True)
class BaseMetadata:
    schema_version: int
    base_id: str
    requirements_sha256: str
    approved_index_url: str
    artifact_manifest_sha256: str
    freeze_sha256: str
    abi: RuntimeAbi
    created_at: str
    python_relative: str
    purelib_relative: str
    bin_relative: str
    lock_relative: str

@dataclass(frozen=True)
class ReleaseCandidate:
    schema_version: int
    runtime_revision: str
    wheel_name: str
    wheel_sha256: str

@dataclass(frozen=True)
class ReleaseMetadata:
    schema_version: int
    release_id: str
    runtime_revision: str
    wheel_sha256: str
    base_id: str
    base_requirements_sha256: str
    base_metadata_sha256: str
    app_freeze_sha256: str
    created_at: str
    python_relative: str
    purelib_relative: str
    base_link_relative: str
    base_pth_relative: str

@dataclass(frozen=True)
class RuntimeIdentity:
    mode: RuntimeMode
    runtime_revision: str
    release_id: str | None
    wheel_sha256: str | None
    base_id: str | None
    base_requirements_sha256: str | None
    interpreter_realpath: str
    environment_prefix: str
    source_root: str | None

    def as_dict(self) -> dict[str, object]:
        return dataclasses.asdict(self)

@dataclass(frozen=True)
class ActivationResult:
    active_release: str
    previous_release: str | None

@dataclass(frozen=True)
class SystemdRuntime:
    release_root: Path

    @property
    def python(self) -> Path:
        return self.release_root / "current" / "venv" / "bin" / "python"
```

## 精确 ID 算法与碰撞规则

所有规范 JSON 使用 UTF-8、`ensure_ascii=True`、`allow_nan=False`、`sort_keys=True`、`separators=(",", ":")`；键和值均来自类型化模型，禁止加入时间、路径或环境字段。算法固定为：

```python
base_identity = {
    "abi": dataclasses.asdict(current_abi()),
    "requirements_sha256": lock.requirements_sha256,
    "schema_version": 1,
}
base_id = sha256(canonical_json_bytes(base_identity)).hexdigest()

release_identity = {
    "base_id": base.base_id,
    "base_metadata_sha256": sha256_file(base_json),
    "runtime_revision": candidate.runtime_revision,
    "schema_version": 1,
    "wheel_sha256": candidate.wheel_sha256,
}
release_id = sha256(canonical_json_bytes(release_identity)).hexdigest()
```

`requirements_sha256` 是完整锁文件字节的 SHA-256，因此 `base_id` 精确绑定锁+ABI；`base_metadata_sha256` 是规范 `base.json` 字节的 SHA-256，因此 `release_id` 精确绑定修订+wheel+`base_id`+基座元数据。两个 ID 均只使用完整 64 位小写十六进制。目标目录已存在时，必须从元数据重建上述身份输入并与本次输入逐字段比较；ID 相同但任一输入不同即抛 `RuntimeIdCollisionError`、写无秘密隔离审计并停止，禁止复用、覆盖或删除现有完成目录。

## 受管存储布局

```text
<root>/locks/base/<base_id>.lock
<root>/locks/release/<release_id>.lock
<root>/locks/activation.lock
<root>/bases/<base_id>/.incomplete
<root>/releases/<release_id>/.incomplete
<root>/quarantine/incomplete/base/<base_id>.<utc>.<nonce>/
<root>/quarantine/incomplete/release/<release_id>.<utc>.<nonce>/
<root>/journal/runtime-storage.jsonl
```

构建/隔离使用对应 ID 的排他 `flock`；公开校验按版本 ID、基座 ID 的固定顺序获取共享锁，激活/回滚先持有全局激活锁再调用公开校验。构建函数持有写锁时调用不重复加锁的私有校验辅助函数，禁止同一进程用第二个文件描述符重入同一锁；禁止同时持有两个写锁。`.incomplete` 存在时，无论 `base.json/release.json` 是否存在，都不得删除标记后复用；必须验证预期路径不是符号链接且仍位于受管根下，再用 `os.replace` 把整个目录移入 quarantine，`fsync` 源/目标父目录并追加审计记录。该 `rename` 只用于把不可执行半成品移出最终路径，绝不把 venv `rename` 到可发布路径，也绝不从 quarantine 恢复。

## 精确公共函数

| 模块 | 函数 |
|---|---|
| `core.runtime_models` | `current_abi() -> RuntimeAbi`；类型化 JSON `read_*`/`write_*_atomic`；`sha256_file(path) -> str`；`canonical_json_bytes(value) -> bytes`；`compute_base_id(requirements_sha256, abi) -> str`；`compute_release_id(runtime_revision, wheel_sha256, base_id, base_metadata_sha256) -> str` |
| `core.runtime_identity` | `runtime_identity() -> RuntimeIdentity`；声明的版本不可信时抛 `RuntimeIdentityError` |
| `runtime_lock` | `build_hashed_lock(raw_freeze, approved_source, output, download_dir) -> RequirementsLockInfo`; `validate_requirements_lock(path, approved_source) -> RequirementsLockInfo` |
| `runtime_storage` | `id_lock(root, kind, object_id, *, shared) -> ContextManager[None]`；`isolate_incomplete(root, kind, object_id) -> Path | None` |
| `runtime_base` | `build_base(root, lock, approved_source) -> BaseMetadata`; `verify_base(root, base_id) -> BaseMetadata` |
| `runtime_build` | `build_candidate(repo, out_dir) -> tuple[Path, Path, ReleaseCandidate]`; `stage_release(root, wheel, candidate_file, base_id) -> ReleaseMetadata`; `verify_release(root, release_id) -> ReleaseMetadata` |
| `runtime_release` | `release_root(cfg, explicit=None) -> Path`; `activate_release(root, release_id) -> ActivationResult`; `rollback_release(root) -> ActivationResult` |
| `runtime_preflight` | `permission_requirements(cfg, runtime) -> tuple[PathRequirement, ...]`; `probe_current_user(requirements) -> None`; 模块 CLI 只输出无秘密检查名与结果 |
| `mcp_systemd` | `rewrite_python_argv(original, runtime) -> list[str]`；所有 Python 单元渲染器均接收 `runtime: SystemdRuntime | None` |

## 受管 Python 单元清单

Plan C 必须生成并验证以下固定集合；`clock-resync` 是非 Python 操作系统单元，明确排除运行时身份。

| 单元 | argv 尾部 | 受保护运行时状态/导入证明 |
|---|---|---|
| `codev-mcp-platform-docs.service` | 保留端点 `ep.cmd[1:]` | `/platform/status`；`codev_platform.chroma.server` |
| `codev-mcp-codegraph.service` | 保留端点 `ep.cmd[1:]` | `/platform/status`；`codev_platform.codegraph.server` |
| `codev-mcp-agent-memory.service` | 保留端点 `ep.cmd[1:]` | `/platform/status`；`codev_platform.agent.memory_mcp` |
| `codev-mcp-graph.service` | 保留端点 `ep.cmd[1:]` | `/platform/status`；`codev_platform.graph.mcp_server` |
| `codev-reindex.service` | `-m codev_platform.cli reindex-queue worker` | Plan A worker 状态/`runtime_revision`；`codev_platform.cli` |
| `codev-webhook.service` | `-m codev_platform.cli webhook serve` | `/platform/status`；`codev_platform.webhook.server` |
| `codev-agent.service` | `-m codev_platform.cli agent serve` | 新增受保护 `/platform/status`；`codev_platform.agent.service` |
| `codev-web.service` | `-m codev_platform.cli web serve` | 新增受保护 `/api/v1/runtime/status`；`codev_platform.web.app` |
| `codev-memory-maintenance.service` | `-m codev_platform.ops.memory_maintenance` | 导入证明/一次性任务退出证明 |

## 测试支撑契约

任务 1 先创建 `tests/runtime_support.py`，之后所有测试只使用这里已定义的辅助函数，不使用未声明的 fixture：

```python
def write_json(path: Path, data: Mapping[str, object]) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, sort_keys=True), encoding="utf-8")
    return path

def init_git_repo(root: Path) -> Path:
    root.mkdir(parents=True)
    subprocess.run(["git", "init"], cwd=root, check=True, capture_output=True)
    subprocess.run(["git", "config", "user.email", "runtime@example.invalid"], cwd=root, check=True)
    subprocess.run(["git", "config", "user.name", "运行时测试"], cwd=root, check=True)
    (root / "tracked.txt").write_text("tracked\n", encoding="utf-8")
    subprocess.run(["git", "add", "tracked.txt"], cwd=root, check=True)
    subprocess.run(["git", "commit", "-m", "test: 初始化仓库"], cwd=root, check=True)
    return root

def minimal_config(root: Path) -> dict[str, object]:
    return {
        "runtime": {"release_root": str(root), "chroma_venv": str(root / "legacy" / ".venv")},
        "projects": {},
    }

class AcceptingAuthenticator:
    def authenticate(self, headers, query=""):
        return Identity(user_id="runtime-user", org_id="runtime-org", via="test", all_projects=True)

class RejectingAuthenticator:
    def authenticate(self, headers, query=""):
        raise Unauthorized("测试认证器拒绝请求")
```

本文件还实现并测试 `write_fake_base(root, metadata) -> Path`、`write_fake_release(root, metadata) -> Path`、`assert_path_inside(path, parent) -> None`；实现必须只调用本节已有编解码器和 dataclass，不隐藏生产逻辑。

---

### 任务 1：叶子模型、基线版本兼容入口与具体测试支撑

**文件：** 新建 `core/runtime_models.py`、`core/runtime_metadata_io.py`、`ops/memory_maintenance.py`、`tests/runtime_support.py`、`tests/test_runtime_models.py`、`tests/test_runtime_maintenance_entrypoint.py`；修改本计划的文件映射与任务清单，使实现真值保持一致。

**接口：** 产出本计划全部 dataclass/编解码器，以及基线版本和新版本共同拥有的 `python -m codev_platform.ops.memory_maintenance` 入口；后续任务只能导入这些定义，不得复制模型。

- [ ] 写红灯：40/64 位 OID、SHA-256、未知 JSON 字段、ABI 往返、原子 JSON 失败、规范化 `base_id/release_id` 算法和完整 64 位输出、maintenance 入口导入和退出码透传。
- [ ] 运行：`python -m pytest tests/test_runtime_models.py tests/test_runtime_maintenance_entrypoint.py -q`。
- [ ] 预期红灯：`ModuleNotFoundError: codev_platform.core.runtime_models`。
- [ ] 实现本计划全部 dataclass、严格编解码器、`current_abi()`、文件/字典 SHA；`runtime_models` 保持公共编解码函数，内部委托单一职责的 `runtime_metadata_io`。原子 JSON 使用同目录临时文件、`os.replace` 和父目录 `fsync`。
- [ ] 把现有 memory maintenance 脚本逻辑包装为 wheel 内模块入口，保持现有参数与退出码。该文件必须在记录 `plan-c-leaf-head` 前提交，使后续 `plan-c-main-base` 基线版本 wheel 能被最终单元启动；任务 8 不得再新增第二个入口。
- [ ] 绿灯：`python -m pytest tests/test_runtime_models.py tests/test_runtime_maintenance_entrypoint.py tests/test_resources_packaging.py -q`，预期全部通过。
- [ ] 提交：

```powershell
git add codev_platform/core/runtime_models.py codev_platform/core/runtime_metadata_io.py codev_platform/ops/memory_maintenance.py tests/runtime_support.py tests/test_runtime_models.py tests/test_runtime_maintenance_entrypoint.py docs/plans/roadmap-2026-07-11/reindex-versioned-wsl-runtime-implementation-plan-2026-07-11.md
git commit -m "feat(runtime): 增加运行时叶子模型"
```

### 任务 2：运行时身份证明

**文件：** 新建 `core/runtime_identity.py`、`core/runtime_release_identity.py`、`tests/test_runtime_identity.py`；修改本计划的文件映射、任务文件清单和提交清单，使实现真值保持一致。

**接口：** 消费任务 1 模型，产出唯一 `runtime_identity() -> RuntimeIdentity`；Plan A 只消费这个函数。

- [ ] 红灯覆盖发现顺序：环境元数据；`sys.prefix` 邻接 `release.json`；可编辑 PEP 610 真实根目录/完整 HEAD；可编辑 Git 超时/错误；已安装模式 64 位摘要；声明版本前缀不匹配。
- [ ] 测试使用 `write_fake_release()`，不使用其他辅助函数。
- [ ] 运行：`python -m pytest tests/test_runtime_identity.py -q`。
- [ ] 预期红灯：身份模块不存在。
- [ ] 实现固定发现顺序：

```text
CODEV_PLATFORM_RELEASE_FILE
→ Path(sys.prefix).parent/release.json
→ PEP 610 editable direct_url + full git HEAD
→ SHA256(distribution-version + NUL + RECORD bytes)
```

- [ ] 版本证明委托单一职责的 `runtime_release_identity`，同时验证 `.incomplete` 不存在、prefix、完整 `release_id`、`base_id`、基座元数据摘要、原始 requirements 锁摘要、ABI，以及规范基座路径无符号链接逃逸；结果使用 `lru_cache(maxsize=1)`，服务启动后不随 `current` 变化。
- [ ] 可编辑分支只允许一次本地只读 Git 探测：`git rev-parse --verify HEAD^{commit}`，参数固定为 `stdin=DEVNULL`、`capture_output=True`、`text=True`、`check=True`、`timeout=3`，并设置 `GIT_TERMINAL_PROMPT=0`；禁止 fetch、pull 或网络访问。
- [ ] 可编辑 Git 超时、非零退出、短 OID 或非 40/64 位十六进制均抛 `RuntimeIdentityError`。Plan A 必须在打开 queue/claim 前调用；异常使 worker readiness 失败并退出，不得回退版本字符串或本地时间。
- [ ] 已安装分支将 UTF-8 发行版本、单个 NUL 字节和原始 RECORD 字节依次送入 SHA-256，`runtime_revision` 只返回 64 位小写十六进制；`mode="installed"` 区分其来源。
- [ ] Plan A 消费方式固定为：worker 启动时调用一次 `identity = runtime_identity()` 并缓存，创建每个 `AttemptSpec` 时复制 `identity.runtime_revision`；不新增 `RuntimeRevisionProvider` 或第二份缓存。
- [ ] 绿灯：`python -m pytest tests/test_runtime_identity.py -q`；`rg -n "codev_platform\.reindex|Workspace|Attempt" codev_platform/core/runtime_identity.py` 无输出。
- [ ] 提交：

```powershell
git add codev_platform/core/runtime_identity.py codev_platform/core/runtime_release_identity.py tests/test_runtime_identity.py docs/plans/roadmap-2026-07-11/reindex-versioned-wsl-runtime-implementation-plan-2026-07-11.md
git commit -m "feat(runtime): 增加可验证运行身份"
```

### 任务 3：受批准 CUDA 摘要锁

**文件：** 新建 `runtime_lock.py`、`requirements/wsl-runtime.lock`、`tests/test_runtime_lock.py`。

**接口：** 产出完整锁文件摘要和 `RequirementsLockInfo`；任务 4 以该摘要和 ABI 计算 `base_id`。

- [ ] 红灯覆盖：批准来源恰有一条官方 HTTPS 索引；拒绝 userinfo；拒绝 VCS/本地/可编辑依赖；只允许移除本项目自身可编辑记录；所有 pin 精确；每个 pin 至少一个 SHA-256；`+cuNNN` 与索引 `/cuNNN` 一致；受管服务所需发行包集合齐全。
- [ ] 必需发行包集合固定包含：`torch, chromadb, sentence-transformers, rank-bm25, jieba, watchfiles, websockets, httptools, cryptography, fastapi, uvicorn, anthropic, openai, pydantic, mcp, psycopg, sqlalchemy, alembic`。
- [ ] 运行：`python -m pytest tests/test_runtime_lock.py -q`；预期红灯：锁模块不存在。
- [ ] 生成时先保存原始 freeze；除唯一规范化 `codev-platform` 记录外，任何可编辑/本地/VCS 记录都使任务失败，禁止用通用 `grep -v '^-e'` 静默删除。
- [ ] 对每个精确 pin 执行 `pip download --no-deps --only-binary=:all:`，计算 wheel SHA-256 并写入 lock；安装端使用 `pip install --require-hashes -r lock`。不保留重复 wheelhouse；摘要锁提供制品完整性，不可变基座提供离线回滚。
- [ ] `artifact_manifest_sha256` 的唯一算法为：生成按 wheel 文件名排序的 `[{"filename": str, "sha256": str}]`，用 UTF-8、`sort_keys=True`、`separators=(",", ":")` 序列化后做 SHA-256；校验器必须重算一致。
- [ ] 批准索引逐字继承 `requirements-runtime.txt` 唯一 `--extra-index-url`；用 `urlsplit` 要求 `https`、`download.pytorch.org`、无 userinfo。
- [ ] 绿灯：

```powershell
python -m pytest tests/test_runtime_lock.py -q
python -m codev_platform.runtime_lock validate requirements/wsl-runtime.lock --approved requirements-runtime.txt
```

- [ ] 提交：

```powershell
git add codev_platform/runtime_lock.py requirements/wsl-runtime.lock tests/test_runtime_lock.py
git commit -m "feat(runtime): 固化受审 CUDA 依赖锁"
```

### 任务 4：内容寻址存储与不可变依赖基座

**文件：** 新建 `runtime_storage.py`、`runtime_base.py`、`tests/test_runtime_storage.py`、`tests/test_runtime_base.py`。

**接口：** 消费任务 1 的完整 64 位 `base_id` 算法及 `RequirementsLockInfo/RuntimeAbi`，产出每 ID 锁、半成品隔离记录和 `BaseMetadata`；任务 5 只通过 `verify_base(root, base_id)` 消费基座。

- [ ] 写红灯并命名覆盖：`test_base_id_is_canonical_lock_plus_abi_sha256`、`test_base_id_uses_full_64_hex`、`test_forced_base_id_collision_fails_without_overwrite`、`test_same_base_id_builders_serialize_and_second_reuses_verified_base`、`test_different_base_ids_do_not_share_lock`、`test_incomplete_base_is_quarantined_before_rebuild`、`test_incomplete_symlink_escape_fails_closed`。
- [ ] 对每个故障点 `after_marker`、`after_venv`、`after_install`、`after_base_json` 注入进程级失败；下一次构建必须先隔离旧目录、保留审计记录再重建，且任何时刻都不能把旧 `.incomplete` 当成完成基座。
- [ ] 运行：`python -m pytest tests/test_runtime_storage.py tests/test_runtime_base.py -q`；预期红灯：存储与基座模块不存在。
- [ ] `build_base()` 调用任务 1 的 `compute_base_id(requirements_sha256, abi)`，拒绝非 64 位 ID，并在任何目录检查前获取 `locks/base/<base_id>.lock` 的排他 `flock`；锁文件保持稳定且不删除。
- [ ] 若 `bases/<base_id>/.incomplete` 存在，调用 `isolate_incomplete()`：校验最终目录和隔离目标均位于受管根、均非符号链接；以 `os.replace` 移入 `quarantine/incomplete/base/`，`fsync` 两侧父目录，再向 `journal/runtime-storage.jsonl` 追加只含 `kind`、ID、时间、故障阶段和隔离相对路径的规范记录。隔离失败即停止，不递归删除未知路径。
- [ ] `build_base()` 直接创建 `bases/<base_id>/` 和 `.incomplete`，在最终路径创建 venv，使用摘要锁安装；不得把临时 venv rename 成完成基座。不同 `base_id` 的构建可并行。
- [ ] 把输入锁文件原样复制为 `base/requirements.lock` 并在 `base.json.lock_relative` 记录相对路径；成功顺序为 `pip check`→受管依赖导入→freeze 摘要→原子写 `base.json`→从保存的锁字节重算 requirements SHA、`base_id` 和元数据→删除 `.incomplete`→`fsync` 基座/根目录。
- [ ] 已存在完成目录只有在 `verify_base()` 重算锁、ABI、完整 `base_id`、freeze 和全部元数据均通过时才复用；同 ID 异输入抛 `RuntimeIdCollisionError`，普通漂移抛完整性错误，两者都不得原位修复或覆盖。
- [ ] 绿灯：`python -m pytest tests/test_runtime_storage.py tests/test_runtime_base.py -q`，预期全部通过且故障注入子进程无残留锁。
- [ ] 提交：

```powershell
git add codev_platform/runtime_storage.py codev_platform/runtime_base.py tests/test_runtime_storage.py tests/test_runtime_base.py
git commit -m "feat(runtime): 增加内容寻址依赖基座"
```

### 任务 5：精确应用 wheel 与薄版本

**文件：** 新建 `runtime_build.py`、`tests/test_runtime_build.py`；修改 `core/config.py`。

**接口：** 消费经过验证的 `base_id` 和 `BaseMetadata`，按修订+wheel+`base_id`+基座元数据产出完整 `release_id` 与 `ReleaseMetadata`。

- [ ] 红灯覆盖：脏/未跟踪源码；仓内输出目录；候选摘要；规范化 `release_id`；完整 64 位 ID；强制 ID 碰撞；同 ID 并发；不同 ID 并行；最终目录 `.incomplete` 四个故障点；纯路径 `.pth`；应用/基座优先级；跨层 `pip check`；受管导入；拒绝 `/mnt/*` 和检出目录来源。
- [ ] 运行：`python -m pytest tests/test_runtime_build.py -q`；预期红灯：构建模块不存在。
- [ ] `build_candidate()` 要求干净的完整 HEAD，在仓外构建 wheel，写 `<wheel>.candidate.json` 并返回 wheel 路径、候选路径、类型化模型。
- [ ] `stage_release()` 先 `verify_base(root, base_id)`，读取规范化 `base.json` 摘要，再按精确算法计算 `release_id`；任何缩写 ID 或同 ID 异身份输入都失败且不覆盖现有版本。
- [ ] 在检查/创建 `releases/<release_id>` 前获取 `locks/release/<release_id>.lock` 的排他 `flock`。对 `.incomplete` 使用与任务 4 相同的路径围栏、隔离审计和故障注入规则；同 ID 第二写者等待后只能复用完整验证通过的版本。
- [ ] 直接创建最终 `releases/<release_id>` 与 `.incomplete`，创建薄 venv，执行 `pip install --no-deps app.whl`；不得把临时 venv rename 成完成版本。
- [ ] 建相对链接 `release/base -> ../../bases/<base_id>`；在版本 `purelib` 中把一行规范化绝对基座 `purelib` 写入 `codev_platform_base.pth`。
- [ ] 校验器拒绝 `.pth` 中的 `import`、相对路径、第二行、非目标基座；断言版本 `purelib` 在 `sys.path` 中早于基座 `purelib`、应用来自版本、`torch` 来自基座。
- [ ] 由版本 Python 执行 `pip check` 和受管 Python 单元清单的所有导入探针；配置使用临时 data/config，不连接生产服务。由于 maintenance 入口已在任务 1 提交，`plan-c-main-base` 基线版本也必须通过相同导入清单。
- [ ] 成功顺序：应用 freeze→原子写 `release.json`→重算完整 `release_id` 与基座元数据摘要→删除 `.incomplete`→`fsync`；不得 rename venv。
- [ ] 绿灯：

```powershell
python -m pytest tests/test_runtime_build.py tests/test_resources_packaging.py -q
```

- [ ] 提交：

```powershell
git add codev_platform/runtime_build.py codev_platform/core/config.py tests/test_runtime_build.py
git commit -m "feat(runtime): 构建薄版本运行环境"
```

### 任务 6：幂等原子激活与回滚

**文件：** 新建 `runtime_release.py`、`tests/test_runtime_release.py`。

**接口：** 消费任务 5 的完整验证结果；只由本模块改写 `current/previous`，并返回 `ActivationResult`。

- [ ] 红灯覆盖：首次激活；A→B；A→B→C；同目标空操作不改 `previous`；替换失败保持 `current`；首次回滚无 `previous`；损坏的 `previous`；基座缺失/漂移；激活锁并发；每 ID 锁顺序；根目录逃逸；目录 `fsync`。
- [ ] 运行：`python -m pytest tests/test_runtime_release.py -q`；预期红灯：版本模块不存在。
- [ ] `release_root()` 优先级：显式 CLI 路径→`runtime.release_root`→`~/.local/share/codev-platform`。
- [ ] 激活先获取 `locks/activation.lock` 排他 `flock`，再按版本 ID、基座 ID 字典序获取共享的每 ID 锁并执行 `verify_release()`；目标等于 `current` 时直接返回且保留 `previous`，否则先原子写 `previous`，再单次 `os.replace` 替换 `current`，最后 `fsync` 根目录。
- [ ] 回滚在相同锁顺序下验证 `previous`、完整 `release_id`、对应 `base_id` 与基座元数据摘要；只原子替换 `current`，`previous` 保持不变。
- [ ] 变更操作仅支持 POSIX；`fcntl.flock` 随进程自动释放。Windows 可执行 build/status，activate/rollback 必须关闭失败；只允许这些变更用例明确跳过。
- [ ] 绿灯：`python -m pytest tests/test_runtime_release.py -q`；POSIX 全部通过，Windows 仅变更用例有明确跳过。
- [ ] 提交：

```powershell
git add codev_platform/runtime_release.py tests/test_runtime_release.py
git commit -m "feat(runtime): 增加幂等原子切换"
```

### 任务 7：运行时 CLI

**文件：** 新建 `ops/runtime.py`、`tests/test_runtime_cli.py`；修改 `ops/__init__.py`、`tests/test_cli_parser.py`。

**接口：** 只组合任务 3–6 的公开函数；命令行接受并输出完整 ID，不另实现摘要、锁或激活逻辑。

- [ ] 红灯覆盖全部 parser、根目录优先级、完整 ID 校验和碰撞错误结构化输出，不使用隐藏 fixture。
- [ ] 精确 CLI：

```text
codev-platform runtime lock --raw-freeze FILE --approved-requirements FILE --output FILE
codev-platform runtime base --lock FILE --approved-requirements FILE
codev-platform runtime build --repo PATH --out-dir PATH
codev-platform runtime stage --candidate FILE --wheel FILE --base-id BASE_ID
codev-platform runtime verify RELEASE_ID
codev-platform runtime status --json
codev-platform runtime activate RELEASE_ID
codev-platform runtime rollback
```

- [ ] 运行：`python -m pytest tests/test_runtime_cli.py tests/test_cli_parser.py -q`；预期红灯：未知 `runtime` 命令。
- [ ] 所有动作返回 0/1；JSON 只序列化类型化模型，不输出环境、token、pip 完整命令或凭据；错误只含固定错误码、kind 和完整对象 ID。
- [ ] `status` 不依赖 `systemd` 环境，必须通过 Task 2 前缀邻接元数据识别版本，并同时验证 `release_id/base_id`。
- [ ] 绿灯：`python -m pytest tests/test_runtime_cli.py tests/test_cli_parser.py -q`。
- [ ] 提交：

```powershell
git add codev_platform/ops/runtime.py codev_platform/ops/__init__.py tests/test_runtime_cli.py tests/test_cli_parser.py
git commit -m "feat(runtime): 增加版本运行时命令"
```

### 任务 8：感知版本的 systemd 与全部入口

**文件：** 新建 `runtime_preflight.py`、`tests/test_runtime_preflight.py`；修改 `mcp_systemd.py`、`mcp_serve.py`、`cli.py`、`cli_cmds/mcp.py` 及 systemd 测试。任务 1 已创建 `ops/memory_maintenance.py`，本任务只消费它。

**接口：** 所有 Python 单元渲染器接收同一个 `SystemdRuntime`；安装脚本在复制/重启前以每个单元的目标 `User` 执行 `runtime_preflight`，失败即关闭安装。

- [ ] 红灯使用 `minimal_config()` 并故意设置旧 `.venv` 与检出目录 `PYTHONPATH`；验证固定单元集合、真实 argv、PATH、WorkingDirectory、导入来源、`Delegate/KillMode`、禁止重启模式，以及现有 `User=`、`EnvironmentFile=`、文件模式不漂移。
- [ ] 新增目标用户权限用例：不可遍历版本根、不可执行当前 Python、加载 `EnvironmentFile` 后缺少后端必需环境、不可写 File 队列隔离区、不可原子替换 journal 探针文件、不可在 run-lock 同目录创建文件并 `flock` 时，安装脚本都必须在复制、`daemon-reload` 和任何受管服务操作前失败。目标用户无需直接读取 `EnvironmentFile`；该文件仍由 systemd 管理器按既有语义加载。
- [ ] 运行：

```powershell
python -m pytest tests/test_mcp_serve.py tests/test_systemd_restart.py tests/test_systemd_agent_clock.py tests/test_runtime_systemd.py tests/test_runtime_preflight.py -q
```

- [ ] 预期红灯：运行时参数、web 单元和目标用户探针不存在。
- [ ] `rewrite_python_argv()` 只替换原 `argv[0]` 并插入 `-I`：`[runtime.python, "-I", *original[1:]]`；不得把 MCP 模块改写成 CLI 动作。
- [ ] 所有 Python 渲染器接收同一个 `runtime`；新增 `render_web_unit()`；maintenance 调用任务 1 已打包模块并保留现有退出码和参数兼容。
- [ ] 每个渲染器必须逐字保留传入的 `User=<user>` 和 `_environment_file_line(cfg)` 结果；不得把 `EnvironmentFile=-...` 删除、改成必需文件或复制其内容。版本环境覆盖只能写在 `EnvironmentFile` 之后，顺序固定为：

```ini
Environment=PYTHONPATH=
Environment=PYTHONHOME=
Environment=PYTHONNOUSERSITE=1
Environment=PYTHONDONTWRITEBYTECODE=1
Environment=CODEV_PLATFORM_RELEASE_FILE=<root>/current/release.json
Environment=PATH=<root>/current/venv/bin:<root>/current/base/venv/bin:/usr/local/bin:/usr/bin:/bin
WorkingDirectory=%h
```

- [ ] reindex 单元同时断言 `Delegate=yes`、`KillMode=control-group`、`TimeoutStopSec=30`、`SendSIGKILL=yes`；Plan C 不得删除 Plan A 配置。所有生成的 unit 文件模式为 `0644`，`install.sh` 为 `0755`；版本根、`current`、版本/基座目录和 Python 对目标 `User` 至少具备遍历、读取和执行权限，不得用 `0777` 兜底。
- [ ] `permission_requirements()` 从同一配置与 Plan A 已有路径解析器生成具名检查：当前版本/基座读执行、配置/日志/数据访问、File 队列隔离区读写、supervisor journal 所在目录原子写+`fsync`、run-lock 所在目录创建同目录临时文件并 `flock`。PG 隔离状态没有文件路径时，改做加载同一 `EnvironmentFile` 后的只读队列状态探针；探针不得认领、续租或修改作业。
- [ ] 安装脚本必须先用临时 `systemd-run --wait --pipe --collect --uid=<User>` 加载同一 `EnvironmentFile`、环境覆盖和 `WorkingDirectory`，再运行 `<root>/current/venv/bin/python -I -m codev_platform.runtime_preflight probe`。探针只创建并删除带随机名的哨兵文件，不触碰真实 quarantine 记录、journal 文件或 run-lock；输出只含固定检查名与通过/失败，不回显路径值、环境或凭据。
- [ ] `serve-mcp install-systemd --runtime-root ROOT --no-restart` 贯穿全部渲染器。目标用户探针通过后，禁止重启脚本只允许 copy、`daemon-reload`、`enable`、探针和 `echo`；禁止对任何受管 unit 执行 `start/stop/restart/reset-failed`。普通模式也必须先通过探针，才允许 copy/restart。
- [ ] 由当前版本 Python 逐项打印 `codev_platform.__file__`；不得位于 `/mnt/*`、项目检出目录或基座。另断言基线版本的 maintenance 入口可导入，保证 `previous` 回滚后单元 argv 仍有效。
- [ ] 绿灯：上述 pytest 全部通过；`rg -n "scripts/run_memory_maintenance|pip install -e" codev_platform/mcp_systemd.py` 无输出。
- [ ] 提交：

```powershell
git add codev_platform/runtime_preflight.py codev_platform/mcp_systemd.py codev_platform/mcp_serve.py codev_platform/cli.py codev_platform/cli_cmds/mcp.py tests/test_runtime_preflight.py tests/test_mcp_serve.py tests/test_systemd_restart.py tests/test_systemd_agent_clock.py tests/test_runtime_systemd.py
git commit -m "feat(systemd): 从当前版本启动全部服务"
```

### 任务 9：不扩大 webhook 认证面的受保护状态

**文件：** 修改五个 MCP/webhook 状态文件、`agent/routes/meta.py`、`agent/service.py`、`web/routes/health.py`；新建 `tests/test_runtime_status_surfaces.py`。

**接口：** 每个服务只暴露启动时缓存的 `RuntimeIdentity`；不导入版本变更模块，也不改变 Plan B 的 webhook 入队或物化链。

- [ ] 红灯覆盖：公共 health 无运行时详情；token 模式的受保护状态无 token 返回 401、有效 token 返回 200；passthrough 保持本地兼容；所有状态面 revision 一致；名为 `platform` 的 webhook provider 不放开 `/platform/status`。
- [ ] 运行：`python -m pytest tests/test_runtime_status_surfaces.py tests/test_webhook_body_limit.py tests/test_health_all_auth.py -q`；预期红灯：运行时载荷和路由不存在。
- [ ] 每个服务在 app/server 构造期调用一次 `runtime_identity()` 并闭包保存；handler 只调用 `identity.as_dict()`，不导入版本变更、reindex、attempt 或 workspace。
- [ ] webhook 不得把动态 provider 路径加入 `AuthMiddleware.public_paths`。`build_app` 新增可注入 `status_authenticator`；仅 `platform_status` handler 调用它，捕获 `Unauthorized` 并返回静态 401；provider HMAC POST 链完全不变。
- [ ] agent 新增受保护 `/platform/status`；web 新增非 public `/api/v1/runtime/status`。Plan A worker 状态负责 reindex revision，Plan C 只做交叉验收。
- [ ] 绿灯：

```powershell
python -m pytest tests/test_runtime_status_surfaces.py tests/test_webhook_body_limit.py tests/test_health_all_auth.py tests/test_gateway_auth.py tests/test_agent_memory_authz.py -q
```

- [ ] 提交：

```powershell
git add codev_platform/chroma/server.py codev_platform/codegraph/server.py codev_platform/graph/mcp_server.py codev_platform/agent/memory_mcp.py codev_platform/webhook/server.py codev_platform/agent/routes/meta.py codev_platform/agent/service.py codev_platform/web/routes/health.py tests/test_runtime_status_surfaces.py
git commit -m "feat(runtime): 在受保护状态面暴露运行身份"
```

### 任务 10：首次切换、回滚与最终验收

**文件：** 新建 `tests/test_runtime_cutover.py`、`tests/test_runtime_acceptance.py`、运行时版本操作规程。

**接口：** 只调用任务 3–9 的 CLI/公开接口；首次切换必须形成 `current=new`、`previous=baseline(main-base)` 的可验证双版本状态。

- [ ] 红灯使用真实临时 venv/subprocess，不使用自定义未声明 fixture：启动旧版本探针进程并缓存身份，切换 `current` 后旧进程仍报旧身份；重启后报新身份；损坏的新版本 readiness 后回滚报 baseline 身份。
- [ ] 增加首次切换用例：从空根目录先激活基线版本，再激活新版本；断言 `previous` 不是空、解析到基线版本完整 `release_id`，且 `verify_release(previous)` 连同 `base_id`、基座元数据和 maintenance 入口全部通过。直接从空根激活新版本并进入生产的操作规程测试必须失败。
- [ ] 在 Linux/WSL 测试环境运行：`python -m pytest tests/test_runtime_cutover.py -q`；预期红灯持续到完整链路落地。
- [ ] 操作规程先用 `git rev-parse --git-path plan-c-main-base` 读取锚点，以 `git worktree add --detach <temp> <main-base>` 创建仓外干净基线源码；使用当前任务 3–10 工具构建该提交的 wheel，结束时移除临时 `worktree`。禁止从当前 HEAD、脏目录或手工复制文件伪造基线版本。
- [ ] 首次切换固定顺序：构建/验证共享基座→从 `main-base` 构建并 stage baseline→`verify_release(baseline)`→在尚未迁移的旧服务运行期间首次 `activate baseline`→从当前 HEAD 构建并 stage 新版本→`verify_release(new)`→`activate new`→验证 `current=new` 且 `previous=baseline`→再次验证 previous 及其基座→执行 `--no-restart` 目标 User 探针并预装 units→停 webhook→等待 active attempt 清零→停 reindex 并证明进程树死亡→停其余受管 units→restart→逐状态对齐→真实 MCP 工具→受控 reindex→恢复 webhook。
- [ ] 基线版本与新版本必须使用各自的修订+wheel，但可以共享同一 `base_id`；二者的 `release_id` 均按完整规范算法生成。任何碰撞、`.incomplete`、隔离审计未落盘、目标 `User` 权限失败或 `previous` 不可验证，都必须在停止生产服务之前中止。
- [ ] 回滚固定顺序：停入口和新服务→证明进程树死亡→解析并 `verify_release(previous)` 及其 `base_id`→rollback→restart→用基线版本可用的 `runtime_identity` 导入探针、服务健康检查、真实 MCP 和受控 reindex 重复验收。基线版本不要求具备任务 9 后新增的状态路由；禁止 schema downgrade。
- [ ] 真实 MCP 冒烟固定为：platform-docs `list_collections`、codegraph `codegraph_status`、graph `search_nodes` 最小 query、agent-memory `recall` 最小合法 scope；每个必须返回结构化成功，而非仅 HTTP 200。
- [ ] 使用两个 Plan C 提交范围运行范围门禁：

```powershell
$leafBasePath = (git rev-parse --git-path plan-c-leaf-base).Trim()
$leafHeadPath = (git rev-parse --git-path plan-c-leaf-head).Trim()
$mainBasePath = (git rev-parse --git-path plan-c-main-base).Trim()
$leafBase = (Get-Content -LiteralPath $leafBasePath -Raw).Trim()
$leafHead = (Get-Content -LiteralPath $leafHeadPath -Raw).Trim()
$mainBase = (Get-Content -LiteralPath $mainBasePath -Raw).Trim()
$changed = @(
  git diff --name-only "$leafBase..$leafHead"
  git diff --name-only "$mainBase..HEAD"
) | Sort-Object -Unique
$changed | Select-String '^codev_platform/reindex/'
git diff --check "$leafBase..$leafHead"
git diff --check "$mainBase..HEAD"
```

预期：两段 Plan C 变更均无 reindex 路径；两个 diff check 均以 0 退出。A/B 只允许位于 `leafHead..mainBase`，不用 allowlist 掩盖顺序错误。

- [ ] 文件规模只检查本计划提交范围内的 Python：

```powershell
$leafBasePath = (git rev-parse --git-path plan-c-leaf-base).Trim()
$leafHeadPath = (git rev-parse --git-path plan-c-leaf-head).Trim()
$mainBasePath = (git rev-parse --git-path plan-c-main-base).Trim()
$leafBase = (Get-Content -LiteralPath $leafBasePath -Raw).Trim()
$leafHead = (Get-Content -LiteralPath $leafHeadPath -Raw).Trim()
$mainBase = (Get-Content -LiteralPath $mainBasePath -Raw).Trim()
$py = @(
  git diff --name-only "$leafBase..$leafHead" -- '*.py'
  git diff --name-only "$mainBase..HEAD" -- '*.py'
) | Sort-Object -Unique
$tooLarge = $py | Where-Object { Test-Path $_ } | Where-Object { (Get-Content -Encoding UTF8 $_).Count -gt 600 }
if ($tooLarge) { $tooLarge; exit 1 }
```

- [ ] 定向门禁：

```powershell
python -m pytest tests/test_runtime_models.py tests/test_runtime_maintenance_entrypoint.py tests/test_runtime_identity.py tests/test_runtime_lock.py tests/test_runtime_storage.py tests/test_runtime_base.py tests/test_runtime_build.py tests/test_runtime_release.py tests/test_runtime_cli.py tests/test_runtime_systemd.py tests/test_runtime_preflight.py tests/test_runtime_status_surfaces.py tests/test_runtime_cutover.py tests/test_runtime_acceptance.py -q
python -m pytest tests/test_mcp_serve.py tests/test_systemd_restart.py tests/test_systemd_agent_clock.py tests/test_webhook_body_limit.py tests/test_health_all_auth.py tests/test_health_reindex_worker.py tests/test_cli_parser.py tests/test_resources_packaging.py -q
python -m ruff check codev_platform tests
python -m pytest tests/
```

- [ ] 预期：全部通过；POSIX 变更测试只可在 Windows 跳过；无意外跳过。
- [ ] 提交：

```powershell
git add tests/test_runtime_cutover.py tests/test_runtime_acceptance.py docs/plans/roadmap-2026-07-11/reindex-runtime-release-runbook-2026-07-11.md
git commit -m "test(runtime): 固化版本切换与恢复门禁"
```

---

## 最终验收矩阵

| 门禁 | 通过条件 |
|---|---|
| 来源证明 | 完整 Git OID→应用 wheel 摘要→`release.json`→`runtime_revision` 一致 |
| ID | `base_id=SHA256(canonical(lock+ABI))`；`release_id=SHA256(canonical(revision+wheel+base_id+base metadata))`；只用完整 64 位 ID，强制碰撞关闭失败 |
| 基座完整性 | requirements 摘要、制品摘要、ABI、freeze、基座元数据摘要全部匹配 |
| 存储 | 同一 `base_id` 只存一份重依赖；每个版本仅薄 venv/应用；同 ID 写者由每 ID `flock` 串行化 |
| 导入顺序 | 版本 `purelib` 先于基座；应用来自版本；Torch 来自基座 |
| 完成度 | `.incomplete` 存在时基座/版本不可复用、激活或回滚；故障注入后先隔离并写 journal 再重建 |
| 原子性 | 同目标空操作；替换失败保留 `current`；回滚先验证基座；锁顺序固定 |
| systemd | 固定 unit 全集；真实 argv；保留 `User/EnvironmentFile` 与文件权限；无可编辑 PATH/PYTHONPATH/检出目录 |
| 权限探针 | copy/restart 前以目标 `User` 验证解释器、导入、quarantine、journal、run-lock 和服务写目录 |
| 围栏 | reindex unit 同时保留 `Delegate=yes` 与 `KillMode=control-group` |
| 禁止重启 | 预装脚本不对受管服务执行 `start/stop/restart/reset-failed`，但必须执行 transient 目标用户探针 |
| 安全 | public health 无运行时详情；protected status 认证；webhook provider 不扩大 public prefix |
| 跨计划 | C1–2→A→B→C3–10；两个 Plan C 范围均无 `codev_platform/reindex/**`，不改 materializer/workspace |
| 可维护性 | Plan C 新增/修改 Python≤600 行，叶子模型无上层 import，存储与权限探针职责独立 |
| 首次切换 | 先从 `main-base` 构建并激活基线版本，再激活新版本；`previous` 指向且可验证基线版本 |
| 恢复 | `previous` 及其 `base_id` 可验证；失败版本/半成品保留取证；无需重装重依赖即可回滚 |

## 明确排除

- Queue、AttemptOrchestrator、executor containment、InputMaterializer、WorkspaceProvider和Git worktree。
- ResultPublisher、manifest发布顺序和索引active-generation。
- 多job并发、通用DAG、插件DSL和通用部署平台。
- 自动SSH、CLI内sudo/systemctl和自动操作生产WSL。
- 自动版本/基座 GC；首版必须保留 `current/previous` 引用的全部基座。
- 依赖升级、CUDA/Torch重选型、容器、远程制品仓库、签名和SBOM。
- 破坏性 schema migration、数据库 downgrade 和索引存储格式迁移。
