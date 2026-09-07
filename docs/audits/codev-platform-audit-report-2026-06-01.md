# codev-platform 审计复核报告

日期: 2026-06-01

范围: 当前仓库 `C:\workspace\project` 的代码结构、测试基线、MCP/Agent/索引运行链路、打包交付、配置安全、私有化部署可验证性。

结论: 你前面能做的主要修复已经基本做完。当前项目已经从“内部原型”推进到“可继续做内部 POC / 可打包验证”的状态，但还不建议直接作为客户正式交付产品发布。主要剩余风险已经不在单元测试，而在服务编排、Docker 部署闭环、历史乱码清理、正式授权/升级/运维机制。

## 1. 本轮复核结果

### 测试基线

已执行:

- `python -m compileall -q codev_platform tests`
- `python -m pytest -q`
- `python -m pip check`
- `python -m pip wheel . -w dist --no-deps`
- 干净 venv 安装 wheel 后执行:
  - `codev-platform --version`
  - `codev-platform sync-rules --dry-run`
  - `codev-platform sync-skills --dry-run`
- 定向测试:
  - `tests/test_resources_packaging.py`
  - `tests/test_health_cross_link_path.py`
  - `tests/test_health_split_security.py`

结果:

- 全量测试: `351 passed, 5 skipped`
- 编译检查: 通过
- `pip check`: `No broken requirements found`
- wheel 构建: 通过
- 干净 venv 安装后 `sync-rules` / `sync-skills`: 通过
- 仓库明文密钥扫描: 0 命中
- Docker: 当前机器无 `docker` 命令，不能做容器启动验证

### 当前本机服务状态

执行 `python -m codev_platform.cli serve-mcp status`:

| 服务 | 端口 | 状态 | 说明 |
|---|---:|---|---|
| platform-docs | 18083 | OK | `/healthz` 和 `/health` 都返回最小健康信息 |
| cross-link | 18086 | DOWN | 当前端口未监听 |
| codegraph | 18091 | DOWN | 当前端口未监听 |

这说明旧报告里的 `platform-docs /healthz 404` 已经修复；当前 DOWN 是 cross-link/codegraph 服务没有启动，不是 health 路由泄露或探针格式问题。

## 2. 已确认修复的问题

### 2.1 Chroma 顶层重依赖问题已修复

之前问题:

- `codev_platform.chroma.server` 顶层 import `chromadb`，轻量安装或安全测试 import 时会失败。

当前状态:

- `chromadb` 已延迟到 `_get_client()` 内部导入。
- `ProjectIdError` 不再导致 import 阶段退出。
- `tests/test_health_split_security.py` 已通过。

结论: 已修复。

### 2.2 wheel 资源缺失已修复

之前问题:

- wheel 不包含 `rules/`、`skills/`，普通 pip 安装后 `sync-rules` / `sync-skills` 失败。

当前状态:

- 资源已迁移到 `codev_platform/resources/`。
- `pyproject.toml` 已配置 package data。
- wheel 内包含:
  - `codev_platform/resources/rules/`: 13 项
  - `codev_platform/resources/skills/`: 3 项
- 干净 venv 安装后同步命令可运行。

结论: 已修复。

### 2.3 健康检查路径和敏感信息拆分已修复

之前问题:

- `/health` 暴露内部详情。
- 新旧 health 探针不一致导致服务误判 DOWN。

当前状态:

- `/healthz` 与 `/health` 返回最小信息。
- 详情面转到 `/platform/status` 或 `/platform/health`。
- 定向安全测试通过。

结论: 已修复。

### 2.4 Chroma per-project reload stamp 已修复

之前问题:

- Chroma reload 依赖全局 `.last_build.json`，多项目场景容易互相影响。

当前状态:

- `_maybe_reload_project()` 优先读取 `.last_build.<project_id>.json`。
- 全局 `.last_build.json` 仅作为兼容 fallback。

结论: 已修复。

### 2.5 health cross-link 路径已修复

之前问题:

- health 里 cross-link DB 路径硬编码到仓库 `data/`，不尊重 `PLATFORM_DATA_DIR`。

当前状态:

- 已改用 `cross_link_db_path(project_id)`。
- 定向测试通过。

结论: 已修复。

### 2.6 生产认证 fail-fast 已补上

之前问题:

- 示例配置默认 `auth_mode=passthrough`，生产部署容易误暴露。

当前状态:

- 增加 `deployment.mode=dev|prod`。
- `prod` 模式下非 `token` 拒绝启动。
- `platform.url` 指向远程且非 `token` 也拒绝。
- token 只存 sha256 hash。

结论: 已修复基础防线。

### 2.7 非法 project_id 返回 400 已修复

之前问题:

- SSE 入口非法 `project_id` 分支可能裸 `return`，触发 Starlette `NoneType` 异常。

当前状态:

- chroma / cross-link / codegraph SSE 入口都返回 `JSONResponse({"error": "invalid project_id"}, status_code=400)`。

结论: 已修复。

## 3. 当前仍需处理的问题

### P1: cross-link / codegraph 当前未启动

现象:

- `serve-mcp status` 显示:
  - `platform-docs OK`
  - `cross-link DOWN`
  - `codegraph DOWN`

影响:

