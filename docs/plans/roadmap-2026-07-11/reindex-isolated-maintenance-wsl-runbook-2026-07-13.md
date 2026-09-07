# 隔离 Reindex 维护窗口与 WSL 发布/回滚运行手册

> 状态：🟡 待随本轮代码在 WSL 实测后关闭
>
> 日期：2026-07-13
>
> 2026-07-16 修订：CodeGraph 维护边界已升级为永久 guard + 耐久 hold + runtime mask + stop，
> reindex marker 改为整个组合恢复的最后提交点。本文中的顺序以本修订后的第 3.1、5、6.1 节为准。
>
> 2026-07-23 修订：冻结 runtime 的 worker 在 marker 内取得 `intent SH → gate SH` 待命许可；若整个
> CodeGraph/入口恢复一直占用 gate EX，会在其有界等待后按 fail-closed 退出。旧的“单个 gate EX 覆盖全部组合恢复”描述废止，
> 改为 controller 会话锁覆盖全流程、gate EX 只覆盖 CodeGraph 长阶段和最终短提交。CodeGraph/入口可先达到已验收状态，
> 但 marker 仍存在且 worker 仍无索引写许可；受控生产者仅可 enqueue pending，最终删除 marker 后不再执行可能失败的外部动作。
>
> 适用范围：Linux/WSL 上的 `codev-reindex.service`、其 reindex 队列和 manifest 验收；以及为维护期排除 CodeGraph 追赶同步写入而进行的**唯一** `codev-mcp-codegraph.service` 受控停启和主 unit 布局迁移
> 不适用范围：除此二者外的其他 `codev-*` 服务、旧 WSL 工作树、全量 systemd 的功能部署、数据库结构回退；第 3.5 节仅规定全量安装器与维护窗口的互斥边界

## 1. 目标与硬性边界

本手册用于把一个已推送的完整 Git 提交发布到 WSL 的隔离 reindex worker，并在出现故障时回到上一个已验证版本。它解决的是维护窗口内的 worker 竞争、legacy 队列遗留、错误运行目录和“队列空但 manifest 仍是旧提交”四类风险。

必须遵守以下边界：

- 默认只操作 `codev-reindex.service`。唯一的窄例外是 `prepare` 对 `codev-mcp-codegraph.service` 建立永久 condition guard、耐久 hold、runtime mask 和停机证明，并由第 6.1 节受控恢复。不得在 `prepare` 前手工 mask/stop，也不得用批量 `restart` 脚本触及其他 `codev-*` 单元。
- 本轮“零并发”承诺只覆盖 AI 索引数据面：worker 消费、直接 `reindex`、CodeGraph `sync`/`serve`、Chroma、`ingest` 与 `code_vec` 的实际索引或 manifest 写入。它**不**宣称维护期内队列控制面零写入；受控生产者仍可追加 pending 待办，相关边界见第 3.4 节和第 4 节。
- 发布验收前不得在旧 WSL 工作树上执行 `pull`、`reset`、`checkout`、删除文件或修改其 `.venv`。完整验收后，仅可按第 6.3 节证明 worktree clean、非活动且不承载数据，再由 `git worktree remove` 与 `git worktree prune` 清理。
- 不得手工删除 reindex marker/drop-in、CodeGraph hold/runtime mask、永久 guard drop-in 或门禁锁。永久 guard 在恢复成功后也保留；其他对象只能由受控状态机创建或解除。
- 不得在维护 marker 存在时直接运行 `reindex-queue worker`、`drain-once`、`reindex`、`graph ingest`、`python -m codev_platform.chroma.indexer`、`python -m codev_platform.recall.code_vector_store`，或任意 CodeGraph `serve/init/index/sync/uninit`。marker 存在即拒绝所有通用 reindex 写许可，`codev-reindex.service` cgroup 也不例外；恢复阶段该 service 只能按受控状态机启动为**待命实例**，不得加载运行时、打开/认领/消费队列，也不得写 manifest、索引或数据库。只有 marker 被最后耐久删除后，待命实例才可进入正常消费。
- 不得把 token、DSN、完整用户配置、个人目录或任何真实凭据写进命令历史、手册、映射文件或提交。
- 从第 2.2 节开始到第 6 节 `codegraph` manifest 成功并完成受控恢复前，禁止在旧 WSL 工作树运行 Git hook、`reindex`、`reindex-queue drain-once`、旧 CodeGraph MCP 或任何自定义索引写脚本。新维护协议会扫描已存在的已知写入口，并在管理员锁内再次证明；但旧版直接 `reindex`、旧 launcher、旧 CodeGraph shared daemon 或自定义写入进程若在第二次扫描后才启动，既不读取新 marker，也不共享新 run lock，不能由新代码单方原子约束。若本次发布需要形式化的零并发保证，必须先取得 OS 级旧运行用户 quiesce/freeze 的明确授权。

以下变量均由操作员在受控终端中设置，示例不包含个人绝对路径：

```bash
set -euo pipefail

export RELEASE_SOURCE='<干净的普通 clone 或 Gitea bare 接收仓>'
export TARGET_SHA='<已推送至 origin/dev 的完整小写提交 OID>'
export PROJECT_ID='<逻辑项目标识>'
export RELEASE_ROOT='<受管 release 根目录>'
export RELEASE_DIR="$RELEASE_ROOT/$TARGET_SHA"
export RELEASE_VENV="$RELEASE_DIR/.venv"
export RELEASE_UNIT_PATH="/usr/local/bin:/usr/bin:/bin:$RELEASE_VENV/bin"
export RELEASE_CODEGRAPH_CMD=''
export CONFIG_OVERLAY='<本次 release 专用配置覆盖文件>'
export PLATFORM_DATA_DIR='<与 CONFIG_OVERLAY 中 data.platform_data_dir 完全一致的绝对路径>'
export SERVICE_USER='<codev-reindex.service 的 User 值>'
export CODEGRAPH_UNIT='codev-mcp-codegraph.service'
export CODEGRAPH_PORT='<从当前受管 unit 读取的端口>'

release_service_python() (
  cd -- "$RELEASE_DIR"
  exec sudo -H -u "$SERVICE_USER" env -u PYTHONPATH -u PYTHONHOME \
    -u PLATFORM_DOCS_VENV -u CODEV_REINDEX_REPO_OVERRIDE \
    -u CODEV_REINDEX_CONFIG_SHA256 \
    PYTHONNOUSERSITE=1 PYTHONDONTWRITEBYTECODE=1 \
    CODEV_PLATFORM_CONFIG="$CONFIG_OVERLAY" CODEV_RELEASE_DIR="$RELEASE_DIR" \
    CODEV_TARGET_SHA="$TARGET_SHA" PATH="$RELEASE_UNIT_PATH" \
    CODEGRAPH_CMD="$RELEASE_CODEGRAPH_CMD" \
    "$RELEASE_VENV/bin/python" -I "$@"
)

release_root_python() (
  cd -- "$RELEASE_DIR"
  exec sudo env -u PYTHONPATH -u PYTHONHOME \
    -u PLATFORM_DOCS_VENV -u CODEV_REINDEX_REPO_OVERRIDE \
    -u CODEV_REINDEX_CONFIG_SHA256 \
    PYTHONNOUSERSITE=1 PYTHONDONTWRITEBYTECODE=1 \
    CODEV_PLATFORM_CONFIG="$CONFIG_OVERLAY" PLATFORM_DATA_DIR="$PLATFORM_DATA_DIR" \
    CODEV_RELEASE_DIR="$RELEASE_DIR" CODEV_TARGET_SHA="$TARGET_SHA" \
    PATH="$RELEASE_UNIT_PATH" \
    CODEGRAPH_CMD="$RELEASE_CODEGRAPH_CMD" \
    GIT_CONFIG_COUNT=1 GIT_CONFIG_KEY_0=safe.directory \
    GIT_CONFIG_VALUE_0="$RELEASE_DIR" \
    "$RELEASE_VENV/bin/python" -I "$@"
)
```

