# 运行代际事务接线、真实演练与清理实施计划

> **执行要求：** 必须使用 `superpowers:subagent-driven-development` 实施；每个状态机行为使用 `superpowers:test-driven-development`；正式提交和 WSL 操作前后使用 `superpowers:verification-before-completion`，合并前使用 `superpowers:requesting-code-review`，提交使用项目 `git-commit` skill。

**目标：** 将领域基础和资源适配器接成 deploy/rollback/recover 共用的持久事务，完成真实双版本、故障窗口和唯一 WSL 生产环境的往返演练，最后安全清理 legacy 与旧数据。

**架构：** `GenerationTransactionCoordinator` 是薄状态机，控制面、资源面、验收面通过三个小型端口组注入；每个步骤执行“持久意图 → 幂等动作 → 持久完成”。生产 deploy 和 rollback 只做组合根接线。恢复由 release 外 launcher 使用同一 attempt 的新 lease 接管，不创建第二份部署事实。

**技术栈：** 正式运行目标 Linux CPython 3.12 x86_64、Windows 测试宿主 Python 3.14、pytest、生产 runtime candidate/stage 构建链、systemd、WSL 2、Bash、PowerShell、Gitea `origin/dev`。

**全局约束：** 中文注释和文档；只有一个 WSL 生产环境；禁止 GitHub/origin push；正式 baseline 来自 `GenerationState` 与 MainPID 观察；回滚不做数据库 downgrade；任一证明不足入口保持关闭；真实版本测试不得使用同源 controller mock；legacy 清理只能在两代往返通过后执行。

---

## 任务 1：实现通用持久步骤执行器

**文件：**

- Create: `codev_platform/runtime_transaction_step.py`
- Create: `codev_platform/runtime_transaction_ports.py`
- Test: `tests/test_runtime_transaction_step.py`
- Test: `tests/test_runtime_transaction_ports.py`

### 步骤

- [ ] 先用 in-memory journal 和 recording resource 写失败测试，覆盖每个动作的四个持久窗口：PREPARED 已 fsync 但资源未变化、资源已变化但 APPLIED 未 fsync、APPLIED 已 fsync 但 COMMITTED 未 fsync、COMMITTED 已 fsync 但下一步尚未开始。
- [ ] 实现一个资源动作执行器，不包含具体部署顺序：

```python
@dataclass(frozen=True, slots=True)
class TransactionStepSpec:
    step_id: str
    resource_kind: str
    resource_id: str
    desired_sha256: str


class RecoverableAction(Protocol):
    def observe(self) -> ResourceObservation:
        """返回当前资源身份。"""

    def apply(self, expected_before: ResourceObservation) -> ResourceEvidence:
        """幂等应用期望状态。"""

    def restore(self, original: ResourceObservation, expected_current: ResourceObservation) -> ResourceEvidence:
        """幂等恢复并拒绝第三方漂移。"""


def execute_transaction_step(
    spec: TransactionStepSpec,
    action: RecoverableAction,
    *,
    journal_store: RuntimeTransactionStore,
    lease: ControlLeaseProof,
) -> TransactionAction:
    """按 PREPARED/APPLIED/COMMITTED 三段持久执行单资源动作。"""
```

- [ ] 恢复逻辑先读取 journal 和当前资源事实：已经是 desired 则补写 APPLIED/COMMITTED；仍是 original 则重做；两者都不是则标记 safety_unproven，绝不覆盖第三方变化。
- [ ] 端口分为三个 dataclass：`TransactionControlPorts`（store/lease/clock）、`TransactionResourcePorts`（release/config/db/index/systemd/ingress）、`TransactionAcceptancePorts`（probe/acceptance/permit），避免巨型 Protocol。
- [ ] 运行：

```powershell
python -m pytest tests/test_runtime_transaction_step.py tests/test_runtime_transaction_ports.py -q
```

期望：三个崩溃窗口均只有一次有效资源状态变化。

- [ ] 提交：`feat(runtime): 增加可恢复事务步骤执行器`

## 任务 2：实现 deploy/rollback/recover 共用协调器

**文件：**

- Create: `codev_platform/runtime_transaction_coordinator.py`
- Modify: `codev_platform/runtime_deployment_coordinator.py`
- Modify: `codev_platform/runtime_deployment_steps.py`
- Modify: `codev_platform/runtime_deployment_worker.py`
- Modify: `codev_platform/runtime_deployment_recovery.py`
- Test: `tests/test_runtime_transaction_coordinator.py`
- Modify Test: `tests/test_runtime_deployment_coordinator.py`
- Modify Test: `tests/test_runtime_deployment_steps.py`
- Modify Test: `tests/test_runtime_deployment_recovery.py`

### 步骤

