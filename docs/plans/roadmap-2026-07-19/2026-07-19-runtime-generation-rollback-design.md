# WSL 运行代际部署与完整回滚设计

## 一、背景与结论

2026-07-19 对提交 `b3749e1` 的正式 WSL 部署在 `release_staged` 阶段安全失败。部署尚未进入维护窗口，未切换 systemd、配置、数据库或索引，旧服务继续运行。

失败揭示的不是单一模块缺失，而是跨版本契约边界不完整：

- 目标版本使用自己的 `MANAGED_IMPORTS` 验证基线版本，要求基线导入当时尚不存在的 `codev_platform.chroma.daemon_entry`；
- 目标版本写入 schema 3 的 systemd 阶段回执，旧基线只接受 schema 2；
- 现有 `rollback_release()` 只替换 `current` 代码引用，不能同步恢复 systemd 载荷、配置、阶段回执和四类索引；
- 现有测试主要在同一源码版本内使用替身，未真实构建目标与基线两个 wheel，也未验证跨版本切换后再完整回滚。

因此，生产部署和生产回滚的最小一致性单位必须从“代码 release”提升为“运行代际”。一个运行代际绑定代码、启动契约、systemd 载荷、配置、数据库兼容证明和索引发布身份。任何生产入口都不得只切换其中一个资源。

## 二、目标与非目标

### 2.1 目标

1. 正式部署前，证明目标代际和实际在运行的回滚代际都可独立启动。
2. 部署或回滚过程中断后，可以依据持久日志确定性续跑或补偿，不依赖进程内状态。
3. 代码、systemd、配置、阶段回执与四类索引不会以跨代际组合重新开放流量。
4. 数据库迁移只允许目标与回滚代际共同支持的向前兼容形态，不执行破坏性自动降级。
5. 首次接管遗留环境时保留一次性、可审计的兼容通道；接管完成后所有新代际使用统一契约。
6. 只维护一个 WSL 生产环境。构建和索引准备尽量在维护窗口前完成，维护窗口只执行有界切换与验收。
7. 领域契约、持久化、systemd、配置、数据库、索引和验收分别由窄模块负责，组合根只编排顺序。

### 2.2 非目标

- 不引入第二套长期运行的 WSL 服务环境，也不建设蓝绿集群。
- 不把 Git 工作树作为生产运行目录或长期回滚副本。
- 不在回滚时自动执行 Alembic downgrade 或恢复数据库备份。
- 不把密钥、DSN、环境变量正文写入普通 JSON、日志或部署证据。
- 不用宽松兼容解析器猜测未知旧协议；未知状态一律失败关闭。

## 三、方案比较与决定

### 3.1 方案 A：当前故障点兼容桥

保留旧 Chroma 入口并让 schema 3 读取器兼容 schema 2。改动最小，但只能处理已观察到的两个不兼容点；配置、索引和后续协议变化仍可能产生部分回滚。

### 3.2 方案 B：相邻版本兼容门禁

要求目标版本始终可以启动 Git 父提交，并在发布前构建两个 wheel 做兼容测试。该方案能阻止部分不兼容发布，但实际生产回滚基线不一定等于 Git 父提交，而且无法独立恢复配置和索引代际。

### 3.3 方案 C：运行代际事务（采用）

为目标与实际回滚基线分别生成自描述契约和不可变载荷，持久化完整回滚包，以写前日志编排多资源切换。相邻版本测试继续作为开发门禁，但生产回滚依据实际已验收代际，而不是推断 Git 父提交。

该方案改动面较大，但能够同时封闭本次暴露的启动入口、回执协议、配置、数据库和索引一致性缺口。

## 四、领域模型

### 4.1 `DeploymentAttempt`

每次部署或显式回滚都拥有独立尝试身份，不能再用目标 Git SHA 充当部署主键。中断恢复续用原 `attempt_id`，通过新的单调租约记录接管，不创建伪装成新部署的恢复 attempt：

- `attempt_id`：由 root 控制器使用密码学随机源生成的 128 位小写十六进制值，并通过 `O_EXCL` 首次落盘；
- `operation`：`deploy` 或 `rollback`；journal 可记录同一 attempt 内的 `recover` 与自动补偿方向；
- `plan_sha256`、`target_generation_id`、`baseline_generation_id`；
- `baseline_observation_sha256`：绑定切换前 MainPID、解释器、有效 unit 和现场代际；
- `controller_sha256`：绑定负责续跑该尝试的不可变控制器；
- `initial_lease_epoch`：记录创建尝试时取得的首个单调租约代数，仅用于审计，不要求恢复时保持不变。

默认人工调用总是创建新 `attempt_id`。人工续跑必须显式指定 `--resume <attempt_id>`；开机恢复则必须持有该 attempt 已冻结且摘要匹配的 recovery envelope，两者都要求计划、控制器和基线观察一致。恢复器取得更高 `lease_epoch` 后，必须在证明旧 owner 已消失的同一全局锁内，以 CAS 原子接管 attempt，并先向 journal 追加 `lease_transferred` 记录；所有 writer 随即拒绝旧 token。已完成尝试不可重开；对同一目标提交再次部署必须创建独立 journal、回滚包和验收证据。

