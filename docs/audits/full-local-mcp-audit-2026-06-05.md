# codev-platform 本地全量扫描 + MCP 交叉审计报告

审计日期：2026-06-05

审计口径：以后端、平台、MCP、Agent、Memory、Graph、Reindex、运维和 Web 后端控制面为主。`web-ui/` 前端界面不作为重点，仅在被 Graph/插件扫描或 Web 后端 API 依赖时提及。

本报告取代本轮对根目录 `AUDIT_REPORT.md` 的引用。当前审计依据为本地代码、`docs/audits/` 目录、MCP codegraph 索引和实际命令输出。

## 1. 执行摘要

本轮已完成全量本地扫描和 MCP 交叉验证，不是只抽样看主干。

结论：

1. 当前主线已经是 `graph` 统一图谱，`cross_link` 不再是活跃服务或 Agent 工具。
2. MCP 当前暴露 4 个 SSE 服务：`platform-docs`、`codegraph`、`agent-memory`、`graph`。
3. 本地 Python 源码语法检查通过，`pip check` 无依赖冲突。
4. 全量测试当前不是全绿，失败集中在 runtime extra 未安装导致的依赖型测试。
5. 排除依赖型测试文件后，主干回归稳定。
6. `ruff` 仍有 5 个小问题，需要单独清理。

关键结果：

```text
python -m compileall -q codev_platform tests scripts tools eval
=> passed

python -m pip check
=> No broken requirements found.

python -m pytest -q
=> 8 failed, 1135 passed, 5 skipped, 1 warning

python -m pytest -q -k "not agent_embed_registry and not agent_recall_pipeline"
=> 1102 passed, 5 skipped, 41 deselected, 1 warning
```

当前失败集中在：

- `tests/test_agent_embed_registry.py`：缺 `sentence-transformers`，`qwen-local` embedder 构造为 `None`。
- `tests/test_agent_recall_pipeline.py`：缺 `rank_bm25` / `jieba`，BM25 相关测试失败。

当前环境依赖探测：

```text
sentence_transformers: False
rank_bm25: False
jieba: False
fastapi: True
sqlalchemy: True
torch: False
```

这些缺失项位于 `pyproject.toml` 的 `runtime` extra，不在轻量 dev 基础依赖内。

## 2. 扫描范围

本地扫描目录：

- `codev_platform/`
- `tests/`
- `scripts/`
- `tools/`
- `eval/`
- `docs/audits/`

未作为重点：

- `web-ui/` 前端界面代码
- `.venv/`
- `linux-venv/`
- `node_modules/`
- `__pycache__/`
- `.pytest_cache/`
- `.git/`

文件面统计：

```text
codev_platform Python 源码：260
tests Python 测试文件：134
scripts/tools/eval/docs/audits 相关文件：37
```

`codev_platform/` 分目录 Python 文件数：

```text
agent       58
web         67
plugins     27
core        23
ops         23
chroma      19
graph       18
cli_cmds     6
reindex      5
gateway      3
webhook      3
codegraph    2
```

MCP codegraph 索引：

```text
598 files indexed
406 Python files indexed
```

MCP codegraph 覆盖了 `codev_platform/`、`tests/`、`scripts/`、`eval/` 以及 Web 后端控制面。

## 3. MCP 当前事实

当前 `.mcp.json` 配置的是：

```text
platform-docs  -> http://127.0.0.1:19083/sse?project_id=codev-platform
codegraph      -> http://127.0.0.1:19091/sse?project_id=codev-platform
agent-memory   -> http://127.0.0.1:19087/sse?project_id=codev-platform
graph          -> http://127.0.0.1:19092/sse?project_id=codev-platform
```

当前 MCP 主线：

- `codev_platform/chroma/server.py`：`platform-docs`，负责 docs search、embed、rerank。
- `codev_platform/codegraph/server.py`：`codegraph`，代理外部 codegraph MCP 后端，并按 `project_id` 路由。
- `codev_platform/agent/memory_mcp.py`：`agent-memory`，负责 memory recall/list/write/forget/supersede。
- `codev_platform/graph/mcp_server.py`：`graph`，负责 impact、table usage、API callers、node/domain search。

共同安全模型：

