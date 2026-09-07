# daily-summary 2026-07-16 —— WSL 运行时与维护恢复收口

> 本日完成 reindex/systemd 维护路径的 WSL 兼容性收口，修复四个 MCP 入口 401，
> 并将旧提交专用安装脚本替换为服务器优先、可重复执行的通用运行时安装器。

## 一、维护恢复兼容性修复

- CodeGraph runtime mask 停机证明改为显式接受 `Restart=no`，不再复用常态服务的重启策略。
- systemd 已停机单元返回空 `ControlGroup` 时直接接受停机事实，不再读取不存在的 `cgroup.events`。
- `systemctl show` 多字段结果统一按字段名严格解析，消除不同 systemd 版本输出顺序差异。
- 受管路径原子替换使用支持 `dir_fd` 的 `os.rename`；条件首建能力只依赖自身所需的 I/O 能力，
  不再被无关的 replace/rename 能力误拦。
- 对应回归测试覆盖 runtime mask、空 ControlGroup、字段乱序、`rename(dir_fd)` 和条件首建能力。
- Windows pre-push 原本通过 UNC 直接打开 WSL 中处于 WAL/锁管理下的 SQLite，先后触发
  `invalid uri authority` 与 `database is locked`。门禁现复用 post-commit 的 WSL owner 配置，
  在正式 WSL Python 和数据根内执行全项目 graph audit；没有 WSL 运行时时才回退本地运行时。
- 首次推送后四类任务均在 13 秒内退出、worker 未卡住，但 manifest 因 configured 主仓不洁净而失败。
  根因是 WSL 工作仓的 `.venv` 为指向正式 release 的符号链接，而 `.gitignore` 中 `.venv/` 只匹配目录。
  忽略规则改为 `.venv`，同时覆盖目录和符号链接，并增加真实 `git check-ignore` 回归测试。

### 维护状态机的最终收口

- `prepare` 的唯一耐久顺序固定为 `intent EX → marker(M) → gate EX → guard(G) → G 证明 → hold(H) → runtime mask(R) → stop/reset(S)`；掉电投影只允许 `∅ → {M} → {M,G} → {M,G,H}`，X/R/S 不被误当成重启后仍存在的事实。
- CodeGraph 新增永久 condition guard：canonical unit 与 `99-codev-maintenance-condition.conf` 共用同一条件真值。guard 原像、root:root `0644`、有效 drop-in 顺序和唯一条件均严格证明；guard 在恢复后永久保留。
- guard 建立或首次证明失败时绝不创建 hold，但仍穷尽 runtime mask、停机和最终证明；hold 之后的普通失败或中断均延后结算，不能截断补偿。
- 组合补偿删除独立 hold 端口，统一委托 CodeGraph lifecycle 执行 `G → proof → H → R/S`，防止聚合层绕过硬屏障。
- 恢复改为 marker-last：先证明 staged payload，解除 runtime mask、证明有效主 unit/drop-in/ExecStart，解除 hold，启动 CodeGraph，复用同一 InvocationID/NRestarts 身份完成 running、health、稳定窗口与 enable 证明，再证明并完成 reindex handoff，最后耐久删除 marker。
- transition-intent 与 gate 都使用不可伪造、离开临界区立即失效的能力令牌；逃逸的 gate 工厂在原上下文和复制上下文中均被拒绝。写侧固定 intent EX → gate EX，读侧固定 intent SH → gate SH。
- systemd canonical 条件与 ExecStart 共用同一严格指令解析器，拒绝反斜杠续行、CR/NUL、Unicode 伪换行、非 ASCII 空白和错误 section，消除“未知 drop-in 提供相同命令”造成的假闭环。
- 大文件按职责拆分：恢复状态机与生产适配器分离；维护门禁拆为策略聚合、Linux 可信锁存储、恢复窄门面和稳定错误合约。所有生产 Python 文件重新满足 600 行预算。
- 旧 systemd 聚合测试补齐 `Path.home`、运行解释器和 runtime identity 隔离，不再写真实用户目录，也不削弱生产环境对 release/RECORD 身份的失败关闭。

