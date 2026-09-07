# roadmap-2026-07-19

> 本目录是运行代际部署、完整回滚、资源适配器与真实 WSL 验收的唯一任务真值源。
>
> **2026-07-25 收口状态：** Task 7 已完成。`code_vec` 写端释放修复已在 `ee39226` 验收；四项索引均已由当前 release 成功生成，`target_commit` 与 `runtime_revision` 精确一致。官方 CodeGraph 恢复、四个 MCP endpoint、串行 worker 与空队列均已复核通过。全过程只使用增量队列，不重建数据库、索引、base 或依赖，也未推送 GitHub。

| 文件 | 状态 | 说明 |
|---|---|---|
| `2026-07-19-runtime-generation-rollback-design.md` | 已评审 | 总体设计与安全不变量 |
| `2026-07-19-runtime-generation-rollback.md` | 进行中 | 29 项总实施计划 |
| `2026-07-19-runtime-generation-foundation.md` | 进行中 | Foundation Task 1–7 |
| `2026-07-19-runtime-generation-adapters.md` | 待执行 | Adapters Task 1–9 |
| `2026-07-19-runtime-generation-integration.md` | 待执行 | Integration/WSL Task 1–13 |
| `runtime-generation-progress-2026-07-20.md` | 已更新 | Foundation Task 4 / 任务 1–7 的完成证据、收口边界和后续起点 |
| `runtime-generation-foundation-task-3-completion-report-2026-07-20.md` | 已完成 | Task 3 纯领域实现、回修与终审证据 |
| `2026-07-21-runtime-generation-store-task-4.md` | 已完成（Task 1–7） | 已完成受控 transaction records、terminal evidence、精确 tombstone 清理、generation fence→acceptance→target-B permit→state CAS、control lineage scope、私有 deployment-lock capability、rollback bundle 生产装配与公共输入边界映射，以及 Task 7 的跨域恢复、descriptor-bound 隔离、用户 systemd 瞬时 cgroup、`ExitType=main`/SIGKILL 收口、pidfd 父死亡真值、真实 WSL `setsid()` 生命周期验收和 `code_vec` 跨进程可见性修复。最终当前 release、四项 manifest、队列、worker、官方 CodeGraph 恢复与四个 MCP endpoint 已全部通过；日常 push 继续只走 WSL 增量队列，未经用户明确要求不得重建。|

## 最新运行态收口（2026-07-23）

`9328507` 已仅推送 `origin/dev` 并完成官方 CodeGraph 恢复；worker 正常、既有队列已消费、`codegraph` 与 `ingest`
manifest 已对齐。最终仅 `chroma`、`code_vec` 因旧 vector build 的 compaction/segment I/O 错误未验收。计划已确认它们会由
既有普通增量队列自动创建 side-build 并原子发布，故只重入这两个 scope，不重建健康库、runtime 或依赖；完成条件仍是四类
manifest 同时对齐目标提交和 runtime。

Chroma 已经由该受控重入恢复；code_vec 的第二次 side-build 暴露了独立的 Chroma 1.5.9 HNSW 默认阈值问题：固定 64 条
checkpoint 写入在第 2 次跨过默认 100 条 compaction 门槛。已用同版本临时目录完成 6,521 条 / 102 次写入的复现与安全阈值验证，
并已实现版本化 HNSW `batch_size`/`sync_threshold=50000` 配置和旧 checkpoint 的 side-build 迁移门禁；本地回归为
`258 passed, 1 skipped`。

当前 worker 由 immutable release 加载，单纯 push 不会使该修复生效。最终收口只允许一次复用现有 base 的薄应用发布：本地 wheel
不解析/安装依赖，release 安装固定离线且无依赖；当前 release 作为原子回滚锚点。发布后按正常队列更新四类 manifest，只有
code_vec 会因策略升级进行一次 side-build，其余均为普通增量。禁止重新构建 base、下载依赖、手工删除数据库或裸启停服务。

thin release 的候选输入也已完成权限边界复核：root-fd worker 保持 `NoNewPrivileges`，因此不能读取服务账号 `0700` 候选目录；
后续候选只由 root controller 写入 root `0700` 目录，服务账号候选保留审计但不再作为发布输入。此处理不放宽目录权限，也不影响
base、依赖或数据库。