- SSE 建连时读取 `project_id`。
- 先 validate project_id。
- 再执行 `can_access`。
- 写 audit。
- token 模式下缺显式 project_id 会被拒绝，passthrough dev 才允许回退默认项目。

## 4. cross_link 当前状态

本地全量搜索确认：`cross_link` 当前不是活跃服务主线。

活跃替代路径：

- Agent 影响分析工具：`codev_platform/agent/tools/impact.py`
- Graph MCP：`codev_platform/graph/mcp_server.py`
- Graph store：`codev_platform/graph/store.py`
- Graph impact 引擎：`codev_platform/graph/impact.py`
- Graph ingest：`codev_platform/graph/ingest.py`

当前代码中 `cross_link` 残留主要是：

- 历史注释
- 迁移说明
- 测试断言，确认旧 `cross_link_*` 工具不再注册
- 旧日志或 pycache 中的历史内容

关键测试已经确认：

```text
tests/test_agent_tools.py
=> assert "cross_link_table_refs" not in names
=> assert "cross_link_endpoint_callers" not in names
```

本轮定向验证：

```text
python -m pytest -q tests\test_agent_tools.py tests\test_agent_vector_recall.py tests\test_serve_mcp_diagnose.py
=> 25 passed
```

## 5. 本地代码逐目录扫描结论

### 5.1 agent

范围：Agent 服务、LLM provider、执行 loop、工具注册、Memory、Session、Recall、Embedding、RBAC store。

主要链路：

- `agent/routes/chat.py`：HTTP `/chat` 入口，解析 identity/project_id，执行 ACL 和 audit。
- `agent/services/chat_service.py`：构造 session、recall memories、tool registry、RunContext、AgentLoop。
- `agent/loop.py`：工具调用循环，带重复调用、无效调用、检索无增量、步数上限护栏。
- `agent/tools/__init__.py`：默认工具注册。
- `agent/tools/search_docs.py`：走 MCP SSE 调 `platform-docs.search_docs`。
- `agent/tools/codegraph.py`：直接只读 SQLite codegraph DB。
- `agent/tools/impact.py`：直接只读 graph store SQLite，替代旧 cross_link。
- `agent/tools/remember.py`：写 memory，经 RunContext、scope_decision、audit。
- `agent/memory_mcp.py`：MCP memory 服务，org/user 来自认证身份，不信任客户端伪造。
- `agent/recall/`：新 recall pipeline，支持 keyword/vector/bm25/fusion/reranker 可插拔。

当前判断：

- Agent 架构主线清晰，工具分层明确。
- `search_docs` 走 MCP，`codegraph/impact` 直接读 SQLite，这是刻意的性能设计，但排障时要记住两条路径不同。
- Memory 写入权限已统一到 `scope_decision`。
- 当前失败集中在 embed/BM25 runtime 依赖未安装，不是 Agent 主流程逻辑失败。

### 5.2 chroma

范围：文档索引、Chroma daemon、search_docs、BM25、reranker、embedding、project state。

主要链路：

- `chroma/server.py`：MCP server 和 HTTP daemon。
- `chroma/_tools.py`：`search_docs`、`list_collections`、`get_by_file`。
- `chroma/indexer.py`：索引构建。
- `chroma/bm25.py`：BM25 + tokenization + RRF。
- `chroma/_project_state.py`：多项目 collection 状态。

当前判断：

- Chroma 仍是 docs RAG 主服务。
- BM25 依赖 `jieba/rank_bm25`，属于 runtime extra。
- search_docs 已具备 stale collection retry 和 project state reset。
- embed/rerank HTTP 能力复用 daemon 已加载模型，避免重复加载。

风险：

- 如果用户期望轻量 dev 环境全量测试通过，BM25 相关测试应该 importorskip 或改为可选依赖测试。
- 如果用户期望 runtime 全功能测试通过，需要安装 `.[runtime]`。

### 5.3 codegraph

范围：codegraph MCP proxy、Agent codegraph SQLite 工具、平台集中 codegraph 索引目录。

主要链路：

- `codegraph/server.py`：多项目 SSE proxy，后端用外部 `codegraph serve --mcp`。
- `agent/tools/codegraph.py`：Agent 内部直接读 `data_root/codegraph_ext/<pid>/codegraph/codegraph.db`。
- `ops/codegraph.py`：`.codegraph` junction/symlink 和平台集中目录管理。

