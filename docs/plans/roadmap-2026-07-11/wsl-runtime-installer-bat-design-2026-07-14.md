# WSL 服务器运行时安装器设计

> 状态：旧提交专用脚本已退役。当前以 `scripts/install-wsl-runtime.sh` 为服务器真值源，
> `scripts/install-wsl-runtime.bat` 仅作为 Windows/WSL 薄入口。

## 目标

提供一个可直接在服务器执行的 SH 安装器，为已创建的受管 WSL release 安装 CUDA、
平台 `runtime` 与 `agent` 依赖；Windows BAT 只负责路径转换和参数转交。

## 边界

- 仅操作指定 release 的 `.venv` 与可选 wheelhouse。
- 默认解析服务器 `origin/dev` 最新提交，也支持显式提交或 release 目录。
- 常规依赖默认使用清华 PyPI 镜像；CUDA Torch 使用显式 CUDA 专用源或精确匹配的本地 wheel。
- 不执行 `systemctl`、不启动或停止服务、不修改数据库、不触碰旧工作目录和数据目录。
- release 必须位于受管根目录、目录名等于完整提交号，且不得包含符号链接或脏文件。

## 入口与流程

服务器入口 `scripts/install-wsl-runtime.sh` 的职责为：

1. 以服务账号持有全局非阻塞锁，避免并发安装同一运行时。
2. 校验提交、受管目录边界、owner、tracked/staged/untracked 洁净度和符号链接。
3. 使用隔离 Python `-I` 创建或复用 release `.venv`。
4. 精确收敛到 `torch 2.11.0+cu128`，CPU 服务器必须显式传 `--cpu`。
5. 安装当前 release 的 `.[runtime,agent]`，并执行 `pip check`、服务模块导入和 CUDA 探针。

Windows 入口 `scripts/install-wsl-runtime.bat` 只检查 WSL 与脚本路径、转换路径、原样转交参数；
不包含 pip、systemd 或数据库逻辑。

## 失败处理

- 任一边界校验、pip、依赖检查、模块导入或 CUDA 探针失败即返回非零。
- pip 默认缓存开启；再次运行 BAT 会复用已完整下载的包。
- 脚本不自动删除 release、切换服务或恢复服务，避免扩大安装职责和掩盖故障现场。

## 验证

- pytest 固化 SH/BAT 契约，并在 Linux 覆盖 untracked 与符号链接拒绝场景。
- `bash -n` 校验 SH 语法；BAT 保持 UTF-8 BOM 与纯 CRLF，并实测帮助参数位于任意位置。
- 正式 WSL release 完整安装和 `--check-only` 均通过；验证结果见
  [2026-07-16 开发记录](./daily-summary-2026-07-16.md)。
