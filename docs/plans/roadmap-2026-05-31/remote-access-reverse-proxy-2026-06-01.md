# Caddy 反代 + TLS Runbook（远程多机访问）

> 别的机器访问平台的部署方式。服务端 4 个服务**仍绑 127.0.0.1 不改**，由 Caddy 在 :443 终结 TLS 后按路径前缀转发到本地端口。
> 关联：`token-auth-enablement-2026-06-01.md`（token 启用，远程访问的前提）+ `multi-org-server-hardening-2026-05-31.md §四 P3`（A3 TLS）。

## 一、拓扑

```
远程 client(.mcp.json https) ──TLS──> Caddy :443 ──按路径前缀 strip──> 127.0.0.1:
   /platform-docs/sse  ─────────────────────────────────────────────> :18083
   /cross-link/sse     ─────────────────────────────────────────────> :18086
   /codegraph/sse      ─────────────────────────────────────────────> :18091
   /webhook/*  (远程 VCS push) ─────────────────────────────────────> :18099
```

> ⚠️ **端口是 config 驱动,以上是模板默认值**(`config.example.json`)。真实端口取自 `~/.codev-platform/config.json`:`daemon.port`(platform-docs)/ `mcp.cross_link_sse_port` / `mcp.codegraph_sse_port` / `webhook.port`。本机若改过(如用 19xxx),**以你的 config 为准 —— `gateway client-url` 运行后会打印你的实际端口映射**,照它填 Caddyfile。

路径前缀与 `gateway client-url` 写入的 `{base}/<server>/sse` **严格一致**（server 名 = `platform-docs` / `cross-link` / `codegraph` / `webhook`）。Caddy `handle_path` 自动 strip 前缀 → 上游收到 `/sse?...` / `/healthz`，与本地直连完全同形。

## 二、Caddyfile 样例

```caddy
platform.example.com {
  # TLS 由 Caddy 向 Let's Encrypt 自动签发 + 续期（域名须解析到本机公网 IP，开放 80/443）

  handle_path /platform-docs/* { reverse_proxy 127.0.0.1:18083 }
  handle_path /cross-link/*    { reverse_proxy 127.0.0.1:18086 }
  handle_path /codegraph/*     { reverse_proxy 127.0.0.1:18091 }
  handle_path /webhook/*       { reverse_proxy 127.0.0.1:18099 }
}
```

> 端口为模板默认(见上节注解);**用 `gateway client-url` 打印的实际端口替换**。
> `handle_path` strip 前缀；client 打 `/platform-docs/sse?project_id=x` → 上游收到 `/sse?project_id=x`。
> codegraph 现为多租户单端点(`mcp.codegraph_sse_port`,默认 18091),`?project_id=` 路由,**一条 `handle_path` 即可**,不再每项目一端口。

## 三、客户端操作（远程机器，每个业务仓一次）

```bash
# 1. 把 .mcp.json 的 sse url 切到反代基地址（前缀自动用 server 名）
codev-platform gateway client-url --base https://platform.example.com --repo <业务仓路径>
# 2. 注入 token header（引用 env，不写明文进仓库）
codev-platform gateway client-auth --repo <业务仓路径>
# 3. 持久化 token 到 shell profile
export PLATFORM_TOKEN='<分配的明文 token>'
# 4. 重启 Claude Code 让其重读 .mcp.json + env
```

## 四、health / 监控（反代探针）

各服务 `AuthMiddleware` 的 `public_paths={"/healthz", "/health"}` 免认证。**`/healthz` 是公开探针的真值**:只返回 `{"status":"ok","service":...}`,无 `default_project_id` / backend / loaded_projects(审计 #4 —— 公开面零敏感信息);`/health` 保留为最小别名(同样不泄敏)。经反代 strip 前缀后,client 打 `/<prefix>/healthz` 即命中上游 `/healthz`:

```bash
# 远程探活（无需 token，公开，仅 status/service）
curl -fsS https://platform.example.com/platform-docs/healthz
curl -fsS https://platform.example.com/cross-link/healthz
curl -fsS https://platform.example.com/codegraph/healthz
```

Caddy 主动健康检查示例（上游不健康自动摘除）：

```caddy
handle_path /platform-docs/* {
  reverse_proxy 127.0.0.1:18083 {
    health_uri /healthz
    health_interval 15s
    health_status 200
  }
}
```

> **详情面需 token**（审计 #4）：`/platform/status`（loaded_projects / backends / default_project_id 等聚合详情）**不在** `public_paths`,远程访问须带 `Authorization` → 反代仅透传,鉴权仍在各服务内。公开面只有 `/healthz`(+ `/health` 最小别名),不暴露任何项目 / 后端信息。

## 五、安全清单

- **必开 token 模式**：远程暴露前先 `gateway mode token`（见 token-auth runbook）。passthrough 经反代 = 未授权对外开放，**禁止**。
- **passthrough 严禁绑非 loopback**：dev 单机才用 passthrough，且只能 127.0.0.1；启动期 `auth.py` 会 WARN。
- **TLS 只在 Caddy 终结**：4 个上游服务保持绑 `127.0.0.1`（明文仅走 loopback，不出本机）；公网只暴露 Caddy :443。
- **webhook /webhook/***：给远程 VCS（GitHub/GitLab）推送用，须配 `webhook.secret` 验签（fail-closed）；走 TLS。
- **最小暴露**：除上述 4 前缀外不开任何 location；防火墙只放 80/443。

## 关联

- `token-auth-enablement-2026-06-01.md` —— token 模式启用（本 runbook 的前置）
- `multi-org-server-hardening-2026-05-31.md §四 P3` —— A3 TLS / 反代任务来源