`TARGET_SHA` 必须是完整提交 OID，不允许短 SHA、当前旧工作树的 `HEAD` 或模糊分支名。所有代码块必须在同一个受控 shell 中执行；若更换终端，先重新执行本节变量块（含 `set -euo pipefail`），不得省略错误门禁。所有 release 命令必须经上述函数调用：它们固定进入 `$RELEASE_DIR`、清空宿主 `PYTHONPATH`/`PYTHONHOME`/`PLATFORM_DOCS_VENV`、禁用用户 site，并使用 Python `-I` 与固定 `$RELEASE_UNIT_PATH`。该 PATH 必须与受管 Python unit 一致，不能依赖旧 Chroma venv、用户 npm 目录或登录 shell 的 `codegraph` 命令；`RELEASE_CODEGRAPH_CMD` 只能为空（默认 PATH 命令）或与受信 unit 环境完全一致的 root 受控绝对可执行路径。root 函数只为该物理 release 临时声明 Git safe-directory，供运行身份证明读取其不可变 HEAD；不得写入全局 Git 配置或信任通配路径。

## 2. 发布前门禁

### 2.1 固定来源并创建独立发布版本

发布目录的父目录、worktree 与 venv 必须都由 `$SERVICE_USER` 创建和持有；root 只在后续受控命令中读取该 release，并以局部 `safe.directory` 完成运行身份证明。不得先由 root 创建 `$RELEASE_DIR` 再让服务用户接管，也不得复用已有同名目录。创建 worktree 时显式禁用 Git hook，避免旧 hook 在发布过程中自动启动 worker。

`$RELEASE_SOURCE` 有两种受支持形态，二者只能二选一：普通 clone 必须有 `origin` remote；Gitea bare 接收仓通常只有本地 `dev` 分支，不能假定其存在 `origin` remote。bare 形态还要求 `$SERVICE_USER` 对 bare 仓及其 Git worktree 元数据完整可写；基本路径门禁只作早期提示，实际 `git worktree add` 仍是最终 fail-closed 证明。以下命令均先证明已推送的完整 `$TARGET_SHA`，不以旧工作树的 `HEAD` 或模糊分支名替代：

```bash
# 两种来源共用的服务用户目录门禁
sudo -H -u "$SERVICE_USER" test ! -e "$RELEASE_DIR"
sudo -H -u "$SERVICE_USER" mkdir -p -- "$(dirname -- "$RELEASE_DIR")"
sudo -H -u "$SERVICE_USER" test -w "$(dirname -- "$RELEASE_DIR")"

# 形态 A：普通 clone
sudo -H -u "$SERVICE_USER" git -C "$RELEASE_SOURCE" fetch origin \
  'refs/heads/dev:refs/remotes/origin/dev'
test "$(sudo -H -u "$SERVICE_USER" git -C "$RELEASE_SOURCE" rev-parse 'origin/dev^{commit}')" = "$TARGET_SHA"
sudo -H -u "$SERVICE_USER" git -C "$RELEASE_SOURCE" -c core.hooksPath=/dev/null \
  worktree add --detach "$RELEASE_DIR" "$TARGET_SHA"

# 形态 B：Gitea bare 接收仓（不要执行形态 A）
sudo -H -u "$SERVICE_USER" git --git-dir="$RELEASE_SOURCE" rev-parse --is-bare-repository | grep -qx true
sudo -H -u "$SERVICE_USER" test -w "$RELEASE_SOURCE"
test "$(sudo -H -u "$SERVICE_USER" git --git-dir="$RELEASE_SOURCE" rev-parse 'dev^{commit}')" = "$TARGET_SHA"
sudo -H -u "$SERVICE_USER" git --git-dir="$RELEASE_SOURCE" -c core.hooksPath=/dev/null \
  worktree add --detach "$RELEASE_DIR" "$TARGET_SHA"
```

在 `$RELEASE_DIR` 中建立新的 venv 并安装当前 release 的完整运行时依赖。不得复制旧 venv，也不得让 systemd 指向旧工作树；裸 `pip install -e "$RELEASE_DIR"` 只会安装源码，隔离模式下会缺少 `chromadb`、MCP SDK 等服务依赖，严禁用于发布。

```bash
sudo -H -u "$SERVICE_USER" env -u PYTHONPATH -u PYTHONHOME \
  PYTHONNOUSERSITE=1 PYTHONDONTWRITEBYTECODE=1 \
  python3 -I -m venv "$RELEASE_VENV"
sudo -H -u "$SERVICE_USER" env -u PYTHONPATH -u PYTHONHOME \
  PYTHONNOUSERSITE=1 PYTHONDONTWRITEBYTECODE=1 \
  "$RELEASE_VENV/bin/python" -I -m pip install -e "${RELEASE_DIR}[runtime]"
```

`runtime` 会安装 Chroma、MCP SDK、模型运行时等直接依赖。若该 WSL 依赖经验证的 CUDA Torch 轮子，必须在第二条命令前按该主机已验证的版本和受控 wheel 源安装，并在下面的依赖探针中确认 `torch` 可导入；不得因新建 venv 静默降级为不兼容的 CPU 或其他 CUDA 构建。

在写入任一 release drop-in 或生成 manifest 前，必须同时用服务用户与 root 做无副作用的来源探针；它只输出固定成功标志，绝不回显路径、环境或配置内容。

```bash
RELEASE_PROBE='from pathlib import Path; import os, codev_platform; from codev_platform.core.runtime_identity import runtime_identity; root = Path(os.environ["CODEV_RELEASE_DIR"]).resolve(); assert Path(codev_platform.__file__).resolve().is_relative_to(root); assert runtime_identity().runtime_revision == os.environ["CODEV_TARGET_SHA"]; print("release-runtime=ok")'
release_service_python -c "$RELEASE_PROBE"
release_root_python -c "$RELEASE_PROBE"

RELEASE_DEPENDENCY_PROBE='import chromadb, jieba, mcp, rank_bm25, sentence_transformers, torch; import codev_platform.chroma.server, codev_platform.codegraph.server, codev_platform.graph.mcp_server; print("release-dependencies=ok")'
release_service_python -c "$RELEASE_DEPENDENCY_PROBE"
release_root_python -c "$RELEASE_DEPENDENCY_PROBE"

RELEASE_CODEGRAPH_PROBE='import os, shutil; from pathlib import Path; from codev_platform.codegraph.server import _resolve_codegraph_cmd; command = _resolve_codegraph_cmd(); candidate = Path(command); assert (candidate.is_file() and os.access(candidate, os.X_OK)) if candidate.is_absolute() else shutil.which(command) is not None; print("release-codegraph-cli=ok")'
release_service_python -c "$RELEASE_CODEGRAPH_PROBE"
```

