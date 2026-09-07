# WSL 运行代际部署与完整回滚总实施计划

> **执行要求：** 必须使用 `superpowers:subagent-driven-development` 按任务分派，并在每个阶段结束时使用 `superpowers:requesting-code-review`；宣告完成前使用 `superpowers:verification-before-completion`。

**目标：** 在唯一的 WSL 生产环境中实现可中断恢复、可完整回滚的运行代际事务，使任一部署失败或主机重启后都只能收敛到已验收代际，或者保持入口关闭的安全态。

**架构：** 以不可变 `RuntimeGeneration` 为聚合根，以独立 `DeploymentAttempt`、单文件 `GenerationState`、追加式事务日志和可轮换 `ControlLease` 组成事务控制面；稳定 `ServingFence` 绑定 state/acceptance/permit，独立 `GcLease` 只授权清理。数据库、systemd、配置与四类索引均通过小型端口适配器参与同一事务。旧版本接管走显式策略和一次性现场清单；业务服务只在状态、验收记录和 `serve-permit` 三者一致时对外提供服务。

**技术栈：** 正式运行目标 Linux CPython 3.12 x86_64、Windows 测试宿主 Python 3.14、dataclasses、Protocol、FastAPI、SQLAlchemy/Alembic、PostgreSQL、SQLite、Chroma、systemd、Bash、PowerShell、pytest、Ruff。

**全局约束：** 全部沟通、注释和文档使用中文；低耦合、模块化、单一职责、轻量聚合；生产真值不得依赖 Git 父提交；数据库只做向前兼容迁移，不做降级；禁止向 GitHub/origin 推送，只允许 `origin/dev`；不创建长期 worktree；不直接修改 `.venv/`、`data/`、`.codegraph/` 和用户配置；Python 文件原则上小于 600 行；所有敏感 token 只存摘要。

---

## 一、范围冻结

本计划实现设计文档 [2026-07-19-runtime-generation-rollback-design.md](./2026-07-19-runtime-generation-rollback-design.md) 的方案 C，范围只包含以下七个结果：

1. 每次部署都有独立 attempt，同一目标提交可以安全重试且不会覆盖历史。
2. 运行代际绑定代码、入口契约、systemd、配置、数据库兼容契约和四索引集合。
3. 状态切换、资源动作、验收和恢复都有持久证据以及 fencing。
4. 正式回滚恢复完整代际，不再只修改 `current` 代码指针。
5. 旧环境首次接管必须经独立批准策略、现场审计和双向演练。
6. 稳定 recovery launcher 在业务服务之前恢复中断事务。
7. 只有完成两代真实往返演练后才允许删除遗留环境和旧索引。

明确不做：无关业务重构、全仓 Ruff 存量 81 项清理、GitHub 推送、多生产环境编排、数据库降级迁移。

## 二、分阶段计划与依赖

```text
阶段 A：领域与持久化基础
  ├─ 代际、attempt、状态、journal、回滚包
  ├─ descriptor-safe 文件原语与 CAS store
  ├─ ControlLease、ServingFence 与验收/permit 身份
  ├─ release 自描述入口契约
  └─ 遗留环境策略与审计
                │
                ▼
阶段 B：资源适配器
  ├─ 数据库向前兼容契约
  ├─ 队列/发布 writer fencing
  ├─ 四索引代际适配器
  ├─ systemd/config 代际 bundle
  └─ serve-permit 与稳定恢复服务
                │
                ▼
阶段 C：事务接线与真实验收
  ├─ deploy / rollback / recover 状态机
  ├─ CLI、安装器与生产组合根
  ├─ 双真实版本 + 故障窗口测试
  ├─ WSL legacy→A→legacy→A
  └─ WSL A→B→A→B，随后安全清理
```

- 阶段 A 详见 [2026-07-19-runtime-generation-foundation.md](2026-07-19-runtime-generation-foundation.md)。
- 阶段 B 详见 [2026-07-19-runtime-generation-adapters.md](2026-07-19-runtime-generation-adapters.md)。
- 阶段 C 详见 [2026-07-19-runtime-generation-integration.md](2026-07-19-runtime-generation-integration.md)。

