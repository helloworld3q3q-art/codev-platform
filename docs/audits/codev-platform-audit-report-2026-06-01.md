# codev-platform 三轮审计与测试报告

日期: 2026-06-01

范围: 当前仓库 `D:\WorkSpace\codev-platform` 的代码结构、测试基线、MCP/Agent/索引运行链路、打包交付、配置安全、部署可验证性。

结论: 当前项目适合继续做内部 POC 和真实业务仓试点,但还不适合直接作为客户可交付产品发布。主要差距不在想法,而在交付形态、运行时依赖隔离、服务编排一致性、生产认证默认值、健康检查与打包资源。

## 一、三轮审计摘要

### 第一轮: 代码结构与基础能力审计

已确认能力:

- `codev_platform.cli` 已形成统一 CLI 入口,包含项目初始化、注册、规则/技能同步、MCP 编排、重建索引、健康检查、Agent 服务等命令。
- `core.project_id` / `core.paths` 已建立多项目标识和数据路径约定。
- Chroma 文档索引、codegraph、cross-link、Agent HTTP 服务、memory/session、gateway/auth 已有代码落点。
- 测试覆盖已经不是空架子,至少覆盖 CLI、路径、ACL、Agent、MCP 编排、Webhook、reindex、backup、bootstrap 等模块。

当时主要问题:

- 早期全量测试出现 `tests/test_mcp_serve.py` 失败,根因是测试仍按旧的 per-project codegraph 端口模型断言,而实现已经切到单端口多租户代理。
- `serve-mcp start` 的 Chroma 自定义端口注入曾存在风险。
- Chroma manifest 原先有全局化风险,多项目轮流 reindex 会互相覆盖。
- Agent `project_id` 校验和 ACL 一开始需要加强。

当前状态:

- MCP 编排相关测试已恢复通过。
- Agent `project_id` 校验与 ACL 已经在 `/chat`、MCP SSE 入口、memory 路由中落地。
- Chroma per-project manifest 已有修复痕迹,但 server reload 仍有全局 stamp 问题。

### 第二轮: 文档、一图总览与产品边界审计

已完成:

- 更新 `docs/codev-platform-overview.html` 的“一图总览”,只保留当前已落地能力。
- 修复该 HTML 中的 `??/????` 字面乱码残留。
- 验证 HTML 文件仍是 UTF-8,无 replacement character。

验证结果:

- `replacement-char-count=0`
- `svg open=2 close=2`
- `section open=14 close=14`
- `Select-String '??'` 无输出

注意:

- 用户要求中文风格,所以图内术语已改为中文主表达,例如控制平面、知识索引平面、记忆平面、网关权限平面、运行时平面。
- 保留必要技术缩写的位置应以括号或说明出现,不能用英文标题替代中文表达。

### 第三轮: 发布交付、运行时、安全与部署审计

本轮重点验证:

- 当前全量测试基线
- wheel 打包内容
- 干净 venv 安装后 CLI 可用性
- MCP 服务端口状态
- runtime 依赖是否齐全
- 配置文件和仓库密钥扫描
- Docker/compose 可验证性

最新测试结果:

- `python -m compileall -q codev_platform tests`: 通过
- `python -m pytest -q`: `300 passed, 4 skipped, 1 failed`
- `python -m pip check`: `No broken requirements found`
- 权限 / Webhook / Reindex 定向测试: `41 passed`
- MCP / cross-link / codegraph / meta health 定向测试: `31 passed`
- Agent / gateway / path / CLI 定向测试: `125 passed, 4 skipped`
- Bootstrap / backup / systemd / codegraph link: `38 passed`

唯一全量失败:

- `tests/test_health_split_security.py::test_servers_public_paths_are_healthz_only`
- 失败原因: `codev_platform.chroma.server` 顶层 `import chromadb`,当前轻量环境未安装 runtime extra,导致 import 阶段失败。

## 二、当前关键发现

### P0 / P1: 发布前必须修

#### 1. Chroma server 顶层依赖过重,导致测试和轻量安装失败

位置:

- `codev_platform/chroma/server.py:30`
- `tests/test_health_split_security.py:134`

现象:

- 默认安装依赖为空,`chromadb` 在 `[runtime]` extra 中。
- 但 `server.py` import 阶段直接 import `chromadb`。
- 安全测试只是想 inspect 路由源码,也被 runtime 依赖挡住。

影响:

- 轻量 SDK 安装不可靠。
- CI 不能在纯 dev 依赖下稳定跑。
- 安全测试、静态审计、文档生成都会被 runtime 依赖卡住。