任一来源、依赖或 CodeGraph CLI 探针失败都不得继续：尤其不能从 `/mnt/*` 当前目录、继承宿主 `PYTHONPATH`，或借旧 venv 的传递依赖、用户级 PATH 后“碰巧能运行”的 Python 继续发布。若受信 unit 配置了 `CODEGRAPH_CMD`，操作员必须把同一绝对路径显式填入 `RELEASE_CODEGRAPH_CMD` 后再探针；不得让 wrapper 悄悄继承登录环境的同名变量。

发布配置覆盖必须从服务用户当前有效配置的完整副本生成，并由目标服务用户可读；除下列受控字段外不得丢失原有配置：

- `codev-platform` 项目的 `repo_path` 指向 `$RELEASE_DIR`；
- `reindex.execution_mode` 为 `isolated`；
- `runtime.chroma_venv` 精确指向 `$RELEASE_VENV`；该字段只供 `platform-docs` 使用，不能再作为 CodeGraph、worker 或其他平台服务的解释器来源；任一 systemd 静态环境或通用环境文件都不得设置 `PLATFORM_DOCS_VENV`，受管 unit 会在读取通用环境文件后显式清空它；
- 覆盖配置与服务用户的基础配置都将 `reindex.worker_auto_start` 设为 `false`；基础配置只允许改这一个已知键，必须保留其他既有配置；
- `$CONFIG_OVERLAY` 与 `$PLATFORM_DATA_DIR` 都是稳定的绝对路径；覆盖文件必须是常规文件，路径中不得含空白、引号或反斜杠；`$PLATFORM_DATA_DIR` 必须精确等于覆盖配置中的 `data.platform_data_dir`；
- 两个固定 unit 只能在第 3.2 节通过同一份 root 受管快照取得 `CODEV_PLATFORM_CONFIG`、`PLATFORM_DATA_DIR` 与配置摘要；不得依赖当前 shell、静态 `Environment=` 或人工复制的值；
- release 专用 unit 覆盖只改 `codev-reindex.service` 的运行身份。它必须在任一 `EnvironmentFile=` 后写入 `Environment=PYTHONPATH=`、`Environment=PYTHONHOME=`、`Environment=PLATFORM_DOCS_VENV=`、`Environment=PYTHONNOUSERSITE=1`、`Environment=PYTHONDONTWRITEBYTECODE=1`；其 `ExecStart` 使用 `$RELEASE_VENV/bin/python -I -m codev_platform.cli reindex-queue worker --require-execution-mode isolated`，并保留 `Delegate=yes`、`KillMode=control-group`、`TimeoutStopSec=30` 和 `SendSIGKILL=yes`。

写入并复核上述 reindex 专用 unit 覆盖后，只执行该单元所需的 daemon reload；此时不要启动、停止或重启任何服务。不要调用全服务安装器。

### 2.1.1 CodeGraph 主 unit 的 canonical 布局迁移

本步骤只把唯一的 `codev-mcp-codegraph.service` 主 unit 从 legacy 目录
`/etc/systemd/system` 条件迁移至 canonical 目录
`/usr/local/lib/systemd/system`；它不安装其他 unit、不修改 release drop-in，也不启动、停止或重启服务。必须在 `prepare` 前完成：维护 marker 必须明确不存在，CodeGraph 必须尚未 runtime mask，且当前 `FragmentPath` 必须精确指向 legacy 主文件。若当前仅存在已知的无效 `/run/systemd/system/codev-mcp-codegraph.service -> /dev/null` 残留，可由迁移命令受控清理；其他 mask 或无法证明的解析状态一律停止。

先确认 release 头提交精确等于 `$TARGET_SHA`，再仅以服务用户和目标 release venv 生成本次 manifest。该命令的唯一用途是生成 `install-manifest.json` 与同目录源 unit；**不得执行**它打印的 `install.sh` 或任何 `sudo` 安装命令。

```bash
test "$(sudo -H -u "$SERVICE_USER" git -C "$RELEASE_DIR" rev-parse HEAD)" = "$TARGET_SHA"
release_service_python -m codev_platform.cli serve-mcp install-systemd \
  --user "$SERVICE_USER"
export SYSTEMD_MANIFEST="$(getent passwd "$SERVICE_USER" | cut -d: -f6)/codev-systemd/install-manifest.json"
```

迁移前必须以目标 release venv 调用 `load_verified_install_input()` 审计 `$SYSTEMD_MANIFEST`：只接受唯一的 CodeGraph payload、其内容摘要与 manifest 一致，且 unit 内容中的 `ExecStart` 精确指向 `$RELEASE_VENV/bin/python -I -m codev_platform.codegraph.server`；同时核验 `codev-mcp-platform-docs.service` 的 `ExecStart` 精确指向 `$RELEASE_VENV/bin/python -I -m codev_platform.chroma.server`。记录 `$TARGET_SHA`、manifest 的 SHA-256 和 CodeGraph payload 的 SHA-256；不记录配置内容、令牌或环境变量。manifest 生成目录、manifest 或任一源 unit 在校验后发生替换、改名或摘要不匹配时必须失败关闭，重新从目标 release 生成，不得复用旧 manifest。

在真实迁移前，以 root 在 `/usr/local/lib/systemd/system` 下的随机隐藏 probe 名中调用项目的 `create_root_owned_regular_file_atomic_if_absent()` 与 `move_root_owned_regular_file_if_snapshot()`：必须读取创建后原像并验证移动后 device/inode 保持一致，以实测目标 WSL 的 `linkat` 与 `renameat2(RENAME_NOREPLACE)` 能力。probe 的创建、移动、验证与清理由同一受控临时操作记录；任何一步失败都不得执行真实迁移。

满足下列前态后，才执行唯一窄命令：marker 不存在、`LoadState=loaded`、`UnitFileState=enabled`、`FragmentPath=/etc/systemd/system/codev-mcp-codegraph.service`，且 CodeGraph 的 active、result、执行状态、`InvocationID` 与 enable 链接均已记录。

```bash
release_root_python -m codev_platform.cli reindex-maintenance \
  migrate-systemd-layout --manifest "$SYSTEMD_MANIFEST" --yes
```

成功后必须证明 canonical 主文件存在、legacy 同名主文件不存在、legacy 隐藏 backup 保留、`multi-user.target.wants` 链接指向 canonical，且除 `FragmentPath` 外上述运行态与启用态均不变。若报告“安全状态未证明”，立即停止在该阶段：保留 backup、rollback 证据和当前 mask 状态；不得手工删除 unit、裸 `unmask`、执行全量安装器或继续 `prepare`。布局迁移是结构性变更，release 回滚不会自动反向迁移主 unit。

### 2.2 在 `prepare` 中受控停用 CodeGraph MCP

这一步是本次发布的前置，不可与第 3 节并行。实装的 CodeGraph 0.9.7 中，`--no-watch` 只关闭文件 watcher；MCP engine 在首次打开项目后仍会无条件执行追赶同步 `catchUpSync()`，其内部调用 `cg.sync()`。`CODEGRAPH_NO_DAEMON=1` 只切换 direct mode，也不能关闭该同步。因此任意 `codegraph serve --mcp` 或旧 shared daemon 都是潜在索引写者，维护期没有可验证的“只读 CodeGraph MCP”例外。

先从当前 unit 读取并复核 `User`、`WorkingDirectory`、端口、`ExecStart` 与 `ControlGroup`。持久的 release drop-in 仍指向 `$RELEASE_VENV`。`Restart=no` 无法阻止显式启动，runtime mask 又会在重启后消失，因此两者都不能单独作为权威启动禁令。`prepare` 必须先安装并证明永久 `99-codev-maintenance-condition.conf`，再写入 `/var/lib/codev-platform/codegraph-maintenance.gate`，随后才允许 runtime mask 和停机。release drop-in 必须清空旧 `ExecStart`，改为：

