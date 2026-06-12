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

### 3. 建第二个人的账号 + RBAC(CLI 现成,走 RbacStore=隔离真值)
```bash
# 在服务器(WSL)上跑(memory.pg_dsn 已配 → 写 PG)。org 不存在先建。
codev-platform org create <org> --name "<显示名>"          # 若 org 未建
codev-platform org add-user  dev2 --name "Dev Two"          # 建 user
codev-platform org add-member <org> dev2 --role member       # 加 org 成员
codev-platform org project grant openclaw-stock dev2 --role member   # 授项目访问权
codev-platform org list                                      # 核对
```
> token 身份(第 1 步的 user_id/org_id)必须与上面账号(user/org)对齐,RBAC 才一致。
> **要登 web 控制台**(username+密码)再加一步设密码(MCP/SSE token 接入不需要):
> ```bash
> codev-platform org set-password dev2 --password "<密码>"   # 写 users.password_hash(A)
> ```
> 双 store 厘清:orgs/users/org_members 同表单一真值源;`org add-user/add-member/grant`(RbacStore=授权)
> 与 `org set-password`(account_store=身份+密码)**同表互补不冲突**(详见 [[account-rbac-two-store-model]])。

### 4. 第二台 client 的 .mcp.json(带 token 连 SSE)—— 一条命令搞定
```bash
# 在第二台仓里跑: 自动把 token 内联进各 sse url 的 ?token=(任何 MCP 客户端可用, url 一定能填)
export PLATFORM_TOKEN='<dev2 明文 token>'
codev-platform gateway client-auth --repo . --query-token        # 写 ?token=<明文> 进 url
# 远程: 再 gateway client-url --base https://<server> 把 url 指向服务器反代
```
生成形如:`http://<server>:19092/sse?project_id=openclaw-stock&token=<dev2-token>`(4 端点)。
> **header 形式更安全**(token 不进 URL/log):客户端支持 header 则用 `gateway client-auth`(不带
> --query-token)写 `Authorization: Bearer ${PLATFORM_TOKEN}`。服务端两种都认(2026-06-12 加 ?token= 兜底)。

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
- [x] `client-auth --query-token`(2026-06-12,自动写 `?token=` url + 服务端 query 兜底)
- [x] 账号 onboarding CLI(已现成 `org` 命令,走 RbacStore;deleted 重复 account.py)
- [x] token-mode 端到端验证(in-process:AuthMiddleware→query_string→authenticator→identity 整链测过)
- [x] **PG token 路径端到端验证**(2026-06-12,真 PG 9/9 通):build_authenticator→PgTokenResolver→
      PgTokenStore.lookup(join users/orgs status)→TokenAuthenticator→can_access 全栈。验:对 token
      解析身份 / 授权项目 ALLOW / 越权项目 DENY(闸1 org + 闸2 白名单)/ 错·无 token 401 / `?token=`
      兜底 / **禁用用户即失效 / 禁用 org 即失效**。token-add(config)与 pg-token-issue(PG 实时禁用)二选一,
      多人服务器用 PG token(web 禁用即生效); config token 是 break-glass(不受 web 控,启动会告警)。
- [ ] sqlite→pg graph 迁移命令(切 PG 不丢现有图谱)— Stage C,并发上来再做
- [ ] health/status pg-aware(pg 模式不误报 "未建")— Stage C
- [x] web 控制台密码-set CLI(2026-06-12 `org set-password`;厘清双 store=同表互补非 fork,
      密码定向 UPDATE users.password_hash 不覆盖 B 的 display_name)
- [ ] 真机端到端(真 Claude Code 客户端连 token-mode 服务器)— 周末接入时验