当前判断：

- codegraph MCP 和 Agent 工具不是同一访问路径。
- Agent 优先读取平台集中 DB，再回退业务仓 `.codegraph`。
- 外部 MCP 更适合 IDE 工具，Agent 直读更适合低延迟。

### 5.4 core

范围：project_id、paths、ACL、audit、config、RBAC、identity、rate limit、service identity。

主要链路：

- `core/project_id.py`：project_id 校验与解析。
- `core/paths.py`：data_root、chroma collection、codegraph index、graph store 路径。
- `core/acl.py`：project/memory scope 基础授权。
- `core/audit.py`：访问审计。
- `core/rbac.py`：纯 RBAC 角色与可见 scope 计算。
- `core/config.py`：配置加载。

当前判断：

- `project_id` 是平台隔离主键。
- token 模式下缺 project_id 会被拒绝，符合多项目隔离要求。
- audit 失败不阻塞主流程，这是高可用取向，但审计日志写失败需要靠 health/metrics 观察。

### 5.5 gateway

范围：认证器、中间件、token/passthrough、部署策略。

主要链路：

- `gateway/auth.py`：PassthroughAuthenticator、TokenAuthenticator、deploy_policy_error、multi_user_policy_error。
- `gateway/middleware.py`：ASGI AuthMiddleware，兼容 SSE。

当前判断：

- dev passthrough 和 prod token 的边界清晰。
- prod 或非 loopback URL 下 passthrough 会 fail-fast。
- 多用户场景拒绝 passthrough，避免混用模式。

### 5.6 graph

范围：统一图谱 schema/store/ingest/impact/MCP/call resolver/analyzer。

主要链路：

- `graph/schema.py`：NodeKind、EdgeKind、GraphNode、GraphEdge、Evidence、Finding。
- `graph/store.py`：per-project SQLite store，按 project_id 写入/读取。
- `graph/ingest.py`：插件结果入库、API link、call resolver、frontend deps、analyzers。
- `graph/impact.py`：impact/table usage/page deps/API callers/search/domain。
- `graph/mcp_server.py`：Graph MCP 服务。
- `graph/call_resolvers/`：codegraph/fastapi resolver。
- `graph/analyzers/`：业务域 soft analyzer。

当前判断：

- `graph` 是当前跨层影响分析真主线。
- store 是 per-project SQLite，并且读取按 project_id 过滤。
- soft node/edge 经过 validator，默认不污染确定性 impact 查询。
- `cross_link` 的功能已被 graph store 和 impact tools 承接。

### 5.7 ops

范围：health、backup、metrics、bootstrap、gateway、logs、hooks、reindex commands。

主要链路：

- `ops/health/_checks.py`：Chroma、Graph、Codegraph、MCP、hooks、freshness 检查。
- `ops/metrics.py`：audit、recall、codegraph usage 聚合。
- `ops/backup.py`：PG memory、data 子目录备份。
- `ops/reindex/commands.py`：手动 reindex 命令。
- `ops/codegraph.py`：codegraph 集中目录和链接。

当前判断：

- 运维闭环较完整，能覆盖服务存活、索引新鲜度、审计/usage 聚合。
- subprocess 调用集中在运维边界，精确扫描未发现 `shell=True`。

### 5.8 plugins

范围：Graph ingest 插件系统和内置语言/框架扫描器。

主要链路：

- `plugins/base.py`：AnalyzerPlugin 协议。
- `plugins/registry.py`：内置插件发现和运行。
- `plugins/executor.py`：插件执行错误归一化。
- `plugins/builtin/_stack_scan/`：FastAPI/React/Vue/Node/Spring/frontend deps 等扫描。
- `plugins/builtin/sql/`：DDL、ORM、XML mapper、SQLAlchemy Core 等数据库使用扫描。

当前判断：

- 插件系统是 Graph ingest 的数据来源。
- 插件执行 fail-soft，不会让单个插件失败拖垮 ingest。
- ownership 测试用于防止重复插件重新生产同一类节点。

### 5.9 reindex

范围：spool queue、runner、worker、git sync。

主要链路：

