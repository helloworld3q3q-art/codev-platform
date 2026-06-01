# codev-platform 全链路扫描审计报告

日期: 2026-06-01

范围: 当前 Windows 工作区 `D:\WorkSpace\codev-platform`。本次复核覆盖代码、测试、打包、干净安装、服务健康、日志、安全、编码、交付状态。

## 1. 总结

当前项目代码层面已经比较稳，单元测试和打包链路是绿的。真正阻断“发布给别人试用 / 客户私有化交付”的点不在测试，而在运行态闭环:

- `platform-docs` 当前可用。
- `cross-link` 和 `codegraph` 的平台 SSE 服务当前未监听。
- Docker 环境不可用，容器部署不能验证。
- 本机配置里有一个 provider API key，已能脱敏显示，但仍建议迁移到环境变量或密钥管理。
- 仓库没有发现真实明文密钥。
- 没有发现批量真实乱码；之前看到的乱码主要是 PowerShell 输出编码问题。

结论: 当前适合继续做内部 POC，不适合直接作为正式客户交付版发布。

## 2. 执行项

### 测试和编译

已执行:

- `python -m compileall -q codev_platform tests`
- `python -m pytest -q`
- `python -m pip check`
- `python -m codev_platform.cli --help`

结果:

- 编译检查: 通过
- 全量测试: `351 passed, 5 skipped, 1 warning`
- 依赖检查: `No broken requirements found`
- CLI 入口: 可用

跳过项主要来自轻量环境下的可选依赖，例如 `fastapi`、`psycopg_pool`、`torch` 等 importorskip。

### 打包和干净安装

已执行:

- `python -m pip wheel . -w dist --no-deps`
- 新建临时 venv
- 安装 wheel
- 执行:
  - `codev-platform --version`
  - `codev-platform sync-rules --dry-run`
  - `codev-platform sync-skills --dry-run`

结果:

- wheel 构建: 通过
- wheel 总文件数: 112
- `codev_platform/resources/rules/`: 13 项
- `codev_platform/resources/skills/`: 3 项
- 干净 venv 安装后资源同步命令: 通过
- wheel 中不包含 `docs/` 和 `tests/`，符合当前 `pyproject.toml` 排除策略。

### 服务健康

已执行:

- `python -m codev_platform.cli serve-mcp status`
- 直接探测:
  - `http://127.0.0.1:18083/healthz`
  - `http://127.0.0.1:18083/health`
  - `http://127.0.0.1:18086/healthz`
  - `http://127.0.0.1:18091/healthz`
  - `http://127.0.0.1:8848/health`
  - `http://127.0.0.1:18099/healthz`
- `Get-NetTCPConnection`

结果:

| 服务 | 端口 | 当前状态 | 说明 |
|---|---:|---|---|
| platform-docs | 18083 | OK | 端口监听，`/healthz` 返回 200 |
| cross-link | 18086 | DOWN | 端口未监听 |
| codegraph | 18091 | DOWN | 端口未监听 |
| Agent HTTP | 8848 | DOWN | 端口未监听 |
| webhook | 18099 | DOWN | 端口未监听 |

注意: `codev-platform health --mode full` 显示关键项 OK，但有 1 个 WARN:

- 当前 HEAD 命中索引范围，但没有进入 `reindex.log`，建议补跑 `post-commit` 或对应 reindex。

### 日志复核

重点查看:

- `codev_platform/mcp_serve_logs/cross-link.log`
- `codev_platform/mcp_serve_logs/codegraph.log`
- `codev_platform/cross_link/mcp_server.log`

发现:

- `cross-link.log` 中存在历史 `TypeError: 'NoneType' object is not callable`。
- 该错误发生在旧日志里，当前测试已覆盖非法 `project_id` 返回 400，不再作为当前代码缺陷判断。
- 当前 cross-link/codegraph DOWN 的直接现象是端口未监听，不是健康路由格式错误。

### 安全扫描

已执行仓库文本密钥扫描，排除 `.git`、`.venv`、`dist`、`build`、`data`、二进制和索引文件。

结果:

- 仓库真实明文密钥: 0 命中
- 命中项为测试和文档示例:
  - `tests/test_backup.py` 中的测试 DSN 示例
  - `docs/plans/...` 中的占位 DSN 示例

本机配置:

- `codev-platform config doctor --redact` 可正常脱敏。
- 当前本机配置存在一个 DeepSeek provider key，输出已脱敏为 `sk-9***`。

建议:

- 真 key 不要长期放本机 JSON，优先迁移到环境变量或密钥管理。
- 客户部署时必须提供 `config doctor --redact` 或等价检查作为交付验收项。

### 编码扫描

复核方式:

