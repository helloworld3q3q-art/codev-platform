# 日常薄运行时版本前移计划

> **状态：** 已完成。运行时已前移至包含生产 Git 跟踪刷新与 Chroma 删除重试的版本；四个 MCP 端点、常驻 worker 与四类索引 manifest 均已验收。除出现精确完整性证据后的单 lane 原子 `code_vec` side-build 外，未执行数据库删除、`--force`、全库重建、base 或依赖重建。
>
> **目标：** 让已推送到 `origin/dev` 的应用代码在唯一 WSL 正式环境中安全生效：复用既有不可变依赖 base，只构建应用 wheel、原子前移 current、受控重启并可回滚；绝不把日常代码更新升级为数据库、依赖或四类索引重建。

## 一、触发事实

- `34d0bd304e2b63f06642440d4292088273ef7be7` 已仅推送到 `origin/dev`，WSL 服务仓已快进到同一提交且干净。
- current runtime 仍运行旧 revision `ee39226ad2d1ea94006c95c626d101ca039becd8`；服务进程不会因 Git worktree 快进自动重新加载 wheel。
- 现有 `runtime deploy` 生产入口被明确退役并 fail-closed；旧 `install-wsl-runtime.sh` 会进入迁移、systemd staged transaction 与 `indexes_rebuilt` 阶段，不能作为日常更新入口。
- 已存在的 `runtime build / stage / activate / rollback` 原语可复用，但缺少把它们与受控服务重启、健康验收和失败回滚组合成日常窄流程的入口。

## 二、方案裁决

| 方案 | 裁决 | 原因 |
|---|---|---|
| 运行旧全量部署脚本 | 禁止 | 会触发迁移与索引重建，违背日常增量边界 |
| 手工调用原语并裸 `systemctl` | 禁止 | 无统一验证、回滚和服务身份门禁，容易留下半切换状态 |
| 新增薄应用版本前移命令 | 采用 | 复用既有内容寻址 release/base 原语，补齐唯一缺失的日常编排边界 |

## 三、影响面与不变量

- **等级：** L4（版本化 runtime、受管服务、发布与回滚）。
- **候选范围：** runtime promotion 编排模块、CLI 窄入口、受控服务生命周期适配层、目标测试与本 roadmap。
- **不变量：**
  - 目标必须精确等于 `origin/dev` tip，服务仓必须干净；只允许单一 runtime root。
  - 仅在目标与 current 的依赖冻结/批准需求完全一致时执行；不同即拒绝并转为显式迁移任务。
  - 不写数据库 schema、不调用全量 reindex、不下载依赖、不重建 base、不手动编辑 `/etc`、不裸调用 `systemctl`。
  - 激活后必须实测服务端点；任一失败自动回到先前 release 并通过同一受控适配层恢复服务。
  - Git 仅 `origin/dev`，不推送 GitHub；token、DSN 与 systemd 环境正文不写日志或计划。

## 四、实现顺序

1. 定位现有 release/base、候选构建和 systemd 受控事务的可复用端口，确认最小重启服务集合与健康探针。
2. 设计纯计划对象与 preflight：目标/当前身份、依赖一致性、干净工作树、单实例锁、回滚锚点。
3. 实现薄 release 构建与 staging，强制复用 current base；在任何依赖差异处 fail-closed。
4. 实现受控激活、服务重启、真实 HTTP/MCP 验收和补偿回滚，不把 `systemctl` 细节泄漏给调用方。
5. 为成功、依赖漂移拒绝、激活失败、健康失败回滚和重复运行补定向测试。
6. 本轮登记表修复用该命令做一次 WSL 实测；只验证既有增量队列状态，不重新入队或重建四类索引。

## 五、已落地的实现

- 新增 `runtime_thin_promotion` 纯编排层：以“预期 current”作为并发门禁，按 **前置校验 → 原子切换 → install-only unit 刷新 → 受控重启 → 服务/MCP/Webhook 验收** 执行；任一后段失败会回到原 release 后重新刷新 unit、重启并复验。补偿后的状态无法证明即 fail-closed。
- `runtime_release` 新增严格的预期 current 激活/回滚原语；它不会覆盖另一条发布链已前移的 `current`，从根上避免并发发布互相覆盖。
- 新增 Linux/systemd 适配层：复用既有 `install_systemd_install_only` 事务和受管 `systemctl` 适配器，不手写 `/etc`、不裸调 shell `systemctl`。CodeGraph 与 reindex 的 immutable unit 绑定会随本次目标 release 受控刷新，其余常驻服务继续通过 `current` alias 加载同一 release。
- 新增源码准备层：目标必须是本地 `origin/dev` tip、服务仓干净且包含当前 revision；候选 wheel 必须由 root 写入既有 runtime 私有 `candidates`，Git 读取经已有 `--source-user` 降权桥完成，root-fd worker 才能在 `NoNewPrivileges` 下安全读取并暂存。
- 日常模式拒绝 `pyproject.toml`、运行时 freeze、批准 requirements 的漂移；同时拒绝 systemd 渲染、目标用户预检与服务端口契约相关源码漂移。这些情况必须进入显式运行时迁移，不允许伪装成日常代码更新。
- `runtime promote` 支持两种入口：常态使用 `--target-revision` 自动完成降权构建和暂存；首次 bootstrap 只接受已暂存 release + 明确回滚锚点，避免让旧 runtime 执行不存在的新命令。

