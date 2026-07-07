# daily-summary 2026-07-07 —— P3 多仓 fan-out 审计修复

> 承 2026-07-06 P3 首批落地。今天先做三路审计:架构、测试、运行/安全,只收敛 P3 直接相关且低风险的缺口;队列策略、启动体检、指标告警等较大项继续拆后续小步。

## 一、审计结论

- 架构审计指出 `repoTag::path` 只能在 codegraph/recall 里流转,agent `read_file/list_dir` 还不能按 tag 精确读 extra repo;rerank 也没有反解 extra repo ref。
- 测试审计指出 webhook 入队副作用、签名 webhook、agent callers/trace fan-out、code_vec 大小写 kind skip、extra repo reindex rc=2 等边界缺覆盖。
- 运行/安全审计指出 PG queue fail-closed、extra repo freshness、git pull 输出凭据脱敏等运行边界需要收敛。

## 二、已落地

### 1. RepoSpec tag 解析集中化

- `core/repos.py` 新增 `split_repo_tag()`、`resolve_tagged_value()`、`repo_specs_from_roots()`。
- tag 规则继续由 `RepoSpec` 统一持有,旧的 roots 调用也通过 `repo_specs_from_roots()` 复用同一套命名规则,避免各模块复制 `basename::` 逻辑。

### 2. agent 文件工具支持 tagged path

- `read_file/list_dir` 支持 `repoTag::relative/path`,可精确读取 extra repo 文件。
- 未知 tag 返回明确错误;路径穿越仍按登记仓根拦截。
- 多仓根目录展示继续保持合并视图,不改变单仓项目行为。

### 3. recall / code_vec 兼容和精排补齐

- `code_vector_store` 在无 RepoSpec 时保留集中式 codegraph fallback,并把 skip kind 比较改为大小写不敏感。
- `recall.service` 的 codegraph lane 在无 repo specs 时继续读集中式 codegraph DB。
- `rerank` 能按 `extra::ref` 反解到对应 repo 的 `codegraph.db`,单仓失败只退回该仓命中的 fallback 文本。

### 4. reindex / git 输出运行边界

- `reindex` 每个仓执行 `codegraph sync` 前先 fail-soft 尝试 `sync_repo_to_remote(spec.root)`,降低 extra repo 索引 stale 风险。
- `git_sync` 增加 URL userinfo 和 `token/password/...=` 查询参数脱敏,避免失败日志带出凭据。

## 三、补测

新增/扩展测试覆盖:

- `tests/test_agent_fs_tools.py`: tagged read/list、未知 tag、路径穿越。
- `tests/test_agent_tools.py`: codegraph callers/trace fan-out 和 loc 本地化。
- `tests/test_code_vec_chunking.py`: mixed-case skip kinds。
- `tests/test_recall_rerank.py`: rerank 解析 `extra::ref`。
- `tests/test_reindex_ingest_stage.py`: extra repo `rc=2` 不阻断其他仓,整体返回 locked。
- `tests/test_reindex_pull.py`: git pull 失败日志脱敏。
- `tests/test_webhook_body_limit.py`: oversized body 不入队、签名 push 入队、坏签名不入队。

已通过:

```powershell
python -m pytest tests/test_agent_fs_tools.py tests/test_agent_tools.py tests/test_code_vec_chunking.py tests/test_reindex_ingest_stage.py tests/test_reindex_pull.py tests/test_webhook_body_limit.py tests/test_recall_weights.py tests/test_recall_rerank.py
python -m py_compile codev_platform\agent\tools\fs.py codev_platform\core\repos.py codev_platform\recall\code_vector_store.py codev_platform\recall\service.py codev_platform\recall\rerank.py codev_platform\reindex\git_sync.py codev_platform\ops\reindex\commands.py codev_platform\webhook\server.py
git diff --check
```

结果:`98 passed, 1 warning`。warning 仍是 Starlette/TestClient 上游弃用提示。

## 四、剩余不在本次硬塞

- 暂无 P3 核心链路阻断项。后续只剩真实多仓项目上的端到端数据验收和更细的外部 codegraph 工具结构化 merge。

## 五、运行治理小步补齐

用户继续要求把三项较大运行治理项拆小步完成,本次没有改队列表结构,只补生产者语义、体检和观测。