为让 root controller 能在不放宽 Git 信任的前提下读取服务账号源码，`runtime build` 已新增显式 `--source-user`：仅 Linux root
在确认账号拥有安全的非链接工作树后，以固定 `runuser` 执行本地精确 Git 读取；不会写全局 `safe.directory` 或改变服务仓权限。
候选 wheel 也固定 `--no-index --no-deps --no-build-isolation`。相关扩展验证为 `100 passed, 27 skipped`；下一步仅用该受控桥构建
一次 root-owned thin release，再恢复正常增量队列。

## 当前收口状态（2026-07-22）

`4aa3923` 已完成配置修复、提交、仅推送 `origin/dev` 和 controller 同源同步；`configure-resume-codegraph` 已成功。随后 restore 成功但 worker 以 exit 1 退出，8 项增量待办未消费。无秘密摘要对比已证明恢复快照与 controller/`runtime/current` 一致，而 `codev-reindex.service` 仍绑定更早的 immutable release，造成默认配置摘要漂移。

进一步只读诊断发现，现有 `runtime/current` 的 base/release 尚未完成服务访问投影：所有对象仍为 root 组，且仅有
18 个历史目录是 `0755` 而策略要求 `0750`。内容、所有者、ACL/xattr、设备边界与写权限均正常；这是权限投影漂移，
不是数据库、CUDA、配置或索引损坏。现有 manifest 生成正确地 fail-closed，且没有可用的已完成对象修复入口。当前按
Task 7 原计划新增一个仅修复 current schema 3 对的 root-only、可重试访问收敛事务：先严格预检和有限 mode 收敛，
再复用既有服务组发布/目标用户探针，绝不 rebuild、下载依赖、修改数据库/索引、切换 release 或手工 chmod/chown。
访问证明完成后，才生成 manifest、maintenance-stage 安装与恢复增量 worker。

真实 dry-run 还发现 current base 留有 56 个历史 root 字节码缓存：它们均为已登记源文件对应的
`__pycache__/*.pyc`，没有缺失登记文件；现有静态 inventory 因此正确 fail-closed。Task 7 已把范围收敛为
受控删除这类“全量证明的生成缓存”并防止修复入口重新写入 pyc，仍不 rebuild runtime、不下载依赖、不修改数据库或索引。

随后同一 dry-run 证明 release 还存在 151 个同类 root 生成缓存，wheel 静态载荷 verifier 因而正确拒绝继续。两类缓存都将
通过同一 fd-relative 机械核心清理，但分别复用 base distribution inventory 与 release wheel verifier 作为唯一真值；仍只限
current 精确对象对、维护许可 `--yes`，不扩大到历史 release、数据库、队列或索引。

共享清理核心、base/release 窄适配器与 dry-run 已完成：187 项相关回归通过，WSL 临时目录 root-fd 删除与中断重试通过，
正式 current 的 dry-run 也已通过且仍为零写。修复已提交为 `3e96c7672ed354a21f80b8f418b25c2fa997771b` 并仅推送
`origin/dev`；服务仓与 root controller 已同 SHA 同源同步，已部署 controller 的 dry-run 同样通过。随后只读维护检查发现
`/etc/systemd/system/codev-reindex.service.d/10-codev-reindex-maintenance.conf` 缺失、父目录可信；下一步仅通过既有
`reindex-maintenance prepare --yes` 原子创建并验证 drop-in、进入维护许可，再执行一次访问修复 `--yes`，继续原有增量队列恢复。

**2026-07-22 访问修复首次执行的编排根因：** 维护窗口、drop-in 和 dry-run 均已证明正常；首次 `--yes` 已安全完成
current base/release 的缓存清理、对象访问收敛及严格内容复验，但父命名空间仍未发布。阶段追踪证明失败发生在
`converge_runtime_service_namespace()` 入口：`_publish_service_access()` 错把 `base_ids`、`release_ids` 与服务身份一起
传给仅接收 `service_uid`、`service_gid` 的函数，触发多余关键字参数的 fail-closed 拒绝。因此不是 WSL 权限、ACL、
文件系统、数据库或索引故障。下一步先以严格函数签名补 RED 回归，最小拆分命名空间/内容对象参数并完成定向验证；之后只用
既有 maintenance 许可重试一次，不重建 runtime、索引或数据库。