```ini
[Service]
Environment=CODEGRAPH_NO_WATCH=1
Environment=CODEGRAPH_NO_DAEMON=1
KillMode=control-group
TimeoutStopSec=30
SendSIGKILL=yes
ExecStart=
Environment=PYTHONPATH=
Environment=PYTHONHOME=
Environment=PLATFORM_DOCS_VENV=
Environment=PYTHONNOUSERSITE=1
Environment=PYTHONDONTWRITEBYTECODE=1
ExecStart=<RELEASE_VENV>/bin/python -I -m codev_platform.codegraph.server --http --port <CODEGRAPH_PORT>
```

保留当前已经验证的 `User`、`WorkingDirectory`、认证环境和端口；不得复制或覆盖完整旧配置，不得执行未经本轮 staged 事务证明的旧安装脚本。若 unit 配置了通用 `systemd.env_file`，其 `EnvironmentFile=` 是**必需文件**（不得使用前导 `-`）；该文件、其父目录链必须由 root 所有且不可被组/其他用户写入。它只能承载无冲突的认证类变量，不能包含三项受保护变量、`CODEV_REINDEX_REPO_OVERRIDE` 或 `PLATFORM_DOCS_VENV`；若含 `CODEGRAPH_CMD`，必须是 root 受控绝对可执行路径并与 `$RELEASE_CODEGRAPH_CMD` 完全一致。`CODEGRAPH_NO_WATCH=1` 与 `CODEGRAPH_NO_DAEMON=1` 仅是常态策略，不能作为维护期放行依据。由受控 `prepare` 执行 `guard → guard proof → hold → runtime mask → stop/reset-failed → final proofs`；不得手工删除 guard/hold、裸 `unmask` 或启动该 unit。

旧 shared daemon 不是 `codegraph unlock` 能处理的对象；`unlock` 只删除索引锁，可能反而放大并发写风险。若发现 `<repo>/.codegraph/daemon.pid` 或 socket，必须先校验 pid 文件记录的 PID、对应 `/proc/<pid>/cmdline` 中受控 bundled CodeGraph 脚本、`serve --mcp` 与该 repo 路径完全一致，才可由获得授权的操作员向该 PID 发送 `SIGTERM`。随后必须证明 PID、pid 文件和 socket 均已消失；任一字段不匹配、不可读或仍存活都 fail-closed，不得使用广泛 `pkill`、手工删 pid/socket 或把 `unlock` 当作替代。

进入第 2.3 节前，至少证明以下事实：

- release drop-in 的 `ExecStart` 指向 `$RELEASE_VENV`，不再指向旧 WSL 工作树；
- 永久 guard drop-in 是 root:root `0644` 的精确原像，并且是最后生效的条件 drop-in；
- CodeGraph 耐久 hold 为 root:root `0644` 的精确固定原像；
- `$CODEGRAPH_UNIT` 的 `LoadState=masked` 且 `UnitFileState=masked-runtime`；
- `$CODEGRAPH_UNIT` 为 inactive/dead、基线 `Restart=always`，且其 `ControlGroup` 已空；
- systemd 外不存在任意已知 CodeGraph `serve` 或 shared daemon；
- 本步骤只停止了 `$CODEGRAPH_UNIT`，没有重启或安装其他 `codev-*` 单元。

从此处直到第 6.1 节完成受控恢复前，禁止解除 hold/runtime mask、启动 `$CODEGRAPH_UNIT`，也禁止为“验证读取”发起任何 MCP 查询；首次查询本身就是潜在追赶同步。永久 guard 始终保留，常态时由 hold 不存在使条件成立。

### 2.3 首次预建由 root 所有的门禁锁

首次在该 WSL 主机启用本机制时，先以 root 执行一次 `provision --yes`。它只预建由 root 所有的全局门禁目录和锁，使普通服务用户可在同一把锁上取得共享许可；它不启动 worker，也不打开维护 marker。

```bash
release_root_python -m codev_platform.cli reindex-maintenance provision --yes
```

若该动作失败，不进入维护窗口。应先由有权限的操作员修复受管门禁目录的所有权或完整性，再重新执行同一动作；不得用宽松权限、手工创建锁文件或跳过门禁作为替代。

**marker 不存在不等于 worker 已获许可。** 服务用户仍必须能以共享方式安全打开由 root 预置的全局门禁锁；锁/目录缺失、所有权或权限不符合契约时 worker 会 fail-closed，不能消费队列。发布验收必须把 `provision --yes` 的成功记录与后续 worker 正常运行同时保留，不能只看 marker 文件是否不存在。

旧版 `reindex-worker-run.lock` 若缺少 schema 版本或 PID 出生身份，即使其中的 PID 目前被其他系统进程复用，也必须由本 release 的严格 run-lock 协议视为 stale 并受控回收。不得手工删除该业务 lock：恢复后的新 worker 获取 lock 时会原子替换它；验收时应确认新 lock 含当前 schema、当前 owner 与可验证的出生身份。`reindex-worker-run.guard` 是永久协调文件，只串行化业务 lock 的读取、回收、创建和删除，**不得人工删除**，也不得把它本身当作 worker 运行证据。仍显示“worker 已在运行”但没有可验证身份时，一律视为旧部署故障证据，不得把它当健康 worker。

## 3. 进入维护窗口

### 3.1 准备与安全证明

以 root 运行准备动作：

```bash
release_root_python -m codev_platform.cli reindex-maintenance prepare --yes
release_root_python -m codev_platform.cli reindex-maintenance status
```

`prepare --yes` 的固定安全顺序是：取得 transition-intent EX 后先耐久发布 reindex marker(M)，再取得 gate EX；随后由唯一 CodeGraph lifecycle 完成永久 guard(G)、首次 G 证明、耐久 hold(H)、runtime mask(R)、stop/reset(S) 和最终四重证明。退出 gate/intent 后再写入 reindex 的 `Restart=no` drop-in、reload、stop/reset，最后证明外部写入者与两个固定 cgroup 均已清空。若 gate EX 有界等待忙，M 已经先落盘，工具只允许受控预停旧 reader 后重试一次；任何失败都穷尽双服务补偿，不能把历史证明当成当前安全。

`status` 是不产生索引写入的安全门禁，只有同时满足下列条件才可进入下一步：

- 维护 marker 已启用；
- drop-in 是本工具写入且内容完整的受管文件；
- CodeGraph 永久 guard、耐久 hold、`masked-runtime`、inactive/dead 与空 cgroup 均已证明；
- 未发现 systemd 外的已知索引写入口：`worker`、`drain-once`、直接 `reindex`、`graph ingest`、Chroma/code_vec 正式模块入口、三个 `post-* --foreground` hook，以及任意 CodeGraph `serve`/shared daemon；
- `codev-reindex.service` 已停，且其 cgroup 中没有残留进程。

任一证明失败都应视为 **fail-closed**：保持 marker、永久 guard、CodeGraph hold/runtime mask 与 reindex `Restart=no`，不启动 worker，不手工删除文件。先调查失败原因，再重复 `status`；不能用“服务看起来没在跑”代替原像、cgroup、mask 与外部进程证明。

### 3.2 安装并证明同源恢复配置