建议:

- 将 `chromadb`、`sentence_transformers`、torch、reranker 等重依赖延迟到 `_get_client()` / `_ensure_model()` / `main()` 内。
- 模块 import 阶段不要 `sys.exit(1)`。
- 缺 runtime extra 时给清晰 503 或 CLI 提示,而不是 import 崩。

#### 2. wheel 交付资源缺失,普通 pip 安装后核心命令失败

位置:

- `codev_platform/cli.py:356`
- `codev_platform/cli.py:390`
- `codev_platform/cli.py:398`
- `pyproject.toml:56`

验证:

- wheel 文件数: 93
- `codev_platform/`: 88
- `rules/`: 0
- `skills/`: 0
- `docs/`: 0
- `config.example.json`: 0

干净 venv 安装后:

- `codev-platform --version`: 可用
- `codev-platform sync-rules --dry-run`: 失败
- `codev-platform sync-skills --dry-run`: 失败

影响:

- 现在不能按普通 Python 包交付给客户。
- 二进制化、Docker 镜像、私有化部署都会继承这个问题。

建议:

- 将 `rules/`、`skills/`、`config.example.json` 纳入 package data。
- 或迁移到 `codev_platform/resources/` 并通过 `importlib.resources` 读取。
- 增加一个 clean install 测试: wheel 安装后跑 `sync-rules --dry-run`、`sync-skills --dry-run`。

#### 3. platform-docs 健康探针版本不一致,编排器误判 DOWN

验证:

- `serve-mcp status`: `platform-docs DOWN`, `cross-link OK`, `codegraph OK`
- `http://127.0.0.1:18083/healthz`: 404
- `http://127.0.0.1:18083/health`: 200,返回旧详细格式
- `http://127.0.0.1:18086/healthz`: 200
- `http://127.0.0.1:18091/healthz`: 200

影响:

- Chroma daemon 实际在跑,但新编排器按 `/healthz` 探测会判定 DOWN。
- 可能导致重复拉起、误报、运维误判。

建议:

- 对旧 daemon 做兼容探测: `/healthz` 失败时 fallback `/health`,但要只判断 status code,不要把详细信息当 public 合规结果。
- 或提供 daemon 版本检测和强制重启迁移命令。

#### 4. Chroma reload 仍依赖全局 build stamp

位置:

- `codev_platform/chroma/server.py:171`
- `codev_platform/chroma/server.py:435`
- `codev_platform/chroma/indexer.py:93`
- `codev_platform/chroma/indexer.py:592`

现状:

- indexer 已写 `index_manifest.<project_id>.json` 和 `.last_build.<project_id>.json`。
- server 的 `_maybe_reload_project()` 仍看全局 `.last_build.json`。

影响:

- 一个项目重建可能触发其他项目状态失效。
- 多租户场景下会造成不必要 reload 或新鲜度误判。

建议:

- `_maybe_reload_project(state)` 改为优先读取 `.last_build.<state.project_id>.json`。
- 全局 `.last_build.json` 只作为 legacy fallback。

#### 5. health cross-link 路径仍硬编码本仓 data

位置:

- `codev_platform/ops/health.py:562`
- `codev_platform/ops/health.py:889`

现状:

- `chroma_data = chroma_dir()` 已走统一路径。
- 但 cross-link DB 仍用 `cdv_root / "data" / "codegraph_ext" / project_id / "cross_layer.sqlite"`。

影响:

- 客户配置 `PLATFORM_DATA_DIR` 或 `data.platform_data_dir` 后,health 会误报 cross-link 缺失。

建议:

- 改为 `cross_link_db_path(project_id)`。
- 测试覆盖 `PLATFORM_DATA_DIR` override。

#### 6. 生产认证默认值不适合客户部署

位置:

- `config.example.json:66`
- `config.example.json:67`
- `codev_platform/gateway/auth.py`
- `codev_platform/ops/agent.py:35`

现状:

- 示例配置默认 `auth_mode=passthrough`。
- 对本机开发可接受,但对客户部署不应作为默认生产模式。
- `warn_if_insecure()` 目前只是 warning,不是 fail-fast。

影响:

- 反代或远程访问配置不当时,可能形成裸访问。

建议:

- 增加 `deployment.mode=dev|prod`。
- prod 模式下非 token 直接拒绝启动。
- `platform.url` 非 localhost 且 auth_mode 非 token 时拒绝启动或 health 红灯。

### P2: 产品化前应修

#### 7. 用户本机配置存在明文 API Key

验证:

- 仓库已跟踪文件未发现常见明文密钥。
- `~/.codev-platform/config.json` 中存在一个非空 provider API key。