- [ ] 先写表驱动状态机测试，按以下精确顺序断言 deploy：O_EXCL 预留 attempt → 冻结 journal genesis 和 per-attempt recovery envelope → expected-absent CAS 发布 active envelope → 在全局部署锁内 CAS 签发初始 `ControlLeaseProof` → 边界检查与 serving baseline 预观察 → 在克隆库完成 migration 双 wheel proof → 以 journaled `prepare_additive` 步骤安装 shadow schema → stage release/entrypoint/config/systemd/db contract → 构建并验证四索引候选 → 冻结 generation 和完整 attempt（只绑定预观察，不冻结 rollback bundle）→ CAS 状态为 switching（旧 ServingFence 立即失去发布资格）→ 撤销 permit、失效 gate、关闭外部入口并排空连接 → 提升各后端 candidate control fence、quiesce writers → 最终逐字节复验 baseline 并 O_EXCL 冻结 rollback bundle → 发布目标配置并记录逐文件补偿 → DB expand/backfill 及 target/baseline 双探针 → 安装目标 systemd bundle → 切换 release 与四索引 → CAS 状态为 validating → 为 target 签发 provisional `ServingFence` 并通过 credential 注入（此时仍无生产发布资格）→ daemon-reload、精确 unmask 并启动内部服务 → 冻结绑定 ServingFence 的 acceptance → CAS 提交 serving state/ServingFence 为状态 A（仍保持维护门禁）→ 纯计算维护门禁关闭后的目标状态 B → 使用当前 ControlLease 预签发绑定 B 完整摘要的 staged permit（A 下仍 fail closed）并复证入口 → CAS A→B，使 state/acceptance/permit 同时一致并开放稳态发布 → 完成 journal → CAS 退休当前 ControlLease → 最后清 active envelope。
- [ ] 实现协调器外形：

```python
@dataclass(frozen=True, slots=True)
class GenerationTransactionCoordinator:
    control: TransactionControlPorts
    resources: TransactionResourcePorts
    acceptance: TransactionAcceptancePorts
    attempt_id_factory: Callable[[], str]
    token_factory: Callable[[], bytes]
    clock: Callable[[], str]

    def deploy(self, plan: DeploymentPlan, *, resume_attempt_id: str | None = None) -> TransactionJournal:
        """创建新 attempt，或显式恢复指定 attempt。"""

    def rollback(self, request: RollbackRequest) -> TransactionJournal:
        """以 rollback bundle 为目标运行同一状态机。"""

    def recover(self, attempt_id: str, envelope_sha256: str) -> TransactionJournal:
        """提升同一 attempt 的 control lease epoch，并保持已提交 ServingFence 不变地收敛。"""
```

- [ ] `resume_attempt_id` 必须显式传入；发现同 revision 的旧 receipt/attempt 不得自动续跑。
- [ ] 新 deploy 发现 active envelope 时一律拒绝；只有 envelope 指向已完成 journal 且现场复证一致时，先走 recover 的幂等清理分支，再允许创建新 attempt。expected-absent CAS 失败不能覆盖旧恢复入口。
- [ ] 全局锁顺序固定为 generation state → systemd → configuration → database → 按稳定 kind/project 排序的索引 → cleanup。reservation/genesis/envelope 在任何生产资源变化前持久化；初始 control lease 只在 active envelope 成功发布后签发，恢复在同一 attempt 上提升 control epoch，签发 lease 本身不改变 ServingFence 或撤销线上 permit。
- [ ] 最终 baseline freeze 只能在外部入口关闭、writer 已静默后执行；若 `GenerationState`、MainPID、systemd 或 index pointer 与最初观察不同，终止且保持 restricted。
- [ ] rollback bundle 绝不能在 writer 静默前冻结；最终复验包含 legacy 原地索引的真实文件/manifest 字节、systemd 有效载荷、配置身份和数据库事实，不只比较 pointer。
- [ ] resource activation 按 journal 顺序执行；第 N 个索引失败时逆序 restore 已激活索引；DB expand 不进入逆补偿，只验证 baseline 兼容后恢复旧代码/systemd/config/index。
- [ ] state CAS 成功但 permit 未发布的重启场景，recover 复验 acceptance 后确定性补发 permit；permit 已发布但 state 漂移时先撤销。
- [ ] serving commit 后崩溃时，recover 只轮换 ControlLease；不可变 acceptance、state 和 permit 的等值关系使用原 ServingFence。新 control lease 在同一 store 临界区授权补发/撤销 permit、完成 journal 和清 envelope，不重写 acceptance。
- [ ] complete journal 后必须先 CAS 退休当前 ControlLease，再清 active envelope。若 complete 后、retire 前崩溃，recovery 接管同一 attempt；若 retire 后、clear 前崩溃，recovery 只能凭 tombstone 和终态证据清 envelope，不再获取资源写权限；只有 envelope 已清且旧 lease 为 retired 时，新 attempt 才能原子接棒。
- [ ] `commit_serving` 不关闭维护门禁。控制器确定性计算目标状态 B 并让 staged permit 预先绑定 B 摘要；当前 A 下 gate 因摘要失配关闭，复证入口后只用一次 A→B CAS 打开门禁，禁止 CAS 后再改写 permit 追赶新摘要。Serving writer 的每次生产发布都在同一锁/事务内复验 mode、generation、current fence 和 `maintenance_active=False`，不信任进程启动时缓存。
- [ ] 旧 `DeploymentCoordinator` 降为兼容门面并委托新协调器；旧 receipt writer 停止用于正式部署。
- [ ] 运行：

```powershell
python -m pytest tests/test_runtime_transaction_coordinator.py tests/test_runtime_deployment_coordinator.py tests/test_runtime_deployment_steps.py tests/test_runtime_deployment_recovery.py -q
```

