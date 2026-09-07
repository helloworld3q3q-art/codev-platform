# CodeGraph 维护停机契约实施计划

> 面向执行 agent：按任务逐项使用测试驱动开发；本计划在隔离 worktree 内执行，并在每个任务后做独立复审。
>
> **2026-07-16 实施修订：** 本文下方的任务勾选保留为演进记录；凡是与本修订冲突的
> “runtime mask 唯一权威”“CodeGraph 先于 marker”“普通 restore 删除 marker”等旧描述均已废止。

**目标：** 把 CodeGraph MCP 的 catch-up 写入纳入 reindex 维护窗口的机器可证明边界，只有 `codegraph` manifest 已精确落到目标提交后才能恢复该服务。

**架构：** 将转换意图与 gate 锁、耐久 marker、CodeGraph 永久 condition guard、耐久 hold、runtime mask、共享操作租约、运行实例身份和只读 manifest 证明拆为独立叶子模块。维护编排固定为 `intent EX → marker(M) → gate EX → guard(G) → G 证明 → hold(H) → runtime mask(R) → stop(S)`；掉电投影只允许 `∅ → {M} → {M,G} → {M,G,H}`。恢复时 marker-last：先恢复并稳定证明 CodeGraph，再完成 reindex handoff，最后耐久删除 marker。

**技术栈：** Python、systemd、cgroup v2、SQLite 只读 URI、pytest。

## 全局约束

- 所有新增注释、错误信息、运行文档使用中文。
- 仅触及 `codev-reindex.service` 和 `codev-mcp-codegraph.service`，不得操作其他服务或全量安装器。
- CodeGraph 0.9.7 的 `--no-watch` 与 `CODEGRAPH_NO_DAEMON=1` 不构成维护期只读证明。
- CodeGraph 的维护边界由永久 condition guard、耐久 hold、runtime mask 与停机/cgroup 证明共同组成；runtime mask 位于 `/run`，不能单独承担重启安全。
- 永久 guard drop-in 固定为 `/etc/systemd/system/codev-mcp-codegraph.service.d/99-codev-maintenance-condition.conf`，内容与 canonical unit 共用同一 `ConditionPathExists=!/var/lib/codev-platform/codegraph-maintenance.gate` 真值，恢复后也不得删除。
- `serve-mcp` 在 Linux + systemd 环境不得 detached spawn CodeGraph；遗留 detached 代理由外部写入者证明失败关闭。
- CodeGraph reindex 与 MCP 后端必须共享仓级操作租约；租约忙只允许重试，租约状态不可证明必须失败关闭。
- 所有停机/读取异常必须失败关闭；不得输出 PID、命令行、token 或 DSN。
- 运行锁、marker、manifest 的破坏性操作必须先有严格证明；只读 manifest 不得 mkdir、建表、迁移或写库。
- Python 生产文件保持 600 行以内；职责过大时新建叶子模块。
- 普通 `restore` 只建立 reindex 待命 handoff，不删除 marker；仅 `resume-codegraph --project --target-commit --yes` 可在同一转换锁中解除 runtime mask/hold、启动并稳定证明 CodeGraph，完成 handoff 后最后删除 marker。

---

### 任务 1：泛化 systemd 单元停机证明

**文件：**

- 修改：`codev_platform/ops/reindex_admin_systemd_guard.py`
- 测试：`tests/test_reindex_admin_systemd_guard.py`

**接口：**

- 产出 `verify_systemd_unit_stopped(unit: str, *, expected_restart: str, platform_name: str | None = None, command_runner: CommandRunner | None = None, cgroup_events_reader: CgroupEventsReader | None = None, cgroup_root: Path = _CGROUP_ROOT) -> None`。
- 保留 `verify_codev_reindex_stopped()`（固定 `Restart=no`）；新增 `verify_codev_codegraph_stopped()`，其单位固定为 `codev-mcp-codegraph.service`、重启策略固定为基线 `always`，实际启动禁令由 runtime mask 另行证明。

- [ ] 写失败测试：CodeGraph 单元仍 active、Restart 非 `always`、ControlGroup 尾段不精确、`cgroup.events` 为 populated，均抛受控停机证明异常。

- [ ] 运行：`python -m pytest tests/test_reindex_admin_systemd_guard.py -q`，确认新断言在接口不存在或旧硬编码实现下失败。

- [ ] 最小实现：把现有 reindex 状态解析和 cgroup 路径校验提为接收 unit 的通用函数；单位名只允许精确安全的 `.service` 名称，已移除的 cgroup 仍视为已清空。

- [ ] 运行：`python -m pytest tests/test_reindex_admin_systemd_guard.py -q`，预期全绿。

### 任务 2：实现 CodeGraph runtime mask、共享租约与只读 manifest 证明叶子

**文件：**

- 新建：`codev_platform/ops/reindex_codegraph_maintenance.py`
- 新建：`codev_platform/ops/reindex_codegraph_manifest_proof.py`
- 新建：`codev_platform/codegraph/operation_lease.py`
- 测试：`tests/test_reindex_codegraph_maintenance.py`
- 测试：`tests/test_reindex_codegraph_manifest_proof.py`

**接口：**