只有 `prepare --yes` 与 `status` 已成功、两个固定 unit 均已停机且 CodeGraph 的 G/H/R/S 边界已证明时，才以 root 安装恢复配置快照。该步骤不会启动、停止或重启服务；它只原子写入唯一共享环境文件与两个固定 drop-in，重载 systemd 后立即做同源证明。

```bash
release_root_python -m codev_platform.cli reindex-maintenance \
  configure-resume-codegraph --project "$PROJECT_ID" \
  --target-commit "$TARGET_SHA" --yes
```

该命令从调用方的同一份显式覆盖配置计算摘要，并写入：

- `/etc/codev-platform/reindex-codegraph-resume.env`：root 所有、模式 `0600`，且只能含 `CODEV_PLATFORM_CONFIG`、`PLATFORM_DATA_DIR`、`CODEV_REINDEX_CONFIG_SHA256` 三项；
- `/etc/systemd/system/codev-reindex.service.d/20-codev-reindex-codegraph-resume.conf`；
- `/etc/systemd/system/codev-mcp-codegraph.service.d/20-codev-reindex-codegraph-resume.conf`。

两个 drop-in 均以 `EnvironmentFile=/etc/codev-platform/reindex-codegraph-resume.env` 引用同一快照。三项受保护变量与 `PLATFORM_DOCS_VENV` 不得出现在任一 unit 的静态 `Environment=`、`PassEnvironment`、`UnsetEnvironment`、PAM 环境或其他 `EnvironmentFile` 中；`CODEV_REINDEX_REPO_OVERRIDE` 也不得出现在这些来源或调用命令的环境中。保留的辅助环境文件必须是绝对路径、必需文件、root 受控的常规文件，且不能覆盖上述变量；若另行提供 `CODEGRAPH_CMD`，其路径必须与本节发布探针的 `RELEASE_CODEGRAPH_CMD` 一致。三份文件的原像读取、临时写入、替换与失败回滚删除均锚定在逐级验证的 root 目录句柄中；每份原像必须在同一受信文件描述符上绑定内容、权限位、uid 与 gid，回滚写回后再次逐项复核这四项，不得以新目标文件的默认权限或默认属组替代。父目录为链接、非 root 所有或可被组/其他用户写入时必须失败关闭，不能以路径式 `cp`、`rm` 或补一次 `stat` 替代。

不带 `--yes` 仅输出演练提示，不写入文件或重载 systemd。安装或证明失败时保持维护态，修复 release 配置或受管文件后重跑同一命令；不得用 `tee`、`cp`、`chmod` 或手工 `Environment=` 补写来绕过证明。每次改变 `$CONFIG_OVERLAY`、`$PLATFORM_DATA_DIR`、两个 unit 的环境来源或发布版本后，都必须在下一次 `resume-codegraph` 前重新执行本步骤。

### 3.3 门禁对象的含义

| 对象 | 作用 | 未通过时的处理 |
|---|---|---|
| 维护 marker | 持久化维护状态机；`maintenance` 阶段拒绝所有写入，恢复阶段只允许已绑定身份的 service 待命 | 保持或回退到维护状态，不手工清除 |
| 由 root 所有的门禁锁 | 把“检查无 worker”和“新的 launcher spawn”串行化 | 修复锁的受管状态后重新 `provision` |
| `Restart=no` drop-in | 防止停止 reindex 后被 systemd 自动重启；待命实例稳定后才恢复常态策略 | 仅在已证明 `Restart=always` 后由工具完成 marker 交接 |
| CodeGraph 永久 guard | 让 canonical unit 与永久 drop-in 都受同一个 hold 路径约束，跨重启仍生效 | 原像、顺序或有效条件不精确时不得创建 hold或进入 stage |
| CodeGraph 耐久 hold | 在受管数据根中持久表达维护期启动禁令 | guard 未先证明时不得创建；恢复只由状态机删除 |
| CodeGraph runtime mask | 当前启动周期的附加显式启动禁令 | 保持 mask；持久 mask、未知状态或解除失败均不继续 |
| 外部写入者证明 | 拒绝服务 cgroup 之外的已知 worker、直接索引命令、前台 hook，以及任意 CodeGraph `serve`/shared daemon；维护期不存在 CodeGraph MCP 只读放行 | 找到并受控停止来源后重新 `status` |
| systemd/cgroup 证明 | 确认 `codev-reindex.service` 与 CodeGraph 固定 unit 已 inactive/dead 且控制组为空 | 不以 PID 复用、进程名猜测或队列空替代 |

### 3.4 维护期的队列操作边界

维护窗口允许受控生产者执行 `reindex-queue enqueue`，把带完整 `target_commit` 的任务追加为 pending 积压；这只是在控制面记录待办，**不会**授予 worker 消费权限、认领 lease 或执行索引写入。维护 marker 存在期间，普通 `worker`、`drain-once` 和 service 的正常消费均必须拒绝；service 即使在恢复中已启动，也只能待命。`codev-mcp-codegraph.service` 在整个维护期保持 stopped，索引新鲜度只能由恢复后的隔离 reindex worker 负责。

本轮 P0 不把队列控制面声明为零并发：pending 积压是有意允许的运行契约，待 marker 被最后耐久删除后再由恢复后的隔离 worker 消费。`reindex-queue prune-stale --yes` 与 `reindex-queue break-lease --yes` 当前尚未被本维护入口自动纳入维护独占许可；它们会删除、释放或改写既有队列状态，属于已知 P1，**本次发布窗口不得执行**。若后续获批执行，应先单独实现并验证“停机证明 + 维护独占许可 + run lock 内外部写者复证”的专用入口，演练和只读 `status` 均不能替代该互斥。

`migrate-legacy --yes` 与 `init-owner --yes` 仍必须遵守第 4 节的受控流程；但其当前预检可能在取得维护独占许可前打开队列，因而创建受管 File 目录或初始化 PG schema。这是已知控制面 bootstrap 副作用，不是 AI 索引或 manifest 数据面写入，亦不纳入本轮 P0 的零并发承诺；预检成功不能替代确认写操作的维护互斥。

### 3.5 全量 systemd 安装器与维护窗口互斥

本手册的发布步骤仍**不得执行**全量 `serve-mcp install-systemd` 的安装脚本；第 2.1.1 节仅允许目标 release、服务用户受控生成 manifest，绝不执行其输出的安装命令。但常态运维的全量安装器必须与维护窗口共用同一把全局门禁锁，避免其在 CodeGraph runtime mask 检查之后、实际重启之前被 `prepare` 穿透。