## 六、当前验证

- Windows Python 3.13 定向测试：`103 passed, 17 skipped`，覆盖薄发布成功、MCP 验收失败回滚、回滚不可证明、CLI 解析、候选回执、install-only 安装包边界与既有 systemd 事务契约。
- 定向 Ruff：新增/修改模块与测试均通过。
- POSIX release 指针测试已纳入定向集；当前 WSL runtime venv 未安装 pytest，因此首次 WSL bootstrap 会以真实受控命令和服务验收代替在生产 venv 中安装测试依赖。

## 七、完成定义

- 单条受控命令可将一个已推送、依赖未变的目标提交前移为 current release。
- 输出仅含提交、release 身份和阶段结果；服务重新加载后状态/MCP 真实可用。
- 任一失败保持或恢复先前 release；当前数据、队列、manifest 和依赖 base 均不被重置。

## 八、首次 WSL bootstrap 发现与修复步骤

- 已构建服务账号私有缓存下的候选 wheel，现有 root-fd `root-identity` 只读探针正常，说明 user systemd、pidfd 和受控 worker 链路均可用。
- 正式 `runtime stage` 在读取候选输入前失败。服务账号缓存父目录为 `0700`，而 worker 强制 `NoNewPrivileges`，不能也不应借 root 的 DAC 权限穿透该目录；这是历史候选访问门禁的预期安全行为。
- 修复范围收窄为：日常发布改用既有 runtime 根 `candidates` 目录，root 使用既有 `runtime build --source-user <服务账号>` 精确 Git 身份桥构建候选；不新建候选存储、不放宽服务账号目录权限、不直接调用 release 内核。
- 已补齐候选根路径、root 构建 argv、回执与暂存输入一致性的定向测试；Windows 定向集为 `118 passed, 17 skipped`，Ruff 与 `git diff --check` 均通过。旧 current 已只读确认支持 `runtime build --source-user`，正式 runtime 的 `candidates` 为 root `0700`，可作为一次 bootstrap 的受控输入根。
- 下一步仅提交并推送 `origin/dev`，再用 root 私有候选做一次 bootstrap。若 stage 或后续验收失败，保留 current，不删除对象、不重建任何数据库或索引。

## 九、WSL 已完成对象访问投影收敛

- `687302bc6f3561401aec0821e76221945129bdcb` 已仅推送 `origin/dev`；WSL 服务仓已快进且干净。root 私有候选已成功构建，目录与 wheel 均为 root 所有，候选可读性不再是阻塞项。
- 正式 `runtime stage` 尚未创建 release 目录即 fail-closed。只读 root-fd 同构探针确认共享 base 的内容、所有者、普通文件模式和候选均正常；唯一差异是 9 个历史目录仍为 `0755`，而 base 的既有对象策略要求 `0750`。这是已完成对象访问投影漂移，不是候选、依赖、数据库、索引或 systemd 故障。
- 允许的唯一修复是既有 root-only `runtime access-repair-current`：dry-run 已通过；维护窗口尚未进入，固定顺序为 `reindex-maintenance prepare --yes` → `reindex-maintenance status` → `runtime access-repair-current --yes`。prepare 只经既有生命周期状态机暂停 reindex、CodeGraph 与 webhook 并发布维护门禁，随后才收敛当前精确 base/release 对的目录模式并复验服务访问。禁止手工 `chmod/chown`、调用 release 内核、重建 base/release、数据库或索引。
- 该事务失败时保持 current 和服务安全状态；成功后重新执行一次候选暂存，再从新 release 的 `runtime promote --target-release ... --rollback-anchor ...` 执行受控切换、unit 刷新、重启与健康验收。

## 十、root CLI 字节码写入防护

- 官方访问修复已成功一次；其后只读诊断使用旧 runtime 的 `-I` 而未带 `-B`，root Python 自动写回 56 个可证明 `__pycache__/*.pyc` 和 9 个 `0755` 缓存目录，重新触发严格对象访问门禁。这是 Python 字节码副作用，不是访问修复、候选、base、数据库或索引失败。
- 日常薄发布的 root 子进程必须固定使用 `-B -I`；补齐 argv 回归，防止常态 `runtime promote` 在构建候选期间重建同类缓存。所有后续 WSL runtime CLI 调用也固定使用 `-B -I`。
- 本地定向测试 `72 passed`、Ruff 通过；提交该最小修复后，维护窗口内再运行一次既有 `runtime access-repair-current --yes` 清理并收敛已证明缓存；之后不再执行无 `-B` 的 root Python 诊断或发布命令。