期望：完整 deploy、每阶段失败、显式 resume 和 recovery takeover 全部通过。

- [ ] 提交：`feat(runtime): 接通运行代际部署事务`

## 任务 3：接入生产部署和正式完整回滚

**文件：**

- Create: `codev_platform/runtime_production_rollback.py`
- Modify: `codev_platform/runtime_production_deployment.py`
- Modify: `codev_platform/runtime_production_boundary.py`
- Modify: `codev_platform/runtime_production_acceptance.py`
- Modify: `codev_platform/runtime_deployment_services.py`
- Modify: `codev_platform/runtime_release.py`
- Test: `tests/test_runtime_production_rollback.py`
- Modify Test: `tests/test_runtime_production_deployment.py`
- Modify Test: `tests/test_runtime_production_boundary.py`
- Modify Test: `tests/test_runtime_production_acceptance.py`
- Modify Test: `tests/test_runtime_release.py`

### 步骤

- [ ] 先写失败测试：production deploy 必须从 state/MainPID 得到 baseline；`target^`、调用参数伪造 baseline、只切 release pointer 的 rollback 均被拒绝；managed A/B 使用不同 `base_id` 仍能 A→B→A，legacy external interpreter 也能恢复且不进入 managed pointer 链。
- [ ] `runtime_production_deployment.py` 只构造端口、加载 plan 并调用 `GenerationTransactionCoordinator.deploy()`；将现有 473 行 `runtime_deployment_services.py` 中数据库、索引、systemd 逻辑移入各自 adapter，避免继续增长。
- [ ] 生产组合根只能由 `runtime_controller_service` 在受保护 systemd controller unit 中调用；直接从交互式 CLI 进程运行完整事务的测试必须失败。
- [ ] `runtime_production_rollback.py` 只接受受验证的 `RollbackRequest(bundle_attempt_id, expected_serving_generation_id)`，加载不可变 bundle 后调用同一协调器；不提供“上一个 Git 提交”语义。
- [ ] 低层 `rollback_release()` 保留给事务 adapter 内部使用，改名或可见性标明不得作为生产入口；其调用必须伴随 config/systemd/index 恢复 journal。
- [ ] production acceptance 输出 `GenerationAcceptance`，绑定 attempt、generation、provisional ServingFence、DB proof、systemd proof、四索引 proof、内部健康探针和时间；ControlLease 只记审计摘要、不参与有效性；acceptance 按 attempt O_EXCL 保存。
- [ ] 运行：

```powershell
python -m pytest tests/test_runtime_production_rollback.py tests/test_runtime_production_deployment.py tests/test_runtime_production_boundary.py tests/test_runtime_production_acceptance.py tests/test_runtime_release.py -q
```

期望：不存在绕开完整事务的生产回滚路径。

- [ ] 提交：`feat(runtime): 接入生产代际部署与完整回滚`

## 任务 4：更新 runtime CLI、状态诊断和恢复入口

**文件：**

- Create: `codev_platform/ops/runtime_generation.py`
- Modify: `codev_platform/ops/runtime.py`
- Modify: `codev_platform/ops/runtime_deploy.py`
- Modify: `codev_platform/cli.py`
- Test: `tests/test_runtime_generation_cli.py`
- Modify Test: `tests/test_runtime_cli.py`
- Modify Test: `tests/test_runtime_deploy_cli.py`
- Modify Test: `tests/test_cli_parser.py`

### 步骤

- [ ] 先写 parser/dispatch 失败测试，固定公开命令：

```text
codev-platform runtime deploy --plan <path> [--resume <attempt_id>]
codev-platform runtime rollback --bundle-attempt <attempt_id> --expected-serving <generation_id>
codev-platform runtime recover --attempt <attempt_id> --envelope-sha256 <sha256>
codev-platform runtime generation-status
codev-platform runtime legacy-policy create --plan <path> --approval <path>
codev-platform runtime legacy-audit --policy <path>
codev-platform runtime legacy-takeover --policy <path> --manifest <path> --plan <path>
```

- [ ] 删除生产 CLI 中无参数裸 `runtime rollback` 和直接 activate 指针；兼容低层命令只能放到明确的 `runtime internal` 命名空间并要求测试/维护显式开关，默认不可达。
- [ ] deploy 输出 attempt ID、target generation ID、baseline generation ID、journal state 和安全模式；错误输出不包含 token、DSN 和配置内容。
- [ ] deploy/rollback CLI 先预留 attempt 与 recovery genesis/envelope，再提交 `codev-runtime-controller@<attempt>` systemd unit并跟踪状态；CLI 断开或退出不终止 controller，unit 失败必须触发可重复 recovery worker。
- [ ] `--resume` 与 `recover` 只允许 root 调用，只接受固定 active envelope 指向的 attempt、完全匹配的 envelope 摘要和 lease CAS；任意历史 attempt 即使文件存在也不可恢复。稳定 launcher 调用的 recover handler 不接受调用者覆盖模块、解释器、cwd 或环境。
- [ ] `runtime internal` 不得靠环境变量开放；生产安装默认不注册低层指针命令。确需维护时必须由 root-owned、O_EXCL、带过期时间的单次维护许可启用。
- [ ] `generation-status` 分开显示 state/ServingFence、active recovery envelope/ControlLease、acceptance、permit 和四索引 pointer 的一致性；不得把 control epoch 与 serving fence epoch 混为一列，任何不一致返回非零状态。
- [ ] CLI handler 拆到新模块，保持 `ops/runtime.py` 小于 600 行。
- [ ] 运行：