- 用 Python 按 UTF-8 读取，不依赖 PowerShell `Get-Content` 的控制台输出。
- 检查 UTF-8 decode error、`U+FFFD`、典型乱码样例字符。

结果:

- UTF-8 解码错误: 0
- `U+FFFD`: 0
- 真实命中:
  - `codev_platform/resources/rules/windows-powershell.md`
  - `.claude/rules/windows-powershell.md`

这两处是文档中故意保留的 PowerShell 乱码示例，不是文件损坏。

说明:

- PowerShell 直接显示 `pyproject.toml`、`config.example.json`、`docker-compose.yml` 时会看到乱码，但 Python UTF-8 读取确认内容正常。
- 后续编辑中文文件应继续避免 PowerShell 5.1 默认编码写入，优先使用 UTF-8 明确写入。

### 运行产物扫描

已执行:

- `git ls-files "*.pyc" "*.log" "*.jsonl" "*.jar" "codev_platform/cross_link_asm/target/*" "dist/*" "build/*"`

结果:

- 当前没有发现这些运行产物被 git 跟踪。
- 工作区中存在 `dist/`、`build/`、日志、索引、`.codegraph/` 等本地运行产物，但已被 `.gitignore` 覆盖或未跟踪。

### Docker

已执行:

- `docker --version`

结果:

- 当前机器没有 Docker 命令。

影响:

- 不能证明 `docker-compose.yml` 能启动。
- 私有化部署闭环仍未完成。

## 3. 当前主要风险

### P1: 平台服务没有全链路启动

当前只有 `platform-docs` 在线，`cross-link` 和 `codegraph` 的平台 SSE 服务未监听。对于“全链路 AI 平台”定位，这是当前最大运行态缺口。

建议:

1. 增加或完善 `serve-mcp start --wait`。
2. 启动后必须探测三个端口。
3. DOWN 时输出具体原因: 进程未启动、依赖缺失、端口占用、DB 缺失、项目未注册。

### P1: Docker 私有化部署未验证

不能只靠 wheel 证明能交付客户。客户侧最可能需要 Docker 镜像、配置挂载、数据卷、授权文件和升级脚本。

建议:

1. 找一台有 Docker 的机器做闭环。
2. 输出固定验收脚本。
3. 把 compose 启动、health probe、日志收集纳入发布检查。

### P1: 当前 HEAD 没有进入 reindex.log

`health --mode full` 提示 `hook missed?`。这说明最近改动命中索引范围，但索引日志没有记录当前 HEAD。

建议:

- 执行 `python -m codev_platform.cli post-commit --foreground` 或对应 reindex 命令。
- 后续把该 WARN 作为 POC 前置验收，避免演示时查不到最新文档。

### P2: 发布命令需要统一

`pip wheel` 可用，但之前 `python -m build` 在当前环境不可用。

建议:

- 发布文档里明确统一使用 `python -m pip wheel . -w dist --no-deps`。
- 或固定安装正确的 `build` 包后再使用 `python -m build`。

### P2: 授权、升级、二进制化未落地

当前是 Python 包和本地服务形态，还没有:

- license 校验
- 离线授权
- U 盘授权
- 版本升级脚本
- 数据迁移脚本
- 二进制化或容器镜像权益保护

这不是 POC 阶段必须完成的事，但进入客户正式交付前必须规划。

## 4. 当前可用能力

已经验证或已有测试覆盖:

- 多项目 `project_id` 隔离
- Chroma 文档索引服务
- BM25 / RRF 检索融合基础
- gateway token / passthrough 认证
- ACL 和 token project 白名单
- wheel 资源打包
- rules / skills 同步
- health / backup / bootstrap / metrics / logs 等 CLI 入口
- cross-link/codegraph 的核心代码和测试

尚未完整运行验证:

- cross-link SSE 服务在线可用
- codegraph SSE 服务在线可用
- Agent HTTP 面向非开发人员使用
- webhook 常驻触发 reindex
- Docker 私有化部署
- 客户项目插件化扫描链路

## 5. 下一步建议

优先级从高到低:

1. 修正 `serve-mcp start/status` 的全链路启动验收，让 `platform-docs/cross-link/codegraph` 三个服务同时 OK。
2. 补跑当前 HEAD 的 reindex，消掉 `hook missed?`。
3. 在 Docker 环境验证 compose，并写成固定脚本。
4. 做一个 demo 项目和只读 API/Web playground，让别人能试用。
5. 再规划 license、U 盘授权、二进制化、升级迁移。

最终判断:

codev-platform 当前已经不是空想项目，基础设施和测试底座已经存在；但还没达到“发布出去给客户自助部署”的级别。现在最应该补的是运行闭环和交付闭环，而不是继续堆新概念。