## 十一、WSL bootstrap 的 wheel 载荷门禁漏配

- `0a586e3ea9b24d466c16e5016caec0f9a9c6ee66` 已仅同步到本机 WSL 服务仓；在既有维护窗口内以 `-B -I` 重跑 `runtime access-repair-current --yes` 成功。该步骤只收敛 current 对应 base/release 的访问投影，没有重建 base、依赖、数据库或索引。
- 随后的正式 `runtime stage` 在写入目标 `.incomplete` 和 wheel 制品后 fail-closed；只读诊断确认 base、候选和受控 worker 均正常，失败点是 `inspect_application_wheel` 拒绝了 `platform_meta/**`。
- `platform_meta` 不是未知第三方载荷：它已由 `pyproject.toml` 的 setuptools 包发现规则明确声明，并由 `codev_platform.core.platform_meta` 在运行时加载项目登记表。当前 wheel 校验仅允许 `codev_platform/**`，与项目真实发布契约不一致。
- 修复不得放宽为“允许任意顶层包”。应把应用 wheel 的允许包根定义为不可变精确集合（`codev_platform/`、`platform_meta/`），继续对 `.data` 非 purelib、重复路径、任意未知根、RECORD 摘要与安装后未知文件 fail-closed。
- 同时把 wheel 静态预检前移到创建目标 release 目录之前，使后续错误不再留下可预期但无价值的 `.incomplete`；worker 仍是唯一实际写 release 的边界。补齐“合法 `platform_meta` 通过、未知包拒绝、预检失败不创建 release 目录”的定向回归。
- 修复经本地测试、只构建一个新候选 wheel 并重试现有 bootstrap；旧 `.incomplete` 只由下一次官方 `runtime stage` 的既有隔离逻辑处理，禁止手工删除。成功后先通过既有受控入口恢复常态，再执行目标 release 的 `runtime promote` 和增量队列恢复。
- 当前 immutable release 仍携带旧白名单，不能用自身暂存包含新白名单的 wheel。为避免修改 current、base 或执行服务账号工作树，本次仅使用一次性 root 私有桥接：先校验候选 JSON、wheel SHA、精确 revision 与归档路径，再把 wheel 中 `codev_platform/**` 与 `platform_meta/**` 安全解出到 `0700` 临时目录；现有 root 解释器从该临时只读源码启动同一个官方 `runtime stage`。root-fd worker 的 bootstrap 也只读取这个 root 私有目录；返回后立即删除临时目录。该桥接不下载依赖、不写数据库/索引、不会成为日常发布路径。

## 十二、维护态与日常薄发布的生命周期边界

- 目标 release 已由官方 `runtime stage` 成功生成；在实际执行 `runtime promote` 前，静态审计发现生产适配器的前置校验调用 `default_install_only_runtime_proof`，该证明同时接受常态与维护态。
- 但薄发布的下一阶段会固定重启完整常驻服务集合，而维护态按契约要求 CodeGraph runtime mask 生效、Webhook 停止。因此维护态进入薄发布会在 current 已前移后必然触发失败补偿，属于发布状态机的真实契约矛盾，不能通过“先试一次”处理。
- 裁决：日常薄发布只允许已证明的**常态**。把“CodeGraph 未被 runtime mask 且维护门禁未激活”的组合证明收敛在薄发布适配层；install-only 事务继续保留“常态或维护态”的自身语义，二者不得混用。
- 为消除前置校验与服务重启之间的 TOCTOU，薄发布从前置校验起至最终验收止持有既有 `maintenance_systemd_transition_session`，再在其内持有 runtime 部署锁。既有 install-only 事务可在同一会话中重入自己的短 gate 临界区；任何 prepare/resume 等维护转换必须等待本次发布完整结束。
- 本修复不自动退出维护窗口、不在薄发布中调用 CodeGraph 恢复、不修改队列、manifest、数据库、依赖或 base。维护恢复仍必须走既有 `configure-resume-codegraph` / `resume-codegraph` 状态机并满足其目标 manifest 证明。
- 验证：新增正常态通过、维护门禁激活拒绝、runtime mask 激活拒绝的定向回归；运行薄发布、systemd 策略和 CLI 定向测试及 Ruff。修复推送后构建一个新候选并按同一官方 stage 流程生成新 release，旧候选/release 保留给既有受控清理，不手工删除。

## 十三、缺失受管配置的一次性引导

