# Plan — 业务↔平台 MCP 服务化(脱文件路径,走服务地址)2026-05-30

> **定位**:让业务仓访问平台的三套 MCP(代码图谱 / 文档检索 / 跨层链路)从"stdio + 本地文件路径"迁到"**服务地址(HTTP/SSE)**",支撑多用户多机共享。
>
> **关联**:`platform_status.py`(已落地 health --all 走 HTTP)/ `codev_platform/chroma/server.py`(daemon 已原生 SSE)/ 业务仓 `.mcp.json`。

---

## 一、原则(用户拍板 2026-05-30)

1. **平台内部本地**:平台服务 ↔ 模型 ↔ data/(同一台服务器,co-located)→ 直接本地读,**不 HTTP 化**(更快;没有跨边界)。
2. **业务 → 平台 走服务地址**:业务仓的 MCP **不能再走文件路径**(`cmd /c ..\codev-platform\tools\...` / 读本地 `.codegraph`),必须连平台的 **HTTP/SSE 端点**。
3. **不手搓协议**:传输用 **MCP 标准远程协议**(Streamable HTTP,SSE 兼容),桥接用现成 **`mcp-proxy`** 库(venv 已装)+ **MCP Python SDK**;慢了在库支持的协议里换,不自己写传输层。

---

## 二、现状与硬约束

| MCP | 现在(业务 .mcp.json) | 走 HTTP 的可行性 |
|---|---|---|
| platform-docs(chroma) | stdio launcher → mcp-proxy 桥到 daemon HTTP :18083 | ✅ daemon **已原生 SSE**(`/sse?project_id=`),可直连 |
| codegraph | `codegraph serve --mcp`(**外部工具,stdio-only**)读本地 `.codegraph` | ⚠️ 工具本身无 HTTP;**用 mcp-proxy 把 stdio 包成 SSE**(不丢工具集) |
| cross-link | stdio launcher → 我们的 `cross_link.server` 读本地 sqlite | ✅ 我们的代码,加原生 Streamable HTTP 或 mcp-proxy 包 |

**关键约束**:
- `codegraph serve` 实测只有 `--mcp`(stdio),**无 HTTP/SSE 选项**(`codegraph serve --help` 已验)。其工具集(callers/callees/impact/context/explore)比 codegraph-api REST(stats/search/node/neighbors/file-tree/graph)**更全** → **不能用 codegraph-api 替换 codegraph MCP**(会丢工具)。正解是 **mcp-proxy 把 stdio 工具包成 SSE**,保留全工具集。
- codegraph-api(:18082)保留作**平台 status / 健康**的 HTTP 面(已落地),**不承担** AI 的 MCP 查询。

---

## 三、传输协议选型(不手搓,用库)

| 选择 | 库 | 用途 |
|---|---|---|
| **Streamable HTTP**(MCP 2025 标准远程传输,首选) | MCP Python SDK(`mcp`)`streamable_http` / Claude Code `.mcp.json` `type:"http"` | chroma daemon 加该 transport;业务直连 |
| **SSE**(兼容/过渡) | 同 SDK `mcp.server.sse`(daemon 现用)/ `type:"sse"` | 现成可用,先用它打通 |
| **stdio→SSE/HTTP 桥** | **`mcp-proxy`**(venv 已装 mcp-proxy.exe) | 把 codegraph(外部 stdio)+ cross-link 包成 HTTP 端点 |

**协议决策(已分析,2026-05-30 锁定)**:
- **锁定 Streamable HTTP** —— MCP 标准远程传输 + Claude Code 原生 `type:http` + MCP SDK/mcp-proxy 现成 + HTTP/2 持久连接。
- **WebSocket / 裸 TCP / Unix socket 一律不用**:三者都**非 MCP 标准传输**,`.mcp.json` 无对应 `type` → 客户端连不上 → 只能 fork/手搓(违背"用库不手搓");且相对 Streamable HTTP(HTTP/2 多路复用 + 二进制帧 + header 压缩)的提升是**亚毫秒级**,被 LLM/工具耗时(秒 / 百毫秒级)完全淹没 = 伪优化。
- 同机极致快本就是 **stdio**(管道 < loopback TCP),但它是路径,与"服务地址"矛盾 → 只在不要求服务化时用。
- **真正的提速杠杆**:① 连接复用(长连接,别 per-call 握手)② 暖 daemon ③ 工具本身快。**换协议不是杠杆**。
- 门槛:**先埋点测 p50/p95**,有数据才动 —— 预计不会动。

