# codev-platform 全链路扫描审计报告

日期: 2026-06-01

范围: 当前 Windows 工作区 `D:\WorkSpace\codev-platform`。本次复核覆盖代码、测试、打包、干净安装、服务健康、日志、安全、编码、交付状态。

## 1. 总结

当前项目代码层面已经比较稳，单元测试、打包链路和本机服务链路已经跑通。真正阻断“客户正式私有化交付”的点不在当前代码测试，而在 Docker 部署闭环、授权/升级/运维机制。

- `platform-docs` 当前可用。
- `cross-link` 当前可用。
- `codegraph` 当前可用。
- Agent HTTP 当前可用。
- webhook 当前可用。
- Docker 环境不可用，容器部署不能验证。
- 本机配置里有一个 provider API key，已能脱敏显示，但仍建议迁移到环境变量或密钥管理。
- 仓库没有发现真实明文密钥。
- 没有发现批量真实乱码；之前看到的乱码主要是 PowerShell 输出编码问题。

结论: 当前适合继续做内部 POC 和演示版，不适合直接作为正式客户交付版发布。

## 2. 执行项

### 测试和编译

已执行:

- `python -m compileall -q codev_platform tests`
- `python -m pytest -q`
- `python -m pip check`
- `python -m codev_platform.cli --help`

结果:

- 编译检查: 通过
- 全量测试: `427 passed, 5 skipped, 1 warning`
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

- `python -m codev_platform.cli post-commit --foreground`
- `python -m codev_platform.cli serve-mcp start --wait --timeout 90`
- `python -m codev_platform.cli serve-mcp status`
- `python -m codev_platform.cli webhook serve`
- `python -m codev_platform.cli webhook status`
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
| cross-link | 18086 | OK | 端口监听，`/healthz` 返回 200 |
| codegraph | 18091 | OK | 端口监听，`/healthz` 返回 200 |
| Agent HTTP | 8848 | OK | 端口监听，`/health` 返回 200 |
| webhook | 18099 | OK | 端口监听，`/healthz` 返回 200 |

`codev-platform health --mode full` 当前结果:

- `READY`
- `all checks green`
- 当前 HEAD 新鲜度: OK
- `reindex 7d`: OK

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

### 代码级审计

本轮额外做了代码级静态扫描，重点看高风险调用、进程编排、认证边界和可维护性。

规模:

- `codev_platform`: 138 个文件，约 17,771 行文本，其中 Python 文件 92 个。
- `tests`: 59 个文件，约 4,927 行文本，其中测试文件 58 个。
- `docs`: 51 个文件，约 12,679 行文本。
- `scripts`: 17 个文件，约 680 行文本。

复杂度集中点:

- `codev_platform/chroma/server.py`: 约 1286 行。
- `codev_platform/ops/health.py`: 约 1119 行。
- `codev_platform/cli.py`: 约 786 行。
- `codev_platform/chroma/indexer.py`: 约 704 行。
- `codev_platform/mcp_serve.py`: 约 701 行。
- `codev_platform/cross_link/server.py`: 约 623 行。
- `codev_platform/ops/reindex.py`: 约 595 行。

结论:

- 没有发现 `shell=True`、`os.system`、字符串执行型 `eval()` 这类直接高危点。
- `eval(` 的命中实际是 PyTorch 模型的 `.eval()`，不是执行字符串。
- `pickle` 的命中是 BM25 文档说明，当前没有真实 `pickle.load()` 反序列化调用。
- `subprocess` 调用主要集中在 CLI、reindex、health、MCP 启停和 launcher，属于平台运维类代码的正常需求，但发布前应继续保留白名单命令和参数数组调用方式。
- `except Exception` 命中较多，主要是 CLI/daemon 的 fail-soft 边界。短期可接受，但长期会降低定位效率，建议对核心链路逐步收窄异常类型并补结构化错误码。
- 网关认证、token 哈希、过期时间、项目白名单、路径穿越防护已有测试覆盖。
- webhook 走 provider 签名，不走 gateway token；空 secret 默认 fail-closed，只有显式 `webhook.allow_insecure=true` 才允许不安全模式。
- `project_id` 已做 slug 校验，数据路径也会复用校验，当前没有发现明显路径穿越问题。

需要继续加强:

- 大文件拆分: `chroma/server.py`、`ops/health.py`、`mcp_serve.py` 已经偏大，后续新增能力前应先拆边界。
- 错误分类: 对外 HTTP/API 返回应减少笼统异常，区分配置错误、依赖缺失、索引缺失、权限错误和下游服务不可用。
- 进程治理: 后台进程已有健康检查，但正式交付还需要统一 pid、日志轮转、重启策略和退出清理。
- 配置治理: 本机 provider key 能脱敏，但正式交付应迁移到环境变量、密钥文件或客户侧密钥管理。
- 产品配置一致性: Agent health 当前能返回 200，但 provider/model 的命名组合需要再核对，避免演示时出现“provider 与模型名不一致”的理解成本。

## 3. 当前主要风险

### P1: Docker 私有化部署未验证

不能只靠 wheel 证明能交付客户。客户侧最可能需要 Docker 镜像、配置挂载、数据卷、授权文件和升级脚本。

建议:

1. 找一台有 Docker 的机器做闭环。
2. 输出固定验收脚本。
3. 把 compose 启动、health probe、日志收集纳入发布检查。

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
- platform-docs / cross-link / codegraph 三套 MCP SSE 服务在线
- Agent HTTP 在线
- webhook 在线

尚未完整运行验证:

- Docker 私有化部署
- 客户项目插件化扫描链路
- webhook 真实 VCS 签名请求触发 reindex
- Agent 面向非开发人员的产品化 UI

## 5. 下一步建议

优先级从高到低:

1. 在 Docker 环境验证 compose，并写成固定脚本。
2. 做一个 demo 项目和只读 API/Web playground，让别人能试用。
3. 验证 webhook 接真实 Gitea/GitLab push 后能入队 reindex。
4. 再规划 license、U 盘授权、二进制化、升级迁移。

最终判断:

codev-platform 当前已经不是空想项目，基础设施、测试底座和本机运行链路已经存在；但还没达到“发布出去给客户自助部署”的级别。现在最应该补的是 Docker 交付闭环、可试用入口和授权升级机制，而不是继续堆新概念。
