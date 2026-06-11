# 多人服务器部署 runbook(WSL-sim → 真服务器,token 模式接入)

> 目标:把平台从单机 passthrough 变成**多人 token 模式服务器**,第二台机/第二个人安全接入(被 RBAC 隔离)。
> WSL 现在就是"服务器"(systemd 跑全套服务),第二台是 client。真服务器后步骤同构(改 IP/TLS)。
>
> **前置事实**(2026-06-12 核查):auth 代码全建好(token 验证 / AuthMiddleware / 账号 org 成员 API /
> bootstrap admin / 密码 hash / session / `gateway token-add` / `client-auth`);当前 `gateway.auth_mode`
> 未设 = passthrough(**多人零隔离,必须切 token**)。平台有 fail-fast 护栏:多人 + passthrough 会拒启动。
>
> **本轮平台侧已补**(commit 见下):authenticator 支持 `?token=` query 兜底 → 任何能设 SSE URL 的 MCP
> 客户端(.mcp.json url 一定能填)都能带 token 认证,不依赖客户端是否支持 Authorization header。

## 临界路径(让第二个人安全接入 = 这 5 步)

### 1. 建 token(先建,再切模式,否则自己也连不上)
```bash
# 给自己 + 第二个人各发一个(明文只打印一次,记下来)
codev-platform gateway token-add <self>    --org <org> --projects '*'
codev-platform gateway token-add <dev2>    --org <org> --projects 'openclaw-stock,codev-platform'
# token-list 看已发, token-rm 撤销。hash 存 config,明文不落盘。
```

### 2. 切 token 模式(config)
```jsonc
// ~/.codev-platform/config.json
"gateway": { "auth_mode": "token" }
```
> 切完 passthrough 失效:所有 client 必须带 token 才连得上(包括你自己,所以第 1 步先做)。

### 3. 建第二个人的账号(web 登录 + RBAC 真值)
- 首个 admin:web app 启动自动 bootstrap(`CODEV_PLATFORM_ADMIN_PASSWORD` / config.web.bootstrap_password / 默认 'admin')。
- 第二个人:admin 登录后调 web API(或 web-ui orgs/users 页,若已做):
  `POST /api/v1/orgs/<org>/members/add`(加成员)+ 给 project_access。
- token 身份(第 1 步)与账号身份(org/user)要对齐(同 user_id/org_id),RBAC 才一致。

### 4. 第二台 client 的 .mcp.json(带 token 连 SSE)
每个平台 MCP 端点的 url 加 `&token=`(server IP 换成真服务器 IP;header 形式更安全,客户端支持则用 `client-auth`):
```jsonc
{
  "mcpServers": {
    "graph":        { "type": "sse", "url": "http://<server-ip>:19092/sse?project_id=openclaw-stock&token=<dev2-token>" },
    "platform-docs":{ "type": "sse", "url": "http://<server-ip>:19083/sse?project_id=openclaw-stock&token=<dev2-token>" },
    "codegraph":    { "type": "sse", "url": "http://<server-ip>:19091/sse?project_id=openclaw-stock&token=<dev2-token>" },
    "agent-memory": { "type": "sse", "url": "http://<server-ip>:19087/sse?project_id=openclaw-stock&token=<dev2-token>" }
  }
}
```
> header 形式(支持的客户端):`codev-platform gateway client-auth --repo . --env PLATFORM_TOKEN` 写
> `Authorization: Bearer ${PLATFORM_TOKEN}`,client 启动前 `export PLATFORM_TOKEN=<明文>`。

### 5. 网络可达 + 重启服务
- WSL 服务绑到可被第二台访问的地址(WSL mirrored/NAT + 端口转发;见记忆 [[wsl-mirrored-fixes-clash-tun]] / [[wsl-forwarding-restart-and-win-services]])。真服务器:绑 0.0.0.0 + 防火墙开端口 + **HTTPS/TLS**(反代终结,远程必须;`?token=` 走明文会进 access log,远程务必 TLS + 关/脱敏 access log)。
- 重启服务(sudo 123456):`sudo systemctl restart codev-mcp-graph codev-mcp-platform-docs codev-mcp-codegraph codev-mcp-agent-memory codev-web codev-agent`。

## 验证(第二个人接入成功 = 这些通)
1. 第二台 `/mcp` 四套全绿(带 token 连上,无 token → 401)。
2. 第二个人只看得到自己 project_access 的项目(查别 org/无授权 project → 403)。
3. web-ui 第二个人登录 → 只见自己的 org/项目。

## 健壮性(并发上来再做,非周末必须)
- **Stage C(graph + reindex → PG)**:多人并发写时 sqlite 文件易撞,切 PG 后端(memory 已 PG)。
  代码已建(PgGraphStore/PgJobQueue + 契约/审计),开关 `graph.store_backend=pg` / `reindex.queue_backend=pg`
  + sqlite→pg 迁移脚本(待补)+ health/status pg-aware(待补)。触发条件:多人并发索引/写。
- org_id 防御纵深:闸已 org 隔离;存储层 org_id 留统一多组织-DB 加固阶段(见 [[multi-machine-platform-arc]])。

## 待补平台工作(我负责,按需排)
- [ ] sqlite→pg graph 迁移命令(切 PG 不丢现有图谱)
- [ ] health/status pg-aware(pg 模式不误报 "未建")
- [ ] 用户创建 web-ui 页(现仅 API)/ `user-add` CLI(批量便捷)
- [ ] `client-auth` 增 `--query-token` 选项(自动写 `&token=` url 形式,给 header-less 客户端)
- [ ] token-mode MCP 客户端接入的端到端真机验证
