# WSL 旧 release shadow 事务化退役实施计划

> **执行要求：** 使用测试驱动逐任务实施；每项先确认目标测试失败，再写最小实现并复审。

**目标：** 在现有 systemd 安装事务内一次性退役旧 release shadow，使全部受管服务统一运行目标
release，同时保持 live clone 作为后续 push 的索引输入。

**架构：** shadow 文件能力由独立叶子模块提供，安装事务仅通过类型化端口编排。受管 systemd
统一解释器，但本地 editable 启动兼容行为不变。

**技术栈：** Python、systemd、`renameat2(RENAME_NOREPLACE)`、pytest、WSL。

## 全局约束

- 所有注释、异常和文档使用中文。
- 不手改用户配置、数据库、锁、maintenance marker 或 runtime mask。
- 不新增通用部署框架；所有文件目标由适配器固定推导。
- 任一状态或补偿无法证明时失败关闭。
- 每个生产 Python 文件不超过 600 行。

---

### 任务 1：shadow 原像与条件文件适配器

**文件：**
- 新建：`codev_platform/mcp_systemd_release_shadow.py`
- 修改：`codev_platform/mcp_systemd_install_contract.py`
- 新建：`tests/test_mcp_systemd_release_shadow.py`

**接口：**
- `LegacyReleaseDropInSnapshot`：绑定 unit、活动路径、归档路径及 root 文件完整原像。
- `snapshot_legacy_release_dropins(unit_names) -> tuple[LegacyReleaseDropInSnapshot, ...]`
- `retire_legacy_release_dropins(snapshots) -> None`
- `restore_legacy_release_dropins(snapshots) -> None`
- `verify_legacy_release_dropins_retired(snapshots) -> None`

- [x] 写红灯：缺失为空操作；活动 shadow 可冻结；已归档幂等；双路径、符号链接、非 root、
  不安全父目录、manifest 外 shadow 和 inode 变化均失败。
- [x] 运行目标测试，确认接口缺失导致失败。
- [x] 复用受管路径原语与条件同目录移动，实现固定文件名和固定归档名，不接收任意目标路径。
- [x] 运行目标测试并确认通过。

### 任务 2：安装事务接入与完整补偿

**文件：**
- 修改：`codev_platform/mcp_systemd_install_contract.py`
- 修改：`codev_platform/mcp_systemd_install_transaction.py`
- 修改：`codev_platform/mcp_systemd_install_systemd.py`
- 修改：`tests/mcp_systemd_install_transaction_support.py`
- 修改：`tests/test_mcp_systemd_install_transaction.py`

**接口：**
- `SystemdInstallPorts` 新增 shadow 的 snapshot/retire/restore/verify 四个窄端口。
- `_PreparedInstall` 在首次写入前冻结 shadow 原像，并把恢复与复证加入既有补偿计划。

- [x] 写红灯：安装成功时主 unit 与 shadow 一次提交；第 N 个退役、reload、restart 或最终证明
  失败时恢复全部原像、启用态和活动态。
- [x] 写红灯：maintenance 活跃、runtime mask、manifest 外 shadow 时零修改退出；第二次安装幂等。
- [x] 运行目标测试并确认旧事务缺少 shadow 生命周期。
- [x] 接线四个端口；顺序固定为冻结、写主 unit、退役 shadow、reload、有效载荷证明、
  enable/restart 与最终复证。
- [x] 将准备/补偿和有效载荷证明移入窄模块，确保事务文件仍不超过 600 行。
- [x] 运行安装事务、SIGINT、布局迁移和 maintenance 互斥回归。

### 任务 3：受管 systemd 统一 release Python

**文件：**
- 修改：`codev_platform/mcp_systemd.py`
- 修改：`tests/test_mcp_systemd_install.py`
- 修改：`tests/test_mcp_serve.py`

**接口：**
- `render_systemd_units(..., runtime_python: Path | None = None)`：显式提供时替换所有 Python
  MCP endpoint 的解释器；默认值保持本地兼容。
- `install_systemd()` 总是把当前绝对 `sys.executable` 作为受管 runtime Python 传入。

- [x] 写红灯：配置故意指向旧 `runtime.chroma_venv`，受管 platform-docs 与其余 unit 仍全部使用
  显式 release Python；本地 endpoint 枚举行为不变。
- [x] 运行测试确认 platform-docs 仍泄漏旧解释器。
- [x] 实现窄覆盖，不修改用户配置，也不改变 `serve-mcp start`。
- [x] 运行 MCP/systemd 渲染、安装 manifest 和运行时隔离回归。

### 任务 4：运行文件稳定落点与清理门禁

**文件：**
- 新建：`codev_platform/core/runtime_artifacts.py`
- 修改：`codev_platform/codegraph/server.py`
- 修改：`codev_platform/agent/memory_mcp.py`
- 修改：`codev_platform/mcp_runtime.py`
- 修改：`codev_platform/ops/health/__init__.py`
- 修改：对应路径与使用率测试。

**接口：**
- usage、serve-mcp 日志、Chroma 日志/锁和默认 health 快照均由窄路径函数落在 `data_root`。
- 显式 `health --json-out <path>` 保持兼容。

- [ ] 写红灯：所有默认运行文件路径位于 `data_root` 且不在 release/package。
- [ ] 运行测试确认旧路径仍写 package。
- [ ] 迁移写入者和读取者到同一真值源，不保留第二套拼接逻辑。
- [ ] 运行 health、metrics、platform status、MCP usage 与日志回归。

### 任务 5：本地回归、WSL 发布和普通 push 验收

**文件：**
- 修改：`docs/plans/roadmap-2026-07-11/reindex-isolated-maintenance-wsl-runbook-2026-07-13.md`
- 修改：`docs/plans/roadmap-2026-07-11/README.md`
- 新建：`docs/plans/roadmap-2026-07-11/daily-summary-2026-07-17.md`

- [x] 运行 Ruff、`git diff --check`、文件预算、高风险回归和全仓 pytest。
- [ ] 核对作者/提交者，显式只推送 `origin/dev`。
- [ ] 创建并验证 bootstrap release；执行 shadow 事务切换全部受管 unit。
- [ ] 使用 live clone 配置完成 maintenance、四类索引与 CodeGraph 恢复。
- [ ] 在真实 CodeGraph 会话存活时提交运行文件迁移，观察普通 push 协作排空且 InvocationID 不变。
- [ ] 创建最终 release，验证 CUDA、worker、四类 manifest、四个 MCP 真调用和全部 unit 运行身份。
- [ ] 迁移旧运行证据后仅用 Git 清理 inactive worktree，确认只剩一个活动环境。