- WSL 只读核对发现：`/etc/codev-platform/platform.env` 存在且可通过既有 root 受管环境文件校验，但 `/etc/codev-platform/config.json` 缺失；环境文件中也尚未声明 `CODEV_PLATFORM_CONFIG`。当前服务账号的历史用户级配置仍可读取，因此维护窗口本身能成立，但 `resume-codegraph` 的“调用方/服务同源配置”证明会拒绝恢复。
- 这不是让日常薄发布承担配置迁移：日常前移仍只读服务账号配置、刷新 unit、重启和验收。缺失配置是一次历史落地缺口，必须用独立、显式确认、仅在已证明维护窗口内可执行的 bootstrap 入口修复。
- 设计：新增窄职责的受管配置 bootstrap。它只接受固定服务账号，验证其用户级 JSON 来源、现有 root 受管环境文件及维护管理员许可；仅当固定受管 JSON 目标确实不存在时，复用既有严格规范化与原子发布机制写入 JSON，并为环境文件补入唯一固定的 `CODEV_PLATFORM_CONFIG` 赋值。发布顺序固定为“配置先于环境引用”；若中断在两文件之间，仅允许内容摘要完全一致的安全半态续跑，未知既有内容一律拒绝覆盖。
- 该入口不接收任意源路径、不输出配置正文、token、DSN 或环境值；不改数据库、队列、manifest、base、依赖或索引。未知目标已存在时 fail-closed，后续配置变更仍必须走完整受控部署流程。
- 验证：覆盖 dry-run、非 root/非维护拒绝、来源所有权与符号链接拒绝、既有目标拒绝、原子发布后的 root/group/mode/摘要证明，以及 CLI 参数边界。成功发布后再用既有 `configure-resume-codegraph` / `resume-codegraph` 正常恢复，最后执行新的薄发布切换。

### 当前实现与验证

- 已新增 `runtime_managed_configuration_bootstrap` 编排层与 `runtime bootstrap-managed-config` 窄入口：来源固定为服务账号 `~/.codev-platform/config.json`，读取时校验账号目录、root 控制的祖先目录、普通文件、所有权、模式、硬链接和 fd 身份稳定性；CLI 与回执只输出固定状态和 SHA-256，不输出正文。
- 发布核心复用现有规范化、root 原子写入、服务组权限和环境文件校验。dry-run 零写入；`--yes` 在维护管理员 permit 内做前后维护态证明。发布顺序为 JSON 后环境引用，若进程恰在两者之间中断，只允许相同内容摘要的半态继续完成。
- 本地定向验证：`73 passed`（受管配置、bootstrap 编排/CLI、runtime CLI、总 CLI parser）；Ruff 与 `git diff --check` 通过。
- 下一步：提交并仅推送 `origin/dev`，用已验证候选桥在 WSL 维护窗口执行该一次性引导；之后仍走既有 CodeGraph 配置/恢复状态机和队列的增量消费，不执行重建。

### WSL dry-run 兼容回修

- 候选桥的首次 dry-run 已证明固定服务账号来源和候选校验链可达，但在读取历史环境文件时拒绝。只读元数据核对确认该文件是安全的 root-only `0600`；这是旧部署的受支持初始形态，不应在读取阶段错误要求最终服务组 `0640`。
- 已将兼容边界精确收窄为：只接受历史 `root:root 0600` 或最终 `root:服务组 0640`；其他组/模式一律拒绝。实际写入仍通过原子发布把目录和环境文件收敛为最终服务组投影，不放宽文件安全性。
- 回修定向验证：`77 passed`，Ruff 与 `git diff --check` 通过。待提交并仅推送后，以新候选重做 dry-run 与 `--yes`；此前 WSL 未写入 `/etc`、未改数据库或索引。

### 恢复快照与日常环境同源兼容

- 在受管配置发布后，`configure-resume-codegraph` 的现有证明会同时挂载日常 `platform.env` 与恢复快照。原规则把日常文件里的固定 `CODEV_PLATFORM_CONFIG` 一概当成覆盖，导致它与恢复快照已经证明的同一路径也被拒绝。
- 已将规则收敛为值相等证明：仅允许日常辅助环境文件以**完全相同的绝对路径**重复 `CODEV_PLATFORM_CONFIG`；`PLATFORM_DATA_DIR`、配置摘要、仓覆盖和任意不同/转义/重复配置路径继续 fail-closed。令牌变量仍只做语法检查，不被读取或输出。
- 本地定向验证：恢复配置证明、恢复配置安装、恢复状态机、维护命令与受管配置共 `127 passed`；Ruff 与 `git diff --check` 通过。待组合回归、提交并仅推送后再用新候选恢复 WSL 服务。

## 十四、WSL CodeGraph 恢复诊断与受控 release 来源

