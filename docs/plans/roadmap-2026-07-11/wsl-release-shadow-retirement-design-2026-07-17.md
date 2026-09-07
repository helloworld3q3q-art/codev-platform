# WSL 旧 release shadow 事务化退役设计

## 一、问题

当前 WSL 的受管 systemd 主 unit 可以由新 release 重新生成，但每个服务仍加载
`90-codev-release.conf`，有效 `ExecStart` 因而继续指向旧提交。单独覆盖主 unit、手工删除
drop-in 或在维护窗口批量重启，都无法同时满足并发互斥、失败补偿和运行身份复证。

另有两个必须同步消除的耦合：

- 运行时解释器属于版本固定 release，索引输入仓必须继续使用可快进的 live clone；
- systemd 受管的 platform-docs 必须使用同一 release Python，不能继续读取用户配置中的旧
  `runtime.chroma_venv`。

## 二、方案比较

### 方案 A：先删除 shadow，再执行现有安装器

实现最快，但两个命令之间存在半迁移状态，安装失败也无法把 shadow 与主 unit 作为一个整体补偿。

### 方案 B：扩展现有安装事务（采用）

复用既有全局 systemd 转换锁、SIGINT 延后、unit 原像、状态恢复和逐项复证。在首次可变动作前
冻结全部旧 shadow；同一事务内安装新主 unit、条件归档 shadow、reload、restart 并验证。失败时
恢复主 unit、shadow、启用态和活动态。迁移成功后主 unit 成为唯一真值，后续安装幂等。

### 方案 C：立即完成内容寻址 runtime 的 Task 6～10

长期能力最完整，但包含 wheel/base、`current/previous`、原子激活、回滚和状态面，范围远大于本次
旧 shadow 根因。本次不重复实现另一套激活框架，后续沿既有版本化 runtime 计划完成。

## 三、职责边界

- `mcp_systemd_install_transaction.py`：只编排安装、退役、补偿和证明顺序。
- `mcp_systemd_release_shadow.py`：固定路径推导、原像冻结、条件归档、恢复和幂等证明。
- `mcp_systemd_install_contract.py`：只声明类型化原像与四个窄端口。
- `mcp_systemd_install_systemd.py`：组合 Linux 适配器，不包含业务状态机。
- `mcp_systemd.py`：受管 systemd 生成时让四个 MCP 和其他 Python 服务统一使用当前 release Python；
  本地 `serve-mcp start` 仍保留 `runtime.chroma_venv` 兼容语义。

不新增数据库、配置键、后台线程、PID/TTL 文件或通用文件操作 manifest。

## 四、事务顺序

1. 取得全局 systemd 转换独占锁，复核 maintenance marker 不存在且 CodeGraph 未被 runtime mask。
2. 在锁内摘要绑定 manifest 和全部源 unit。
3. 在首次写入前冻结主 unit、manifest 内全部 `90-codev-release.conf`、启用态和活动态。
4. 扫描全部 `codev-*` shadow；存在 manifest 外 shadow 时零修改失败。
5. 写入全部新主 unit。
6. 按 device/inode 与完整原像条件移动 shadow 到同目录、不以 `.conf` 结尾的 root-only 归档名。
7. 执行 `daemon-reload`、enable、restart 和 owner-bootstrap 专用启动流程。
8. 证明主 unit 原像等于目标载荷、旧 shadow 不再生效、目标服务运行状态符合 manifest。
9. 任一步失败时，在同一锁内恢复主 unit和 shadow，reload 后分别恢复并复证启用态与活动态；
   任一补偿或证明失败只报告“安全状态未证明”。

## 五、运行时与索引输入

- systemd `ExecStart`、`PATH` 和实际进程解释器必须来自同一目标 release。
- `projects.<project_id>.repo_path` 必须保留为 `/home/user/project` 这类稳定 live clone；
  release 与 repo_path 不得相等或互相包含。
- reindex 的精确 SHA 仍由 live clone 快进和隔离 attempt 保证，不能把 detached release 当输入仓。
- `data.platform_data_dir` 继续作为索引、队列、日志与健康快照的稳定状态根。

## 六、兼容与清理

- shadow 不存在时事务为幂等空操作；活动 shadow 与归档同时存在时失败关闭。
- 归档保留在 root 受控目录中，不被 systemd 识别为 drop-in，也不随旧 release 删除。
- 旧 release 清理前先把 usage、服务日志和健康快照迁入 `data_root` 或审计归档；仅允许 Git 对
  已证明 inactive、无进程和 unit 引用、无未知文件的精确 worktree 执行 `worktree remove --force`。
- 当前 SHA worktree 使用 editable 安装，只称“版本固定运行目录”；不冒充完整内容寻址 release。

## 七、验收

- 单元测试覆盖 shadow 缺失、存在、已归档、双路径冲突、manifest 外残留、inode 变化和中途失败。
- 故障注入覆盖第 N 个归档、reload、enable、restart、最终证明及补偿单项失败。
- WSL 验收必须证明全部受管 Python unit 的有效 `ExecStart` 与 MainPID 均来自目标 release。
- 首次 CodeGraph 真调用建立 stdio 后端后，用真实代码提交触发普通 push；服务 InvocationID 不变、
  三类代码索引成功且无失败 manifest，才证明日常协调不是依赖重启实现。