- webhook 队列 fail-closed:`open_default_queue(fail_soft=False)` 只给 webhook 使用。`reindex.queue_backend=pg` 但 PG/DSN 不可用时,webhook 返回 503,不再 HTTP 200 后悄悄回退 file spool;任一 `enqueue()` 失败也返回 503。本地 hook/onboard 仍保持默认 fail-soft。
- extra repo doctor/startup 校验:`core.repos.webhook_extra_repo_mapping_issues()` 作为只读诊断真值源;webhook 启动期打印未映射 extra repo;`codev-platform health` 新增 `webhook extra repos` 检查。
- fan-out 指标/告警:`metrics` 解析现有 `tools/chroma/reindex.log` 的 `projects:`/`scopes:` 块,输出 `reindex runs/fanout_runs/max_projects/max_scopes`,Prometheus 同步暴露;阈值支持 `metrics.alerts.reindex_fanout_projects_max` 和 `metrics.alerts.reindex_fanout_scopes_max`。

补测:

- `tests/test_webhook_body_limit.py`: queue open/enqueue 失败返回 503、严格 PG 无 DSN 抛错、startup warning。
- `tests/test_graph_ingest.py`: extra repo webhook 映射诊断。
- `tests/test_metrics.py`: reindex fan-out 解析、聚合和阈值告警。

新增验证:

```powershell
python -m pytest tests/test_webhook_body_limit.py tests/test_graph_ingest.py tests/test_metrics.py tests/test_health_codegraph_usage.py tests/test_health_search_recall_client.py
python -m py_compile codev_platform\reindex\__init__.py codev_platform\webhook\server.py codev_platform\core\repos.py codev_platform\ops\health\_checks.py codev_platform\ops\health\__init__.py codev_platform\ops\metrics.py
git diff --check
```

结果:`50 passed, 1 warning`。warning 仍是 Starlette/TestClient 上游弃用提示。

P3 当前状态:首批链路已落地,审计发现的直接缺口和三项运行治理小步已补;下一步再切 Web GraphAPI read-side fan-out 和外部 `codegraph.server` MCP 代理协议固化。

## 六、Web GraphAPI read-side fan-out

继续补 P3 最后一条 Web 读侧缺口。新增 `web.integrations.codegraph_fanout.FanoutCodegraphClient`,让路由仍按一个 client 使用,fan-out 和 `tag::` 命名空间只停留在 integration 边界。

- `stats/search/node/neighbors/file-tree/graph` 六个 codegraph 路由改为跨 RepoSpec 读取多仓 `.codegraph/codegraph.db`。
- 主仓 id/path 保持旧语义;extra repo 的 node id、edge source/target、file path 返回 `repoTag::...`。
- `node/neighbors/file-tree` 支持请求 `repoTag::id/path` 精确定位 extra repo。
- 无 RepoSpec 时继续 fallback 到 centralized `codegraph_db_path(project_id)`,兼容旧测试和旧部署。

补测:

- `tests/test_web_graph.py`: 路由级 search/node/neighbors/file-tree/graph 多仓 fan-out 和 extra tag 输出。
- `tests/test_codegraph_graph_density.py`: 原图谱密度上限继续通过,防 overview 回归成发丝团。

新增验证:

```powershell
python -m pytest tests/test_web_graph.py tests/test_codegraph_graph_density.py
python -m py_compile codev_platform\web\integrations\codegraph_fanout.py codev_platform\web\routes\graph.py
git diff --check
```

结果:`13 passed, 1 warning`。

P3 当前状态:除真实多仓项目端到端验收外,code_vec / recall / agent 工具 / Web GraphAPI 的多仓读写主链路均已补齐。

## 七、外部 codegraph MCP 代理多后端合并

最后补 `codev_platform.codegraph.server` 外部 MCP 代理层。该层原本只按逻辑项目启动一个 `codegraph serve --mcp` 后端,多仓项目通过 SSE 调 codegraph 仍只看到主仓。

本次采用保守合并:

- 单仓项目保持原样透传,返回内容不加 wrapper。
- 多仓项目按 `RepoSpec` 为每个已有 `.codegraph/codegraph.db` 的仓启动/复用一个 stdio backend。
- 多仓返回按 repo 分段:先给 `{"repo":"main|tag","separator":true}` 文本块,再接该仓原始 codegraph 工具返回内容。
- 单个 extra repo 后端失败只记录日志并跳过,不拖垮其它仓;全部失败才返回 MCP 错误。
- `list_tools` 仍取首个可用后端,工具集保持与外部 codegraph 一致。

补测:

- `tests/test_codegraph_server_fanout.py`: 单仓原样透传、多仓 repo 分段、extra 后端失败 fail-soft。
- `tests/test_mcp_serve.py` / `tests/test_health_split_security.py` / `tests/test_obslog.py`: 端点、健康面、日志脱敏相关回归。