- 已验证候选运行 `configure-resume-codegraph` 后，配置同源、维护态、目标 manifest 与 staged unit 静态证明均通过；失败不涉及数据库、队列、base、依赖或四类索引内容，状态机每次失败后均已证明回到维护态。
- 阶段化诊断确认：一次性启动 bridge 必须是 root 所有、父目录不可被非 root 写入且 CodeGraph 服务账号可读取的稳定来源。`/tmp` 因父目录可写被受管读取器拒绝；root 私有 `candidates` 虽可通过 root 证明，但服务账号不可遍历，因而健康探针无法就绪。这两个拒绝均是正确的安全行为，禁止放宽目录权限或绕过检查。
- 修复步骤收敛为：复用已完成的精确候选 wheel 和 current 的同一 immutable base，先经官方 `runtime stage` 生成一个尚未激活的薄 release；随后仅以该 release 中 root 封存、服务账号可读的 bridge 源执行既有 `resume-codegraph` 状态机。该 stage 不下载依赖、不构建 base、不切换 current、不创建第二套服务或数据库。
- 验证顺序：复验 release 身份与 current base 一致；恢复后确认 CodeGraph、webhook、reindex handoff 均由原状态机证明；最后仅观察 worker 消费既有增量 scope 与四类 manifest 对齐。若任一阶段失败，保持或回到维护态，绝不手工启动服务、手工改 `/etc`、删除队列或全量重建索引。

## 十五、暂存 release 的服务访问发布缺口

- 已验证的薄 release 会先按 root-only 对象策略封存，这是正确的构建边界；`runtime access-repair-current` 也按名称只允许修复 current 精确对象，不能错误用于尚未激活的 release。
- 诊断确认日常 `runtime promote` 在切换前缺少“把目标 release 及其复用 base 发布为服务主组只读”的受控步骤。若只靠临时 bridge 或先激活再修复，都会造成服务账号无法读取目标代码，且把可恢复问题变成切换后的可用性故障。
- 裁决：新增单一职责的“已验证 staged release 服务访问发布”领域能力与显式 CLI；它只接受精确 release ID、固定服务账号和已完成对象，先静态复验，再通过既有 runtime service-access 原语发布父命名空间、base/release 内容并做目标账号探针。它不改 current、不重启服务、不改数据库、队列或索引。
- 日常薄发布在独占锁内按 **常态预检 → 目标服务访问发布 → 原子激活 → unit 刷新/重启/验收** 执行；访问发布失败时 current 尚未变化。维护窗口中的一次性 CodeGraph 恢复只使用同一已发布 release 的 bridge 源，不再使用 `/tmp` 或 root 私有候选目录。
- 验证：覆盖未激活 release 的 dry-run/发布/拒绝 current、错误服务账号与静态对象漂移；覆盖 promotion 在激活前调用发布端口、发布失败不切换、发布成功后保持既有回滚契约。WSL 仅构建新的应用候选并复用当前 base，保留已暂存对象，不手工删除。

### 当前实现与验证

- 新增 `runtime_staged_release_access` 领域模块：以精确、非 current 的 release ID 为输入，在 shared activation 锁内完成静态 base/release 复验；写入时仅复用既有 `runtime_service_access` 的命名空间/内容发布与目标账号探针。dry-run 零写入，任何异常不输出路径、账号名、命令或配置正文。
- 新增 `runtime publish-staged-access <release-id> --service-user <user> [--yes]` 窄 CLI，默认 dry-run；它不复用或放宽 `access-repair-current`，也不重启服务或变更 current。
- `runtime promote` 新增独立端口，顺序固定为常态预检后、原子激活前发布目标服务访问；发布失败时 current 尚未变化。生产适配器只接受目标账号探针、release ID 与服务 UID/GID 一致的回执。
- 新增领域/CLI/适配器回归，并覆盖原有 runtime service-access、target-user probe、CodeGraph 恢复、受管配置和 systemd 事务组合。Windows 定向组合验证：`302 passed, 15 skipped`；Ruff 与 `git diff --check` 待提交前复核。
- 已暂存的旧 f375 release 保留为不可变历史对象，不手工删除；新提交会只构建一个应用候选、复用 current base，先执行新访问发布命令，再以其可读 bridge 恢复 CodeGraph，最后在常态下走日常薄发布。

## 十六、timer unit 的 install-only 进程状态兼容