生产输入的基线只能来自已验收 `serving_generation_id`，或首次接管时由遗留适配器冻结的实际 MainPID 现场。`target^` 只用于 CI 的 N-1 测试，禁止作为生产基线兜底。

### 4.2 `RuntimeGeneration`

运行代际是生产切换的唯一聚合根，只保存不可变身份，不保存任意日志：

- `generation_id`：规范 JSON 的 SHA-256；
- `revision`、`release_id`、`base_id`：代码和解释器身份；
- `entrypoint_contract_sha256`：本代启动入口契约；
- `systemd_bundle_sha256`：本代 systemd 载荷；
- `configuration_bundle_sha256`：本代配置载荷；
- `database_contract_sha256`：数据库兼容契约；
- `index_set_sha256`：四类索引发布集合；
- `kind`：`managed` 或仅首次接管允许的 `legacy_external`；
- `created_at`：审计时间，不参与运行判断。

只要任一成员身份变化，就必须生成新的 `generation_id`，不得原地修改已发布代际。

### 4.3 `RuntimeEntrypointContract`

每个受管 release 在构建时生成自己的静态启动契约，至少声明：

- Python 解释器与包根的 release 相对路径；
- 每个受管服务的模块、参数模板和必需环境键名；
- 可读取的 systemd 阶段回执 schema 范围；
- 运行时元数据 schema 范围；
- 必需的服务探针和能力标识；
- 契约 schema 与生成器版本。

契约必须由该 release 的构建输入生成并纳入 release 摘要。其外层使用长期稳定、字段极少的 envelope，只包含 schema、契约摘要和受约束的 `self_test_argv`。目标控制器只解析 envelope，不解析基线内部回执；它以无 shell 的固定 argv 调用基线不可变解释器，由基线 verifier 解析自己的 receipt、systemd 和配置，再返回稳定 proof schema。

`self_test_argv` 只能引用已验证 release 内的解释器和固定模块，拒绝绝对越界路径、附加 shell、任意环境覆盖及未知参数。双版本测试必须证明目标进程没有导入目标版 `MANAGED_IMPORTS` 或 receipt parser 去验证基线。

### 4.4 `SystemdPayloadBundle`

每个代际独立保存完整 systemd 载荷：

- 主 unit、drop-in、环境文件引用和 enablement 期望；
- 每个 `ExecStart` 使用该代际的不可变绝对 release 路径，不通过 `current` 间接解析；
- 本代能够读取的阶段回执原始载荷及其摘要；
- unit 名单、文件模式、所有者和有效载荷摘要；
- `systemctl cat/show` 预期事实。

目标与回滚代际不得共用由目标代码动态生成的同一份 systemd 载荷。release 只提供受限的声明式服务契约；root 控制器依据 root-owned 模板渲染 unit，并校验命令白名单、不可变绝对路径、服务账号、可写目录和沙箱选项。目标 release 不得以 root 身份生成或执行任意 unit 内容。

### 4.5 `ConfigurationBundle`

配置分为公开身份和受保护载荷：

- JSON 证据只保存路径角色、schema、摘要、模式和所有者，不保存值；
- 配置及环境文件正文保存在 root 所有的代际目录，目录 `0700`、文件 `0600`；
- 读写继续使用描述符约束、拒绝符号链接、原子替换与目录 `fsync`；
- 目标配置在维护窗口前完成解析和静态校验，切换时只发布已验证载荷；
- 回滚恢复基线原始字节，不用目标版本重新渲染基线配置。

### 4.6 `DatabaseCompatibilityContract`

数据库不作为可随意倒退的文件快照处理。每个代际声明：

- 支持的 Alembic revision 集合或连续区间；
- 必需表、列、索引和约束的只读指纹；
- 迁移前、允许过渡态与迁移后数据库指纹；
- 本次迁移是否属于 expand、backfill 或 contract；
- 最低兼容应用代际和禁止回滚边界。

部署前必须证明：执行目标迁移后，目标和回滚代际都能使用最终数据库形态。迁移阶段取得数据库 advisory lock 和当前 fencing token，复证迁移前指纹，以目标 release 的固定迁移入口执行单调前进，再保存迁移回执和迁移后指纹，并分别运行目标与基线真实只读探针。

数据库变化不进入逆序补偿。若迁移后无法证明基线兼容，入口保持关闭并进入 `safety_unproven`，不得尝试 downgrade。无法预先满足双兼容的变更必须拆分为“先 expand/backfill 并保持双兼容”和“回滚窗口结束后再 contract”两个独立部署。

### 4.7 `IndexGenerationSet`

四类索引作为一个发布集合：

- platform-docs / Chroma 文档库；
- CodeGraph 代码图谱；
- graph 统一图谱 ingest；
- code_vec 代码向量库。

每个索引适配器只暴露 `prepare`、`verify`、`activate`、`restore` 和 `inspect` 窄端口。不可变集合记录项目标识、目标提交、嵌入模型身份、数据 schema、后端 generation 身份及 manifest 摘要。fencing token 不进入集合摘要；它只存在于 attempt、活动选择的 CAS 条件和后端发布许可中，因此恢复旧集合不改变原 `index_set_sha256`。

文件型后端使用不可变代际目录和原子活动引用；支持命名空间的数据库后端使用 generation 键或 schema，并在单个后端事务中切换活动身份。没有确定性 `restore` 能力的后端不得声明可回滚，也不得进入正式切换。