该回修已完成 TDD：新增严格签名回归先得到预期 RED，随后 `_publish_service_access()` 将命名空间身份参数与内容对象参数
分离，GREEN 后 `160 passed, 17 skipped`，Ruff、格式、编译与 diff 检查均通过。当前仅等待提交、只推送 `origin/dev` 并把
controller 同源同步；维护窗口继续保持，不启动 worker。

`5641609` 已完成上述回修的提交、仅推送 `origin/dev` 和 controller 同源同步。官方访问修复已完成严格服务访问证明，唯一剩余
门禁是目标用户探针：真实受限进程导入 `setuptools` 时会在 app/base 主路径之后追加一个 root 只读的 base `_vendor` 目录，旧协议把
这个安全导入副作用误判为路径注入。下一步以 Linux root 真实 RED/GREEN 把“路径完全不变”替换为更严格的窄白名单：只接受该类
base 内、不可写、后置且不含 `codev_platform`/`torch` 的新增路径；外部、可写、抢占优先级或关键包影子路径仍 fail-closed。维护态
继续保持；不重建 runtime、数据库或索引，探针通过后才继续既有 manifest 和增量队列。

**2026-07-23 访问修复已通过：** 最新 controller 已在 maintenance 许可内完成一次官方访问修复，退出 0 且最终状态为 `ok`；
maintenance status、访问修复 dry-run 与真实服务用户 probe 复验全部成功。运行时未重建，worker 仍由 maintenance 门禁保护，
数据库和索引尚未被重新构建或手工修改。下一步只执行既有受控链：生成绑定 `runtime/current` 的 manifest → maintenance-stage
安装及 payload 证明 → configure → restore → 既有增量队列 → `resume-codegraph`；任一失败保持维护态。

**当前阻塞（已定位）：** manifest 静态审计通过，但 maintenance-stage 在首次写入前拒绝，因为 Webhook 仍活动；这暴露了
reindex/CodeGraph maintenance 与 Webhook 延迟激活契约的缺口，未产生半安装。将补齐独立 Webhook 条件 guard、持久 hold 和受控
恢复，并把它接入现有 prepare/inspect/最终 CodeGraph 恢复链；不使用手工 systemctl、不重建运行时或数据库/索引。

**Webhook 修复已部署后的当前阻塞：** Webhook guard/hold/lifecycle 已由 `ca32abf` 提交、仅推送 `origin/dev` 并同步 root
controller；新的 prepare、status、访问修复 dry-run、manifest 与静态审计均通过，Webhook 不再阻塞 maintenance-stage。installer
仍在首次写入前 fail-closed，现已精确定位为 WSL transient `systemd-run --uid=<target-user>` 不支持预检命令中的
`WorkingDirectory=%h`。同一命令改用目标账号的绝对 home 后返回完整 12-unit proof，故不涉及 runtime、配置、payload、数据库或索引。
回修已通过命令契约、账号解析 fail-closed、安装事务、Webhook maintenance、CodeGraph resume 和 runtime proof 回归（`199 passed,
2 skipped`）。下一步仅推送 `origin/dev`、同步 controller，再继续原有 maintenance-stage → configure → restore → 增量队列 →
resume 链，不启动裸服务、不重建数据库或索引。

**stage receipt 的后续阻塞（已定位）：** 新 controller 的目标用户预检已通过；maintenance-stage 随后在首次 unit 写入前拒绝，
因为生成的 reindex 和 CodeGraph 受保护 unit 仍使用 `runtime/current` alias，而 stage receipt 必须绑定当次 current 所解析出的
immutable release interpreter。maintenance status 仍正常，无半安装。下一步只调整 manifest 专用渲染：这两个受保护 unit 改用
`SystemdRuntimeBinding.immutable_python`，其余普通服务保留 current 语义；补齐回归后仅推送 WSL、同步 controller 并重试。

由于目标用户 proof 仍运行在现有 immutable release，controller 更新不能直接替换它；不会为此重建 release。预检适配器将只对
这两个受保护 unit 识别经过严格字节替换验证的旧 `current` 摘要，其余 10 项仍精确匹配，stage receipt 本身继续要求 immutable。
该实现及 renderer/transaction/maintenance 回归已通过 `294 passed, 3 skipped`；下一步仅提交、推送 WSL、同步 controller 后实机
重试 maintenance-stage，成功前仍不恢复 worker 或启动增量队列。

