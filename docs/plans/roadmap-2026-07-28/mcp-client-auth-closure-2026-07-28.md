# MCP 客户端认证链路收口计划

> **状态：** 已完成（2026-07-29 新 Codex 会话最终验收通过）
>
> **目标：** 在不降低 Bearer 鉴权、不泄露或轮换 token 的前提下，修复 Codex/CLI 到 WSL 四个 MCP 端点的实际认证链路；服务端存活不能替代客户端可调用。

## 一、问题与已知事实

- 项目 `.codex/config.toml` 已为 platform-docs、codegraph、agent-memory、graph 配置 `bearer_token_env_var`，且均指向同一环境变量名。
- Windows 当前命令环境存在该变量，WSL MCP 服务进程也存在该变量；四个服务端点的运行状态均为 OK。
- 但 Codex MCP 初始化及 WSL 服务账号执行 `codev-platform health --all` 都返回 HTTP 401。
- 因此先证明“客户端 token 与服务端 token 是否一致”“header 是否被发送/接受”“CLI env-file 回退是否受权限阻断”，再选择最小修复。

## 二、影响面与约束

### 等级与范围

- **等级：** L4（MCP 配置、认证、长驻服务）。
- **候选文件范围：** `codev_platform/ops/health/**`、认证 token 读取的公共模块、目标测试；只有证据证明项目配置错误时才修改 `.codex/config.toml`。
- **外部范围：** 仅只读验证 WSL 服务和 HTTP 端点；未证明需要前，不重启服务、不改 `/etc` 私有 env 文件、不修改用户级配置。

### 不变量

- token、DSN、密码、Authorization header 及其可逆派生值不得写入日志、计划、测试、Git 或对话。
- 不通过关闭 token 模式、开放匿名端点或把 token 写死来“修复”401。
- 不重建 runtime、base、依赖、数据库或四类索引；不操作裸 `systemctl`。
- 不推送 GitHub；未经用户后续指令，本任务不提交、不推送。
- 所有配置修改须保留 `bearer_token_env_var` 的环境变量注入模式；修改后需明确 Codex 会话重启要求。

## 三、诊断与实施顺序

1. 只读确认工作树、项目 MCP 配置、Windows 客户端变量存在性、WSL 服务进程变量存在性与四端点状态。
2. 通过安全布尔比较确认 Windows 客户端 token 与 WSL 服务 token 是否相同，不输出任一 token 或摘要。
3. 使用真实 Bearer 只读 HTTP 请求验证 `/platform/status` 与至少一个 `/mcp` initialize 请求的认证结果；输出仅限 HTTP 状态和结构化错误类别。
4. 检查 `health --all` 的 token 读取顺序、`systemd.env_file` 可读性和权限边界，定位 401 是 token 不一致、客户端未注入还是 CLI fallback 缺陷。
5. 仅在根因明确后实施最小修复：
   - token 不一致：修复受控同步/配置来源，保持环境变量注入；
   - header 未发送或服务端未接收：修复单一认证适配层；
   - env-file fallback 不可读：改为安全的最小读取/调用路径，不放宽私有文件权限。
6. 补定向回归，复核四端点的真实 MCP 只读调用；如项目 MCP 配置变更，明确要求新会话生效。

## 四、验证矩阵

| 层面 | 验证 |
|---|---|
| 配置 | `codex mcp list`、`codex mcp get platform-docs`，仅核对端点与 token 环境变量名 |
| 认证读取 | 定向 pytest，覆盖 env 优先、受控 env-file 回退、缺 token 401 与错误 token 401 |
| HTTP | 使用真实 token 发起只读 `/platform/status` 和一个 MCP initialize；仅记录状态码 |
| 服务 | `codev-platform serve-mcp status` 四端点 OK |
| 回归 | 认证/health 目标测试、`python -m pytest tests/test_cli_parser.py`、Ruff 与 `git diff --check` |

## 五、回退与完成定义

- 代码变更可由单独 commit 回退；不触碰 token 本身，因此不需要 token 轮换或服务数据回滚。
- 若根因位于 Codex 宿主进程环境且仓库无法安全修复，记录最小、无秘密的用户侧重启/环境同步动作，不伪造“已修复”。
- 完成条件：Windows 用户级 token 与 WSL 服务 token 精确一致；真实 MCP 协议 initialize 和平台状态请求均携带 Bearer 且成功；四端点继续保持 token 模式。当前 Codex 会话重启后以同一环境变量完成最终客户端验收。
- `health --all` 的运行账号边界单列处理：root 受控调用可读取 root 私有 env 文件并已成功；服务账号不能读取该文件是现行权限设计，不以放宽权限或复制 token 的方式绕过。Windows CLI 的平台地址配置属于后续“单一增量状态视图”任务，不与 MCP 认证问题混改。

## 六、完成证据（2026-07-28）

- [x] 已确认项目配置、Windows 命令环境和 WSL 服务进程均声明/持有 token 环境变量。
- [x] 安全布尔比较证明：Windows 进程/用户级旧 token 与 WSL 服务 token 不一致；WSL 服务 token 在各服务进程中一致。
- [x] 已将 Windows **用户级** `CODEV_PLATFORM_MCP_TOKEN` 精确同步为 WSL 受控服务 token；未轮换 token、未改服务端、未写入仓库或日志。
- [x] 用同步后的 Windows 环境实调 `/platform/status` 与 platform-docs、codegraph、agent-memory、graph 四个 `/mcp` initialize，均返回 HTTP 200。
- [x] `codex mcp list/get` 复核四端点仍声明同一 Bearer 环境变量；`tests/test_health_all_auth.py` 为 `9 passed`。
- [x] WSL root 受控 `health --all` 成功；服务账号读取 root `0600` 私有 env 文件被拒绝，属于权限边界而非 Bearer 协议故障，未擅自 chmod/chown 或复制 token。
- [x] 2026-07-29 已在新 Codex 会话中完成最终客户端验收：platform-docs、codegraph、agent-memory、graph 四个 MCP 的真实只读工具调用均成功；`codex mcp list` 仍显示它们使用同一 Bearer 环境变量，未出现 HTTP 401。