```powershell
python -m pytest tests/test_runtime_generation_cli.py tests/test_runtime_cli.py tests/test_runtime_deploy_cli.py tests/test_cli_parser.py -q
```

期望：旧危险入口不可达，新命令参数和退出码稳定。

- [ ] 提交：`feat(runtime): 提供运行代际部署回滚命令`

## 任务 5：增加真实双 revision 构建与跨版本测试夹具

**文件：**

- Create: `tests/runtime_revision_pair_support.py`
- Create: `tests/runtime_revision_pair_wsl_runner.py`
- Create: `tests/test_runtime_real_revision_pair.py`
- Create: `tests/test_runtime_legacy_takeover_real_revision.py`
- Modify: `pyproject.toml`（仅当测试 marker 尚未声明时增加 `real_revision_wsl` marker）

### 步骤

- [ ] 先写失败测试，证明旧同源 fixture 会错误通过：目标控制器把新模块列表注入旧 release 时，旧 release 真实解释器应失败；新自描述协议应成功。
- [ ] Windows host helper 只负责调用 WSL runner；runner 在 Linux CPython 3.12 中对两个精确 revision 分别调用生产 `runtime_candidate.build_candidate()`、`runtime_build.stage_release()` 以及真实 base/release verifier。禁止用 `python -m build` + 临时 prefix 旁路生产构建/暂存链，不创建持久 worktree，不修改仓库工作树。
- [ ] runner 必须走生产 staging 组合端口并记录身份：candidate 由正式构建服务账号执行，root 只负责受保护 stage/install，发布后以目标服务 UID 运行 `probe_target_user`；测试断言每阶段 EUID、目录访问证明和目标用户导入，不允许整个 runner 以 root 包办后误绿。
- [ ] fixture 显式传入 baseline revision 和 target revision；若 revision 不存在则测试失败，不得退化到 HEAD 两份副本。
- [ ] `CODEV_RUN_WSL_REVISION_PAIR=1` 一旦设置，缺少 WSL、Linux CPython 3.12、构建服务账号或生产 staging 依赖必须失败，不得 skip；门禁未设置时才允许开发机快速回归显式跳过。
- [ ] 测试至少覆盖：两个 release 各自真实且不同的 base、旧 schema 2 stage receipt 与新 schema 3、旧 release 无 selftest、新 release 有 selftest、相同 target revision 的两个独立 attempt、A 回滚后再次部署 A、shadow graph 中已有 managed 候选时 legacy wheel 仍只读写 legacy 五表。
- [ ] 运行：

```powershell
$env:CODEV_RUN_WSL_REVISION_PAIR='1'
python -m pytest tests/test_runtime_real_revision_pair.py tests/test_runtime_legacy_takeover_real_revision.py -m real_revision_wsl -q --junitxml=.superpowers/wsl-revision-pair.xml
[xml]$revisionReport = Get-Content -Encoding UTF8 '.superpowers/wsl-revision-pair.xml'
$skipped = ($revisionReport.testsuites.testsuite | Measure-Object -Property skipped -Sum).Sum
if ([int]$skipped -ne 0) { throw 'WSL 双 revision 测试存在跳过项' }
```

期望：真实 Linux wheel/base/release 分别来自两个 commit，走生产 candidate/stage 链；不存在共享 `codev_platform` 源路径或 Windows wheel。

- [ ] 提交：`test(runtime): 增加双真实版本兼容回归`

## 任务 6：增加事务故障注入矩阵

**文件：**

- Create: `tests/runtime_generation_fault_support.py`
- Create: `codev_platform/runtime_drill_checkpoint.py`
- Create: `tests/test_runtime_generation_fault_injection.py`
- Create: `tests/test_runtime_drill_checkpoint.py`
- Modify Test: `tests/test_runtime_transaction_coordinator.py`
- Modify Test: `tests/test_runtime_recovery_service.py`
- Modify Test: `tests/test_file_queue_recovery.py`

### 步骤