---

## 四、目标架构

```
平台服务器(服务 + 模型 + data/ 全本地 co-located)
├── chroma daemon         :18083  /sse(已有) + /streamable(可加)  · 多租户 contextvar
├── codegraph MCP 端点      :PORT_CG   mcp-proxy 包 `codegraph serve --mcp`(per-project)
├── cross-link MCP 端点     :PORT_CL   原生 Streamable HTTP 或 mcp-proxy 包
└── codegraph-api          :18082  REST(平台 status/健康用,非 MCP 查询)
         ▲ HTTP/SSE(服务地址,无文件路径)
         │
业务仓 .mcp.json:
  "platform-docs": { "type": "sse",  "url": "http://<平台>:18083/sse?project_id=<id>" }
  "codegraph":     { "type": "sse",  "url": "http://<平台>:PORT_CG/sse?project_id=<id>" }
  "cross-link":    { "type": "sse",  "url": "http://<平台>:PORT_CL/sse?project_id=<id>" }
```

---

## 五、多租户(按 project_id 隔离)

- **chroma**:daemon 已用 `?project_id=` + contextvar 路由,多项目共享一 daemon。✅ 现成。
- **cross-link**:我们的 server 加 `?project_id=` 路由(按 project 选 `cross_layer.sqlite`),单端点多租户。中等。
- **codegraph**:`codegraph serve` 是 per-repo(`--path` / 客户端 rootUri)。SSE 多租户两选:
  - **a. 每项目一个 mcp-proxy 实例**(一项目一端口,`--path <repo>`)—— 简单,起步用;
  - **b. 一个网关按 project_id 路由到对应 codegraph 实例** —— 干净,后做。
  起步 **a**(端口表写 config),量大再上 b。

---

## 六、分阶段实施

| 阶段 | 内容 | 风险 | 验证 |
|---|---|---|---|
| **P0 网关/认证基础 ✅ 已落地(含高并发+认证加密硬化)** | `codev_platform/gateway/`(auth 可插拔:passthrough/token + **纯 ASGI** 统一拦截中间件)→ 挂到 **agent 服务 + chroma daemon**(两 HTTP 入口),`/health` public;config `gateway.auth_mode`。**高并发**:纯 ASGI 不缓冲 /sse 长连接 + 认证器只读无锁。**认证加密**:token 存 sha256 hash(明文不落盘)+ `hmac.compare_digest` 常量时间比对;HTTPS/TLS 记为部署层 | 低(passthrough 非破坏;daemon 生效需重启) | 单测 12 通过(hash 命中/明文不通过/错 token 401)+ 全量 135 + agent/daemon app 均加载 AuthMiddleware |
| **P1 chroma 直连 SSE ✅** | 业务 `.mcp.json` platform-docs → `type:sse` 直连 daemon `/sse?project_id=openclaw-stock` | 低 | ✅ MCP client 连通 + list_tools 3 个(search_docs/list_collections/get_by_file) |
| **P2 cross-link HTTP 端点 ✅** | `cross_link.server` 加 SSE transport + `?project_id=` contextvar 路由 + `--http` 启动 + gateway 认证;`serve-mcp start` 拉起常驻 | 中 | ✅ 5 单测(per-project 隔离)+ /health 200 + MCP client list 4 工具 |
| **P3 codegraph SSE(mcp-proxy)✅** | `mcp-proxy --port <p> -- codegraph serve --mcp`(cwd=repo, per-project);`mcp_serve.iter_endpoints` 自动枚举 | 中 | ✅ 实包起 :18091/sse + MCP client list **全 9 工具**(无退化:search/context/callers/callees/impact/node/explore/files/status) |
| **P4 业务 .mcp.json 切换 ✅** | 三套全 `type:sse`+URL,删 `cmd /c ..\tools` 路径;备份 `.mcp.json.stdio.bak` 可回退 | 中 | ✅ 业务仓 `git commit 9943bfe`;3/3 端点 MCP client 连通 |
| **P5 端口/启动/健康编排 ✅** | `codev-platform serve-mcp status\|start`(chroma+cross-link+codegraph 一键常驻);`platform_status.mcp_endpoints` + `health --all` 渲染 reachability | 中 | ✅ serve-mcp start 拉起 4 端点全 OK;9 单测(端点枚举/probe) |

