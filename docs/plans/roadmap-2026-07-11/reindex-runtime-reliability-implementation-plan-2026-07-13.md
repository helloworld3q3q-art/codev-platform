# 重新索引运行时可靠性实施计划

> 面向执行者：按任务顺序完成，每个任务先写失败测试，再以最小实现转绿；完成后必须独立审查。

**目标：** 让 worktree、WSL systemd worker 与 manifest 在版本升级和首次隔离部署时保持可诊断、可恢复且不越过安全边界。

**架构：** manifest 读取只投影本版本认识的字段，隔离未来追加列；Git hook 始终以当前工作树源码和可信 venv 启动；owner 就绪状态收敛为只读模型，自动启动只在 READY 时 spawn，而直接 systemd worker 以专用退出码阻止重启风暴。首次 owner 初始化继续复用既有维护窗口、停机证明、恢复审计和运行锁，不引入自动写入。

**技术栈：** Python 3、SQLite、Git sh hook、systemd、pytest。

## 全局约束

- 所有代码、注释、文档和 CLI 文案使用中文。
- 不手改 `data/`、`.codegraph/` 或用户级配置；不自动执行 `init-owner`。
- 只允许推送 `origin/dev`，禁止访问或推送 GitHub/origin。
- 保持 fail-closed；未知 owner 状态不得 spawn worker，不得建议 `prune-stale` 删除积压。
- 保持低耦合、单一职责和文件不超过 600 行。

---

### 任务 1：Manifest 前向兼容读取

**文件：**

- 修改：`codev_platform/index_manifest.py`
- 测试：`tests/test_index_manifest.py`

**接口：** `read_manifest()` 只读取 `_BUILD_COLUMNS`，忽略未来 writer 追加的列；旧表仍由现有迁移补齐已知可选列。

- [x] 写失败测试：创建正常 manifest 后追加 `future_writer_field`，断言 `read_manifest()` 和 `latest_build()` 仍可读取原记录。
- [x] 运行目标测试，确认现有 `SELECT *` 导致 `BuildRecord` 参数错误。
- [x] 用静态列投影和单一行映射实现最小修复。
- [x] 运行 `tests/test_index_manifest.py`。

### 任务 2：工作树 hook 运行时一致性

**文件：**

- 修改：`codev_platform/ops/hooks.py`
- 测试：`tests/test_post_hooks.py`

**接口：** 生成的 hook 优先用当前工作树或共享主工作树的 `.venv` Python，通过 `-m codev_platform.cli` 执行；无可信 venv 才回退 PATH CLI。安装器须能定位 linked worktree 的共享 hooks 目录。

- [x] 写失败测试：断言 stub 含共享 Git 目录解析、Python module 启动和 PATH 回退；模拟 linked worktree hooks 目录解析。
- [x] 运行目标测试并确认旧 console-script 模板不满足断言。
- [x] 最小实现解析器和安装目录解析；不在 hook 内写入运行态。
- [x] 运行 `tests/test_post_hooks.py tests/test_cli_parser.py`。

### 任务 3：只读 owner 就绪模型

**文件：**

- 修改：`codev_platform/reindex/runtime_owner.py`
- 新建：`codev_platform/reindex/owner_readiness.py`
- 测试：`tests/test_reindex_owner_readiness.py`

**接口：** `inspect_owner_readiness(binding, path=None)` 返回 `READY`、`BOOTSTRAP_REQUIRED`、`RECOVERY_REQUIRED`、`BINDING_MISMATCH` 或 `UNAVAILABLE`，且不创建目录、owner 或锁文件；`OWNER_BOOTSTRAP_EXIT_CODE` 是 systemd 与 CLI 共用的单一真值。

- [x] 先覆盖缺 owner 不创建父目录、匹配 owner、损坏 owner、绑定不匹配和读取异常五种状态。
- [x] 运行目标测试确认模块缺失。
- [x] 提取纯读取 owner 文件的窄函数，让原 `QueueOwnerStore` 复用解析逻辑；实现就绪模块。
- [x] 运行目标测试和 `tests/test_reindex_isolated_startup.py`。

### 任务 4：worker、状态与 systemd 的安全接线

**文件：**

- 修改：`codev_platform/reindex/isolated_worker_startup.py`
- 修改：`codev_platform/reindex/worker_launcher.py`
- 修改：`codev_platform/reindex/supervisor.py`
- 修改：`codev_platform/ops/reindex/dispatch.py`
- 修改：`codev_platform/ops/reindex_queue_worker.py`
- 修改：`codev_platform/reindex/status.py`
- 修改：`codev_platform/mcp_systemd.py`
- 修改：`codev_platform/mcp_systemd_install_contract.py`
- 新建：`codev_platform/mcp_systemd_install_bootstrap.py`
- 修改：`codev_platform/mcp_systemd_install_transaction.py`
- 修改：`codev_platform/mcp_systemd_install_systemd.py`
- 测试：`tests/test_reindex_isolated_startup.py`、`tests/test_reindex_worker_supervisor.py`、`tests/test_reindex_worker_owner_gating.py`、`tests/test_reindex_queue_cli_worker.py`、`tests/test_reindex_status_core.py tests/test_reindex_status_owner.py`、相关 systemd 渲染、输入、事务与 Linux 适配器测试。

**接口：** 自动启动收到非 READY 报告时返回 `bootstrap-required` 等受控 action 且不 spawn；worker 仍在组合根复核并以专用退出码停止；状态把该状态建议为 `init-owner`；`codev-reindex.service` 将专用退出码列入 `RestartPreventExitStatus`。systemd 安装 manifest 升级为 v3，以 `PostRestartExpectation` 显式区分普通 active 验证和 reindex 的受控 bootstrap 停机；历史失败状态在写入前失败关闭，本次精确 exit 77 只返回待人工恢复报告，绝不自动初始化、reset 或重启。

- [x] 写失败测试：缺 owner 不 spawn；组合根暴露 bootstrap 专用异常；worker 将其记录为 `bootstrap-required` 并返回专用码；status 不再建议 `prune-stale`；systemd unit 阻止该码重启。
- [x] 分别运行各目标测试并确认失败原因正确。
- [x] 以 readiness 端口接入 launcher，dispatch 传递已打开的队列；direct worker 保留二次保护；不改变 `init-owner --yes` 写边界。
- [x] 用 manifest v3、受控启动专用端口与安装报告实现 systemd 事务；普通服务先通过 active 验证，历史失败状态不写入，精确本次 exit 77 只输出人工恢复步骤。
- [x] 运行所有 reindex 目标测试和 systemd 渲染、输入、事务测试。

### 任务 5：端到端复核与受控交付

**文件：**

- 修改：必要时仅更新 `docs/plans/roadmap-2026-07-11/reindex-isolated-maintenance-wsl-runbook-2026-07-13.md`

- [ ] 检查所有改动 Python 文件行数不超过 600，运行 ruff、compileall、`git diff --check`。
- [ ] 运行完整 Python 测试集。
- [ ] 用项目 venv 做只读 `reindex-queue status`、`health --mode light` 复核；不得清理本机 pending 或手改数据库。
- [ ] 以 `helloworld3q3q <helloworld3q3q>` 提交；仅执行 `git push origin HEAD:dev`。
