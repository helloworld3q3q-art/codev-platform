# 部署说明 (Docker Compose / systemd / Windows WSL) — Phase 0 底座

> 定位: Phase 0「安全和部署底座」的部署骨架文档。把已经散落在代码 (mcp_systemd /
> docker-compose.yml) 与各旧 plan 里的部署事实**汇总到一处**, 让新用户能按文档在本机起栈。
>
> 命令真值源: `codev-platform <subcommand>` (CLI) + `docker-compose.yml` (仓根)。本文档不
> 发明命令, 只串联既有命令; 命令细节以 `--help` 为准。

---

## 0. 安全默认 (Phase 0 Gate)

部署前先记住几条不可放宽的默认:

| 项 | 默认 | 远程部署要求 |
|---|---|---|
| 监听地址 | 所有服务绑 `127.0.0.1` | 暴露公网必须走反代 + TLS, 不直接改 host=0.0.0.0 |
| 鉴权 | gateway `auth_mode=token` 时启用 token | 远程必须配 token; 非授权 token 不能访问其它项目 |
| 日志脱敏 | token 模式自动推断 `logging.mode=prod` (脱敏 query/路径) | 生产保持 prod; dev 仅本机调试逃生口 |
| webhook | `webhook.secret` 未配 = fail-closed 拒绝 (本机信任可设 `webhook.allow_insecure`) | 远程必须配 secret 验签 |
| webhook body | 1MB 上限 (超限 413) | — |

对应代码: `core/obslog.py` (脱敏推断) / `webhook/server.py` (body 上限 + fail-closed) /
`ops/gateway.py` (token 鉴权 + 限流)。

---

## 1. 本机最小启动 (开发机, Windows/WSL/Linux 通用)

```powershell
# 1. 装平台 (editable) 到平台 venv
pip install -e D:\WorkSpace\codev-platform           # 需 agent HTTP 面再加 [agent]

# 2. 首次配置: 探测 venv / 模型 + 写 ~/.codev-platform/config.json
codev-platform setup

# 3. 注册当前业务仓为一个项目
codev-platform init <project_id>

# 4. 拉起 4 个 MCP 端点 (chroma 预热 ~30-60s)
codev-platform serve-mcp start --wait

# 5. 体检 (退出码 0=全绿 / 2=仅 WARN / 1=有 FAIL)
codev-platform health            # 当前仓
codev-platform health --all      # 平台全局视图 (所有项目 x 三库 + memory)
```

> 重启电脑后 SSE 端点不会自动复活 → 必须再跑一次 `serve-mcp start` (或装 systemd 常驻, 见 §3)。

---

## 2. memory 用的独立 PostgreSQL (Docker Compose)

memory 模块需要一个**独立**库 (红线: 严禁与业务库同库)。两条路:

- **机器已有原生 PG**: 在现有 server 建独立库 `codev_platform_memory`, 无需 compose。
- **无原生 PG / 团队服务器**: 用仓根 `docker-compose.yml` 起平台专属 PG (5433 端口, 避开业务 5432)。

```powershell
# 密码走 env, 不进 git
$env:CODEV_PG_PASSWORD='***'
docker compose up -d          # 起 PG (首次自动建库 codev_platform_memory)
docker compose down           # 停 (数据保留在 data/postgres/)
docker compose down -v        # 停 + 删数据卷 (慎用)
```

起好后配 `config.memory.pg_dsn`:
`postgresql://codev_platform:***@localhost:5433/codev_platform_memory`

详见 `docker-compose.yml` 顶部注释 + `roadmap-2026-05-29/memory-permission-model-2026-05-29.md §3.2c`。

---

## 3. systemd 常驻 (Linux / WSL2 服务器)

平台按 config 自动渲染所有常驻 unit (MCP 端点 + reindex worker + webhook + agent + 时钟重同步),
并生成单条一键安装脚本 —— 不手写 unit 文件:

```bash
# 1. 用户态生成 unit 到 ~/codev-systemd/ + 打印唯一一条 sudo 安装命令
codev-platform serve-mcp install-systemd --user <run_user>

# 2. 按打印提示执行 (单条, 装到 /etc/systemd/system + enable + restart)
sudo bash ~/codev-systemd/install.sh
```

生成的 unit (来自 `mcp_systemd.py`, 路径/端口全来自 config):

| unit | 作用 |
|---|---|
| `codev-mcp-chroma.service` | platform-docs GPU daemon (prewarm, 起来即加载模型) |
| `codev-mcp-cross_link.service` / `codev-mcp-codegraph.service` | cross-link / codegraph SSE 端点 |
| `codev-reindex.service` | reindex 写队列**串行**消费者 (写侧串行化) |
| `codev-webhook.service` | VCS push → enqueue reindex |
| `codev-agent.service` | agent HTTP 面 (需 venv extra `[agent]`) |
| `codev-clock-resync.{service,timer}` | WSL2 睡眠后时钟漂移纠偏 (hwclock → system) |

> 不在此直接 sudo: sudo 会切到 root 的 HOME → 读错 config。生成 (用户态) 与安装 (root) 分离。
> 看日志: `journalctl -u codev-webhook -n 50 --no-pager` (或 `codev-platform logs`)。

---

## 4. Windows WSL2 特有注意

- 真值源仓 / 平台服务跑在 WSL 时, Windows 侧连不上多半是 **Clash TUN 劫持 WSL 网段** 或
  **wslrelay churn** (见 `docs/incidents/2026-06-01-wslrelay-restart-forwarding-drop.md`)。
  急救: `wsl --shutdown` 重同步。
- WSL2 从睡眠恢复后系统时钟可能漂移 → `codev-clock-resync.timer` 缓解; 彻底失效仍可 `wsl --shutdown`。
- 新建索引**不要**手动重启 codegraph (churn 会掉 wslrelay 转发)。
- `.ps1` 脚本禁含非 ASCII; 读中文 MD/JSON 必须 `-Encoding UTF8` (见 `windows-powershell.md`)。

---

## 5. 远程部署 checklist (Phase 0 Gate)

- [ ] 服务仍绑 `127.0.0.1`, 公网仅经反代 (nginx/caddy) + TLS 暴露。
- [ ] `gateway.auth_mode=token` 已启用; 已为每个使用方签发独立 token。
- [ ] 验证非授权 token 不能访问其它项目 (ACL 集成测试覆盖)。
- [ ] `logging.mode` 保持 prod (或留空, token 模式自动推断 prod)。
- [ ] `webhook.secret` 已配, 未设 `allow_insecure`。
- [ ] memory PG 是独立库, 密码走 env, 备份策略就位 (见 `persistence-backup-2026-06-01.md`)。
- [ ] systemd unit 全部 active: `systemctl is-active codev-*`。

详细反代 / TLS / token 方案见 `roadmap-2026-05-31/remote-access-reverse-proxy-2026-06-01.md`
与 `token-auth-enablement-2026-06-01.md`。