## 三、里程碑门禁

### M1：纯领域与存储闭环

- [ ] 同一 revision 连续创建两个 attempt，ID 与 journal 均独立。
- [ ] `GenerationState` 只能通过命名转换函数迁移，非法边被拒绝。
- [ ] attempt 先 `O_EXCL` 预留，再冻结 journal genesis/envelope 并发布 active envelope，随后获取初始 ControlLease；恢复只提升同一 attempt 的 control epoch，不改变 ServingFence。
- [ ] generation、rollback bundle、验收记录均不可变；状态文件使用比较交换。
- [ ] release 使用自己的解释器执行自己的入口自检；控制器不导入目标版模块列表。
- [ ] controller 只解析稳定 entrypoint envelope/proof，opaque contract 由各 release 自己解析；A/B 不同 base 和 legacy external 均可恢复。
- [ ] 遗留接管策略与现场观察分离，并通过摘要绑定。

### M2：所有资源适配器可独立补偿

- [ ] 数据库迁移后的结构同时被基线和目标的兼容探针接受。
- [ ] 队列 claim、manifest 发布和结果确认全部校验 writer fence。
- [ ] chroma、codegraph、ingest、code_vec 都能 prepare/verify/activate/restore/inspect。
- [ ] systemd 每个文件、unit 和 enablement 都是可重复执行的单资源动作。
- [ ] `serve-permit` 缺失、篡改或与状态漂移时入口默认关闭。
- [ ] 运行中的 Web/MCP/Webhook/SSE/WebSocket 每个新请求均持续校验 runtime gate，撤销 permit 会排空旧连接。
- [ ] PostgreSQL graph 使用 legacy 不可见的 shadow 表，managed 候选存在时旧 wheel 仍只看到 legacy 数据。
- [ ] 稳定 recovery service 在重启后能定位唯一活跃 envelope 并恢复。

### M3：生产事务闭环

- [ ] deploy、rollback、recover 共用一个事务协调器和相同资源端口。
- [ ] 每个外部动作都覆盖“写意图前、动作后、写完成后”三个崩溃窗口。
- [ ] 旧的 `runtime rollback` 裸代码指针入口从生产 CLI 移除。
- [ ] 真实旧版和新版在 WSL CPython 3.12 中分别走生产 candidate/stage 链，跨版本测试不使用同源 mock 或 Windows wheel。
- [ ] 安装脚本幂等安装稳定 recovery launcher/service。

### M4：WSL 可用并允许清理

- [ ] `legacy → A → legacy → A` 往返成功。
- [ ] 在 A 基础上形成有真实代码/审计意义的 B，完成 `A → B → A → B`。
- [ ] 每次 serving 提交后四索引 manifest、数据库兼容证明、systemd 生效证据和 permit 一致。
- [ ] 主机重启与 SIGKILL 故障演练均确定性收敛。
- [ ] 四个 MCP 使用真实鉴权完成 initialize 和只读调用，401 不得豁免。
- [ ] 只在上述全部通过后清理 legacy 目录、旧 bundle、旧索引代际和临时工作目录。
- [ ] 清理必须经过带 fencing 的 dry-run 摘要确认、墓碑和引用复验，不手工递归删除。

## 四、提交与推送边界

每个提交前执行对应子计划的最小验证；阶段结束执行阶段验证。建议提交顺序：

1. `refactor(runtime): 建立运行代际领域与持久化基础`
2. `feat(runtime): 接入数据库与写者代际围栏`
3. `feat(runtime): 实现四索引代际切换与恢复`
4. `feat(runtime): 固化 systemd 代际载荷与启动许可`
5. `feat(runtime): 接通部署回滚与中断恢复事务`
6. `test(runtime): 增加双真实版本与 WSL 故障演练`
7. `docs(runtime): 记录运行代际投产与清理结果`