- 每次生成后必须使用新的 `install.sh` 与同目录 `install-manifest.json`；旧版脚本直接执行 `cp/systemctl`，不具备本节互斥与补偿语义，不得在 WSL 使用。
- Bash 脚本只做 marker/hold/runtime mask 的快速零副作用拒绝，随后 `exec` 受控 Python 事务。事务以 root 预置门禁锁，持有共享许可后再次复核维护边界；再以同一个无跟随父目录句柄读取 manifest 与全部源文件，并逐项校验 SHA-256。生成目录并发重建、目录改名或源文件替换都会失败关闭。
- 事务在任何写入前快照目标 unit 的内容、权限位、uid、gid 与可精确恢复的启用/活动状态；只接受明确的 `enabled`/`disabled`/`not-found` 和 `active`/`inactive`，`static`、`indirect`、`masked`、`failed` 及过渡态一律在写入前拒绝。写入、`daemon-reload`、`enable`、`restart` 后逐项证明目标 unit 为 active；不执行不可逆的 `reset-failed`，避免丢失旧 failed/start-limit 语义。
- 任何步骤失败时，必须在同一共享许可内逆序恢复 unit 原像、再次 reload、分别恢复并复证原启用/活动状态及文件原像；启用态恢复失败也不得阻断活动态恢复。许可正常体完成后若 `__exit__` 异常，事务只会重新取得并复核新的共享许可后补偿；不能重新取得或补偿证据不足时只报告安全状态未证明。
- 受信输入读取完成后、快照与首次写入前，事务在主线程安装 SIGINT 延后守卫，并保持到原许可退出、必要的二次许可补偿和全部复证结束。连续 `Ctrl+C` 只会被计数，不得截断任何恢复或证明；守卫建立失败时在首次快照/写入前失败关闭。结算已证明时恢复调用方原处理器并重放一次中断；若安全状态未证明，则仍以“安全状态未证明”为顶层错误，并把延后中断保留为异常原因。该守卫只延后 Python 进程中的中断；若终端信号使正在执行的 `systemctl` 子进程失败，后续补偿和复证仍必须失败关闭，不能把子进程退出当作已恢复。
- `prepare` 在 `M → gate EX → G → G proof → H → R/S` 期间持有对应 intent/gate 能力。安装器先取得共享许可时，`prepare` 必须等待；`prepare` 先发布 marker 或建立维护边界时，安装器会在许可内二次复核后零副作用退出。

生成目录与其 Python/venv 是获授权操作员调用 `sudo` 的输入边界；该命令不试图把可自行改写 sudo 命令的本地用户当作独立攻击者。即便如此，事务仍拒绝最终叶子的符号链接、稳定读取输入文件并把 `/etc/systemd/system` 的目标写入锚定到 root 受控目录，避免执行中的检查后替换。

## 4. Legacy 队列收口与稳定 owner 初始化

以下控制面写操作的**确认执行**必须在第 3 节的 `status` 成功后执行，且仍处于同一维护窗口和维护独占许可内。它们以 `SERVICE_USER` 和 release 配置运行；不要用 root 的默认配置、旧 venv 或旧工作树替代。维护期可单独积压 `enqueue`，但它不改变本节“先停机证明、再互斥迁移/初始化”的顺序。注意：命令的演练/预检打开队列时仍可能产生上一节所述 bootstrap 副作用；本节不把该副作用表述为零写入或迁移成功。

### 4.1 先演练 legacy 迁移

先运行不带 `--yes` 的演练：

```bash
release_service_python -m codev_platform.cli reindex-queue migrate-legacy
```

若演练提示 pending 遗留，需要根据输出的模板创建严格 JSON 映射，并为每条记录填写完整小写 `target_commit`。映射文件必须是服务用户可读的受限普通文件，不能是符号链接，且不得包含凭据。再以相同方式重新演练：

```bash
release_service_python -m codev_platform.cli reindex-queue migrate-legacy \
  --pending-map '<受限映射文件>'
```

演练只用于确认迁移计划、映射完整性和预期遗留；当确有 legacy 数据时，它报告“迁移未完成”或以非零退出并不等同于执行失败，因为演练不会迁移、删除、释放或改写既有任务。当前队列后端在首次打开时仍可能创建受管 File 目录或初始化 PG schema；这是后端 bootstrap 副作用，不得被视为迁移成功，也不能替代确认写操作的维护互斥。只有当输出不再要求补充映射或修正不可迁移原因时，才允许确认执行。确认命令只多出 `--yes`：

第 1 节要求的 `set -euo pipefail` 会让上述“预期有 pending 的演练非零退出”停止当前受控 shell；这是有意保留的 fail-closed 行为。操作员必须审阅该次输出、补齐映射后重新打开受控终端并完整执行第 1 节变量块，再运行下一次演练。不得用 `|| true`、`set +e` 或忽略退出码把后续确认操作接在同一个失败路径后。

```bash
release_service_python -m codev_platform.cli reindex-queue migrate-legacy \
  --pending-map '<受限映射文件>' --yes
```

没有 pending 映射时，确认命令可以省略 `--pending-map`，但仍必须先完成相同的演练。确认操作完成后必须报告“迁移完成”；迁移失败或确认后仍报告未完成时，隔离 worker 不得恢复运行。

### 4.2 再初始化稳定 queue owner

迁移确认完成后，先演练、再显式确认初始化稳定 owner：

```bash
release_service_python -m codev_platform.cli reindex-queue init-owner

release_service_python -m codev_platform.cli reindex-queue init-owner --yes
```

`init-owner --yes` 与确认迁移都会要求 worker 已停、旧写入者已得到 systemd/cgroup 证明，并取得 reindex run lock；取得锁后会再次执行宽写入者证明，缩小扫描与写入之间的窗口。`init-owner` 不会输出 owner token；任何 token、队列连接信息或内部路径都不应记录在运维票据中。

完成迁移和 owner 初始化后，再运行一次维护窗口安全证明：

```bash
release_root_python -m codev_platform.cli reindex-maintenance status
```

## 5. 恢复与稳定性确认

所有维护门禁、迁移和 owner 步骤成功后，才以 root 恢复该一个服务：

```bash
release_root_python -m codev_platform.cli reindex-maintenance restore --yes
```

`restore --yes` 不等同于简单 `systemctl start`，必须按下列持久状态机串行交接：

1. `maintenance`：已证明停机、marker 和 `Restart=no` 均存在，所有 reindex 写入被拒绝。
2. `restore_armed`：工具写入带短时截止时间和随机 generation 的待命记录；随后只启动 `codev-reindex.service`。该实例只能待命，不能触碰运行时、队列或索引。
3. `restore_claimed`：工具读取本次 active/running 单元的 systemd `InvocationID`，把它与同一 generation 绑定；只有这一实例可继续待命，其他实例一律拒绝。
4. 在待命实例和重启计数均稳定后，工具删除受管 `Restart=no` drop-in、重载 systemd，并证明常态策略已恢复为 `Restart=always`；marker 继续保留，待命实例仍无写许可。
5. 第 6.1 节的 `resume-codegraph` 在 controller 会话锁内先以长 gate EX 证明 staged payload、解除 runtime mask/hold、启动 CodeGraph，完成 running、InvocationID/NRestarts、health、稳定窗口与 enable；释放该 EX 后仍持会话锁，才启动并稳定复证 reindex handoff、恢复 `Restart=always`。Webhook 在 marker 仍存在时完成受控入口验收，至多 enqueue pending；最后只用短 gate EX 快速复核 handoff 并耐久删除 marker。只有 marker 缺失后 reindex 才能消费，删除后不再执行可能失败的外部动作。

`restore_armed`、`restore_claimed` 的截止时间失效、`InvocationID` 不匹配、状态内容不受信任、稳定性证明失败或任何中断都必须 **fail-closed**：不把待命许可解释为写许可。工具会尝试把 marker 恢复为 `maintenance`、恢复 `Restart=no`、停止 reindex 并重新证明安全态；若补偿证据不完整，也必须保持停机并升级处理，不能手工删除 marker/drop-in 或直接启动 worker。

这一步只把 `codev-reindex.service` 恢复为受控待命，marker、CodeGraph hold/runtime mask 仍保留且 CodeGraph 继续 stopped。它不因 reindex 待命获得启动或写许可，最终组合提交只能由第 6.1 节执行。