## 二、MCP 401 根因与修复

- Codex 项目配置此前指向 Windows `180xx` 端口，已统一改为 WSL MCP 网关 `190xx` 端口。
- 正式 release 虚拟环境缺少 `psycopg`/`psycopg_pool`，导致 PostgreSQL token resolver 退化后无法解析有效 token；
  运行时安装改为安装 `.[runtime,agent]`，并把两项数据库依赖纳入导入探针。
- 修复后 platform-docs、codegraph、graph、agent-memory 的 MCP initialize 均返回 HTTP 200。
  已启动的旧 Codex 会话不会热加载 MCP 配置，需要新会话重新握手。

## 三、服务器优先运行时安装器

- `scripts/install-wsl-runtime.sh` 为唯一安装逻辑真值源；可直接在服务器运行。
- `scripts/install-wsl-runtime.bat` 只完成 WSL 检测、路径转换和参数转交，不复制安装逻辑。
- 默认获取服务器 `origin/dev` 最新提交，也支持 `--commit`、`--release-dir`、`--wheelhouse`、
  `--check-only` 和显式 CPU 模式。
- 使用服务账号级全局 `flock` 防止并发安装；release 必须位于受管根目录且目录名等于完整提交号。
- 拒绝 release 或 `.venv` 符号链接、tracked/staged 改动和非 ignored untracked 文件，
  防止 editable install 运行未提交代码或越界修改其他 release。
- GPU 模式精确要求 `torch 2.11.0+cu128` 且 CUDA 可用；本地 wheel 仅选择精确版本，
  否则使用 CUDA 专用源。安装后再次执行版本、CUDA 与 GPU 探针。
- 安装器只管理目标 release 的 Python 运行时，不切换或重启 systemd，不重建或删除数据库。

## 四、真实 WSL 验证

目标 release：`2c5500a51467d586e8dd3ef0c6439b5c9df2f60a`。

- 完整安装路径成功，重复运行保持幂等；已有正确 Torch 时不重复下载。
- `pip check`：`No broken requirements found.`
- 隔离导入：Chroma、FastAPI、MCP、PostgreSQL 驱动及四个平台服务模块全部通过。
- CUDA：`torch=2.11.0+cu128`、CUDA 12.8、`torch.cuda.is_available=True`，
  GPU 为 NVIDIA GeForce RTX 5060 Laptop GPU。
- 实际拒绝非 ignored untracked、release 符号链接和并发锁冲突。
- platform-docs、codegraph、graph、agent-memory 四个 systemd 服务均为 `active`。
- BAT 实测 `--help` 与 `--cpu --help` 均正常退出，不误报完成、不暂停。

## 五、验证命令

```powershell
python -m pytest tests/test_post_hooks.py tests/test_install_wsl_runtime_scripts.py tests/test_reindex_codegraph_lifecycle.py tests/test_reindex_codegraph_resume_managed_path.py tests/test_reindex_maintenance_restore.py tests/test_systemd_unit_layout_filesystem.py -q
python -m ruff check tests/test_install_wsl_runtime_scripts.py
git diff --check
wsl.exe -d Ubuntu -- bash -lc 'bash -n /mnt/d/WorkSpace/codev-platform/scripts/install-wsl-runtime.sh'
powershell -NoProfile -ExecutionPolicy Bypass -File tools/dev/pre-push-audit.ps1
```

早期扩展回归结果为 `293 passed, 16 skipped`；最终收口后重新执行全量 `tests/`，结果为
`4064 passed, 132 skipped`，仅有 1 条第三方 Starlette/httpx 弃用警告。维护/安装定向集
`426 passed`，WSL 真实 Linux `flock`、属主/权限与文件预算 `62 passed`；Ruff、格式、
`py_compile` 和 `git diff --check` 全部通过。两个独立终审均未发现遗留 P0/P1。

## 六、后续使用

服务器在目标 release 已创建后执行：

```bash
cd /home/user/project
bash scripts/install-wsl-runtime.sh
```

Windows 仅作为可选入口：

```bat
scripts\install-wsl-runtime.bat
```