**新的最后门禁（已定位）：** maintenance-stage 已通过 manifest、目标用户和 stage identity，但 WSL systemd 把时钟恢复 unit 的
`sh -c` 参数扁平化，导致不可逆的 ExecStart 文本比较失败并自动回滚。仅对注册表已标记 `runtime_bound=False` 的时钟类 unit 改为
“精确原像 + FragmentPath + 唯一部署 guard drop-in”证明；所有运行时服务仍严格比较 ExecStart。维护态仍安全，未启动 worker 或索引任务。
该修复严格要求唯一部署 guard 的路径与原像，完整受影响回归已通过 `297 passed, 3 skipped`，Ruff、格式、编译和 diff 检查均通过。`c1e20ca` 已仅推送 WSL，四类既有增量 scope 已入队但仍受维护门禁保护；下一步同步 controller 并重试官方 maintenance-stage。

服务源码与 root controller 已对齐 `c1e20ca`，维护状态、manifest 原像、绑定、目标用户 transient proof 和 protected unit 身份均通过。maintenance-stage 的受控诊断已定位到 clock unit 被错误要求部署 guard；真实清单排除 clock、包含 memory timer，下一步将该清单下沉为注册表唯一真值后重试，成功前不恢复 worker 或消费队列。

注册表分类回修与完整受影响回归已通过 `315 passed, 3 skipped`，静态检查通过；下一步仅提交、推送 WSL、同步 controller 后重试官方 maintenance-stage。

分类门禁已越过；新的受控诊断证明 platform-docs 在 enable 后瞬时为 `enabled,activating`，持久链接正确但旧的活动态冻结模型错误拒绝它。下一步拆分持久启用证明接口，保留活动态补偿契约不变，再重试事务。

持久启用证明接口已拆分，完整受影响回归为 `318 passed, 3 skipped` 且静态检查通过；下一步仅提交、推送 WSL、同步 controller 后重试官方 maintenance-stage。

启用态门禁已越过；restart 后的即时 `is-active` 对正常 `activating` 过渡仍过严。将改为共享 deadline 的 readiness 证明：只等待 `activating/deactivating/reloading` 收敛到 `active`，终态和超时继续失败关闭。

readiness 回归与静态检查已通过（`333 passed, 3 skipped`）；下一步仅提交、推送 WSL、同步 controller 后重试官方 maintenance-stage。

实机证明 15 秒 readiness 小于五个重启 service 的 systemd `TimeoutStartUSec=1min 30s`；将拆分命令 I/O timeout 与 90 秒共享启动收敛期限，终态仍立即失败。

90 秒期限拆分与完整受影响回归已通过（`333 passed, 3 skipped`）；下一步仅提交、推送 WSL、同步 controller 后重试官方 maintenance-stage。

官方 maintenance-stage 已在 `61b3be6` controller 上成功并复证维护态；接下来按状态机配置 CodeGraph 恢复、restore、消费既有增量队列、最后 resume-codegraph，仍不重建数据库或索引。

CodeGraph 配置已成功，但 restore 被 reindex unit 的 `WorkingDirectory=/root`（服务用户无权限）阻断并安全回维护态。将改为生产 manifest 显式写入目标服务账号 home，修复后重走 stage/configure/restore。

该根因已完成永久回修：生产 manifest 在 root 生成时也会显式绑定目标服务账号的绝对 home，兼容渲染的 `%h` 默认值不再进入生产 unit；不安全目录和账号解析失败均在写入前拒绝。定向回归 `97 passed, 1 skipped`，完整受影响回归 `338 passed, 3 skipped`，Ruff、格式、编译和 diff 检查均通过。下一步仅提交并推送 `origin/dev`，同步 controller 后重走官方 maintenance-stage → configure → restore → 既有增量队列 → resume-codegraph；不重建 runtime、数据库或索引。

新 controller 的 manifest 静态证明已通过，但冻结 runtime 的目标用户预检仍以旧 `%h` 与两个受保护 `current` 解释器摘要运行；受控探针证明二者同时兼容才与旧 release 的唯一摘要一致。下一步只在 fail-closed 的预检兼容层补该两个历史差异的精确联合转换，任一形状、unit 集合或摘要差异仍拒绝；完成 TDD、提交和 WSL controller 同步后再重试 stage。