## 6. 精确 SHA 索引与 manifest 验收

维护期如确有需要，可以由受控生产者先 `enqueue` 积压带完整 SHA 的 pending 任务；它不会触发消费。本手册默认在恢复成功后再由服务用户显式入队目标提交，以减少控制面变更与恢复交接重叠。无论时机如何，都不要依赖旧 hook、旧工作树 HEAD 或“队列自动发现”的版本。

```bash
release_service_python -m codev_platform.cli reindex-queue enqueue "$PROJECT_ID" \
  --kind all --target-commit "$TARGET_SHA"
```

恢复后的运行态观察使用队列状态命令；维护窗口的 `reindex-maintenance status` 只用于 marker 与停机证明，不应用于已恢复的服务。

```bash
release_service_python -m codev_platform.cli reindex-queue status
```

只通过受控队列状态观察队列与 worker，不要为加速而另起 detached worker。

### 6.1 CodeGraph MCP 的受控恢复

仅当 `codegraph` manifest 已为 `ok` 且目标提交精确等于 `$TARGET_SHA` 时，才可恢复 `$CODEGRAPH_UNIT`。恢复必须只走受控命令：

```bash
release_root_python -m codev_platform.cli reindex-maintenance resume-codegraph \
  --project "$PROJECT_ID" --target-commit "$TARGET_SHA" --yes
```

该动作先从显式覆盖配置构造唯一上下文，证明它与共享快照一致，并取得主仓与 extra 仓的共享操作租约及 controller 会话锁，以只读 URI 复核 manifest。长 gate EX 内固定执行：复证 G/H/R/S、同源配置、staged payload；解除 runtime mask 后证明唯一有效主 unit/drop-in/ExecStart，再解除 hold、启动固定 unit、读取同一 InvocationID/NRestarts 身份、证明 running/health/稳定窗口并 enable。随后释放长 gate EX、继续持会话锁，启动和绑定 reindex 待命实例、恢复基线并稳定复证；Webhook 在 marker 仍存在时完成入口验收，所有待命/服务就绪边界复证完成后，短 gate EX 只快速复核 handoff 并耐久删除 marker。任一步失败都穷尽组合补偿回到 `M+G+H` 与双服务停机边界；补偿证据不完整时只报告安全状态未证明。

成功后工具会证明固定 unit 为 active/running、调用前后 InvocationID 未变且 NRestarts 未增加、基线 `Restart=always`，并且仅接受配置导出的 `https://example.invalid/reference:<端口>/healthz`：只能 GET，必须得到 HTTP 200 和精确 JSON `{"status":"ok","service":"codegraph"}`。不接受 TCP 探测、`internal.example.invalid`、HTTPS、`/health`、查询参数或 MCP 查询。

此后 CodeGraph 的首次 MCP 查询仍可能执行追赶同步；这是恢复后的常态行为，不是“只读验证”。如确需实调 MCP 端点，只能在本节的恢复证明完成后进行，并把该查询按可能写入索引的操作记录。

### 6.2 完整 manifest 验收

即使 `$CODEGRAPH_UNIT` 已按第 6.1 节恢复，最终验收仍必须同时满足：

- `codegraph`、`ingest`、`code_vec`、`chroma` 四条 manifest 任务均为 `ok`；
- 四条任务的目标提交均精确等于 `$TARGET_SHA`；
- 队列清空不是单独验收条件；任何 pending、failed、旧提交或缺少任务都视为发布未完成；
- worker 运行身份、配置覆盖和项目仓路径均仍指向本次 `$RELEASE_DIR`，而不是旧 WSL 工作树。

验收结果应记录完整 SHA、四项状态、验证时间和 release 标识，不记录凭据或完整配置内容。

### 6.3 验收后清理临时 release

本手册只允许一个**活动** release：两个固定 unit、配置覆盖与四项 manifest 都已指向 `$RELEASE_DIR` 后，旧 release 不再是第二套环境，只是短时回滚证据。只有第 6.1 与第 6.2 节全部成功、marker 已耐久删除、CodeGraph 稳定健康，且两个固定 unit 的 `ExecStart` 精确指向 `$RELEASE_VENV` 时，才可清理非活动候选。不得清理活动 `$RELEASE_DIR`、`$RELEASE_SOURCE` 或任何活动数据目录；旧 WSL/local worktree 还必须额外证明 clean、已合并且未被 systemd/配置引用。

先由服务用户把三个目录规范化，证明候选的规范父目录精确等于受管 release 根、名称是完整 SHA、不是当前 release，且没有未提交内容。后续命令只使用规范化后的变量，不能用 `..`、符号链接或字符串前缀绕过删除保护；同时记录旧候选的完整 `$ROLLBACK_SHA` 作为未来按提交重建回滚版本的非敏感证据：

```bash
export INACTIVE_RELEASE_DIR='<本轮已验证完成后不再活动的候选 release>'
RELEASE_ROOT="$(sudo -H -u "$SERVICE_USER" realpath -e -- "$RELEASE_ROOT")"
RELEASE_DIR="$(sudo -H -u "$SERVICE_USER" realpath -e -- "$RELEASE_DIR")"
INACTIVE_RELEASE_DIR="$(sudo -H -u "$SERVICE_USER" realpath -e -- "$INACTIVE_RELEASE_DIR")"
RELEASE_VENV="$RELEASE_DIR/.venv"
RELEASE_UNIT_PATH="/usr/local/bin:/usr/bin:/bin:$RELEASE_VENV/bin"
test "$INACTIVE_RELEASE_DIR" != "$RELEASE_DIR"
test "$(dirname -- "$INACTIVE_RELEASE_DIR")" = "$RELEASE_ROOT"
printf '%s\n' "$(basename -- "$INACTIVE_RELEASE_DIR")" | grep -Eq '^[0-9a-f]{40}([0-9a-f]{24})?$'
test -z "$(sudo -H -u "$SERVICE_USER" git -C "$INACTIVE_RELEASE_DIR" status --porcelain --untracked-files=normal)"
export ROLLBACK_SHA="$(sudo -H -u "$SERVICE_USER" git -C "$INACTIVE_RELEASE_DIR" rev-parse HEAD)"
test "$ROLLBACK_SHA" != "$TARGET_SHA"
for unit in codev-reindex.service "$CODEGRAPH_UNIT"; do
  systemctl show "$unit" --property=ExecStart --value | grep -Fq -- "$RELEASE_VENV"
done
```

Git 默认会因 release venv 等 ignored 文件拒绝删除 worktree。为防止用宽泛命令清理证据，先只接受本轮可再生的 ignored 条目；出现任何未列出的 ignored 路径即停止并保留候选，不能扩大白名单或手工删除。只有全部门禁通过后，才允许 Git 对唯一规范路径使用 `--force`；不得使用 `rm -rf`、通配路径或其他目录上的 `--force`。

```bash
ignored_release_listing="$(sudo -H -u "$SERVICE_USER" git -C "$INACTIVE_RELEASE_DIR" \
  ls-files --others --ignored --exclude-standard --directory)"
if [ -n "$ignored_release_listing" ]; then
  while IFS= read -r ignored_release_entry; do
    case "$ignored_release_entry" in
      .venv/|codev_platform.egg-info/|__pycache__/|.pytest_cache/|.ruff_cache/) ;;
      *) exit 1 ;;
    esac
  done <<< "$ignored_release_listing"
fi

# 形态 A：普通 clone
sudo -H -u "$SERVICE_USER" git -C "$RELEASE_SOURCE" worktree remove --force "$INACTIVE_RELEASE_DIR"

# 形态 B：Gitea bare 接收仓（不要执行形态 A）
sudo -H -u "$SERVICE_USER" git --git-dir="$RELEASE_SOURCE" worktree remove --force "$INACTIVE_RELEASE_DIR"
```