- `reindex/queue.py`：按 `(project_id, kind)` 合并任务。
- `reindex/runners.py`：chroma/codegraph/ingest runner。
- `reindex/worker.py`：drain/run_forever，先 sync repo，再执行 runner。
- `reindex/git_sync.py`：远端同步。

当前判断：

- reindex 是索引新鲜度主链路。
- runner 有 timeout，避免 worker 永久卡死。
- rc=2 保留重试，其他失败丢弃以避免无限循环。

### 5.10 web

范围：Web 后端 API、账户/RBAC、项目/任务/审计/图谱/报告/Agent proxy。

主要链路：

- `web/app.py`、`web/main.py`：FastAPI app。
- `web/routes/`：auth/users/orgs/projects/jobs/indexes/graph/reports/audit/memory/agent。
- `web/services/`：业务编排层。
- `web/repositories/`：memory/PG account store、job/project read/write repo。
- `web/security/`：session auth、password、RSA、membership。
- `web/db/`：SQLAlchemy tables 和 Alembic。

当前确认已修复的问题：

- `user_service.create_user` 不传 role 时默认 `member`，保证写 org membership。
- `user_service.set_roles` 已加 `_guard_same_org`，并校验请求 org_id 与用户真实 org_id 一致。
- `account_store.bind_account_stores` 在 prod + PG 配置失败时 fail-fast，不静默回退内存。

验证：

```text
python -m pytest -q tests\test_account_store_pg_failfast.py tests\test_web_users.py
=> 20 passed, 1 warning
```

### 5.11 webhook

范围：Git/webhook 入口、body limit、reindex dispatch。

当前判断：

- webhook 是外部触发 reindex 的入口之一。
- 相关安全测试已覆盖 body limit。

## 6. 静态风险扫描

本轮扫描项：

- `TODO/FIXME/HACK/XXX/P0/P1/审计/风险`
- `except Exception` / `except ImportError`
- `pass`
- subprocess / Popen / os.system / shell=True
- pickle / eval / exec / yaml.load
- secret/token/password/private key 形态
- cross_link/cross-layer 残留

结论：

1. 未发现活跃 `shell=True`、`os.system`、危险 `eval/exec` 使用。
2. 命中的 `eval` 是 `model.eval()` 或函数名，不是动态代码执行。
3. 未发现真实生产密钥；命中项是测试假 token 和文档示例。
4. `except Exception` 较多，但主要集中在工具边界、daemon 边界、插件 fail-soft、审计/obslog 不阻塞主流程、模型加载降级等预期场景。
5. `TODO(PG)` 仍存在于 job/project repo，表示 Web 控制面的部分读写仓储仍有内存态或后续 PG 化计划。

## 7. 测试结果

### 7.1 全量测试

```text
python -m pytest -q
```

结果：

```text
8 failed, 1135 passed, 5 skipped, 1 warning
```

失败项：

```text
tests/test_agent_embed_registry.py::test_default_backend_qwen_local
tests/test_agent_embed_registry.py::test_device_precedence_new_over_alias_over_default
tests/test_agent_embed_registry.py::test_embed_path_from_config
tests/test_agent_recall_pipeline.py::test_bm25_ranks_term_match_first
tests/test_agent_recall_pipeline.py::test_bm25_all_zero_score_keeps_pool_order
tests/test_agent_recall_pipeline.py::test_bm25_single_doc_corpus_no_crash
tests/test_agent_recall_pipeline.py::test_registry_bm25_built_when_available
tests/test_agent_recall_pipeline.py::test_registry_vector_plus_bm25
```

失败原因：

- 当前环境没装 `sentence-transformers`，`build_embedder({})` 返回 `None`。
- 当前环境没装 `rank_bm25` 和 `jieba`，BM25 直接实例化测试失败。
- 当前 `pyproject.toml` 把这些依赖放在 `runtime` extra。

### 7.2 主干回归

```text
python -m pytest -q -k "not agent_embed_registry and not agent_recall_pipeline"
```

结果：

```text
1102 passed, 5 skipped, 41 deselected, 1 warning
```

### 7.3 已修复问题定向验证

```text
python -m pytest -q tests\test_agent_tools.py tests\test_agent_vector_recall.py tests\test_serve_mcp_diagnose.py
=> 25 passed

python -m pytest -q tests\test_account_store_pg_failfast.py tests\test_web_users.py
=> 20 passed, 1 warning
```