风险:

- 本机配置不会进 git,但客户部署/截图/日志/备份时可能泄漏。

建议:

- 立即轮换该 key。
- 配置文件中只放环境变量名或 secret 引用。
- 增加 `codev-platform config doctor --redact`。

#### 8. Docker 部署未验证

验证:

- 当前机器没有 `docker` 命令。
- 因此 `docker-compose.yml` 只能静态读取,不能证明可部署。

风险:

- 私有化部署如果依赖 Docker,现在没有本机验证闭环。

建议:

- 增加 CI 或专用环境跑 `docker compose config`、`docker compose up -d`、health probe。
- 明确 Docker 镜像边界: runtime 服务镜像、PostgreSQL、模型挂载、data volume、配置/secret 挂载。

#### 9. 源码注释/docstring 仍有大量 mojibake

验证:

- 文件本身可 UTF-8 decode。
- 但多处注释/docstring 已经是乱码内容。

影响:

- 不一定影响运行。
- 但影响客户审计、二次开发、交付观感。

建议:

- 不要机械全局替换。
- 按模块逐个恢复: gateway/core/agent/chroma/mcp_serve/reindex 优先。

#### 10. cross-link / codegraph SSE 非法 project_id 分支返回 None

现象:

- 历史日志中出现 `TypeError: 'NoneType' object is not callable`。
- 原因是非法 `project_id` 分支里直接 `return`,Starlette 需要 Response。

影响:

- 日志噪音,客户端看到 500 或连接异常。

建议:

- 统一返回 `JSONResponse({"error": "invalid project_id"}, status_code=400)`。

## 三、当前能力判断

### 已经有实际价值的能力

- 多项目 project_id 体系
- 文档检索增强
- codegraph 代码图谱接入
- cross-link 跨层链路
- Agent HTTP 服务
- 分层 memory/session
- gateway 认证中间件与 ACL
- webhook + reindex queue
- health / backup / bootstrap / systemd 相关命令

### 还不是正式产品的原因

- 交付包不完整。
- runtime 依赖和轻量 SDK 边界没有拆干净。
- 服务编排与运行中 daemon 版本不一致。
- 生产认证默认值偏开发。
- Docker 私有化部署没有验证闭环。
- 客户可读源码质量受乱码影响。
- 授权、升级、License、U 盘授权、二进制化还没有实现。

## 四、建议修复路线

### 阶段 1: 先把 POC 跑稳

目标: 内部真实项目稳定运行。

任务:

1. 修 Chroma lazy import 和 import-time `sys.exit`。
2. 修 `/healthz` 探针兼容旧 daemon。
3. 修 Chroma per-project reload stamp。
4. 修 health cross-link data_root。
5. 修非法 project_id 返回 400。
6. 增加 clean wheel install 测试。

验收:

- 全量测试 0 failed。
- `serve-mcp status` 三个端点 OK。
- 干净 venv 安装后 `sync-rules`、`sync-skills` 可运行。

### 阶段 2: 变成可演示版本

目标: 别人能试用。

任务:

1. Docker compose 可跑通。
2. 提供 demo 项目和 demo 数据。
3. 提供只读 Web 演示页或 API playground。
4. 配置全部 redacted,secret 走环境变量。
5. 文档一键启动流程: install -> config -> index -> serve -> test query。

验收:

- 新机器按文档 30 分钟内跑通。
- 非开发人员能通过页面/API 问项目问题。

### 阶段 3: 变成客户私有化交付

目标: 可控授权、可升级、可维护。

任务:

1. 二进制化或容器镜像化。
2. License server / 离线 license / U 盘授权。
3. 生产 token / RBAC / 审计日志。
4. 备份恢复、版本迁移、数据目录升级脚本。
5. 客户项目插件化: Java / Node.js / .NET / Python / React / Vue。
6. 交付包去源码或源码加密策略。

验收:

- 客户拿到镜像 + 配置 + 授权即可部署。
- 平台方能维护权益和版本升级。

## 五、最终结论

codev-platform 不是没有意义。它已经有“企业私有代码知识底座 + Agent 上下文基础设施”的雏形。

但当前阶段应定位为:

> 内部 POC / 技术验证版,用于验证真实业务仓的检索、图谱、跨层链路、Agent 问答和多租户治理是否真正有用。

暂时不应定位为:

> 可直接发布给客户部署的商业产品。

要进入商业交付,优先补齐:测试全绿、wheel/Docker 交付、生产认证、健康检查一致性、secret 管理、部署验证、授权机制。
