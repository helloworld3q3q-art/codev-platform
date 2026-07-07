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

- webhook / reindex 生产者在 PG queue 不可用时是否 fail-closed,需要单独评估调用方契约。
- extra repo 未登记或映射 stale 的 doctor/startup 校验,适合进入 health/doctor 小步。
- fan-out 的指标、阈值和告警,等核心读写链稳定后再补。

P3 当前状态:首批链路已落地,审计发现的直接缺口已补;下一步再切 Web GraphAPI read-side fan-out 和外部 `codegraph.server` MCP 代理协议固化。
