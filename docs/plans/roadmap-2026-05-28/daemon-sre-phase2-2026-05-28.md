# Daemon SRE / 可观测性 Phase 2 计划 (2026-05-28)

> 本计划承接 4 视角头脑风暴共识。**Phase 1 已落地** (见下), 本文档登记需要
> 真实运行时验证 (daemon 重启 / GPU OOM / Windows 服务注册) 的重型项,
> 盲改风险高, 拆出待有验证环境时实施。

## Phase 1 已完成 (commit a202afa / bb7cc0c / fd55cdf)

| 项 | 内容 |
|---|---|
| F2 完整性探针 | indexer 写戳前 `collection.count` vs manifest chunks 偏差告警 |
| F5 GPU 降级 | embed 模型 CUDA 加载失败 (OOM/busy) → CPU 重试一次, 不挂 daemon |
| F9 stale 重试 | reindex 期间 stale collection handle 查询失败 → evict + 重 ensure 一次 |
| F10 日志轮转 | `mcp_server.log` 超 5MiB 滚动 `.1` 单备份 |
| 可观测性 | `/health` 新增 `process(pid/uptime/rss)` + `gpu(free/other)` + `sse_sessions` + stats `errors/last_error` |
| F1-lite | CLI `daemon status` / `daemon stop` 手动运维 (按 /health pid) |

> ⚠️ 这些改动需 **daemon 重启** 才生效 (server.py 已改但运行中的是旧进程)。
> `codev-platform daemon stop` 后下次 Claude session 自动拉新 daemon。

## Phase 2 待实施 (需运行时验证)

### F1-full: nssm Windows 服务 / 开机自启

**动机**: 当前 daemon 由首个 Claude session lazy spawn, 重启电脑后首个 session 要等
模型加载 30-60s。作为常驻服务可消除冷启动。

**设计**:
- `nssm install codev-chroma-daemon <venv-python> -m codev_platform.chroma.server --http`
- 注入 env: `PLATFORM_PROJECT_ID` (默认 project) + `PLATFORM_DATA_DIR`
- `AppExit Default Restart` + RSS 阈值监控 (超 N GiB 自动重启回收显存碎片)
- 提供 `codev-platform daemon install-service` / `uninstall-service` 包装
- **验证要求**: 真实注册服务 + 重启电脑确认自启 + 杀进程确认自愈

**风险**: 服务模式下 project_id 上下文固定为安装时的默认值; 多项目仍靠 SSE
`?project_id=` 路由 (已支持), 但服务自身的"默认 project"语义要文档化。

### F3: 蓝绿双 daemon 零停机升级

**动机**: 升级 server.py / 换模型时, 当前要 stop 旧 daemon → 下次 session 冷启动等待。
蓝绿可让新 daemon 后台 prewarm 完成后原子切换端口。

**设计**:
- 新 daemon 起在临时端口 prewarm → ready 后改写 launcher 端口指向文件 → 旧 daemon graceful drain (等 sse_sessions=0) 退出
- 需要端口注册文件 + launcher 读该文件而非硬编码端口
- **验证要求**: 真实双进程切换 + 切换瞬间并发 search_docs 不丢请求

**风险高**: 改 launcher 端口解析 + 进程编排, 易破坏现有单 daemon 稳定流程。
非必要不做 — collection 热重载已无需重启 (仅模型/代码升级才需)。

### Prometheus /metrics

- `/health` 已有结构化 JSON; 加 `/metrics` 走 prometheus_client 文本格式
- 暴露: embedding/reranker calls/errors/latency histogram, gpu free, sse gauge, uptime
- **验证要求**: 接 Grafana 确认 scrape

### F6: 孤儿 collection 检测

- daemon 启动 / `daemon status` 扫 chroma 所有 collection, 对比 `platform_meta/projects/`
  已注册 project, 报告无主 collection (项目删了但 collection 残留)
- 提供 `codev-platform gc-collections --dry-run` 清理
- **验证要求**: 造孤儿 collection 确认检出且 dry-run 不误删

### F8: codegraph 索引落后告警

- codegraph `.codegraph/codegraph.db` mtime vs 仓最新 commit time 比较
- 落后超阈值 (如 1h / N commits) 在 `ai-health` 报 WARN
- **验证要求**: 改代码不重建索引确认告警

## 实施顺序建议

1. F1-full nssm (消除冷启动, 收益最直接, 风险中)
2. F6 孤儿 collection (多项目卫生, 风险低)
3. F8 codegraph 落后告警 (风险低)
4. Prometheus (可观测性增强, 风险低)
5. F3 蓝绿 (收益最低风险最高, 最后做或不做)