- `apply_codegraph_runtime_mask()`、`verify_codegraph_runtime_mask()`、`remove_codegraph_runtime_mask()`：只施加/证明/解除本工具的 `masked-runtime` 层；持久 mask、字段缺失或状态不受信任一律拒绝。
- `codegraph_operation_lease(repo)` 与 `codegraph_operation_leases(repositories)`：按仓根稳定顺序取得跨进程租约，MCP 后端和 `codegraph sync` 任何一方忙时均不得并发写。
- `require_codegraph_manifest_target_ok(project_id: str, target_commit: str, *, path: Path | None = None) -> None`：以 SQLite `mode=ro` 查询指定项目、`kind='codegraph'` 的 `status,target_commit`，仅完整目标 OID 且 status 为 `ok` 放行。

- [ ] 写失败测试：缺失库、旧 schema、错误 SHA、失败状态、SQLite 读取异常均拒绝且不创建任何文件；runtime mask 不是 `masked-runtime`、持久 mask 或解除后仍被 mask 时拒绝；跨进程租约忙碌时不得进入临界区。

- [ ] 运行：`python -m pytest tests/test_reindex_codegraph_maintenance.py tests/test_reindex_codegraph_manifest_proof.py -q`，确认 RED。

- [ ] 最小实现：runtime mask 仅调用固定 unit 的 `systemctl mask/unmask --runtime` 并读取严格字段；所有 SQLite 连接使用只读 URI，禁止调用会建表/迁移的 manifest API。

- [ ] 运行：同一命令全绿；再运行 `python -m ruff check codev_platform/ops/reindex_codegraph_maintenance.py codev_platform/ops/reindex_codegraph_manifest_proof.py`。

### 任务 3：接入维护 prepare、status 与 restore 的 CodeGraph 证明

**文件：**

- 新建：`codev_platform/ops/reindex_maintenance_prepare.py`
- 修改：`codev_platform/ops/reindex_maintenance.py`
- 修改：`codev_platform/ops/reindex_maintenance_restore.py`
- 测试：`tests/test_reindex_maintenance.py`

**接口：**

- `prepare_reindex_maintenance()` 先取得 intent EX 并耐久发布 marker，再取得 gate EX，由唯一 CodeGraph lifecycle 完成 `G → G proof → H → R/S`；随后收敛 reindex drop-in/停机和最终联合证明。
- `inspect_reindex_maintenance()` 增加必需的 `codegraph_mask_proof` 与 `codegraph_stop_proof`。
- `restore_reindex_maintenance()` 只准备并启动 reindex 待命实例，不执行 marker 最终交接；CodeGraph 仍须满足 guard、hold、runtime mask 与停机四重证明。

- [ ] 写失败测试：intent 取得失败时零修改；marker 落盘后的任意失败都穷尽双服务补偿；G 建立或首次证明失败时绝不创建 H；restore 任一证明失败时 marker、guard、hold 与 runtime mask 保持失败关闭。

- [ ] 运行：`python -m pytest tests/test_reindex_maintenance.py -q`，确认 RED。

- [ ] 最小实现：将现有 prepare 协调体移到专用叶子，facade 仅保留公开 API、CLI 和延迟委托；补偿必须重建 runtime mask、停止固定 CodeGraph unit，并保留 reindex marker。

- [ ] 运行：`python -m pytest tests/test_reindex_maintenance.py tests/test_reindex_admin_systemd_guard.py -q`，并核对 `codev_platform/ops/reindex_maintenance.py` 不超过 600 行。

### 任务 4：新增显式 `resume-codegraph` CLI 与运行手册收口

**文件：**

- 修改：`codev_platform/ops/reindex_maintenance.py`
- 修改：`tests/test_reindex_maintenance.py`
- 修改：`docs/plans/roadmap-2026-07-11/reindex-isolated-maintenance-wsl-runbook-2026-07-13.md`
- 修改：`docs/plans/roadmap-2026-07-11/README.md`

**接口：**

- 子命令固定为 `reindex-maintenance resume-codegraph --project <project_id> --target-commit <完整OID> --yes`。
- 缺 `--yes` 仅演练；缺 project/target、短 SHA、未知动作均返回中文 FATAL，且不得修改 hold 或执行 systemctl。

- [ ] 写失败测试：参数不完整、manifest SHA 不一致、租约忙碌、转换锁失败或启动后健康/身份稳定证明失败时都回到 `M+G+H` 与双服务停机边界；成功路径严格执行有效载荷证明、解除 runtime mask、解除 hold、启动、InvocationID/NRestarts 稳定、enable、handoff 最终证明、marker-last。

- [ ] 运行：`python -m pytest tests/test_reindex_maintenance.py tests/test_cli_parser.py -q`，确认 RED。

- [ ] 最小实现：CLI 只调用窄编排叶子；手册写明 CodeGraph 在普通 `restore` 后继续 runtime mask，`resume-codegraph` 必须重新进入维护稳态、只读核验 manifest、在多仓租约内恢复 reindex 后才解除 mask，维护期禁止 MCP 查询。

- [ ] 运行：目标 pytest、`git diff --check`、`python -m ruff check codev_platform/ops` 全绿。

## 验收

- prepare/status 能机器证明 reindex 的 `Restart=no`，以及 CodeGraph 永久 guard、耐久 hold、`masked-runtime`、inactive/dead 与空 cgroup。
- 任何 CodeGraph serve 在 marker 内均被宽写入者证明拒绝。
- restore 不启动 CodeGraph且不删除 marker；显式恢复只能在只读 manifest 精确 SHA、staged payload、运行身份、健康与稳定窗口全部成功后执行 marker-last 交接。
- 全量回归前先跑上述定向套件，再跑 `python -m pytest tests/`。
