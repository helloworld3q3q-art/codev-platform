# daily-summary 2026-07-19 —— WSL 部署防卡死与稳定产物边界收口

> 本日完成部署、重建队列和稳定运行产物的最后一轮可靠性收口。当前结论是代码与本地/WSL
> 安全回归已通过，尚未提交、推送和切换正式 WSL 运行态；生产数据库、四库和 systemd 仍保持原状。

## 一、根因与设计结论

- 卡死不是单一超时问题，而是进程归属、恢复证明、路径所有权、并发锁和数据根解析在多个边界上
  各自缺少失败关闭。修复按职责拆成受管进程、部署状态机、稳定产物 IO、路径绑定、锁协议和证明契约，
  不在 worker 或部署控制器内继续堆过程式分支。
- 内容寻址 release、显式 baseline/rollback anchor、耐久阶段回执和补偿状态机共同保证失败不会覆盖
  当前可用运行态；systemd/cgroup 负责完整进程树，控制器只聚合窄端口。
- `PLATFORM_DATA_DIR`、受管配置和默认数据根收敛到同一优先级。普通读路径保留既有解析语义；root 权限
  迁移使用独立的词法绝对路径入口，禁止 `~`、`.`、`..`、重复分隔符、控制字符和提前 `resolve()`。

## 二、稳定产物与权限迁移

- Chroma、CodeGraph、Graph、Agent Memory、审计、健康快照、MCP/Webhook 和重建日志统一使用
  `RuntimeArtifactLayout` 与安全 IO 边界；路径定位函数无创建副作用，目录和文件分别收敛为
  `0700`、`0600`，原子写入包含短写循环和目录持久化。
- root 权限迁移只处理固定的 `logs/audit/mcp_serve_logs/platform_meta/run` 命名空间，拒绝符号链接、
  普通文件硬链接、跨设备对象、ACL、特殊文件、未知 owner、组/全局可写和超预算目录树。
- 新增 descriptor-bound 路径模块：从 `/` 逐段以 `O_DIRECTORY|O_NOFOLLOW|O_CLOEXEC` 打开，持有
  完整祖先 fd 链，发布前后复验父子链接和 inode。WSL root 竞态用例证明，服务用户在校验后把祖先
  替换为 alternate 符号链接时，迁移失败且 alternate 中 root 文件的 owner、权限和内容均不变。
- 控制面独立解析服务账号，迁移回执只接受精确七字段，并把 UID/GID 绑定到可信账号；额外字段、
  布尔 schema、错误 UID/GID 或账号解析失败均不能进入数据库迁移。

## 三、锁与服务恢复

- Chroma 冷启动改为双原生锁：launcher 只短期持有启动门，轻量 daemon 入口在导入模型前取得生命
  周期锁并持有到进程退出。父进程确认有界锁交接后才释放启动门；预热期间的新 launcher 只等待，
  失败竞争者不会加载 Chroma/Torch。launcher 或 daemon 崩溃均由内核释放对应锁，不依赖
  mtime/unlink，也不留下需要抢占的 stale 状态。
- 全仓压力测试暴露 Windows File 队列 gate 与 Chroma 启动锁的首字节初始化竞态：多个进程在取得
  byte lock 前写空文件可能触发 `PermissionError`。两处均改为取得 OS 锁后再初始化首字节，保留
  deadline、非阻塞和进程退出自动释放语义。
- 数据库迁移前再次复验部署 guard、全部稳定产物 writer 停机和受管配置；`platform-docs` 常驻服务
  已纳入 writer registry，维护窗口恢复顺序保持“安装并证明 drop-in → 停机/cgroup 证明 → 重建”。
- 四个 MCP HTTP 入口统一绑定固定 systemd unit/cgroup，在加载配置、模型或数据库前失败关闭；部署停写
  通过固定 argv、PID/starttime/cmdline 双读和 cgroup 复证扫描 `/proc`，维护前后及续跑时都拒绝
  detached 或非受管 writer，合法固定 unit 内进程不误报。
- CodeGraph 的公开错误、稳定日志和 fanout 响应分层处理，异常路径、凭据和后端细节不进入稳定日志。

## 四、systemd 门禁交接与开机可用性

- 部署不再复用可接受常态的通用 install-only 证明。guarded-stage 必须显式注入本次
  `DeploymentPlan` 绑定的总门禁与入口门禁端口，并在共享 systemd 转换锁内前后复验。
- stage 回执升级到 schema 3，固定绑定 release 运行身份、受保护 unit 摘要和
  `reindex/codegraph/webhook` 三个延迟单元。`systemctl enable` 返回成功后逐项复读实际持久启用态；
  后续恢复和验收仍会再次复证，命令成功但状态未落地不能生成可接受回执。
- 总门禁撤销前会在第二次共享转换锁内重新证明双门禁、当前 release、schema 3 回执、canonical
  unit 原像和三个 enabled 状态。两次锁之间若插入其他事务并改变任一证据，撤门禁失败关闭。
- runtime mask 与持久启用态已解耦：受管注册表声明唯一 `WantedBy` 目标，Linux 适配器验证 root-owned
  `target.wants` 链接精确指向 canonical unit。CodeGraph 报 `masked-runtime` 时仍能正确快照底层
  enabled/disabled，失败补偿不会误删原有开机启用链接。

## 五、模块化与质量门禁

- 超预算门面已按职责拆分：fd 树分为契约、校验、IO；服务访问分为契约、校验、命名空间和内容；
  CodeGraph server 分为命令、公开错误和响应适配；MCP serve 把端点目录、端口解析、命令构造和客户端
  配置生成下沉到独立 catalog，门面只保留运行时解析、探活和进程编排。各模块保持单一职责与轻量聚合，
  所有 Python 生产文件继续满足 600 行预算。
- systemd/部署扩展回归：`508 passed, 2 skipped`；最新 WSL 原生交接定向回归 `86 passed`，真实
  wants 链接和模拟 `masked-runtime` 读取均通过。
- 完整 Python 回归：`5013 passed, 278 skipped`；仅保留第三方 Starlette/httpx 弃用警告。
- 全仓 Ruff、适用变更文件格式检查、编译、文件预算和 `git diff --check` 均通过；两个旧式紧凑文件
  仅保留必要逻辑差异，未因机械格式化重新突破 600 行。
- 三项并行专项修复均由主线程复核，最终独立只读审查结论为 Ready，无 Critical、Important 或 Minor。
- 当前仅存在主工作树，仓内 `.worktrees` 目录不存在；用户已有改动未被回滚。

## 六、剩余受控步骤

1. 按提交门禁提交到 `dev`，仅推送 `origin/dev`，禁止推送 GitHub/origin。
2. 在唯一 WSL 正式环境部署精确推送 SHA，执行数据库迁移和 systemd 事务。
3. 重建 `chroma → codegraph → ingest → code_vec`，等待四项 manifest 精确绑定目标提交和运行修订。
4. 完成 MCP、Webhook、CUDA、健康面、服务身份、失败恢复和回滚证据验收后，再清理旧库与旧 release。