- [ ] 建立 deterministic fault injector，以 `(step_id, window)` 为键，在 `after_prepared_fsync`、`after_effect_before_applied`、`after_applied_fsync`、`after_committed_fsync` 四处注入；禁止使用含义错误的 `before_intent` 或依赖随机时间。
- [ ] 参数化以下资源：attempt reservation、journal genesis、per-attempt/active envelope 发布、初始/接管/退休 ControlLease CAS、provisional ServingFence 签发、baseline freeze、permit revoke、runtime gate cache 失效、外部入口关闭/开放与连接排空、每个配置文件、writer fence/quiesce、DB prepare/expand/backfill checkpoint、每个 systemd 文件、稳定 launcher/trust manifest 更新、daemon-reload、每个 unit mask/unmask/start、四个索引各自 activate/restore、state/ServingFence CAS、acceptance O_EXCL、staged permit write、A→B publication state CAS、journal complete、active envelope clear。
- [ ] `active envelope clear` 必须严格发生在 journal complete 持久化和 ControlLease retire 之后；测试拒绝任何相反顺序，并分别覆盖“journal 已完成但 lease 尚未退休”和“lease 已退休但 envelope 尚未清理”的幂等恢复。
- [ ] 真实 WSL 故障使用注入式 `RuntimeDrillCheckpointPort`：生产组合根默认 no-op；只有 root-owned、O_EXCL、绑定 attempt/step/window/plan 摘要且未过期的 drill plan 才启用。到达窗口后先 fsync `fault-checkpoint.json`（attempt、step、window、journal 摘要、boot ID）再阻塞，Windows harness 验证“已到达且尚未越过”后终止发行版。
- [ ] 增加 FileSpool hard-kill 子进程故障用例：持有 per-key lock 时强制终止进程，重启后必须按 owner/进程存活与 stale 证据安全识别并回收遗留锁；不得误删仍由存活 owner 持有的锁。该项承接 reindex-worker-v2 的持续非阻断风险，不新增任务编号。
- [ ] 每个故障用例重建 coordinator 后调用 `recover()`，断言只允许两种终态：原 serving generation + 有效 permit，或新 serving generation + 有效 permit；如果证据不足，则 `safety_unproven` + 无 permit + 外部入口关闭。
- [ ] 增加 fencing takeover 并发测试：旧 controller 在恢复 controller 提升 epoch 后继续写 journal、state、index pointer、permit，全部被拒绝。
- [ ] 增加 serving publication 并发测试：旧 writer 在 `begin_switch` 后继续发布必须失败；target 持 provisional proof 在 `commit_serving` 前、commit 后但 maintenance 仍 active 时发布也必须失败；staged permit 写入后、A→B CAS 前因 state 摘要失配仍失败。只有 current fence、B 摘要匹配且门禁关闭后可以写入生产命名空间。
- [ ] 用 barrier 覆盖 publication 已通过复验但尚未 replace 时与 `begin_switch` 竞争：二者必须由同一状态锁/事务锁严格串行，最终不能出现 switching 后落地 pointer/manifest/ack，也不能留下只发布未确认的半结果。
- [ ] 用 barrier 覆盖 complete journal 后 `retire_current()` 与 recovery `take_over()` 竞争：两次 CAS 只能有一个赢家；分别断言 retire-first 只能清 envelope、takeover-first 只能由新 epoch proof 退休，旧 controller 永远不能覆盖新 epoch。
- [ ] 增加 READY 前/后 SIGKILL 模拟：recovery unit 失败时任一外部业务 unit 不得继续服务。
- [ ] 运行：

```powershell
python -m pytest tests/test_runtime_generation_fault_injection.py tests/test_runtime_drill_checkpoint.py tests/test_runtime_transaction_coordinator.py tests/test_runtime_recovery_service.py tests/test_file_queue_recovery.py -q
```

期望：故障矩阵全部确定性通过，无 flaky sleep。

- [ ] 提交：`test(runtime): 覆盖代际事务崩溃恢复窗口`

## 任务 7：更新 WSL 安装器和代际演练脚本

**文件：**

- Create: `scripts/drill-wsl-runtime-generation.sh`
- Create: `scripts/drill_wsl_runtime_generation.py`
- Modify: `scripts/install-wsl-runtime.sh`
- Modify: `scripts/install-wsl-runtime.bat`
- Modify Test: `tests/test_install_wsl_runtime_scripts.py`
- Create Test: `tests/test_runtime_generation_drill_script.py`
- Create Test: `tests/test_runtime_generation_host_harness.py`
- Create Test: `tests/test_runtime_recovery_wsl.py`

### 步骤

- [ ] 先写脚本契约测试：`.bat` 只负责定位 WSL/下载目录并调用 `.sh`，无跨行引号破坏；安装脚本只有顶层 `scripts/` 一份真值源；脚本拒绝未知 runtime root、非 root、dirty release 和 origin remote。
- [ ] 安装器保存 controller tree manifest/digest，原子安装稳定 launcher/recovery unit，并把稳定 CLI 安装到 `/usr/local/bin/codev-runtime`、演练脚本安装到 `/usr/local/libexec/codev-runtime/drill-wsl-runtime-generation.sh`；初始化 generation 目录但不自动删除 legacy 数据。
- [ ] WSL 内 drill 脚本按参数接收 baseline/target attempt，所有状态读取走稳定 `codev-runtime generation-status`；每一步把 JSON 证据保存到 runtime 审计目录，不打印秘密。
- [ ] Windows host harness 负责故障注入、重新拉起和验收：只使用 `wsl.exe --terminate <发行版>` 终止指定发行版，等待 systemd/recovery ready 后继续；禁止用影响所有发行版的 `wsl --shutdown`。WSL 内脚本不得在终止自身后假装继续验收。
- [ ] WSL 真实测试使用显式 `CODEV_RUN_WSL_GENERATION_DRILL=1` 门禁；未设置时只跳过真实 systemd 操作，脚本解析和契约测试仍必须运行。门禁一旦设置，缺少指定 WSL、systemd、Linux CPython 3.12、root EUID、GPU/CUDA 或稳定 runtime CLI 必须 `pytest.fail`，禁止再 skip。
- [ ] 安装脚本只有仓库顶层 `scripts/` 一份真值源；执行安装脚本契约测试，禁止新增第二份手工同步副本。
- [ ] 运行：