只有四个 manifest 全部匹配同一 `IndexGenerationSet`，入口才允许开放。`pending`、旧提交、未知模型或混合 generation 均视为未验收。

### 4.8 `RollbackBundle`

回滚包绑定部署计划与切换前实际已验收代际，包含：

- 基线 `RuntimeGeneration` 及全部子契约摘要；
- 基线 systemd 与配置受保护载荷；
- 基线阶段回执原始字节与摘要；
- 允许的数据库迁移前、过渡和迁移后指纹，以及迁移后形态对基线的兼容证明；
- 基线四索引集合和可恢复位置；
- 切换前服务启用态、活动态、有效 `ExecStart` 与进程身份；
- 保留租约和清理前置条件。

回滚包位于固定生产运行根的 root-only 目录，使用规范 JSON、原子写、文件和目录 `fsync`。维护窗口前先产生只读 `BaselineObservation`；关闭写入口、取得各后端写栅栏并排空写者后，才冻结代表切换瞬间的最终回滚包。回滚包明确允许已声明的单调数据库迁移，其余基线事实发生漂移都导致零切换失败。

## 五、持久目录与真值

在 `/var/lib/codev-platform/runtime` 下增加逻辑布局：

```text
generations/<generation_id>/
  generation.json
  entrypoints.json
  systemd/
  configuration/
  database-contract.json
  index-set.json
acceptances/<attempt_id>.json
rollback-bundles/<attempt_id>/
  bundle.json
  protected/
transactions/<attempt_id>/
  intent.json
  journal.json
  recovery-envelope.json
generation-state.json
serve-permit
```

规则如下：

1. `generation-state.json` 使用一次原子替换保存 `state_version`、`mode`、`serving_generation_id`、已提交 `ServingFence` 身份、`desired_generation_id`、`rollback_generation_id`、活动 control attempt/epoch 审计字段、acceptance 摘要和维护门禁，禁止用两个独立引用拼接状态。
2. `mode` 至少包含 `steady`、`switching`、`validating`、`restricted` 和 `safety_unproven`。`serving` 表示最近已验收代际，`desired` 表示当前正准备或验证的代际；二者不得混用。
3. 每次 attempt 的 acceptance 独立保存在 `acceptances/<attempt_id>.json`，不会修改不可变 generation；`generation-state.json` 只引用当前 acceptance 摘要。`serve-permit` 是从已验收状态派生的 root-owned 最小许可，绑定 generation、稳定 serving fence、acceptance 和目标 generation state 的完整摘要。开放时先预签发绑定目标状态 B 的 permit，再 CAS 当前状态 A→B；CAS 前摘要失配，入口保持关闭。反向关闭时先撤销许可。任一崩溃窗口最多导致入口继续关闭。
4. 正常稳态保留一个 `rollback_generation_id`。首次回滚后若没有更早的已验收代际，该字段允许显式为空，状态进入 `restricted`：当前服务可运行，但不可再次回滚，也不可清理当前基线，直至下一次部署建立新回滚代际。
5. 底层 `current/previous` release 引用属于派生实现细节，必须与单一代际状态复证一致。
6. 事务 journal 是恢复顺序的唯一真值；服务状态、链接和文件仍须现场复证，不能只相信 journal。
7. 代际、回滚包和 journal 都不保存命令输出、令牌或配置正文。
8. 未完成事务、serving、desired、rollback 及其依赖对象全部获得清理租约。
9. 全局锁顺序固定为：部署状态、systemd、配置、数据库、按稳定名称排序的四索引后端、对象清理。control lease 只在新 attempt 获取或同 attempt recovery 接管时 CAS 更新；稳态 writer 校验稳定 `ServingFence`，候选 writer 校验活动 `ControlLease`，清理器校验独立 `GcLease`，三类能力不可互换。`/proc` 扫描只作为补充证据。

## 六、部署状态机

正式部署沿用单一组合根，但阶段调整为以下有界顺序：

