# Reindex 冲突语义整合与受控交付实施计划

> 面向执行者：逐任务执行红灯、最小实现、独立审查；不得把外层旧单体直接覆盖回分层架构。

**目标：** 保存外层未提交 queue migration 的严格保护语义，将其迁入隔离运行时架构，并消除 owner 非 bootstrap 重启风暴、systemd 安装并发补偿和 v3 特殊语义伪成功。

**架构：** 外层 8 个文件先成为只读 WIP 保护分支，`d6c8dfa` 及其分层 queue 模块是唯一生产底座。pending-CAS 语义分别落入 File/Pg 的窄迁移模块，不恢复 `queue.py` 单体；owner 启动分类与 systemd 受控退出码由独立类型和单一常量连接；systemd 安装事务用独占 installer 锁覆盖快照、写入、验证与补偿全窗口。

**技术栈：** Python 3、pytest、Git worktree/stash、File/Pg queue、systemd。

## 全局约束

- 代码、注释、文档、CLI 文案使用中文；标识符和外部协议字段可使用英文。
- 不手改 `data/`、`.codegraph/`、用户级配置；不执行 `init-owner`、prune、真实 systemd 或 WSL 服务操作。
- 仅允许最终非强制推送 `origin/dev`；禁止访问或推送 GitHub/origin。
- 外层未提交的 2 个已跟踪和 6 个未跟踪文件必须先建立可校验 WIP 保护分支，未获验证不得删除保护分支或丢弃 stash。
- 未知 owner、损坏 owner、绑定不匹配和读取不可用均 fail-closed：不得 spawn、不得错误建议 `prune-stale`、不得进入 systemd 重启风暴。
- 所有 Python 源码与新增测试文件不超过 600 行；保持低耦合、单一职责、窄接口和可测试性。

## 执行记录（2026-07-14）

- 已完成任务 0–3：WIP 保护、File/Pg 严格 pending-CAS、owner 受控停止和 systemd 安装事务边界均已独立审查通过。
- 为保持新增测试的单一职责，将 cgroup、Windows Job、POSIX 三组进程测试拆为 16 个不超过 600 行的测试/支撑模块；测试函数、装饰器、参数和断言与拆分前保持一致。
- 本地完整回归为 `3669 passed, 125 skipped`；唯一警告来自第三方 FastAPI/Starlette 对 `httpx` 的弃用提示。
- 已只读确认当前队列仍有 4 个 pending 任务且建议 `init-owner`；本次不执行 owner 初始化、prune、systemd 操作或 WSL 服务重启。

---

### 任务 0：保护外层 migration 现场并建立整合基线

**文件：**

- 不修改生产源码。
- 保护：外层 `codev_platform/reindex/{pg_queue.py,queue.py,file_queue_codec.py,file_queue_migration.py,pg_queue_migration.py,queue_migration.py}` 与两个 migration 测试文件。
- 创建：`wip/reindex-queue-migration-20260714` 保护分支与独立 worktree。

**接口：** WIP 分支必须精确保存八个文件字节内容；integration 分支从 `d6c8dfa` 继续，不在外层 `dev` 写代码。

- [x] 记录八个外层文件的 Git blob/hash、外层 `HEAD`、状态和 stash 标识。
- [x] 以 `--include-untracked` 建立带时间戳的 stash；创建基于 `09b82c1` 的 WIP worktree，应用 stash 并逐文件核对 hash。
- [x] 使用 `helloworld3q3q <helloworld3q3q>` 提交 WIP 保护分支；保留原 stash 作为第二份回退证据。
- [x] 确认外层 `dev` 干净、仍指向 `09b82c1`，隔离工作树切换至 `integration/reindex-runtime-reliability`（基于 `d6c8dfa`）。

### 任务 1：迁入 pending-CAS 严格语义而不恢复 queue 单体

**文件：**

- 修改：`codev_platform/reindex/file_queue_migration.py`
- 修改：`codev_platform/reindex/pg_queue_migration.py`
- 必要时修改：`codev_platform/reindex/file_queue_codec.py`、`codev_platform/reindex/pg_queue_codec.py`、`codev_platform/reindex/queue_ports.py`
- 新建或修改：`tests/test_reindex_pending_migration_strictness.py`、现有 File/Pg migration 测试。

**接口：** `migrate_pending(expected, new_meta, timeout_sec)` 仅在 pending、无 claim/owner、版本精确匹配时更新 metadata；旧 File sidecar 使用内容指纹，原样重写后必须冲突；Pg 允许受控 v1/xmin 升级但损坏 metadata 绝不覆盖；锁超时返回 `BUSY`，后端错误不泄露 DSN。

- [x] 先从 WIP 测试提取下列红灯：File 原样重写拒绝旧指纹、损坏 pending token 不提供版本、active/result 并存不误迁移、历史 marker 不搬运；Pg 版本冲突/锁超时/后端错误/损坏 metadata 均不越权更新。
- [x] 在 integration 分支运行新测试，记录当前实现与 WIP 语义的差异，确保失败原因是保护尚未迁入。
- [x] 将比较、版本编码和错误归一化放在 File/Pg 各自窄模块；共用值对象只放 `queue_ports.py`，不得恢复 `queue.py` 的存储实现。
- [x] 运行 File/Pg pending migration、queue contract 与相关 worker/dispatch 测试；检查两个后端的 outcome 契约对称。

### 任务 2：将所有需人工处理的 owner 状态变为受控停止

**文件：**