```powershell
python -m pytest tests/test_install_wsl_runtime_scripts.py tests/test_runtime_generation_drill_script.py tests/test_runtime_generation_host_harness.py tests/test_runtime_recovery_wsl.py -q
$repoWsl = (wsl.exe -d Ubuntu -- wslpath -a (Get-Location).Path).Trim()
wsl.exe -d Ubuntu -- bash -n "$repoWsl/scripts/install-wsl-runtime.sh"
wsl.exe -d Ubuntu -- bash -n "$repoWsl/scripts/drill-wsl-runtime-generation.sh"
```

期望：静态与 host harness 测试通过；未设置真实门禁时 `test_runtime_recovery_wsl.py` 明确跳过，任务 10/11 必须设置门禁重新执行，不能把本次跳过计作 WSL 验收。

- [ ] 提交：`feat(runtime): 增加 WSL 代际安装与演练脚本`

## 任务 8：实现带 fencing、墓碑和确认摘要的安全 GC

**文件：**

- Create: `codev_platform/runtime_gc_contract.py`
- Create: `codev_platform/runtime_gc_store.py`
- Create: `codev_platform/runtime_gc_lease.py`
- Create: `codev_platform/runtime_generation_gc.py`
- Create: `codev_platform/runtime_database_retirement.py`
- Modify: `codev_platform/ops/runtime_generation.py`
- Test: `tests/test_runtime_gc_contract.py`
- Test: `tests/test_runtime_gc_store.py`
- Test: `tests/test_runtime_gc_lease.py`
- Test: `tests/test_runtime_generation_gc.py`
- Test: `tests/test_runtime_database_retirement.py`
- Modify Test: `tests/test_runtime_generation_cli.py`

### 步骤

- [ ] 先写失败测试：serving/desired/rollback、未完成 attempt、最近两个已验收 managed generation、进程/MainPID、systemd、open file、backend lease、备份或引用不明的对象绝不能进入删除清单。
- [ ] 定义不可变 dry-run 和墓碑：

```python
@dataclass(frozen=True, slots=True)
class GcObjectRef:
    object_kind: str
    object_id: str
    relative_path: str
    identity_sha256: str


@dataclass(frozen=True, slots=True)
class RuntimeGcPlan:
    schema_version: int
    plan_id: str
    generation_state_sha256: str
    observed_gc_epoch: int
    objects: tuple[GcObjectRef, ...]
    created_at: str
    expires_at: str


@dataclass(frozen=True, slots=True)
class DeletionTombstone:
    schema_version: int
    plan_id: str
    object_ref: GcObjectRef
    phase: str
    source_device: int
    source_inode: int
    inventory_sha256: str
    quarantine_relative_path: str
    evidence_sha256: str


@dataclass(frozen=True, slots=True)
class GcLeaseProof:
    plan_id: str
    epoch: int
    token: bytes = field(repr=False)
```

- [ ] `build_gc_plan()` 在部署/GC 同一锁序下读取 state、attempt 租约、systemd/process/open-file/backend lease 并生成 root-owned O_EXCL plan；dry-run 摘要不包含秘密。
- [ ] GC 不伪造 `DeploymentAttempt`，而是保存 operation-specific `GcReservation(plan_id, confirmed_sha256, observed_state_sha256, observed_refs_sha256)`。`GcLeaseStore` 使用独立 epoch/token 域，在共享全局部署互斥锁内签发，但不能写 GenerationState、ServingFence、acceptance 或 permit。
- [ ] apply 先复证 dry-run 的 `observed_gc_epoch` 和 state/ref 摘要，再签发新的 `GcLeaseProof`；新 epoch 不要求等于 dry-run 观察值。服务保持在线，只有证明对象不被 serving/rollback/process/open-file/backend lease 引用才可隔离；无法证明时失败，不通过撤销 permit 掩盖引用问题。
- [ ] GC 崩溃恢复只允许同一 plan 提升 GcLease epoch 并继续已隔离 quarantine；旧 GC 进程恢复运行后在每个 rename/delete 临界区被新 epoch 拒绝。GcLeaseProof 不能调用任何 control/serving store API。
- [ ] `apply_gc_plan(plan, confirmed_sha256, gc_lease, ports)` 要求 root、当前 GC lease authority、未过期 plan、完全匹配的管理员确认摘要；持锁复读全部引用，任何漂移拒绝。
- [ ] 每个对象先写 PREPARED 墓碑并 fsync，再用同一 runtime root 目录描述符执行 `renameat`，原子移入 root-owned `.quarantine/<plan_id>/<object_id>`；墓碑绑定原 device/inode、完整 inventory 和隔离路径，随后 fsync 源/隔离两侧父目录。只有隔离成功后才逐项 descriptor-safe 删除 quarantine，最后写 DELETED 证据。
- [ ] 重启后只按墓碑中的 quarantine inode/inventory 继续删除，不触碰原路径上后来出现的同名对象；rename 前后身份漂移均拒绝。拒绝符号链接、挂载点和路径逃逸，禁止 shell 拼接递归删除。
- [ ] `runtime_database_retirement.py` 只处理已证明不被任何配置、systemd credential、generation、rollback bundle 或备份策略引用的旧数据库。plan 仅保存 DSN 指纹和 backup proof；apply 经专用数据库管理端口执行，当前生产 PostgreSQL 主库永远拒绝。
- [ ] CLI 增加 `runtime gc-plan`、`runtime gc-apply --plan <path> --confirmed-sha256 <sha256>`、`runtime database-retire-plan` 和对应 apply；所有 apply 默认不注册到非 root 环境。
- [ ] 故障测试覆盖墓碑写后、删除过程中、删除后证据前、证据完成后四个窗口；恢复时不得误删新建同名但身份不同的对象。
- [ ] 运行：