1. **创建尝试与边界复证**：O_EXCL 预留独立 `attempt_id`，冻结 journal genesis 和不可变 recovery envelope，以 expected-absent CAS 发布 active envelope，再在全局部署锁内取得初始 `ControlLease`；确认来源只允许 `origin/dev`、工作仓精确提交、无并发维护和未决事务。
2. **基线预观察**：从已验收 serving 状态和实际 MainPID 生成 `BaselineObservation`；首次接管走遗留清单。current 链接或 `target^` 都不能覆盖现场事实。
3. **目标准备**：在隔离候选路径构建目标 release、声明式服务契约、受保护配置和四索引候选；所有大文件准备在维护窗口前完成，不修改 serving 数据。
4. **契约与兼容门禁**：调用目标与基线各自 verifier，验证入口、回执和 systemd 能力；静态证明目标数据库迁移后的形态应同时兼容两代。
5. **持久恢复意图**：在首次控制面变更前 `fsync` intent、控制器摘要、基线预观察、入口原状态、资源清单和补偿顺序；把代际状态置为 `switching`，但 serving 仍指向基线。
6. **关闭入口与写栅栏**：先撤销 `serve-permit`，关闭 webhook/MCP 写入口，进入维护门禁；让数据库和四索引 writer 接受新 fencing token，拒绝旧 token。
7. **进程静默**：停止并 mask 受管 unit，等待 cgroup 清空，扫描 `/proc` 排除未受管写者。入口门禁与 unit mask 是两个独立 journal 动作。
8. **最终冻结基线**：在写者静默后复核预观察，创建并 `fsync` 完整 `RollbackBundle`；若配置、systemd、索引或未声明数据库事实漂移，零切换失败。
9. **发布目标配置**：安装已验证配置载荷，逐文件记录原身份、目标身份和补偿证据。
10. **数据库单调迁移**：取得数据库 advisory lock，复证迁移前指纹；以目标 release 的固定入口执行 expand/backfill，保存迁移回执和迁移后指纹，再分别执行目标与基线真实探针。数据库不进入逆序补偿。
11. **安装目标 systemd 载荷**：root 控制器从受限契约渲染并发布目标 unit 与阶段回执，逐项持久化动作。
12. **切换代码与索引身份**：激活目标 release 和四索引候选，逐后端核对 generation 与 fencing token。
13. **进入目标验证态**：原子更新单一代际状态为 `validating`，`desired` 指向目标、`serving` 仍保留基线；底层 release 引用必须一致。
14. **恢复内部服务**：执行 `daemon-reload`，在入口和维护门禁仍关闭的条件下，按目标 bundle 精确 unmask、恢复 enablement 并分阶段启动。服务探针以目标服务账号和既有沙箱运行。
15. **完整验收**：验证有效 unit、MainPID 解释器、CUDA、Web、Webhook、MCP 真调用，以及四个 manifest 的项目、提交、模型、schema、generation 和 fencing token。
16. **提交 serving 决策**：先为目标签发仅用于身份探针的 provisional `ServingFence` 并持久化不可变 acceptance 证据，再原子更新代际状态为目标 `serving`、基线 `rollback` 和 `steady`；维护门禁仍保持关闭。
17. **开放入口并完成交接**：先纯计算维护门禁关闭后的目标状态 B，并把绑定 B 完整摘要与 acceptance 的 staged `serve-permit` 写入活动许可路径；此时当前状态仍为 A，摘要不匹配使入口 fail closed。复证入口后单次 CAS A→B，使 state/acceptance/permit 同时一致并让已提交 `ServingFence` 获得稳态发布资格；随后把 journal 标记为完成，CAS 退休当前 `ControlLease`，最后清 active envelope。

第 13 步只是目标验证点，第 16 步才是 serving 逻辑提交点。提交前后只要没有有效 `serve-permit`，外部入口都保持关闭；第 16 步后的崩溃可由恢复器依据 acceptance 确定性补发许可或重新关闭复验。只有第 17 步成功才对外宣告部署完成。

## 七、正式回滚状态机

新增正式回滚用例，生产入口不得直接调用只切换 release 的低层回滚：

1. 取得同一全局锁和新 fencing token；自动补偿在原 attempt 内切换方向，显式回滚创建新的 `attempt_id` 并引用原回滚包。
2. 验证回滚包、基线代际及全部保留对象摘要，使用基线 verifier 确认单调迁移后的数据库仍兼容基线。
3. 在首次变更前持久化恢复 envelope 与 journal，撤销 `serve-permit`，进入维护，停止并 mask unit，证明 cgroup 和未受管写者为空。
4. 恢复基线配置、阶段回执与 root 渲染的 systemd 载荷；数据库保持迁移后形态，不执行 downgrade。
5. 恢复基线索引集合和基线 release，逐后端复证 generation 与 fencing token。
6. 原子把代际状态置为 `validating`，`desired` 指向基线、`serving` 仍记录最近一次已验收代际。
7. `daemon-reload`，在入口仍关闭时按基线 bundle 精确 unmask、恢复 enablement 并启动内部服务。
8. 使用基线自己的 verifier 验证解释器、有效 unit、数据库只读探针、四索引 manifest 和 MCP 真调用。
9. 先写回滚 acceptance，再原子提交基线为 `serving`。失败目标只保留审计租约，不自动成为 rollback；没有更早已验收代际时 rollback 为空且状态为 `restricted`。
10. 预签发绑定维护门禁关闭后目标状态摘要的新 `serve-permit`，复证入口并以单次 state CAS 原子关闭维护门禁，最后依次提交 journal、退休当前 `ControlLease`、清 active envelope。

任一步失败时维持入口关闭、服务停止或已证明的安全子集，并把状态标记为 `safety_unproven`。数据库发生允许的单调迁移后，只有真实证明基线仍兼容才可继续回滚；系统不得在无法证明一致性时尝试“尽量启动”。

现有 `rollback_release()` 保留为代际事务内部的窄端口；面向生产根的 CLI 必须要求有效事务上下文，禁止单独执行指针回滚。

## 八、中断恢复与失败语义

事务采用写前日志与幂等步骤：