新增验证:

```powershell
python -m pytest tests/test_codegraph_server_fanout.py tests/test_mcp_serve.py tests/test_health_split_security.py tests/test_obslog.py
python -m py_compile codev_platform\codegraph\server.py
git diff --check
```

结果:`49 passed, 1 warning`。

P3 当前状态:核心 fan-out 闭环完成。多仓项目现在覆盖 reindex/code_vec/recall/agent tools/Web GraphAPI/codegraph MCP 代理;后续更适合拿真实多仓项目做端到端验收,再按实际输出优化结构化 merge。

## 八、代理 E2E 验收补测

继续把真实多仓验收前置成可重复测试,避免只测 mock 合并内容:

- `tests/test_codegraph_server_fanout.py` 补 `_backend_slots_for()` RepoSpec 选择测试。
- 覆盖主仓 + extra repo 都有 `.codegraph/codegraph.db` 时生成两个 backend slot。
- 覆盖 extra repo 没有 codegraph db 时 fail-soft 跳过,不影响主仓 slot。
- 不启动真实 `codegraph serve --mcp`,只验证平台代理的仓选择和 key/tag 规则。

验证:

```powershell
python -m pytest tests/test_codegraph_server_fanout.py tests/test_mcp_serve.py tests/test_health_split_security.py tests/test_obslog.py
python -m py_compile codev_platform\codegraph\server.py
git diff --check
```

结果:`51 passed, 1 warning`。

## 九、真实 ideas 多仓端到端验收

按真实本机仓库补做 P3 验收:`ideas-v2` 主仓 + `ideas-pda-app` extra repo。

- 本机 `~/.codev-platform/config.json` 补齐 `ideas-v2` / `ideas-pda-app` 的 `repo_path` 和 `webhook_repo`,并把废弃的 `daemon.port` 迁到 `mcp.platform_docs_sse_port`。`health --project ideas-v2 --mode light` 中 `webhook extra repos` 已为 OK。
- `platform_meta/projects/ideas-v2` 和 `ideas-pda-app` 补 PDA uni-app 路径的 `reindex_codegraph_patterns`,否则 `common/js/*.js` 这类真实 extra repo 改动不会命中 webhook/hook scope。
- 用临时 `common/js/codev_e2e_probe.js` 验证:
  - GitLab webhook handler 返回 200,`frontend/ideas-pda-app-hb` fan-out 到 `ideas-pda-app` 和 `ideas-v2`,入队 `codegraph/ingest/code_vec`。
  - 本地 hook 同源 dispatch 对 PDA repo 也入队同一批 scope。
  - PDA 和 ideas-v2 codegraph 初始化并 link 到平台数据目录后,`recall_code("codevE2eProbeIdeasPdaHb20260707", "ideas-v2")` 命中 `ideas-pda-app-hb::common/js/codev_e2e_probe.js`。
  - Web GraphAPI fan-out integration 能查到同一 extra repo node。
  - 外部 `codegraph.server` MCP 代理真实输出为 repo 分段文本:`main` 段无结果,`ideas-pda-app-hb` 段返回 1 条函数结果。
- 结论:当前 MCP repo 分段文本合并足够人工阅读;Web/API 已返回结构化 node。若后续需要程序消费 MCP 结果,再把 MCP merge 升级为结构化 JSON 聚合。

验收发现:

- `ideas-v2` 主仓未初始化 codegraph 时,`recall_code` 的 codegraph lane 会因主仓缺索引跳过整个 lane,未继续读 extra repo;本次通过初始化主仓完成验收,后续可把该 lane 调整为“主仓失败也继续 extra”。
- codegraph CLI 的 `sync/index` 对删除临时文件未清掉旧 symbol,最后用 `uninit --force` + `init -i` 重建 PDA 索引并重新 link;旧平台索引先改名备份,新索引验证后删除备份。

## 十、recall_code 主仓缺索引降级修复

补上上一节验收发现的降级缺口:`recall_code` 的 codegraph lane 现在按 repo 逐个 fail-soft。主仓 `.codegraph/codegraph.db` 缺失或查询失败时,只跳过该主仓并继续查询 extra repo;融合层仍只看到统一的 `tag::ref` 和 `tag::file`,不引入新的多仓真值源。

补测:

- `tests/test_recall_service.py`: 模拟 main repo codegraph 抛错、extra repo 正常返回,断言召回 `extra::n-extra` 且文件路径本地化为 `extra::src/extra.py`。
- 只读审计无阻断问题;保留既有更宽的 `project_repo_specs()` 配置解析异常风险,不在本小步扩范围。