- WSL 受控前移实测显示：`codev-clock-resync.timer` 与 `codev-memory-maintenance.timer` 的 `systemctl show` 返回 `ActiveState`、`InvocationID`，但按 systemd 语义不提供 `MainPID`。现有通用读取器把缺失字段当成不可信，因而 install-only 在首次写入 unit 前安全中止；current 已由既有补偿恢复，数据库、索引、base 与 release 内容均未改变。
- 该问题属于受管 systemd 状态适配层的类型建模缺口，不是 release 绑定、服务访问、队列或服务健康问题。修复范围只限 `mcp_systemd_systemctl` 和对应定向测试，不调整发布编排、不改变任何受管 unit 文件。
- 裁决：仅当 unit 名称严格以 `.timer` 结尾且输出缺少 `MainPID` 时，适配器将其规范为进程不存在的 `0`；仍要求精确的 `ActiveState` 和 `InvocationID`，也仍拒绝重复字段、额外字段、非法 PID、失败返回或任意 `.service` 缺失 `MainPID`。这样 timer 的稳定身份仍会纳入 install-only 前后补偿证明，服务继续 fail-closed。
- 验证：补 timer 缺失 `MainPID` 的通过回归、service 缺失字段拒绝回归、既有异常输出拒绝回归；运行 systemd 状态读取、install-only 事务、薄发布适配器/编排和 CLI 的定向测试及 Ruff。通过后只构建一个新应用候选、复用当前 base，再执行既有 staged-access、CodeGraph 恢复和日常薄发布；不得重建四类索引或数据库。

### 当前实现与验证

- 已将进程状态输出解析收敛为独立函数：timer 只在缺失 `MainPID` 的精确 systemd 语义下补零；若该字段存在仍严格解析，任意非 timer 缺失字段或额外字段继续拒绝。
- 新增真实故障形态和 timer 额外字段拒绝回归。定向链路 `65 passed`；组合验证 `320 passed, 15 skipped`；Ruff 与 `git diff --check` 均通过。提交后只需在 WSL 构建新的应用候选并复用既有 base，完成一次正式受控 promotion 验收。

## 十七、MCP 冷启动就绪证明

- timer 修复后的 WSL 受控 promotion 已越过 unit 刷新和 systemd 稳定性证明，但在服务重启后立即执行一次四 MCP 探针时失败；同一回滚后的服务账号状态命令随后确认四个端点均正常。这证明是进程已被 systemd 标记 active、HTTP/MCP 端口尚在冷启动的时序竞争，不是配置、数据库、索引或目标 release 损坏。
- 修复范围只限日常薄发布的 MCP 验收适配层。不得通过固定盲等、忽略 DOWN、绕过 MCP 探针或在失败后手工重启来掩盖问题；现有失败回滚与入口验收顺序保持不变。
- 裁决：复用既有 `mcp_serve.wait_until_serving`，由它唯一负责真实端点轮询、超时诊断与等待节奏；新增向后兼容的“当前轮全部成功”模式，避免把不同轮次的单端点成功错误拼接成发布成功。薄发布适配层只负责固定四端点的精确集合和最终 `status=ok` 契约，固定以 90 秒/2 秒参数调用该等待器。集合漂移、重复项、畸形行或任一非 OK 状态立即 fail-closed；超时后仍以既有稳定错误进入回滚，不固定盲等、不绕过探针。
- 验证：覆盖旧有锁存语义兼容、当前轮模式下的冷启动收敛和暂态回落拒绝；覆盖薄发布调用参数、集合漂移与非 OK 结果拒绝，以及既有 promotion 的 MCP 失败回滚契约；运行 systemd 状态、薄发布适配器/编排、MCP 探针和 CLI 的组合测试及 Ruff。通过后重新构建一个应用候选、复用原 base，以官方 staged-access 和 promotion 完成实机验收，不重建任何索引或数据库。

### 当前实现与验证

- `mcp_serve.wait_until_serving` 已新增默认关闭的 `require_current_round` 模式。常规 `serve-mcp start --wait` 保持历史成功锁存语义；薄发布开启该模式，每轮真实探测全部端点，只有同轮全部 OK 才能通过。
- 薄发布适配器已删除重复的私有轮询器，只调用既有等待器，并在返回后严格校验四个固定 MCP 名称、无重复及全部 OK；任何 timeout、集合或输出结构异常均映射为既有发布失败和受控回滚。
- 定向验证 `66 passed`；覆盖 systemd、runtime、受管配置、CodeGraph 恢复、维护命令、MCP 和 CLI 的组合验证 `368 passed, 15 skipped`；Ruff 与 `git diff --check` 均通过。
- WSL 已以目标提交 `ce6eaf35481454f9f6398fc0efabadcc637c40d5` 构建候选、暂存 release `5f1e5c8ddf606056cabe3df04a56ef062a8e55add4662a9cce9b3755b3a0a126`，复用原 base 后成功前移。独立快照确认 current 的 revision 精确等于目标提交；四个 MCP 均 OK，受控 worker 正常待命。

## 十八、Chroma 增量失败的受控恢复

