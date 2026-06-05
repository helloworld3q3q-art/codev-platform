# 设计:MCP 端点端口/配置键统一(消除 daemon.port 异类 + 两套端口对齐 + 样本补全)

> **产物性质**:roadmap-2026-06-05 子设计文档。把当前 4 套 MCP 端点(platform-docs / codegraph /
> agent-memory / graph)散落、不统一的端口配置收敛成"单一 resolver + 服务键注册表 + canonical 键",
> 全程 **back-compat**(旧键作 deprecated 别名,现网/现有配置不破),不重命名物理链。
> **一句话**:端口真值收敛一处、键名统一 `mcp.<service>_sse_port`、本机客户端口从 bind 口派生、样本+文档补全。

---

## 一、问题(已分析的三处不统一)

| # | 问题 | 现状 |
|---|---|---|
| ① **键名不统一** | chroma 端口在 `daemon.port`(daemon 段),其它三套在 `mcp.<x>_sse_port`(mcp 段) | chroma 是异类(daemon 早于 serve-mcp 编排) |
| ② **两套端口手动对齐** | 服务端 bind 口(`daemon.port`/`mcp.*_sse_port`)与客户端连接口(`mcp_sources.{local,platform}.<tool>`)各配各的,要手动一致 | 加 agent-memory 时踩过:得手动补 `mcp.agent_memory_sse_port:19087` 才对齐 |
| ③ **config.example 样本滞后** | `mcp` 段只列 `codegraph_sse_port`+`graph_sse_port`,**缺 `agent_memory_sse_port`** | 代码有默认 18087 兜底,但照样本配的人会漏(想 pin 19087 时不知键名) |

## 二、设计原则

1. **统一但 back-compat**:旧键(`daemon.port`、显式 `mcp_sources.local.*`)作 **deprecated 别名**,继续可读,
   不破现网/现有配置(沿用 `recall_backend` / `per_tool_cap` 的别名先例)。
2. **端口解析收敛一处**:单一 `_bind_port(cfg, kind)` + 服务键注册表;消灭散落的
   `_cfg_get("daemon.port") or DEFAULT` vs `_cfg_get("mcp.x_sse_port") or DEFAULT` 不一致。
3. **不动物理链**:chroma server.py 读 env `PLATFORM_DOCS_DAEMON_PORT`、systemd 注入、launcher —— **不改**;
   只统一 **config 侧的端口真值键**,注入 env 的值从统一 resolver 取。
4. **本机口派生**:`mcp_sources.local.<tool>` 缺省 = 该服务 bind 口(同主机),不重复配;`platform`(远程)仍独立。

## 三、方案

### 3.1 ① 键名统一 —— 4 套都归 `mcp.<service>_sse_port`

canonical 键(统一命名 `<service>_sse_port`):

| 服务 | canonical 键 | deprecated 别名 | 默认 |
|---|---|---|---|
| platform-docs(chroma) | `mcp.platform_docs_sse_port` | `daemon.port` | 18083 |
| codegraph | `mcp.codegraph_sse_port` | — | 18091 |
| agent-memory | `mcp.agent_memory_sse_port` | — | 18087 |
| graph | `mcp.graph_sse_port` | — | 18092 |

**服务键注册表 + resolver**(mcp_serve.py):
```python
_SERVICE_PORTS = {   # kind: (canonical_key, [deprecated_keys], default)
  "chroma":       ("mcp.platform_docs_sse_port", ["daemon.port"], 18083),
  "codegraph":    ("mcp.codegraph_sse_port",     [],              18091),
  "agent_memory": ("mcp.agent_memory_sse_port",  [],              18087),
  "graph":        ("mcp.graph_sse_port",         [],              18092),
}
def _bind_port(cfg, kind) -> int:
    canon, aliases, default = _SERVICE_PORTS[kind]
    if (v := _cfg_get(cfg, canon)) is not None: return int(v)
    for a in aliases:
        if (v := _cfg_get(cfg, a)) is not None:
            _warn_deprecated(a, canon); return int(v)   # 用旧键 → 一次性 warn
    return default
```
`iter_endpoints` / `mcp_systemd`(含 chroma 的 `PLATFORM_DOCS_DAEMON_PORT` 注入值)统一从 `_bind_port` 取。
`daemon.port` 永久保留可读(别名),旧配置不破。

### 3.2 ② 两套端口对齐 —— local 派生 + 一致性校验

- `mcp_source_endpoint(cfg, "local", tool)` 的端口缺省 = `_bind_port(cfg, kind)`(本机客户端连本机服务),
  **不再在 `DEFAULT_MCP_SOURCES.local` 写死端口**;`mcp_sources.local.<tool>` 显式配了才覆盖。