## 8. Ruff 结果

命令：

```text
python -m ruff check codev_platform
```

结果：5 个问题。

```text
F401 codev_platform/agent/recall/base.py
  compute_visible_scopes imported but unused

B905 codev_platform/agent/recall/reranker.py
  zip() without strict=

B905 codev_platform/agent/recall/scorers.py
  zip() without strict=

B007 codev_platform/graph/analyzers/brain_labeler.py
  loop variable short unused

F401 codev_platform/graph/mcp_server.py
  pathlib.Path imported but unused
```

这些是小型静态质量问题，不是架构阻塞。

## 9. 当前真实问题清单

### P1. 测试环境和 runtime extra 期望不一致

当前全量 pytest 默认会跑 runtime 依赖测试，但当前环境没装：

- `sentence-transformers`
- `rank-bm25`
- `jieba`
- `torch`

可选处理：

1. 如果 CI/本地全量测试要求验证 runtime 能力，则安装：

```text
pip install -e .[runtime,agent,dev]
```

2. 如果轻量 dev 环境也要求全量 pytest 通过，则这些测试应改为 `pytest.importorskip` 或按 marker 分类。

### P1. Ruff 质量问题未清理

5 个 lint 问题都很小，建议直接修。

### P2. Web job/project repository 仍有 PG TODO

静态扫描发现：

- `web/repositories/job_read_repo.py`
- `web/repositories/job_write_repo.py`
- `web/repositories/project_write_repo.py`

仍有 `TODO(PG)`。

这说明 Web 控制面部分状态仍不是完整 PG 化。是否要提升优先级，取决于这些 job/project 状态是否需要跨进程、跨重启一致。

### P2. 直读 SQLite 工具和 MCP 工具存在双路径

Agent 内部：

- `search_docs` 走 MCP SSE。
- `codegraph` 直接读 SQLite。
- `impact` 直接读 SQLite。

这是性能上合理的设计，但建议保留一致性测试，确保 MCP 查询和 Agent 工具查询对同一 project_id 的行为一致。

## 10. 建议下一步

建议按顺序处理：

1. 决定全量测试口径：runtime 全装，还是轻量 dev 下跳过 runtime 测试。
2. 修掉 5 个 ruff 问题。
3. 给 `agent_embed_registry` 和 `agent_recall_pipeline` 加 marker 或 importorskip，避免轻量环境误报。
4. 补一组 Graph MCP 与 Agent impact 直读 SQLite 的一致性测试。
5. 梳理 Web job/project repo 的 PG TODO，决定是否纳入生产一致性路线。

## 11. 总结

这轮全量扫描后的真实状态是：

- 架构主线清晰：`project_id` 隔离 + MCP 四服务 + Agent loop + Memory/RBAC + Docs/Codegraph/Graph 三类索引 + Reindex/Health/Audit 运维闭环。
- `cross_link` 已退役，当前跨层影响分析由 `graph` 统一图谱承接。
- 之前指出的用户角色、默认 membership、prod PG fail-fast 等问题已在代码和测试中体现修复。
- 当前阻塞全量绿的是环境依赖和少量 lint，不是主干业务逻辑回归。

## 修复状态(2026-06-08)

| 问题 | 状态 | commit |
|---|---|---|
| P1 runtime 依赖测试误 fail | ✅ 修(embed 3 + bm25 5 个加 skipif; 全套 1154 passed, 13 skipped, **0 failed**) | `3c701d0` |
| P1 ruff(报告 5 个, 实为 6) | ✅ 修(F401×2 + F841 + B905×2 + B007; ruff All passed) | `10d2b61` |
| P2 Web job/project repo PG TODO | ⏳ 待(PG 化决策: 取决于 job/project 状态是否需跨进程/跨重启一致) | — |
| P2 直读 SQLite vs MCP 双路径 | ⏳ 待(建议补 Graph MCP 与 Agent impact 直读一致性测试) | — |

bug-edge-audit 的 P1 跨项目隔离 3 漏洞 + P2 reindex/codegraph/IndexService 已修, 见该报告修复状态。
两份报告合计 9 个核心 actionable 已修 8, 余 1(session vs require_project_access)为架构层 + 只 prod 触发, 专门一轮。