- 每一步保存期望原身份、目标身份、完成证据和补偿动作；
- 重启后先比较现场事实与 journal，只允许“尚未执行”“已完整执行”或“可证明由本事务执行”三种状态；
- 发现第三方修改、摘要漂移、未知文件或未知进程时停止自动恢复；
- 第 16 步 serving 提交点前失败时优先恢复基线；提交点后若许可缺失，入口保持关闭并复验已提交目标，复验失败才进入正式回滚；
- `KeyboardInterrupt`、SIGTERM、异常和进程崩溃走同一恢复协议；
- 补偿自身失败时不覆盖原始错误，只记录安全状态未证明和最后可证边界。

所有文件切换继续使用临时文件、`os.replace`、文件 `fsync` 和父目录 `fsync`。systemd 多文件切换通过原像、条件写入、`daemon-reload` 和最终有效载荷证明实现可补偿事务。

为避免由目标或基线任一业务 release 解析另一版本的 journal，恢复入口位于 release 外：

1. root 安装一个职责极小、稳定 schema 的恢复 launcher；它只读取固定外层 `recovery-envelope.json`，验证 attempt、不可变控制器路径、控制器摘要和固定 argv，然后无 shell `exec` 被该事务绑定的控制器。
2. inner journal 只由 intent 中冻结的控制器版本解析。launcher 不理解业务阶段，也不调用活动 release 的 receipt parser。
3. `codev-runtime-recovery-barrier.service` 在 WSL 启动时先于全部业务 unit 等待恢复收敛，`codev-runtime-recovery-worker.service` 可由 path/OnFailure 重复触发；业务 unit 通过 `Requires/After` 和 root-owned `ExecCondition` 共同要求恢复边界已经完成。
4. 未完成事务只能由恢复服务推进到完整目标、完整基线或 `safety_unproven`。恢复失败时业务 target 不会到达，入口许可保持不存在。
5. launcher 自身更新也使用 root 原像与摘要事务，并至少保留能够解析当前全部未完成 envelope 的版本；存在未完成事务时禁止替换其所需 launcher。

每个持久化动作必须测试三个不确定窗口：写前意图已提交但资源未变更、资源已变更但完成 journal 未提交、完成 journal 已提交但下一步尚未开始。无论在哪个窗口终止，现场最终只能收敛到完整目标、完整基线或入口关闭的 `safety_unproven`。

## 九、首次遗留环境接管

当前运行环境早于代际契约，允许执行一次 `LegacyGenerationAdapter`，但策略与观察必须来自两个独立步骤：

1. 先运行纯只读 audit，生成不含秘密正文的候选事实；管理员在部署 attempt 之外显式审定并冻结 root-only `LegacyTakeoverPolicy`。policy 精确列出允许的 revision、解释器路径、unit 集合、receipt schema、配置角色摘要和四索引身份，其摘要进入后续部署计划。policy 不得在执行接管的同一 attempt 内自动生成并批准。
2. 接管 attempt 再由 root-only 预检从实时现场生成单次消费的 `LegacyTakeoverManifest`；manifest 精确绑定 revision、解释器、MainPID、unit/config/receipt 原始摘要和四索引身份，并与已审定 policy 逐字段比较，禁止用现场观察为自己生成允许清单。

3. 从实际 MainPID、解释器、`systemctl cat/show`、配置文件 inode/摘要、阶段回执和四索引 manifest 建立现场快照。
4. 要求运行 revision、绝对解释器路径、unit 集合和摘要全部精确匹配 policy；不做模糊猜测。
5. 遗留解释器目录标记为 `legacy_external` 并取得保留租约，不复制或伪装为可重定位 venv。
6. 使用遗留解释器逐服务执行实际导入和只读启动探针，由新控制器生成明确标记为 adapter 产物的遗留契约；不得声称契约来自旧 wheel。
7. 把原始 systemd、配置和阶段回执字节纳入 root-only 回滚包。
8. manifest 只能绑定一个首次接管 attempt；policy/plan 摘要不一致、重复消费或任一事实不能证明时，在维护窗口前失败，不修改现场。

policy 与 manifest 匹配后，适配器只做一次无服务变更的引导：原子创建以 legacy 为 serving 的初始 `generation-state.json`。若状态文件已经存在，只允许完全相同的 legacy 身份幂等通过，未知或不同身份一律拒绝覆盖。

首次新代际验收后，遗留适配器立即退出新部署路径；后续部署只接受 `managed` 代际，但遗留目录仍作为回滚代际保留。只有出现第二个不同的已验收 managed 代际，并完成“managed-B → managed-A → managed-B”回滚演练后，legacy 才不再承担回滚职责。此时还必须证明不存在 serving/desired/rollback/事务租约、无进程和 systemd 引用且审计归档完成，才可删除遗留目录。

## 十、模块边界

实现按单一职责拆分，避免把多资源逻辑继续堆入现有组合根：

- 代际领域契约：只定义不可变模型、schema 和状态转换；
- 尝试与恢复 envelope：只定义 attempt 身份、显式续跑和稳定外层协议；
- 入口契约：只负责生成、读取和验证 release 自描述能力；
- 代际存储：只负责受信目录、原子持久化、锁和租约；
- systemd bundle：只负责载荷冻结、安装、恢复和有效态证明；
- 配置 bundle：只负责受保护载荷及其发布/恢复；
- 数据库兼容：只做只读能力判断和迁移门禁；
- 索引集合：通过端口聚合四个后端，不包含各后端实现细节；
- 回滚包：只负责冻结和复验基线事实；
- 部署/回滚事务：只编排窄端口、journal 和补偿顺序；
- 恢复 launcher：只校验固定 envelope 并执行摘要绑定控制器，不理解业务 journal；
- 验收：只消费代际契约和现场只读事实，不执行修复。