- `mcp_sources.platform` 仍独立(远程平台 host/port)。
- `serve-mcp status`(或 `ai-health`)加**一致性检查**:`_bind_port(kind)` ≠ `mcp_sources.local.<tool>` → **WARN**
  (防只改一边手抖)。

### 3.3 ③ config.example + 文档补全

- `config.example.json` `mcp` 段列全 **4 个 canonical 键**(`platform_docs_sse_port` / `codegraph_sse_port` /
  `agent_memory_sse_port` / `graph_sse_port`)+ 注释标 `daemon.port` 为 deprecated 别名;`mcp_sources` 注释说明
  `local` 缺省派生自 bind 口。
- 真值源 rules:`ai-tools-mcp.md` §一b 端口说明 + `CLAUDE.md` 相关处更新为统一键(改 `resources/rules/` 真值源
  后 `sync-rules`)。

## 四、模块落点

```
codev_platform/mcp_serve.py     +_SERVICE_PORTS +_bind_port +_warn_deprecated;
                                iter_endpoints / mcp_source_endpoint(local 派生)改用;
                                DEFAULT_MCP_SOURCES.local 去硬端口(派生)
codev_platform/mcp_systemd.py   chroma 的 PLATFORM_DOCS_DAEMON_PORT 注入值从 _bind_port 取
codev_platform/ops/health 或 serve-mcp status   加 bind vs local-source 一致性 WARN
config.example.json             mcp 段补全 4 canonical 键 + 注释
codev_platform/resources/rules/ai-tools-mcp.md + CLAUDE.md   端口键说明统一(后 sync-rules)
```

## 五、分阶段交付

| 阶段 | 内容 | 验证 | 兼容 |
|---|---|---|---|
| **P0 端口解析收敛** | `_SERVICE_PORTS` + `_bind_port` + `_warn_deprecated`;`iter_endpoints`/`mcp_systemd` 改用;`daemon.port` 作别名 | 既有 mcp_serve 测试全绿(行为不变)+ `_bind_port` 单测(canonical>别名 warn>默认) | daemon.port 仍可读 |
| **P1 local 派生 + 一致性校验** | `mcp_source_endpoint` local 缺省派生;`DEFAULT_MCP_SOURCES.local` 去硬端口;status 加 bind≠local WARN | local 派生单测 + 一致性 WARN 单测 | 显式 local 配仍覆盖 |
| **P2 样本 + 文档** | `config.example.json` 补全 4 canonical 键;`ai-tools-mcp.md`/`CLAUDE.md` 统一(sync-rules) | 引用路径存在 + pre-push audit | — |

每阶段独立可上、全 back-compat。

## 六、迁移 / 兼容(不破链)

- `daemon.port` **永久保留为别名**(chroma 历史 + env 链深),不强制迁移;文档标 deprecated、推荐 `mcp.platform_docs_sse_port`。
- env `PLATFORM_DOCS_DAEMON_PORT` **不改**(部署层接口),chroma server.py / systemd / launcher 链不动;只是 config 侧端口真值键统一、注入值来源收敛到 `_bind_port`。
- `mcp_sources.local` 旧显式配置仍生效(覆盖派生);现网 WSL config(已 pin 19xxx)继续工作。

## 七、测试

- `_bind_port`:canonical 优先 / 别名兜底带一次性 warn / 默认;chroma 别名 `daemon.port` 命中。
- local 派生:未配 `mcp_sources.local` → 端口 == bind 口;显式配 → 覆盖。
- 一致性校验:bind ≠ local → WARN(不阻断)。
- `iter_endpoints` / systemd 渲染端口与 `_bind_port` 一致(回归)。

## 八、风险 / 不做

- **不重命名 `daemon.port` 物理键**(只加 canonical 别名)—— 避免破 chroma/systemd/launcher/env 链。
- **不改 env `PLATFORM_DOCS_DAEMON_PORT`**(部署接口)。
- **不做端口自动分配**(显式 pin 防漂移,沿用现状)。
- 一致性检查只 **WARN 不阻断**(端口本就可能有意不同,如反代场景)。

## 九、关联

- `mcp_serve.py`(端口枚举 / 命令构造)、`mcp_systemd.py`(unit 渲染)、`config.example.json`、
  `resources/rules/ai-tools-mcp.md`(§一b 端点表 / §四 故障表的端口)
- 别名先例:`recall_backend`(local/vector 别名)、`per_tool_cap`→`retrieval_distinct_cap`(deprecated 映射)
- 同期:`memory-recall-pluggable-pipeline-2026-06-05.md`(同属"配置驱动、可换、不堆"治理思路)