```powershell
python -m pytest tests/test_runtime_gc_contract.py tests/test_runtime_gc_store.py tests/test_runtime_gc_lease.py tests/test_runtime_generation_gc.py tests/test_runtime_database_retirement.py tests/test_runtime_generation_cli.py -q
```

期望：引用中、身份漂移或未确认对象全部 fail closed；重复恢复幂等。

- [ ] 提交：`feat(runtime): 增加代际与旧库安全清理事务`

## 任务 9：Windows 全量验证、代码评审、提交并仅推送 origin/dev

**文件：**

- Update: `docs/plans/roadmap-2026-07-19/2026-07-19-runtime-generation-rollback.md`（勾选有证据的 M3）
- Update: 项目开发记录的现有真值源文件（按 `.codex/rules/weekly-iteration-cadence.md` 定位，不新建重复日志）

### 步骤

- [ ] 执行受影响面定向测试以及真实 revision 测试。
- [ ] 执行全仓：

```powershell
$env:CODEV_RUN_WSL_REVISION_PAIR='1'
python -m pytest tests/test_runtime_real_revision_pair.py tests/test_runtime_legacy_takeover_real_revision.py -m real_revision_wsl -q --junitxml=.superpowers/wsl-revision-pair.xml
[xml]$revisionReport = Get-Content -Encoding UTF8 '.superpowers/wsl-revision-pair.xml'
$skipped = ($revisionReport.testsuites.testsuite | Measure-Object -Property skipped -Sum).Sum
if ([int]$skipped -ne 0) { throw 'WSL 双 revision 测试存在跳过项' }
Remove-Item Env:CODEV_RUN_WSL_REVISION_PAIR
python -m pytest
python -m compileall -q codev_platform
python -m ruff check codev_platform tests
python -m ruff format --check codev_platform tests
npm --prefix web-ui run tsc
npm --prefix web-ui run lint:js
$repoWsl = (wsl.exe -d Ubuntu -- wslpath -a (Get-Location).Path).Trim()
wsl.exe -d Ubuntu -- bash -n "$repoWsl/scripts/install-wsl-runtime.sh"
wsl.exe -d Ubuntu -- bash -n "$repoWsl/scripts/drill-wsl-runtime-generation.sh"
git diff --check
```

期望：pytest 不新增失败；本次 Python 文件 Ruff/format 全绿；前端与脚本验证通过。全仓已知 Ruff 存量问题如仍存在，记录精确数量和路径，不混入本次重构。

- [ ] 检查敏感信息和 AI 痕迹：

```powershell
rg -n "(Bearer |postgresql://example.invalid/database ]+:[^ ]+@|api[_-]?key|secret|token=)" docs codev_platform tests scripts
rg -n "(Co-authored-by|Generated with|Claude|OpenAI|ChatGPT|AI assistant)" . --glob '!data/**' --glob '!.git/**'
```

期望：没有新增明文凭据；commit message 和开发记录无 AI 痕迹。

- [ ] 请求架构、安全、测试三类最终代码评审，处理后重新执行相关验证。
- [ ] 按项目 `git-commit` skill 提交；确认 author 为 `helloworld3q3q <helloworld3q3q>`。
- [ ] 只执行：

```powershell
git push origin dev
```

- [ ] 验证 `HEAD == origin/dev` 且 `origin/dev` 与实施前一致；不得执行 GitHub PR 或 origin push。

## 任务 10：唯一 WSL 环境首次 legacy 往返演练

**文件：**

- Runtime Audit Output: `/var/lib/codev-platform/runtime/attempts/<attempt_id>/`
- Runtime State: `/var/lib/codev-platform/runtime/generation-state.json`

### 步骤

- [ ] 部署前只读采集：当前 release/MainPID、systemd unit/drop-in 摘要、配置摘要、Alembic head/结构 proof、四索引 pointer/manifest、磁盘空间、GPU 状态；生成独立 legacy policy 供用户确认。
- [ ] 在未关闭入口前完成 target A 的 release、配置/systemd bundle、数据库契约和四索引候选 staging。
- [ ] 运行正式 legacy takeover 和 deploy A；验收 state/acceptance/permit/四索引一致。platform-docs、graph、agent-memory、codegraph 四个 MCP 必须使用实际部署的鉴权配置完成 initialize 和至少一次只读调用；任何 401 都是本次验收失败，不能单独豁免后宣告可用。
- [ ] 运行正式 rollback 到 legacy bundle；验证代码、配置、systemd、四索引全部恢复，数据库保持向前结构且 legacy 自探针通过。
- [ ] 再次部署同一 A，必须创建新 attempt；验证不会复用第一次 receipt/journal。
- [ ] Windows host harness 在 serving 提交前和提交后各用 `wsl.exe --terminate Ubuntu` 执行一次发行版终止，并执行服务 SIGKILL 演练；重新拉起后 recovery service 必须确定性收敛。
- [ ] 每次 terminate 前必须读取并验证 checkpoint 的 attempt、step/window 和 journal 摘要，确认事务尚未越过该窗口；恢复后 boot ID 必须与 checkpoint 不同。checkpoint 不匹配、轮询超时或事务已越过均使演练失败，不能改为终止空闲 WSL。
- [ ] 执行：