联合兼容已完成：只允许完整受管集合中 9 个运行时 service 的单一显式 home→`%h` 与 2 个受保护 immutable→`current` 同时还原，所有摘要和 JSON 字段仍逐字核对；重复/缺失/非运行时目录或普通摘要漂移均拒绝。target-preflight `18 passed`，完整受影响回归 `342 passed, 3 skipped`，静态检查通过。下一步提交、仅推送 WSL、同步 controller 后重试同一 stage。

stage 已越过预检，但旧 `/root` unit 使五个普通 service 处于 `activating/auto-restart/CHDIR=200`，事务正确拒绝把该状态当作可回滚原像。不会放宽状态机；维护门禁下只停止这五个已失败循环服务并证明停稳，再由官方 stage 安装正确 unit 并恢复普通 service，worker/队列继续不被手动操作。

**当前收口阻塞已定位为 controller 锁时序，不是索引卡死：** stage、configure、官方 restore 和既有四类增量任务均已完成，队列已清空；但 `resume-codegraph` 在长时间全局 EX 内恢复冻结 runtime 的 reindex worker。旧 worker 为证明 marker 内待命身份必须获取 reader SH，超过锁等待期限后按原有 fail-closed 语义退出，恢复状态机随即安全回维护。不会为此重建 runtime，也不会放宽身份验证。下一步改为“worker 停止时完成长 EX 内的 CodeGraph/Webhook 恢复；释放 EX 后完成 reindex 待命和稳定；最后短 EX 删除 marker”，并以时序、失败补偿和无提前写入回归覆盖后，仅推送 WSL controller 重试。

方案已完成双重架构复核：为防止长 EX 拆分后有其他管理员事务插入，恢复全程将持有只供 controller 使用的会话锁；worker 不获取该锁，仍可在无 gate EX 的待命阶段完成身份校验。Webhook 会形成“已健康、仅可入队、worker 仍被 marker 封闭”的显式中间相位，随后短 EX 删除 marker；这使 marker 删除后不再有可能失败的外部操作，也避免旧 worker 跨越五秒 gate EX。失败补偿始终回到完整维护态，绝不把 ready proof 当作 maintenance proof。

同步最新 controller 前还发现 WSL 服务仓 `.git/objects` 有 45 个历史 root 所有条目，服务账号无法 fetch；工作区与 Git 对象均已
只读验证正常。只会恢复这批已验证 Git 元数据的所有权，再由服务账号正常 fetch/快进；不会改工作区源码或 runtime。

该窄白名单已完成 TDD：旧语义在同一目标用户受限链路对安全 `_vendor` 返回 70，最小回修后安全路径返回唯一成功行；外部、可写、
非 root 所有、app/base 前置和两类关键包影子路径全部仍返回 70 且不输出错误。Windows 定向回归 `179 passed, 31 skipped`，静态检查
通过。WSL runtime venv 无 pytest，未下载依赖；已用同一 `setpriv + env -i + -I -B` 的精确协议矩阵完成 Linux root 运行验证。当前
等待提交、仅推送 `origin/dev` 与 controller 同源同步；维护窗口继续保持，官方访问修复成功前不启动 worker 或更新数据库/索引。

官方重试还发现第二个独立、可复现的 import 期问题：新路径白名单已使目标脚本返回成功 stdout，但
`codev_platform.chroma._config` 在探针 cwd 无默认项目时打印多租户提示到 stderr，严格回执因此正确拒绝。下一步不放宽 stderr
门禁，而是把该配置叶子的“可选默认项目”降级改为静默 `None`，保留 daemon/SSE 的显式 project 路由；完成定向测试和真实探针后再重试
既有访问修复。维护态不变，不重建 runtime、数据库或索引。

由于 current release 不可变，仓库中 `_config` 的永久修复不会被 controller 覆写进当前 wheel；为这条日志重建 release 不符合本轮
增量恢复约束。探针将另行在其短生命周期导入段内临时提供合法的 `PLATFORM_PROJECT_ID` 并在 `finally` 移除，随后仍以原来的精确环境
断言和 stderr 零输出门禁验证。这样既不掩盖输出，也不影响真实 daemon、数据库或索引。