- 平台文档检索服务可用，但跨层链路和代码图谱当前不可用。
- 如果要给别人试用，演示链路会断。

建议:

1. 增加一条端到端启动验收命令，例如 `codev-platform serve-mcp start --wait`。
2. 验收必须覆盖:
   - `https://example.invalid/reference`
   - `https://example.invalid/reference`
   - `https://example.invalid/reference`
3. 对 DOWN 状态输出具体原因: 端口未监听、进程退出、依赖缺失、DB 缺失、项目未注册。

### P1: Docker 部署仍未验证

现象:

- 当前机器没有 `docker` 命令。
- 因此不能证明 `docker compose up` 能真正跑起来。

影响:

- 私有化交付如果走 Docker，目前还缺部署闭环。

建议:

1. 找一台有 Docker 的机器跑:
   - `docker compose config`
   - `docker compose up -d`
   - 三个 MCP health probe
   - agent health probe
2. 把这套变成 CI 或发布前检查。

### 已复核: 历史乱码不是当前阻断项

第一次扫描曾误报 59 个文件，原因是 PowerShell 编码影响了扫描脚本，把要查的乱码字符转成了 `?`，导致正常的 `?project_id=`、SQL 占位符 `?`、文档问号都被算进去了。

重新用 Unicode 编码点扫描后:

- UTF-8 解码错误: 0
- replacement character `U+FFFD`: 0
- 典型 mojibake 标记 `U+951B/U+9286/U+9225`: 只命中 1 个文件

唯一命中文件:

- `codev_platform/resources/rules/windows-powershell.md`

该文件第 16 行是在说明 PowerShell 5.1 编码问题，里面故意写了乱码样例，不是文件本身损坏。

结论: 当前没有证据表明源码和文档存在批量真实乱码。后续只需要保持 UTF-8 读写规范，不需要安排大规模乱码清理。

### P2: `python -m build` 当前不可用

现象:

- `python -m build --wheel` 失败:
  - `No module named build.__main__; 'build' is a package and cannot be directly executed`

但:

- `python -m pip wheel . -w dist --no-deps` 成功。

影响:

- wheel 本身能构建。
- 但发布文档或 CI 如果写 `python -m build` 会失败。

建议:

- 明确发布命令二选一:
  - 安装正确的 `build` 包后使用 `python -m build`
  - 或统一使用 `python -m pip wheel . -w dist --no-deps`

### P2: 私有化授权、升级和权益保护还没有实现

当前状态:

- Python wheel 可打包。
- 但没有正式授权机制。
- 没有 U 盘授权。
- 没有 license 校验。
- 没有版本升级和迁移机制。

影响:

- 可以内部试用。
- 不适合直接作为商业私有化产品交付。

建议:

1. 先做 Docker 镜像 + 配置挂载。
2. 再做 license 文件或离线授权。
3. U 盘授权作为增强项，不要作为第一版唯一授权方式。
4. 数据目录、配置目录、日志目录必须明确挂载边界。

## 4. 当前能力判断

### 已经具备实际价值的能力

- 多项目 `project_id` 隔离
- 文档检索增强
- BM25 / RRF 等检索融合基础
- Chroma 常驻文档索引服务
- codegraph 代码图谱接入
- cross-link 跨层链路
- Agent HTTP 服务
- memory / session 基础能力
- gateway 认证和 ACL
- webhook / reindex queue
- health / backup / bootstrap / systemd 辅助命令
- wheel 打包和资源同步

### 还不能直接商业发布的原因

- Docker 私有化部署未闭环验证。
- cross-link/codegraph 当前本机服务未运行。
- 未发现批量真实乱码，但仍要保持 UTF-8 读写规范。
- license、授权、升级、运维巡检机制还没落地。
- 真实客户项目的 Node.js / .NET / Python / React / Vue 插件化扫描还没形成统一接口。

## 5. 建议下一步

### 阶段 1: 把内部 POC 跑稳定

验收标准:

- `python -m pytest -q` 全绿。
- `serve-mcp status` 三个服务全部 OK。
- 使用一个真实项目完成:
  - 文档索引
  - codegraph 查询
  - cross-link 查询
  - Agent 问答

### 阶段 2: 做可演示版本

验收标准:

- Docker compose 能一键启动。
- 有 demo 项目和 demo 数据。
- 有只读 Web/API playground。
- 非开发人员能通过页面问业务问题。

### 阶段 3: 做客户私有化交付版本

验收标准:

- Docker 镜像 + 配置挂载 + 数据卷。
- token/RBAC/审计日志。
- 离线 license 或授权文件。
- 升级脚本和数据迁移脚本。
- 插件化扫描接口支持 Java / Node.js / .NET / Python / React / Vue。

## 6. 最终结论

这次复核后，项目状态比旧报告好很多:

- 旧报告里的 P0/P1 大部分已经修完。
- 全量测试已经全绿。
- wheel 打包和干净安装已经可用。
- 生产认证基础防线已经补上。

但它现在仍是“内部 POC 到可演示版本之间”的状态，不是正式客户交付版本。下一步最重要的不是继续堆功能，而是把三件事做实:

1. 三个核心服务一键启动并稳定健康。
2. Docker 私有化部署跑通。
3. 保持 UTF-8 交付文档规范，让客户能看懂、能试用、能信任。