组合根使用显式 dataclass 端口注入，不使用全局可变注册表。领域层不导入 systemd、数据库驱动或具体索引后端。

## 十一、测试设计

### 11.1 领域与持久化测试

- 规范序列化、摘要稳定、未知 schema、身份漂移和非法路径；
- 同一 target SHA 两次部署产生两个 attempt、两份 journal/acceptance，只有显式 attempt 才能续跑；
- 单一代际状态原子替换、serving/desired/rollback 不变量、fencing、崩溃恢复、锁顺序、租约与清理门禁；
- 回滚包缺项、内容篡改、权限错误、符号链接和 inode 替换；
- 状态机每个阶段的合法前缀、重复执行与非法跳转。

### 11.2 契约与适配器测试

- 目标和基线使用各自的模块列表与回执 schema；
- schema 2 基线回执可原字节恢复，schema 3 目标回执不泄漏给基线；
- 目标控制器不导入目标版模块列表或 parser 验证基线，基线 self verifier 独立返回稳定 proof；
- managed 与 legacy 契约严格区分；LegacyTakeoverPolicy 必须由先前 audit 独立审定，LegacyTakeoverManifest 单次消费，policy、计划或现场摘要不匹配时零修改失败；
- 数据库 expand/backfill 允许双兼容，contract 迁移在回滚窗口内被拒绝。

### 11.3 双真实版本测试

常规门禁必须从两个均已支持自描述契约的真实 Git revision 分别构建 wheel 和 release，不再用当前源码为两边生成同构模块：

1. 构建目标 revision 和父 revision 的 wheel；
2. 用各自解释器读取各自入口契约并启动探针；
3. 安装目标 systemd/config/receipt，完成目标切换与验收；
4. 使用已冻结基线 bundle 完整回滚；
5. 证明代码、有效 unit、配置摘要、回执、数据库兼容和四索引身份均恢复为基线代际。

Git 父 revision 测试是持续集成的 N-1 门禁；正式 WSL 部署还必须对实际活动回滚代际重复同一契约验证。

首次 bootstrap 单独使用历史兼容测试：真实构建缺少新契约的历史 wheel，保留 schema 2 receipt 和缺少 `daemon_entry` 的事实，通过单次 LegacyTakeoverManifest 生成 adapter 契约并完成切换/回滚。只有父子两个 revision 都声明 managed 契约后，才进入上述常规 N-1 路径；不得把历史版本伪装成自带新契约。

### 11.4 故障注入

以下每一种持久化动作都在“意图后/资源变更后 journal 前/journal 后下一步前”三个窗口逐点注入失败，并统一断言入口始终关闭、最终状态只能是完整目标、完整基线或 `safety_unproven`：

- 回滚包写入与目录 `fsync`；
- 第 N 个配置或 unit 文件替换；
- `daemon-reload`、enable、mask、stop、start；
- release、单一代际状态和四索引逐后端的激活/恢复；
- 数据库迁移前、迁移提交后与双代际探针之间的进程中断；
- CUDA、Web、Webhook、MCP 或四索引验收失败；
- 自动补偿中再次失败；
- 稳定 launcher 与摘要绑定控制器之间的中断；
- 真实子进程 SIGKILL，以及 WSL 在 serving 提交点前后分别终止并由 recovery service 恢复。

### 11.5 WSL 端到端验收

- 只对单一现有 WSL 环境执行；
- `HEAD` 与 `origin/dev` 精确一致，`origin` 不发生变化；
- 全部受管 unit 的有效 `ExecStart` 和 MainPID 均属于 serving 代际；
- `torch.cuda.is_available()` 为真且 GPU 数量符合预期；
- 四类索引 manifest 均为目标提交且无 pending/failed；
- platform-docs、graph、agent-memory 和 CodeGraph 完成真实 MCP 初始化及只读调用；
- 首次完成 `legacy → managed-A → legacy → managed-A`，证明同一目标再次部署使用独立 attempt；此时 legacy 仍保留；
- 再以不同 revision 完成 `managed-A → managed-B → managed-A → managed-B`，使 managed-A 成为可靠回滚基线；
- 上述演练及保留窗口通过后，才允许清理 legacy、失败候选和超出策略的旧代际。

## 十二、可观测性与安全

- 状态面显示 attempt ID、serving/desired/rollback 代际、mode、fencing epoch、事务阶段、四索引身份和最近验收结果；
- 错误信息只包含稳定错误码和资源角色，不回显命令、配置正文、DSN 或 token；
- root 写入 systemd、代际和回滚包，服务账号只获得运行必需的只读权限；
- 所有外部命令使用固定 argv，拒绝 shell 拼接；
- root 只从受限 schema 和 root-owned 模板渲染 unit；目标/基线 verifier 与健康探针均降权到对应服务账号并应用既有沙箱；
- 载荷摘要、权限、所有者、有效 unit 和实际进程身份均进入验收证据；
- 保留审计证据但限制大小，原始长日志继续进入既有日志目录，不塞入领域回执。