- 提交级 manifest 只显示 Chroma 为 failed；`codegraph`、`ingest`、`code_vec` 均已以目标提交成功。失败发生在旧 current release 尚在运行时：Chroma 1.5.9 删除旧 chunk 触发内部 compaction 日志读取错误。该错误不是新 release、MCP 服务、队列停滞或 CodeGraph 数据库损坏。
- 现有删除原语已按 fail-closed 语义撤销文档 manifest，避免将可能不一致的集合误报为成功。因此不能把空队列当成全部索引已完成，也不能手工删 Chroma 文件或直接改 manifest。
- 裁决：仅在新 current 已验收通过后，以服务账号运行一次官方 `reindex --chroma --repo <服务仓>`，不带 `--force`，不触发 codegraph、ingest、code_vec，不删除数据库或创建第二套环境。它只恢复本项目缺失的文档 manifest / 当前 build；若再次失败，停止而非无限重试，并以新的错误证据单独诊断。
- 已完成单项恢复：官方 Chroma 命令以新 current 成功处理 228 个文档、3706 个 chunk，并输出 `proof: chroma ok`。该直接入口按职责不写提交级 manifest，故旧失败回执仍保留；这不代表新 build 写入失败。
- 下一步只经官方 `reindex-queue enqueue` 投递 `codev-platform/chroma` 和精确目标提交，由既有空闲 worker 做一次常规、无变更的 Chroma 证明并写入新的 ok manifest。不得重新投递其余三类任务，也不得直接写 manifest。
- 完成回执：只投递 `codev-platform/chroma` 的精确目标提交任务后，既有 worker 已写入新的 ok manifest。四类 manifest（`chroma`、`codegraph`、`ingest`、`code_vec`）均精确覆盖 `ce6eaf35481454f9f6398fc0efabadcc637c40d5` 且 status=ok；worker 为 idle、pending=0、active=0，四个 MCP 端点均为 OK。
- 体检中 `codegraph db` 的 `/mnt/d/WorkSpace/...` 缺失仅是 Windows 挂载工作目录的路径误判；WSL 实际服务仓与 CodeGraph MCP 端点均已成功证明，故未错误触发 CodeGraph 重建。
- 本计划至此收口。后续普通代码提交继续只由既有 hook 与 worker 执行增量队列；只有用户明确要求或有新的、可复核的索引损坏证据时，才另立计划讨论恢复范围。

## 十九、Chroma 单次增量删除的可靠性收口

- 实测事实：首次处理目标提交时，Chroma 在删除旧 chunk 处返回 `InvalidArgumentError: Error in compaction: Failed to pull logs from the log store`；fail-closed 原语正确撤销 manifest。随后同一文档提交的下一次既有队列消费成功，最终 manifest 已恢复为 ok。这证明是 Chroma 后端瞬态 compaction 问题，不是索引永久损坏，但单次入队不能依赖偶然的第二次触发。
- 目标：让单次 Chroma 增量提交在这一精确、可恢复的后端错误下有界自愈；其他错误继续立即失败，不掩盖数据损坏，也不通过 `--force` 或手工删除绕过一致性证明。
- 新增实测：本次目标提交在旧 runtime 中由既有 worker 正常消费完毕，但 `chroma` 与 `code_vec` 都因同一精确 compaction 错误失败；`codegraph`、`ingest` 已成功。因此该策略必须覆盖两条独立的 Chroma 删除适配路径，不能只修文档索引。
- 影响范围：`chroma/collection_integrity` 的通用删除策略、文档索引和 `code_vec` 的删除适配、其定向单元测试、Chroma 增量链路回归与本计划。不得让 indexer、worker、MCP 服务或数据库存储层相互反向耦合。
- 预期方案：将“可判定的瞬态 compaction 删除失败”收敛为轻量共享的 collection 删除策略，采用固定、小次数、有间隔的重试；两条调用路径每次仍调用真实 collection 删除。重试耗尽后仍分别撤销自身 manifest 并抛出既有稳定错误。异常分类、等待和删除动作拆成窄函数，测试注入 sleep，零真实等待。
- 设计裁决：只接受顶层 `InvalidArgumentError` 且错误文案精确等于 `Error in compaction: Failed to pull logs from the log store`；不遍历异常链、不做关键词宽匹配。初次调用之外最多再试两次，间隔固定为 0.5 秒、1 秒。异常类别或文案任一不符、或三次均失败时，沿用既有 manifest 撤销和稳定失败语义。
- 验证：覆盖瞬态一次失败后成功、非目标错误不重试、耗尽后撤销 manifest 并失败关闭；跑 Chroma/indexer、reindex worker/runner 与本次 runtime 组合测试、Ruff。通过后仅构建一个新应用候选并执行日常薄发布，再以普通文档增量提交实机验证单次队列成功。

### 当前实现与本地验证