本地 `.worktrees` 同理：只在 WSL 完整验收后移除本轮已经 clean 的集成 worktree 并执行 `git worktree prune`；其他名称、分支或未确认的 worktree 均视为用户数据，不能因为目录同在 `.worktrees` 下而删除。

## 7. 失败处理与回滚

### 7.1 各阶段失败原则

| 失败位置 | 立即动作 | 禁止动作 |
|---|---|---|
| `provision` | 停止流程，修复由 root 所有的门禁锁受管状态后重试 | 手工创建 marker/锁、放宽权限、跳过门禁 |
| `prepare` 或 `status` | intent 未取得则保持零修改；marker 已落地后保持/重建 M+G+H，穷尽 runtime mask、双服务停机与最终证明 | 启动 worker、删除 guard/hold、解除 mask、批量重启服务 |
| `configure-resume-codegraph` | 保持维护态，修复覆盖配置、数据根或受管环境文件后重跑第 3.2 节命令 | 手工写共享环境文件/drop-in、用静态环境变量绕过同源证明 |
| `migrate-legacy` 或 `init-owner` | 维持维护窗口；查清遗留并重新演练 | 恢复目标版本的隔离 worker、伪造已迁移状态 |
| `restore`、待命超时或身份不匹配 | 保持/回退 `maintenance`，恢复 `Restart=no`，停止服务并重新证明 M+G+H/R/S | 手工清 marker/drop-in/hold、直接 `systemctl start`、把待命当成可写 |
| manifest 验收或 `resume-codegraph` | manifest、租约、payload、运行身份、健康或稳定证明失败时保持/重建 M+G+H 与双服务停机边界 | 将队列空、部分成功或旧 SHA 视为成功；手工删除 marker/hold |

### 7.2 回滚顺序

回滚只切回上一个已验证的 release worktree、独立 venv 与配置覆盖；绝不把旧 WSL 工作树改造成发布版本。第 6.3 节尚未执行时可以直接使用保留的上一个候选；若已清理候选，则必须从 `$RELEASE_SOURCE` 的已记录完整 `$ROLLBACK_SHA` 重建同一提交的临时 release。该重建在维护窗口中发生，仍只有一个活动服务环境，不能直接覆盖当前工作树。固定顺序如下：

1. 直接以当前已验证 release venv 执行 `reindex-maintenance prepare --yes` 和 `status`，由状态机按 M→G→H→R/S 建立边界；不得在其前手工 mask/stop。确认永久 guard、hold、双服务停机和外部 worker 证明均成立。
2. 若上一个候选已由第 6.3 节清理，必须按下列精确命令重建。`ROLLBACK_SHA` 必须来自第 6.3 节或交接记录；**不要**重跑“远端 `dev` 必须等于目标”的发布验证，也不要直接覆盖当前工作树：

   ```bash
   export TARGET_SHA="$ROLLBACK_SHA"
   export RELEASE_DIR="$RELEASE_ROOT/$TARGET_SHA"
   export RELEASE_VENV="$RELEASE_DIR/.venv"
   export RELEASE_UNIT_PATH="/usr/local/bin:/usr/bin:/bin:$RELEASE_VENV/bin"
   sudo -H -u "$SERVICE_USER" test ! -e "$RELEASE_DIR"
   sudo -H -u "$SERVICE_USER" test -w "$RELEASE_ROOT"

   # 形态 A：普通 clone
   sudo -H -u "$SERVICE_USER" git -C "$RELEASE_SOURCE" cat-file -e "$TARGET_SHA^{commit}"
   sudo -H -u "$SERVICE_USER" git -C "$RELEASE_SOURCE" -c core.hooksPath=/dev/null \
     worktree add --detach "$RELEASE_DIR" "$TARGET_SHA"

   # 形态 B：Gitea bare 接收仓（不要执行形态 A）
   sudo -H -u "$SERVICE_USER" git --git-dir="$RELEASE_SOURCE" cat-file -e "$TARGET_SHA^{commit}"
   sudo -H -u "$SERVICE_USER" git --git-dir="$RELEASE_SOURCE" -c core.hooksPath=/dev/null \
     worktree add --detach "$RELEASE_DIR" "$TARGET_SHA"
   ```

   随后完整执行第 2.1 节的 venv、`[runtime]` 安装、来源/依赖/CodeGraph CLI 探针和回滚版本配置覆盖门禁。任何历史提交不存在、目录已存在或探针失败都保持停机，不能改用旧 WSL 工作树。
3. 仅通过受控 staged 发布事务把两个固定 unit 的持久载荷切回上一个已验证 release；永久 guard、hold 和 runtime mask 必须继续有效。不得手工覆盖 drop-in 或启动 CodeGraph。随后以回滚版本配置重跑第 3.2 节，使共享环境文件和摘要切换到回滚版本。
4. 仍使用当前这套已验证、具备维护入口的 release venv 执行 `reindex-maintenance restore --yes`；该命令启动的是第 3 步已经切换的 reindex 单元，而不是调用方 venv 本身。若当前维护入口不能安全恢复，保持停机并升级处理，不以旧工作树或裸 `systemctl start` 作为应急替代。
5. 对回滚版本重新入队已验证的完整目标 SHA，按第 6 节逐项验证 manifest；仅在其 `codegraph` manifest 成功后按第 6.1 节恢复 `$CODEGRAPH_UNIT`。不做数据库 schema downgrade、不删除失败 release 和审计证据。

legacy 队列迁移和稳定 owner 是持久控制面操作。正常回滚发布版本不重复迁移或重建 owner；只有诊断证明控制面未完成且维护窗口仍安全时，才从第 4 节重新演练。

## 8. 交接清单

发布或回滚结束时，应交接以下非敏感证据：

- 发布/回滚的完整 `$TARGET_SHA` 与 release 标识；
- 若执行过第 6.3 节，清理前记录的完整 `$ROLLBACK_SHA` 与其在发布源中可验证的提交身份；
- `provision`、`prepare`、`status`、迁移演练/确认、`init-owner`、`restore` 的结果编号或时间；
- 是否检测到外部 worker，以及其是否已在受控窗口内排除；
- `codev-mcp-codegraph.service` 的 release 路径、永久 guard、耐久 hold、runtime mask、停机/cgroup/旧 daemon 清退证明，以及恢复后的 InvocationID/NRestarts 稳定证据；
- 四条 manifest 的任务名、状态和目标 SHA；
- 若未完成，当前是否保持 M+G+H、reindex `Restart=no`、CodeGraph runtime mask 与双服务停机，以及下一位操作员应从哪个安全证明开始。

交接中的 marker 状态必须明确写为 `maintenance`、`restore_armed`、`restore_claimed` 或“已耐久删除”；不得以“服务已启动”推断已完成恢复。`restore_armed` / `restore_claimed` 已过期或无法绑定本次 `InvocationID` 时，一律按未恢复、保持 fail-closed 处理。

任何无法证明的状态都不是“可继续操作”的状态。宁可保持 `codev-reindex.service` 停机并保留证据，也不要为了恢复速度绕过维护门禁。