---

## 七、配置(机器路径/地址进 config 不进 git)

```jsonc
// ~/.codev-platform/config.json
"platform": { "url": "http://127.0.0.1:18083" },     // 平台服务基址(已有)
"mcp": {
  "codegraph_sse": "http://127.0.0.1:18085",          // P3 端口
  "cross_link_sse": "http://127.0.0.1:18086"          // P2 端口
},
"projects": {
  "openclaw-stock": { "codegraph_api_url": "http://127.0.0.1:18082", "codegraph_sse_port": 18085 }
}
```
业务仓 `.mcp.json` 的 URL 可由 `codev-platform init` / 一个 `codev-platform mcp-config` 子命令按 config 生成,避免手填。

---

## 八、启动 / 运维

- 三个 SSE 端点(chroma 已有 + codegraph + cross-link)随平台常驻;mcp-proxy/daemon 进程由 launcher 或 service manager 拉起(沿用 chroma daemon 的 spawn lock 思路)。
- `ai-health` / `health --all` 增加"各 MCP SSE 端点 reachable"检查。
- daemon 必须常驻(失去 stdio 的 per-session auto-spawn)—— 平台开机/首次访问拉起。

---

## 九、风险与"现在不做"

- **单机现在 stdio 更快**(无网络往返,auto-spawn 方便)。本套**收益在跨机共享**;单机是为多机铺路。→ **留接缝、分阶段**,不一次性全切。
- **codegraph 多租户**是最复杂点(per-repo 工具 + 每项目端口/路由)。P3 起步用"每项目一端口",别先做网关。
- **daemon 常驻依赖**:SSE 没了 stdio auto-spawn,平台必须保证端点常驻,否则业务 `/mcp` 红。
- **不手搓传输**:坚持 mcp-proxy + MCP SDK;协议慢先测再在库内换(§三)。

## 十、验收(2026-05-30 全绿)

- [x] 业务仓 `.mcp.json` **零** `cmd /c ..\tools` 文件路径,三套全 `type:sse` + URL(commit 9943bfe)。
- [x] 业务仓三端点 MCP client 连通;codegraph 全工具(9 个:callers/impact/context...)经 SSE 可用(**无工具集退化**)。
- [x] 多项目隔离:cross-link per-project contextvar 路由 + 单测验 proj-a 查不到 proj-b 数据(`test_cross_link_server.py`)。
- [x] `serve-mcp start` 一键拉起 4 端点全 reachable;`health --all` 报 `mcp_endpoints` 状态。
- [ ] 传输延迟 p50/p95 埋点 —— **留接缝未做**(§三:换协议非杠杆,有数据再说;单机 stdio 本就更快,本套收益在跨机)。

> **常驻依赖(运维须知)**:cutover 后业务仓失去 stdio per-session auto-spawn。开机/重启后须跑一次
> `codev-platform serve-mcp start` 拉起 4 端点(chroma 预热模型 ~30-60s)。否则业务仓 `/mcp` 红。
> 急救回退:`copy .mcp.json.stdio.bak .mcp.json`(恢复旧 stdio 自 spawn 模式)。

---

## 十一、与既有的关系

- **已落地**(本轮前):`health --all` / 平台 status 走 daemon `/platform/status` HTTP;codegraph/cross-link **统计**经 codegraph-api HTTP。本 plan 是把 **AI 的 MCP 查询路径**也服务化,补齐最后一段。
- codegraph-api 不改(REST 作 status 面);codegraph 查询走 mcp-proxy 包的 SSE(保全工具集)。