该双层回修已完成 TDD：配置叶子的默认项目失败静默返回 `None`；模拟旧 release 的真实目标用户导入在旧协议下返回 70，接入临时
probe project 上下文后返回唯一成功行且环境已恢复。定向回归 `195 passed, 32 skipped`，静态检查通过。当前等待提交、仅推送
`origin/dev` 和 controller 同源同步，然后重试官方访问修复；维护态继续保持。

真实 current probe 还显示 base 第三方 `jieba` 的固定四条 import warning。下一步同样不放宽 stderr：只在 managed-import 临界区
捕获并校验“零条”或这四条来自已验证 `base_purelib/jieba` 的精确组合；任何 app/外部来源、额外 warning 或类别变化都继续拒绝。
不重建 base，不关闭全局 warnings。

该 warning 门禁已完成 RED/GREEN：预期 base 四条 warning 通过且回执 stderr 为空；应用模块 `RuntimeWarning` 继续返回 70。下一步
仅提交、推送 `origin/dev`、同步 controller 后重试官方访问修复。

**2026-07-23 当前恢复收口实现已完成验证：** controller 采用全程管理员会话锁，并将 CodeGraph 长转换、旧冻结 worker 的
待命稳定、Webhook 入口验收和 marker 短提交分相执行；不会再让 worker 跨越长 gate EX。维护 systemd 叶子已独立，兼容门面保持
稳定。定向回归 `208 passed, 23 skipped`，全仓回归 `5954 passed, 632 skipped, 1 warning`；唯一失败为当前改动未触及且基线已
超 600 行的 `mcp_systemd.py` 与 `runtime_fd_tree.py` 文件预算。下一步只提交并推送 `origin/dev`，同步 root controller 后从现有
maintenance 状态官方恢复增量 worker；不重建 runtime、数据库、索引或依赖。

**2026-07-23 WSL 恢复实机新根因：** 新 controller 的官方恢复在解除 CodeGraph runtime mask 后被严格 effective payload
校验关闭，并已自动回 maintenance。根因不是 worker 或数据库：reindex 在 M1 必须保留 `Restart=no`，服务器还保留 root 受信的
CPU embedding/启动限流本地 drop-in；旧 verifier 却沿用旧时序，错误要求 reindex 只有两个生成 drop-in。下一步将该校验拆为
显式 M1 模式：CodeGraph 仍全量精确校验，reindex 仍验证主载荷、ExecStart、恢复配置和 maintenance drop-in，只保留既有受信
本地运行参数；完成回归、提交和 WSL controller 同步后重试一次官方恢复，仍不重建任何库或运行时。

**2026-07-23 M1 local override 修正已验证：** staged effective verifier 已拆为独立叶子，默认 strict 行为不变；仅 M1
显式允许 reindex 的既有 root 本地运行覆盖，并在此之前证明 `Restart=no`。CodeGraph 的主 unit、条件、ExecStart 和三项
drop-in 仍逐项精确校验，未知 CodeGraph drop-in 继续拒绝。定向回归 `303 passed, 23 skipped`；全仓回归
`5956 passed, 632 skipped, 1 warning`，唯一失败仍是未触及的 `mcp_systemd.py` 与 `runtime_fd_tree.py` 文件预算。下一步
只提交、推送 `origin/dev`、同步 controller 后重试官方恢复；不重建 runtime、数据库、索引或依赖。

**2026-07-23 当前阻塞已更新为 stage 端口载荷漂移：** 官方恢复中的真实 CodeGraph unit 已通过 running/identity 证明，配置健康
地址使用 `19091`，但已安装的旧 unit 只有 `--http`、没有显式 `--port`；25 秒内 101 次公开健康请求均连接拒绝，状态机均已自动
回维护。当前渲染器已按配置生成显式端口，下一步补“stage receipt 的 CodeGraph ExecStart 必须同时绑定当前 health 端口”的早期
fail-closed 证明，再用既有 maintenance-stage 事务刷新 unit；不重建 runtime、依赖、数据库或索引。

