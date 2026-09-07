# WSL 运行时项目登记表解析收口计划

> **状态：** 已完成（2026-07-29 受控 WSL current runtime 验收通过）
>
> **目标：** 修复版本化 WSL runtime 中 `codev-platform list-projects` 无法定位已注册项目的问题，使 CLI、平台状态聚合与 Agent 项目元数据读取共享同一只读、版本化登记表解析入口。

## 一、已验证根因

- 当前 WSL 服务使用 `/var/lib/codev-platform/runtime/current/venv/bin/python` 运行来自 wheel 的应用。
- 该运行时执行 `list-projects` 返回“未找到 `site-packages/platform_meta/projects`”；当前 `pyproject.toml` 明确排除了顶层 `platform_meta*`，wheel 未携带登记表。
- 只读临时注入既有 `CODEV_PLATFORM_META` 后，同一解释器可正确列出 7 个已注册项目，证明 CLI 解析与业务元数据本身正常。
- `platform_meta/projects/<project_id>/meta.json` 是版本化项目登记表；`platform_meta/health/**` 是运行观测产物，不能混入 wheel 或成为第二份登记表。

## 二、影响面与约束

### 等级与范围

- **等级：** L4（版本化 runtime、wheel 资源、CLI、平台状态与 MCP 关联读取）。
- **文件范围：** `platform_meta/**` 的包声明与项目元数据、`pyproject.toml`、`codev_platform/core/` 中的解析边界，以及现有读取方和定向测试。
- **MCP 结论：** platform-docs 已确认 `register → platform_meta/projects → list-projects` 的产品契约；CodeGraph 本轮返回 `upstream_unavailable`，已按规则用最新真实源码和 WSL 运行态做只读兜底。

### 不变量

- `platform_meta/projects/**` 保持唯一版本化登记表真值；不复制到 data、数据库、用户配置或另一套 runtime。
- `CODEV_PLATFORM_META` 显式覆盖语义保持兼容；不记录 token、DSN 或其他秘密。
- 不手改 `/etc`、不直接操作 `systemctl`、不重建 base/依赖/数据库/四类索引，也不创建第二个 WSL 环境。
- 仅将项目登记表作为只读 wheel 资源发布；运行时写注册元数据必须继续走显式、受控的后续写入路径，不能写入封存 release。
- Git 若需推送，仅允许 `origin/dev`，绝不推送 GitHub。

## 三、实施设计

1. 将顶层 `platform_meta` 声明为可安装资源包，仅把 `projects/*/meta.json` 纳入 wheel，排除 health 观测文件。
2. 新建窄职责的 `core.platform_meta` 解析模块：优先显式环境变量，其次已安装的登记表资源；调用方不再自行根据 `__file__.parents` 拼路径。
3. 迁移 CLI、项目仓解析、Agent 元数据读取、平台状态和 health 辅助读取到该模块，保留既有测试对 CLI 常量的可替换接口。
4. 新增定向测试：环境覆盖、打包资源解析、CLI 列表、核心读取方一致性；用临时 wheel/venv 验证干净安装后仍可列出登记表。
5. 完成源码验证后，只通过既有受控 WSL 发布链路部署本次应用代码；不触发 base/依赖/索引重建。以运行时 `list-projects`、平台状态和一次真实 MCP 调用为验收。

## 四、验证矩阵

| 层面 | 验证 |
|---|---|
| 解析 | `pytest` 覆盖环境覆盖与 bundle 回退，不依赖机器路径 |
| CLI / 读取方 | `list-projects`、`core.repos`、Agent 元数据与平台状态使用同一解析入口 |
| 打包 | `pip wheel --no-deps` 后检查 wheel 仅含项目 `meta.json`，并在临时 venv 调用 CLI |
| 回归 | 相关 CLI、onboard、项目状态、Agent 路由测试与 `ruff check` |
| WSL | 现有 runtime 解释器运行 `list-projects` 成功；不以端口存活替代实际命令验收 |

## 五、回退与完成定义

- 代码和包配置通过单个提交可回退；服务发布失败时保留当前 release，不更改索引或数据。
- 完成条件：新 wheel 含且只含版本化项目元数据；未设环境变量的 WSL runtime 也能列出登记项目；显式环境覆盖仍有效；所有读取方不再各自硬编码仓根路径。

## 六、验证与发布验收证据（2026-07-28～2026-07-29）

- [x] 新增 `core.platform_meta` 单一解析入口，覆盖 `CODEV_PLATFORM_META`、历史 Agent 环境变量兼容和已安装资源包回退。
- [x] 顶层 `platform_meta` 已作为资源包发布，wheel 仅包含 `projects/*/meta.json`，不包含 `platform_meta/health/**`。
- [x] CLI、`core.repos`、Agent 项目工具、平台状态、health 辅助读取均改为调用同一入口；Web 读仓仍复用 CLI 兼容常量。
- [x] `.venv` 定向 pytest：`51 passed`；Ruff 目标文件检查通过；`git diff --check` 通过。
- [x] 隔离 wheel/venv 冒烟通过：无 `CODEV_PLATFORM_META` 时 `list-projects` 成功列出 7 个项目。
- [x] 2026-07-29 已验收既有发布后的 WSL current runtime：服务账号 `helloworld` 运行该 runtime；未设置 `CODEV_PLATFORM_META` 时执行 `current/venv/bin/codev-platform list-projects` 成功列出 7 个项目。全过程未重建 base、依赖、数据库或索引。