- 修改：`codev_platform/reindex/isolated_worker_startup.py`
- 修改：`codev_platform/reindex/owner_readiness.py`
- 修改：`codev_platform/ops/reindex_queue_worker.py`
- 修改：`codev_platform/reindex/status.py`
- 修改：`codev_platform/mcp_systemd.py`
- 测试：`tests/test_reindex_isolated_startup.py`、`tests/test_reindex_queue_cli_worker.py`、`tests/test_reindex_status_core.py tests/test_reindex_status_owner.py`、`tests/test_systemd_restart.py`。

**接口：** bootstrap 缺 owner 使用既有 77；损坏、绑定不匹配、不可用使用明确的 operator-blocked 类型和单一不可重启退出码。worker 记录无秘密 exit reason，status 分别建议 `init-owner` 或 `inspect-owner`，systemd 不重启两种人工处理退出码；意外运行时错误仍保持原有失败语义。

- [x] 先写三类红灯：损坏 owner、binding 不匹配、不可读取 owner 均不会 re-raise 为普通 error，也不触发 restart；普通 `RuntimeError` 不得被误分类。
- [x] 为启动分类、CLI exit reason/exit code、status action 与 reindex unit 的 `RestartPreventExitStatus` 写精确断言。
- [x] 以类型化 startup outcome 和无秘密 reason 实现最小接线，复用 owner readiness 单一真值，不在 worker 中解析 owner 文件。
- [x] 运行 owner、worker、status 和 systemd 渲染目标测试。

### 任务 3：收紧 systemd manifest v3 与安装事务并发边界

**文件：**

- 修改：`codev_platform/mcp_systemd_install_contract.py`
- 修改：`codev_platform/mcp_systemd_install_bootstrap.py`
- 修改：`codev_platform/mcp_systemd_install_transaction.py`
- 修改：`codev_platform/mcp_systemd_install_systemd.py`
- 必要时修改：`codev_platform/mcp_systemd.py`
- 测试：`tests/test_mcp_systemd_install_input.py`、`tests/test_mcp_systemd_install_transaction.py`、`tests/test_mcp_systemd_install_transaction_sigint.py`、`tests/test_mcp_systemd_install_systemd.py`。

**接口：** `ACTIVE_OR_OWNER_BOOTSTRAP_STOPPED` 只允许 `codev-reindex.service`；非 reindex v3 manifest 必须 fail-closed。安装事务持有独占 installer 锁直到成功验证或补偿证明结束；并发失败事务不得恢复/删除另一事务成功写入的 unit。77 只表示首次 owner bootstrap；operator-blocked 的退出必须提供人工恢复错误而非伪成功。

- [x] 写红灯：非 reindex service 声明特殊语义被拒绝；两事务交错时，第一事务失败不能使用过期 snapshot 回滚第二事务；operator-blocked restart 不得作为 bootstrap success。
- [x] 选择独立 root-owned installer lock，避免 shared maintenance permit 升级为 exclusive 时死锁；将取得、释放和补偿证明收在独立窄适配器。
- [x] 在 contract/parser 处限制特殊语义，在 transaction 处二次防御；保持 generic transaction 不依赖 reindex 之外的业务细节。
- [x] 运行 systemd 输入、事务、SIGINT、Linux adapter 与渲染测试。

### 任务 4：独立审查、无损收口与仅 WSL Git 交付

**文件：**

- 修改：本计划与 `docs/plans/roadmap-2026-07-11/README.md` 的状态。
- 不修改：外层用户 WIP 保护分支，直到用户明确允许归档删除。

**接口：** integration 头提交必须是 `d6c8dfa` 的后代，WIP 保护分支可回退；外层 `dev` 仅在干净且无远端分叉时 fast-forward；推送目标固定为 `origin HEAD:dev`。

- [x] 每个任务完成后进行独立代码审查，修复 Critical/Important 再复审；记录测试命令与结果。
- [x] 在 integration 分支运行 ruff、compileall、文件行数、`git diff --check`、所有受影响测试和完整 `python -m pytest tests -q`。
- [x] 只读执行 `reindex-queue status`、`health --mode light`，确认不会执行 init/prune；记录 owner 维护仍需人工窗口的事实。
- [x] 用临时空 `core.hooksPath` 避免旧 shared hook 入队，外层 `dev` 已 fast-forward integration；已核对提交、作者、WIP 分支与 `origin/dev` 无分叉，并完成非强制推送。
- [x] WSL 发布仅按维护手册另行执行；本次 Git push 未自动执行 owner 初始化、systemd reset 或 restart。

### 后续可靠性收口（2026-07-14）

本节记录在上述整合完成后发现的运行时并发边界，避免把 Git 交付误写成 WSL 运行态已完成。

- [x] 默认维护 marker 在 CodeGraph 转换锁内写入失败时，先收敛 CodeGraph/reindex 再失败关闭；不得把未持久化的 marker 误报为安全已证明。
- [x] CodeGraph 主 unit 布局迁移：可证明创建或 legacy 改名的叶子以真实 device/inode 校验；创建后置异常若身份无法证明，则保留 canonical 证据并报告安全状态未证明。
- [x] `restore` 补偿、`resume-codegraph` 的 mask/unmask 统一复用转换锁；锁 enter/exit 中断或被租约退出异常覆盖时不得重入维护补偿，且转换锁内必须重验维护 marker。
- [x] CodeGraph MCP 请求与后端创建改为无锁 marker 快路径；冷启动强校验移至工作线程，有界启动协调器在超时后取消并确认任务结束，禁止未就绪任务无限占用全局维护锁。
- [ ] 完成静态检查、目标回归与完整测试后，才允许合并并仅推送 `origin/dev`；禁止 GitHub/origin。
- [ ] 按 WSL 运行手册实测 native `linkat`/`renameat2` probe、布局迁移、`prepare`、owner 初始化、四项 manifest 与 `resume-codegraph`；完成前不得删除隔离 worktree 或宣称数据库已更新。