## 十三、清理与保留策略

1. 正常 `steady` 状态至少保留 serving 和一个已验收 rollback；首次回滚后的 `restricted` 状态允许 rollback 为空，但禁止清理 serving，直至下一次部署建立回滚代际。
2. 未完成事务和失败目标在根因审计完成前保留；它们不能成为 serving 或 rollback。
3. legacy 只有在第二个不同 managed 代际完成回滚/再部署演练并度过保留窗口后才能释放租约。
4. 清理器取得固定顺序的独占锁和当前 fencing token，先为精确对象写删除墓碑，再复证 serving/desired/rollback、journal、进程、systemd 和索引后端租约；墓碑存在后激活器必须拒绝对象。
5. GC 检查与删除保持在同一栅栏保护内，任何未知引用、token 漂移或现场变化都撤销墓碑并停止，不删除对象。
6. `.worktrees` 不作为保留机制；确认无 Git worktree 后目录应不存在。
7. 旧数据库或索引只有在不属于 serving/desired/rollback 集合、已完成备份或审计要求且无读写者时才允许精确删除。

## 十四、实施顺序

1. 先增加 attempt、代际状态、入口 envelope、fencing、存储和回滚包的纯领域能力及单元测试。
2. 增加稳定恢复 launcher/recovery service，以及 systemd/config/database/index 窄适配器和故障注入测试。
3. 将现有正式部署组合根迁移到代际事务，不删除底层 release 能力。
4. 增加正式回滚入口并封闭生产根上的低层指针回滚。
5. 增加历史 bootstrap、常规双 revision wheel、真实 SIGKILL 与 WSL 中断恢复测试。
6. 用遗留适配器冻结当前实际运行环境，完成 managed-A 的部署、回滚和独立 attempt 再部署。
7. 部署不同 revision 的 managed-B，完成回滚 managed-A 和再次部署 managed-B。
8. 完成服务、CUDA、四索引和 MCP 验收并度过保留窗口后，再按 fencing/墓碑协议清理 legacy 与失败候选。

## 十五、最终验收标准

只有同时满足以下条件才可宣告问题彻底解决：

- 不兼容的入口、回执、数据库或索引契约会在维护窗口前失败；
- 任一切换步骤失败时入口保持关闭，且能完整恢复实际基线代际；
- 自动恢复无法证明安全时明确进入 `safety_unproven`，不会启动混合服务；
- 双真实版本与全部关键故障注入测试通过；
- 单一 WSL 环境完成 legacy/managed-A 和 managed-A/managed-B 两组“部署、完整回滚、独立 attempt 再部署”演练；
- GPU、systemd、数据库、四索引和 MCP 现场证据全部指向同一 serving 代际；
- `origin/dev` 与部署提交一致，未推送 `origin`；
- 清理后只保留策略要求的代际，不存在多套工作树或无主旧库。

## 十六、实施前最终安全评审补充

本节来自实施计划的架构、安全和测试三方复核。它细化并收紧前述设计；与前文存在歧义时，以本节为准。