```powershell
$env:CODEV_RUN_WSL_GENERATION_DRILL='1'
python scripts/drill_wsl_runtime_generation.py --distribution Ubuntu --scenario legacy-a-legacy-a
python -m pytest tests/test_runtime_recovery_wsl.py -q --junitxml=.superpowers/wsl-legacy-a.xml
[xml]$wslReport = Get-Content -Encoding UTF8 '.superpowers/wsl-legacy-a.xml'
$skipped = ($wslReport.testsuites.testsuite | Measure-Object -Property skipped -Sum).Sum
if ([int]$skipped -ne 0) { throw 'WSL 真实演练存在跳过项' }
wsl.exe -d Ubuntu -u root -- /usr/local/bin/codev-runtime generation-status
```

期望：最终 serving=A，permit 有效，四类索引均为 A 对应 generation，无 pending/safety_unproven。

## 任务 11：形成第二个真实 managed generation 并完成 A/B 往返

**文件：**

- Modify: 只允许本次演练发现并经测试修复的真实代码或验收记录真值源；禁止空提交制造 B。

### 步骤

- [ ] 若 legacy/A 演练发现缺陷，按系统化调试和 TDD 修复，形成有实际语义的 B；若无缺陷，则将经过验证的投产审计/兼容契约版本化形成真实 B，不创建空 commit。
- [ ] 重跑任务 9 的 Windows 验证，commit 并仅 push `origin/dev`。
- [ ] 在 WSL 执行 A→B，验证成功；执行正式 B→A rollback；再次 A→B，确保第二次 B 使用独立 attempt。
- [ ] 在 B serving 期间重启 WSL，验证 stable recovery launcher 不依赖 A/B 当前 symlink 即可启动。
- [ ] 执行：

```powershell
$env:CODEV_RUN_WSL_GENERATION_DRILL='1'
python scripts/drill_wsl_runtime_generation.py --distribution Ubuntu --scenario a-b-a-b
python -m pytest tests/test_runtime_recovery_wsl.py -q --junitxml=.superpowers/wsl-a-b.xml
[xml]$wslReport = Get-Content -Encoding UTF8 '.superpowers/wsl-a-b.xml'
$skipped = ($wslReport.testsuites.testsuite | Measure-Object -Property skipped -Sum).Sum
if ([int]$skipped -ne 0) { throw 'WSL 真实演练存在跳过项' }
wsl.exe -d Ubuntu -u root -- /usr/local/bin/codev-runtime generation-status
```

期望：最终 serving=B，rollback=A，active recovery envelope 不存在，permit/acceptance/state 完全一致。

## 任务 12：安全清理 legacy、旧库和临时目录

**文件：**

- Update: 项目开发记录真值源，记录保留/删除对象摘要和最终状态。

### 步骤

- [ ] 只有任务 10、11 全部通过且无未完成 attempt 时，使用任务 8 的 GC 生成 dry-run 清单；清单必须排除 serving、rollback、所有未完成 attempt 引用和最近两个已验收 generation。
- [ ] 用户确认 dry-run 对象后，使用 runtime GC 命令删除 legacy release/bundle、旧索引 generation 和过期审计临时文件；不得用手工递归删除计算路径。
- [ ] 检查 Windows `.worktrees`：仅删除已解除 Git worktree 注册、无未提交内容且不被任何分支引用的遗留目录；当前计划不创建新 worktree。
- [ ] 旧数据库仅指已经不再被任何配置、systemd 环境、generation、rollback bundle 或备份策略引用的旧实例。先执行引用审计和备份校验，再通过专用维护命令删除；生产 PostgreSQL 当前主库只做 schema 前向演进，不删除。
- [ ] 清理后再次执行 WSL `generation-status`、MCP/Web 健康检查、四索引查询和一次无变更部署 dry-run。
- [ ] 勾选总计划 M4，更新开发记录，commit 并只 push `origin/dev`。

## 任务 13：最终完成证据

### 步骤

- [ ] 保存以下证据摘要：Windows 全量测试、真实双 revision 测试、故障矩阵、legacy/A 往返、A/B 往返、WSL 重启恢复、最终 generation-status、GC 清单和远端校验。
- [ ] 执行最终 Git 核对：

```powershell
git status --short
git log -5 --format='%H%x09%an <%ae>%x09%s'
git rev-parse HEAD
git rev-parse origin/dev
git rev-parse origin/dev
```

期望：工作树干净；作者和提交信息符合约定；`HEAD == origin/dev`；origin 未变化。

- [ ] 只有证据实际存在时才能宣告“全部完成、WSL 可用、legacy 可删除”；否则按未通过门禁精确报告剩余项，不再给无证据的小时承诺。