每次推送前都必须验证：

```powershell
$head = git rev-parse HEAD
$wsl = git rev-parse origin/dev
$origin = git rev-parse origin/dev
git status --short
Write-Output "HEAD=$head"
Write-Output "origin/dev=$wsl"
Write-Output "origin/dev=$origin"
```

期望：工作树干净，`HEAD == origin/dev`；`origin/dev` 保持实施前的值，禁止执行 `git push origin`。

## 五、总体验证矩阵

### 快速回归

```powershell
python -m pytest tests/test_runtime_generation_contract.py tests/test_runtime_attempt_contract.py tests/test_runtime_generation_acceptance.py tests/test_runtime_generation_state.py tests/test_runtime_transaction_contract.py
python -m pytest tests/test_runtime_generation_store.py tests/test_runtime_transaction_store.py tests/test_runtime_fencing.py tests/test_runtime_fencing_store.py tests/test_runtime_recovery_contract.py tests/test_runtime_rollback_contract.py
python -m pytest tests/test_runtime_entrypoint_selftest.py tests/test_runtime_legacy_takeover.py
```

期望：全部通过；无跳过核心状态转换、CAS 和篡改用例。

### 适配器回归

```powershell
python -m pytest tests/test_runtime_database_compatibility.py tests/test_runtime_writer_fence.py
python -m pytest tests/test_runtime_index_generation.py tests/test_runtime_index_manifest_adapter.py
python -m pytest tests/test_runtime_systemd_bundle_contract.py tests/test_runtime_systemd_bundle_activation.py tests/test_runtime_serve_permit.py tests/test_runtime_serve_gate.py tests/test_runtime_recovery_launcher.py tests/test_runtime_controller_trust.py
```

期望：全部通过；四种索引适配器都覆盖恢复路径。

### 集成和跨版本回归

```powershell
python -m pytest tests/test_runtime_transaction_coordinator.py tests/test_runtime_production_deployment.py tests/test_runtime_production_rollback.py
python -m pytest tests/test_runtime_generation_fault_injection.py tests/test_runtime_generation_gc.py
$env:CODEV_RUN_WSL_REVISION_PAIR='1'
python -m pytest tests/test_runtime_real_revision_pair.py tests/test_runtime_legacy_takeover_real_revision.py -m real_revision_wsl --junitxml=.superpowers/wsl-revision-pair.xml
Remove-Item Env:CODEV_RUN_WSL_REVISION_PAIR
python -m pytest tests/test_install_wsl_runtime_scripts.py
```

期望：全部通过；真实门禁未设置时才允许开发快速回归跳过，门禁一旦设置，缺少 WSL/systemd/Python 3.12/root/GPU 必须失败，并校验 JUnit `skipped=0`；不得静默降级成同源 mock。

### 全仓受影响面验证

```powershell
python -m pytest
python -m ruff check codev_platform tests
python -m compileall -q codev_platform
$repoWsl = (wsl.exe -d Ubuntu -- wslpath -a (Get-Location).Path).Trim()
wsl.exe -d Ubuntu -- bash -n "$repoWsl/scripts/install-wsl-runtime.sh"
```

期望：pytest 不新增失败；Ruff 不新增本次文件问题，存量问题单独记录；Python 和 Bash 语法检查通过。

## 六、停止条件

出现以下任一情况，不继续切换生产资源，状态必须保持或回到 `restricted` / `safety_unproven`，入口保持关闭：

- 无法证明当前 serving generation 或 MainPID 所属 release。
- 数据库无法同时满足 serving 与 desired 的兼容契约。
- 任一旧 writer 未静默或 fencing 无法验证。
- 任一索引代际无法验证或恢复。
- systemd 有第三方 mask、未归属文件或有效载荷摘要漂移。
- active recovery envelope、控制器 tree digest 或解释器摘要不一致。
- state、acceptance 和 serve-permit 任意一项不一致。

这些属于安全拒绝，不得通过人工删除标记、直接改指针或跳过验收绕过。