1. **release 与 base 独立归属代际。** managed-A、managed-B 和 legacy 可以使用不同的 `base_id` 或外部解释器。baseline 必须从 serving generation/rollback bundle 装载自己的 release/base，不得用目标 requirements 重建；legacy 恢复不要求进入 managed `current/previous` 链。
2. **入口契约采用稳定 envelope + opaque contract。** controller 只解析稳定的 `RuntimeEntrypointEnvelope` 和 `RuntimeEntrypointProof`；完整 contract 正文由对应 release 自己解析。envelope 只包含 protocol 范围、contract 摘要和受限 self-test argv，不把目标 contract schema 强加给基线。
3. **事务控制 lease 与 serving fence 分域。** `ControlLeaseStore` 在部署锁内 CAS 保存当前 attempt/epoch/token 摘要/owner/status，recovery 可以在同一活动 attempt 上提升其 epoch；`ServingFence` 在一次已验收 serving 决策内稳定，绑定 state/acceptance/permit 和稳态 writer，下一次 deploy/rollback 提交时才换新。摘要只用于审计，不是 bearer credential；控制面变更必须持有不可伪造的 `ControlLeaseProof` 并在同一锁/事务内复验，稳态发布必须持有 `ServingFenceProof`。原始能力只经继承 FD、systemd credential 或受 peer credential 保护的本地 socket 传递。
4. **运行中入口也必须持续 fail closed。** `ExecCondition` 只是启动期第二道门禁。Web、MCP、Webhook、SSE/WebSocket 和网关的每个新请求或连接都通过共享 runtime gate 原子读取 state/acceptance/permit；撤销 permit 同时失效缓存、摘除外部 route/listener、排空已有连接并复证不可达。
5. **数据库兼容需要结构化契约和真实旧版语义探针。** 契约列出必需表、列、约束、迁移类型、过渡/最终指纹和允许后继；正式生产前在快照/隔离克隆上运行目标 migration，并用真实 target/baseline wheel 执行只读、回滚所需 CRUD、队列和图谱 smoke probe。应用进程没有 DDL 凭据且禁止启动时自迁移；backfill 按 attempt 幂等、可检查点续跑；存在 rollback 租约时拒绝 contract migration。
6. **PostgreSQL graph 不修改 legacy 五表语义。** 新 managed generation 写入 legacy 不可见的 shadow schema/table set，并用独立 active-generation pointer 切换；legacy 五表和旧 wheel 查询保持不变。shadow schema 是纯 additive、经真实 baseline 探针证明可忽略的准备步骤；候选数据存在时旧 wheel 仍只能看到 legacy 数据。
7. **systemd receipt 在最终载荷之后构造。** root 先从受限入口声明渲染 unit/drop-in，再以最终 payload 摘要生成 stage receipt，最后冻结完整 bundle。旧 baseline 的 raw receipt 只从 rollback bundle 原样恢复，不重新渲染。
8. **信任根位于 release 外。** legacy policy 由稳定 root 管理工具 O_EXCL 创建，绑定精确批准事实、nonce、过期时间和单次消费状态；最终 deployment plan 单向引用 policy SHA，policy 不回指 plan；`approved_by` 只作显示。recovery launcher 仅执行 root-owned controller trust manifest 中的不可变 controller tree，使用固定解释器 `-I -s`、清洁环境、固定 cwd 和关闭后的非标准 FD。目标、基线与 legacy verifier 均以服务 UID 和只读凭据在 systemd 沙箱运行。
9. **秘密与身份分离。** generation/证据不保存秘密正文的直接 SHA-256；使用 root-keyed HMAC 或加密载荷身份。秘密优先通过 systemd credential 或受保护 FD 注入；回滚前复验凭据版本，已撤销凭据不恢复。
10. **恢复和清理由稳定控制面负责。** attempt reservation 后先冻结 journal genesis 和不可变 recovery envelope；envelope 只绑定 genesis/store/controller identity，不绑定变化的 journal head 或 control epoch，并以 expected-absent CAS 发布；随后才获取初始 control lease。`recover/resume` 只接受该 envelope 指向的 attempt。开机 barrier 与可重复启动的 recovery worker 分离，生产 deploy 运行于带 `OnFailure=` 的 systemd controller unit。终态必须先持久化 complete journal，再把当前 control lease 以 CAS 退休为绑定终态 journal/evidence 的 tombstone，最后清 active envelope；退休立即使旧 proof 失效。`retire_current()` 与 recovery `take_over()` 竞争同一 active record 时只能有一个 CAS 成功：retire 胜出后 recovery 只能清 envelope；takeover 胜出后只有新 epoch proof 能退休。若在退休后、清 envelope 前崩溃，recovery 只能凭精确 tombstone 和完整终态证据幂等清 envelope，不能重放资源动作；新 attempt 仅能在 active envelope 不存在且前一 lease 已退休时 CAS 获取新 lease。GC 使用独立 `GcLease`，在共享全局互斥下只授权未引用对象的 quarantine/delete，不改变 serving fence；执行清单绑定管理员确认摘要，漂移即拒绝。
11. **真实版本与 WSL 演练走生产路径。** 正式 WSL 目标固定 Linux CPython 3.12 x86_64。双 revision fixture 在 WSL 内通过 `runtime_candidate.build_candidate()` 和 `runtime_build.stage_release()` 构建各自 base/release；Windows 宿主 harness 使用 `wsl --terminate <发行版>` 注入整机中断并重新拉起验收，不使用影响其他发行版的 `wsl --shutdown`。演练入口安装在 release 外稳定路径，不依赖 `current/.venv`。
12. **最终 MCP 验收不得豁免 401。** platform-docs、graph、agent-memory 和 codegraph 必须使用实际部署的鉴权配置完成 initialize 和至少一次只读调用；401 只能作为失败原因报告，不能视为代际部署成功。
13. **provisional serving fence 不等于发布授权。** 目标进程在 `validating` 阶段可通过受保护 credential 获得 provisional `ServingFenceProof` 以完成身份探针，但在 `commit_serving` 成功且维护门禁关闭前不能写生产命名空间。每次稳态发布必须在覆盖整个发布临界区的同一锁或数据库事务内复验：state mode 为 `steady` 或策略允许的 `restricted`、generation 与当前 serving generation 相同、proof 精确匹配 state 中已提交的 serving fence、维护门禁已关闭。`switching` / `validating` 一律拒绝所有 serving proof；候选写入只能使用 `ControlLeaseProof` 和 attempt 隔离命名空间。
14. **切换开始即撤销旧稳态写能力。** `begin_switch` 的状态 CAS 是旧 serving writer 的同步失效点；即使旧进程仍持有 token、permit 缓存或已 claim 的任务，后续 publication/ack 也必须在临界区复验时失败。publication verifier 从前置复验到 pointer/manifest/ack 完成始终持有与 `begin_switch` 相同的状态锁或数据库事务锁，因此二者严格串行：要么完整发布先完成再进入 switching，要么切换先完成且发布被拒绝，不存在检查后切换、切换后落盘的 TOCTOU 窗口。新的 serving commit 保持维护门禁关闭；控制器先纯计算门禁关闭后的目标状态 B，再预签发绑定 B 完整摘要的 staged permit。CAS 前 permit 与当前状态 A 摘要不匹配，gate 必须关闭；CAS A→B 后 state/acceptance/permit 才同时一致并自动开放。CAS 失败或此前崩溃时不得把 staged permit 当作有效许可。