**2026-07-23 端口契约修复已完成验证：** stage receipt 现会无条件复证 CodeGraph 的固定启动入口，并在恢复 M0/M1 将已验证的
health 端口与 unit `--port` 精确对比；旧无端口或错端口载荷会在解除 mask 前拒绝。受影响回归 `425 passed, 2 skipped`，Ruff、
格式、编译和 diff 检查通过。下一步仅提交、推送 `origin/dev`、同步 controller，再走既有 maintenance-stage 刷新 stale unit，
然后官方 configure/resume；不重建任何运行时、依赖、数据库或索引。

**WSL manifest 生成门禁：** 服务账号不能读取 root-owned frozen runtime，生成器已在首次写入前安全拒绝。后续使用 root-only
临时输入目录、显式服务配置和同一 `/var/lib/codev-platform/runtime` 生成 manifest，保持 target user 不变，再由原 maintenance-stage
事务消费并清理临时输入；不改用户配置、不放宽权限、不创建第二套环境。

**maintenance-stage 已成功：** 新 CodeGraph unit 已由事务按当前配置刷新，三个延迟激活服务继续保持维护保护，临时 root-only
输入已清理。下一步只走官方 configure/resume，并以现有成功的 CodeGraph manifest 目标恢复；随后消费本次提交已入队的四类增量任务。

**恢复配置已完成：** maintenance status 前后均通过。下一步仅运行官方 `resume-codegraph`，不手工操作任何 service；成功后观察
既有增量队列和四类 manifest 对齐。

**首次 resume 已安全回维护：** 公开错误未暴露内部阶段；下一步在同一官方状态机内仅记录完成阶段和固定异常类型，定位剩余门禁后
继续，不重建任何库或运行时。

**阶段已缩小到 health：** staged/effective payload、解除 mask、启动、running 与 identity 都通过，配置启动策略允许；下一步仅采集
进程参数形状、重启计数、端口监听和 journal 异常类别，继续保持 maintenance 自动补偿。

**根因已定位为 frozen 服务 gate 与 M1 冲突：** 服务进程已带 `19091` 参数但被 immutable `codegraph/maintenance_gate` 因全局
marker 退出；M1 又必须保留 marker 封闭 worker。将增加 root-owned 的一次性启动 bridge：只在专属 hold 已删、mask 已解、固定
cgroup 内放行首次服务启动，随后恢复原 gate；health/stability 后立即删除 bridge 并复证正常 ExecStart。不会改 marker、runtime、
依赖、数据库或索引。

**bridge 收口实施中：** 临时 drop-in 将纳入 M0 收敛：进入 M0 和任何锁内失败均仅删除内容、权限、属主完全匹配的 bridge，重载后证明
不存在；未知文件拒绝继续。成功路径在初始健康后回切 canonical ExecStart 并再次复证运行实例、health、稳定窗口，随后才进入 handoff。

**bridge 收口代码已完成验证：** 冻结解释器的一次性启动桥、root-only drop-in 生命周期、M0 残留清理、bridge/canonical 双重有效
载荷证明及状态机二次健康验收均已接入。相关 systemd/恢复/维护/handoff 回归 `508 passed, 1 skipped`，Ruff、格式、编译和 diff
检查通过。下一步仅提交并推送 `origin/dev`，同步 root controller 后按官方恢复状态机消费既有增量队列；不重建任何库或运行时。

**WSL 实机发现并修复中：** systemd 会拆分 bridge 的 Python `-c` 文本，状态机已自动回维护态。改为冻结解释器直接执行 root-only
bridge 脚本，消除 shell/systemd 转义差异；不重建数据库、索引、runtime 或依赖。

**direct bridge 验证完成：** 新 argv 不再含 Python `-c` 文本；定向 `100 passed`、完整受影响回归 `509 passed, 1 skipped`，
Ruff、格式、编译和 diff 检查通过。下一步只提交、推送 WSL、刷新 controller 并运行官方恢复。

**2026-07-25 Task 7 收口完成：** 发现三项历史结果仅目标提交匹配、运行时版本仍为旧 release 后，已在维护窗口中仅增量补投
`chroma`、`codegraph`、`ingest`；`code_vec` 无需重复处理。当前四项 manifest 的 target/runtime 均精确匹配 `ee39226`，
worker `running/idle`、队列为空，官方 `resume-codegraph` 与四个 MCP endpoint 均成功。没有重建运行时、依赖、数据库或索引，
也没有推送 GitHub；Task 7 到此停止，不进入 Task 8。