- 已将精确异常分类和有界删除重试收敛为共享的 collection 删除能力；文档索引与 `code_vec` 各自保留原有 manifest 撤销和稳定错误，worker、队列、MCP 与存储 schema 均不变。
- 完整 Chroma、`code_vec`、索引证明、执行器、worker、结果发布和 runner 日志回归：`327 passed, 3 skipped`；MCP 与薄发布回归：`75 passed`。Ruff 与 `git diff --check` 均通过。
- 完成回执：三个最小提交已仅推送本地 `origin/dev`：`6d3a842`（文档索引删除重试）、`03f21fd`（代码向量复用该策略）与 `9137d05`（生产 Git 跟踪刷新）。目标 runtime 启用后，仅重投此前失败的 `chroma`、`code_vec` scope；`chroma` 在 107.52 秒内成功。
- `code_vec` 首次新 runtime 消费发现“collection 比 checkpoint 多 1 条陈旧向量”（17455 对 17454），完整性门禁正确拒绝发布并撤销 manifest。随后仅该 lane 按既有逻辑创建隔离、原子 side-build；全程未使用 `--force`、未删除数据库、未影响另外三类索引或旧可用代码向量。第二次受控消费在 412.27 秒内通过独立完整性证明并原子发布。
- 最终四类 manifest 均为 `status=ok`、目标提交精确为 `9137d055eab74f9f6398fc0efabadcc637c40d5`；新 runtime 下的 `chroma` 与 `code_vec` 回执也精确记录该 runtime revision。worker 为 idle，pending=0、active=0。

## 二十、日常薄发布的生产 Git 跟踪引用刷新

- 实测事实：`runtime promote --target-revision` 在候选构建前 fail-closed；服务仓工作树已经是目标提交且干净，但本地 `origin/dev` 跟踪引用仍停在旧提交。当前薄发布只读取该跟踪引用、不执行受控 fetch，因而把正常推送后的陈旧缓存误判为“目标不是当前生产分支”。current、服务、数据库和索引均未改变。
- 目标：让日常薄发布在身份门禁内刷新**唯一允许的** `origin/dev` 跟踪引用，再要求其 tip 精确等于目标提交；不得接受任意远端、分支、GitHub 地址或工作树覆盖。
- 设计裁决：从既有生产 Git 证明模块提取窄职责刷新能力，由它固定校验远端非 GitHub、以服务账号执行受限 fetch、写入唯一远程跟踪 ref 并返回目标证明。既有完整部署证明复用该能力；薄发布只在静态源码仓校验后调用它，随后仍保留干净工作树、祖先关系和依赖/运行时契约差异门禁，避免把网络刷新与发布编排耦合。
- 本次 bootstrap：当前已激活的旧 runtime 尚不含该修复，允许仅调用它**已有的生产 Git 目标证明函数**刷新固定跟踪引用；该函数只执行受限 fetch 和目标校验，不运行已退役的 `runtime deploy`、不 checkout、不改 current、服务、数据库或索引。随后立即以官方 `runtime promote` 前移包含修复的目标 release。
- 验证：覆盖刷新后目标匹配、远端/目标异常 fail-closed、完整部署证明兼容、薄发布在刷新后才读取 tracking tip、刷新失败不构建候选；运行生产 Git、薄发布 source/runtime/MCP 与本次 Chroma/`code_vec` 组合回归、Ruff。通过后重新提交并仅推送 `origin/dev`，再用官方 thin promote 验收。

### 当前实现与本地验证

- 已新增 `refresh_production_git_target`：它只允许固定 `origin/dev`、固定远程跟踪 ref、服务账号和非 GitHub 地址；既有完整部署目标证明改为委托该能力，保持旧接口与恢复期只读证明不变。
- 日常薄发布在取得安全源码目录与服务账号后、读取 tracking tip 前调用该刷新能力；刷新失败或回执形状异常时，不进入候选构建、暂存或 current 切换。
- Chroma/`code_vec`、运行时部署、薄发布、MCP、队列相邻组合回归：`634 passed, 4 skipped`；Ruff 与 `git diff --check` 均通过。
- 完成回执：旧 runtime 仅调用既有生产 Git 目标证明，受限刷新固定 `origin/dev` 跟踪引用；未执行 checkout、旧 `runtime deploy`、current 切换、服务操作或索引/数据库写入。随后目标候选复用既有 base 进入薄发布。
- 首次 promotion 的通用失败已按既有补偿路径自动回滚，未留下半切换状态；对同一官方阶段状态机的受控阶段复验随后完整通过。当前 release 为 `777e48dbd001ef06e07684636293c89e56239d61e9de60159138f8ee320177f5`，revision 为 `9137d055eab74fbc6ea6b738480c2c23dbd8ecc1`。
- 最终 `platform-docs`、`codegraph`、`agent-memory`、`graph` 四个 MCP 端点均为 OK，常驻 reindex worker 正常、队列为空；日常后续提交继续只走 hook 入队与增量消费，不再触发 runtime/base/依赖或全库重建。