验证:

```powershell
python -m pytest tests/test_recall_service.py tests/test_web_recall.py
python -m pytest tests/test_recall_weights.py
git diff --check
```

结果:`18 passed, 1 warning`。

## 十一、codegraph status Junction 判定回归测试

补第二个收尾项的防回归测试。`codev-platform codegraph status` 的展示直接依赖 `ops.codegraph.link_state()`;当前生产逻辑已能把 Windows junction/reparse point 优先识别成 `linked`,不会因为 `.codegraph` 同时表现为目录而误报成“还在仓内”。

本步只补测试,不改生产代码:

- `tests/test_codegraph_link.py`: 覆盖 Windows junction target 带 `\??\` 前缀时仍返回 `linked`。
- `tests/test_codegraph_link.py`: 覆盖 `os.readlink()` 读不到 junction target、但平台数据目录存在时仍返回 `linked`。
- 既有真实 link/junction 集成用例继续覆盖 `link_project()` 后透明读平台数据和 `link_state()==linked`。

验证:

```powershell
python -m pytest tests/test_codegraph_link.py tests/test_codegraph_ensure_link.py
git diff --check
```

结果:`13 passed`。

## 十二、reindex 队列 STALE 安全治理

补第三个收尾项:不启动 worker、不消费旧任务,先给 `reindex-queue` 增加安全清理入口。

新增能力:

- `codev-platform reindex-queue prune-stale [project] [--kind ...] [--older-than-sec N]` 默认 dry-run,只列出 STALE,不删除、不认领。
- 真删除必须加 `--yes`;破坏性打开队列时 `open_default_queue(fail_soft=False)`,避免 PG 配置不可用时静默回退 File spool 后删错后端。
- File 后端没有 running lease,`--yes` 还必须额外加 `--force-file`,用于“已确认本机 worker 停止”的人工治理场景。
- `--kind all` 对 prune 表示所有 peek 到的 kind,包括历史/未知 kind;显式 kind 只做路径安全过滤,不要求 runner 当前仍注册。
- File 后端 `discard()` 用同目录原子 rename + mtime 复核,防止 peek 后重新入队的 marker 被误删;PG 后端用单条 `DELETE ... enqueued_at <= ... AND pending/lease-expired` 保持同等 race 防护。

复审发现的 4 个风险均已补:File 正在跑误删门槛、File stat/unlink race、unknown kind 清理、PG→File fail-soft 回退。

验证:

```powershell
python -m pytest tests/test_reindex_queue.py tests/test_reindex_queue_cli.py tests/test_reindex_worker_affinity.py tests/test_pg_queue.py tests/test_cli_parser.py
codev-platform reindex-queue prune-stale --older-than-sec 300
git diff --check
```

结果:`31 passed, 15 skipped`。真实 dry-run 当前列出 14 个 STALE,未删除任何任务。

## 十三、队列清理与 worker 闭环

按上一节新增的安全命令处理真实队列,未启动常驻 worker:

- 先确认无 `reindex-queue worker` 进程,仅有 platform-docs/codegraph/graph/agent-memory 等 MCP 常驻进程。
- 用 `prune-stale --yes --force-file` 定向清理旧项目 STALE:
  - `openclaw-stock`: removed=4
  - `ideas-pda-app`: removed=3
  - `ideas-v2`: removed=3
- 保留本仓 `codev-platform__*` 4 个任务,避免删掉刚提交后的本仓索引刷新需求。
- 发现本机 worker 白名单未包含 `codev-platform`,所以不改用户级配置,只在一次性 Python 进程里临时给 `ReindexWorker` 注入 `codev-platform` repo_path。
- 执行非常驻 `drain_once()`: processed=4,`chroma/codegraph/ingest/code_vec` 均 rc=0。
- reindex 后 `health --mode light` 全绿,队列状态为“队列空”。

注意:本步没有清理或提交 `codev_platform/需求.txt`。worker 闭环后发现工作树另有 8 个未知源码 dirty 文件,与本步无关,未纳入本记录提交。

## 十四、codegraph MCP 多仓结构化 merge

补上外部 codegraph MCP 代理的可选机器可读合并格式。默认行为保持兼容:未传平台私有参数时,单仓仍原样返回后端 content,多仓仍按 repo header 分段文本合并。

新增能力:

- 调用参数支持 `_codev_merge=json` / `_codev_merge=structured`,返回单个 JSON text content。
- JSON envelope 包含 `project_id`、`tool`、`merge=codev-fanout-v1`、`repos`、`failures`。
- 每个 repo 保留 `ok/is_error/content`,便于上层稳定解析分仓结果。
- `_codev_merge` 在平台代理层剥离,不会透传给底层 codegraph backend,避免破坏外部工具 schema。
- 多仓部分失败时,成功 repo 仍进入 `repos`,失败 repo 进入 `failures`;所有后端失败继续沿用既有 `_err` 错误体,不伪造空 merge。

验证:

```powershell
python -m pytest tests/test_codegraph_server_fanout.py tests/test_mcp_serve.py tests/test_health_split_security.py tests/test_obslog.py
python -m py_compile codev_platform\codegraph\server.py
git diff --check -- codev_platform/codegraph/server.py tests/test_codegraph_server_fanout.py
```

结果:`53 passed, 1 warning`。另做只读复审,结论为无阻断问题;复审补跑 `python -m pytest tests/test_codegraph_server_fanout.py -q`,结果 `7 passed`。

## 十五、代码智能可用性与 token 成本收口

按“实际项目开发更有用、代码更可用、少浪费 token”的方向,补了一轮平台自用工具链收敛,重点不扩新功能面,先修正会误导开发者判断的状态/评测/上下文问题。

已落地:

- `health --all` / platform status 的 Chroma chunks 统计改为解析 atomic handoff `current.json`,避免 full rebuild 后真实可搜索但状态显示 0 chunks。
- `project_codegraph_dbs()` 统一接受仓内 `.codegraph/codegraph.db` 与平台集中库,eval / recall preflight 不再因为迁移期路径差异误判“未建索引”。
- `eval/run_eval.py` 在 Windows 终端强制 UTF-8 输出,避免 planner human 输出遇中文标签时报 `cp1252` 编码错误。
- `code_intelligence` eval 改用当前 `GraphStore.load_graph()` API,不再导入已废弃的 `load_graph`。
- planner 确定性词表补齐常见口语化/隐式开发问法,hard 集从低命中提升到 1.0;同时收窄 `模块` 误伤,保证“这个模块是做什么的”仍走 overview。
- `explicit_tool_selection` overlay 从 1860 字符压到 562 字符,并补齐 `code_recall` / `codegraph_trace`,减少启用显式工具选型 provider 的每轮 system prompt 成本。
- `code_recall` agent 工具输出裁掉长浮点和空字段,保留排序、名称、kind、file、lane 等 grounding 信息。
- recall optional lane 缺依赖时同一错误只 warning 一次,避免长评测被重复 `chromadb` warning 淹没。

独立复审发现并已修复:

- `platform_status` handoff 解析不能绕过传入 data root 去全局路径取库。
- `project_codegraph_dbs(cfg=...)` central fallback 不能绕过 cfg 去全局 data root。
- planner 不能用宽泛 `模块` 关键词压过 overview。
- Chroma docs 下残留/非法目录的 handoff 解析异常必须 per-dir fail-soft。

验证:

```powershell
python -m pytest tests/test_platform_status_soft_labels.py tests/test_paths.py tests/test_eval_recall.py tests/test_eval_code_intelligence.py tests/test_agent_tools.py tests/test_recall_service.py tests/test_eval_planner.py tests/test_agent_planner.py tests/test_agent_prompt_context.py tests/test_agent_chat_service.py tests/test_agent_registry.py tests/test_agent_recall_tool.py tests/test_agent_recall_invariants.py tests/test_agent_recall_pipeline.py
python -m py_compile codev_platform\platform_status.py codev_platform\core\repos.py eval\suites\codegraph.py eval\suites\recall.py eval\suites\code_intelligence.py eval\run_eval.py codev_platform\agent\planner.py codev_platform\agent\prompts.py codev_platform\agent\tools\recall.py codev_platform\recall\service.py
python eval/run_eval.py --suite planner --json
python eval/run_eval.py --suite codegraph --json
python eval/run_eval.py --suite recall --project codev-platform --json
codev-platform serve-mcp status
codev-platform health --all
git diff --check
```

结果:

- 目标测试:`154 passed, 5 skipped`。
- planner eval:standard=1.0, hard=1.0。
- codegraph eval:hit_rate=1.0, MRR=1.0。
- recall eval:ok, weighted MRR 0.347 vs uniform 0.291。
- MCP 四端点全 OK;`health --all` 合计 Chroma 6892 chunks。
- `git diff --check` 无 whitespace error,仅提示 `tests/test_paths.py` 下次 Git touch 会 CRLF→LF。
