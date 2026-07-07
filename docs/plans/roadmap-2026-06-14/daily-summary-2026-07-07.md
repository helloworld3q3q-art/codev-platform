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

- Web GraphAPI 的 codegraph 只读接口 fan-out。
- 外部 `codegraph.server` MCP 代理多后端合并,需先固化返回协议测试。

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
