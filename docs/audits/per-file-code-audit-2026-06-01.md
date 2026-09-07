# codev-platform 逐文件代码审计报告

日期: 2026-06-01

范围: `codev_platform/`、`tests/`、`scripts/`、`demo/` 下的 Python、PowerShell、TypeScript、JavaScript、JSON、TOML、YAML 文件。运行产物、缓存、虚拟环境、构建目录、索引目录不纳入。

## 总览

- 文件数: 169
- Python 文件: 156
- 脚本/配置文件: 13
- 总行数: 23437
- 需要继续人工复核或后续治理的文件: 21
- 最新全量测试: `427 passed, 5 skipped, 1 warning`
- 最新健康检查: `READY / all checks green`

## 审计口径

每个文件检查: UTF-8 读取、Python AST 解析、模块 docstring、顶层类/函数、导入、危险调用模式、进程/文件删除/网络/密钥字样、大文件复杂度。

判定说明:

- `未见直接阻断项`: 静态扫描和当前测试下没有发现明显发布阻断问题。
- `可接受，发布前复核命令边界`: 有子进程、强杀、递归删除、外部命令等运维逻辑，需要发布前固定命令白名单和参数边界。
- `可运行，但建议拆分/收窄边界`: 文件过大，当前能跑，但后续维护风险较高。
- `需人工复核`: 命中高危模式或解析异常。

## codev_platform

| 文件 | 行数 | 用途 | 关键符号 | 风险命中 | 审计结论 |
|---|---:|---|---|---|---|
| `codev_platform/__init__.py` | 13 | codev-platform: 多项目 AI 工具栈基础设施 (本地原型, server 部署待启)。 | - | - | 未见直接阻断项 |
| `codev_platform/cli.py` | 787 | codev-platform CLI - 多项目 AI 工具栈管理入口。 | _print, _eprint, _mask_value, redact_config, cmd_init, cmd_current, cmd_list_projects, cmd_register | subprocess、通配异常、强杀进程、密钥字样、大文件 | 可运行，但建议拆分/收窄边界 |
| `codev_platform/mcp_serve.py` | 702 | 平台 MCP 端点编排 (P3 + P5) —— 把三套 MCP 以**服务地址**常驻起来。 | MCPEndpoint, _resolve_venv_scripts, _venv_python, _mcp_proxy_exe, build_codegraph_cmd, build_cross_link_cmd, mcp_source_endpoint, mcp_sou... | subprocess、Popen、通配异常、外部网络、密钥字样、大文件 | 可运行，但建议拆分/收窄边界 |
| `codev_platform/platform_status.py` | 256 | 平台数据聚合 —— 服务端逻辑,供 HTTP 控制面端点(daemon `/platform/status`)调用。 | _repo_root, _data_dir, _sqlite_counts, _parse_dt, _query_codegraph_api, _query_crosslink_api, _local_crosslink, _self_project_id | 通配异常、外部网络 | 未见直接阻断项 |

## codev_platform/agent

| 文件 | 行数 | 用途 | 关键符号 | 风险命中 | 审计结论 |
|---|---:|---|---|---|---|
| `codev_platform/agent/__init__.py` | 13 | codev-platform agent — 只读代码理解 HTTP 服务(P0). | - | - | 未见直接阻断项 |
| `codev_platform/agent/brain/__init__.py` | 23 | LLM provider 抽象层 + 各厂商适配器. 公开 API:中性类型 + LLMProvider 抽象。 | - | - | 未见直接阻断项 |
| `codev_platform/agent/brain/anthropic.py` | 94 | Claude 适配器(Anthropic Messages API). | AnthropicProvider | 密钥字样 | 未见直接阻断项 |
| `codev_platform/agent/brain/base.py` | 35 | LLMProvider 抽象. 中性类型见 types.py. 每个 provider 适配器只干一件事:中性 Message/ToolCall <-> 自家原生格式 双向翻译。 | LLMProvider | - | 未见直接阻断项 |
| `codev_platform/agent/brain/openai_compat.py` | 99 | OpenAI 兼容适配器 — 覆盖 GPT / DeepSeek / 通义千问. | OpenAICompatProvider | 密钥字样 | 未见直接阻断项 |
| `codev_platform/agent/brain/registry.py` | 132 | provider 注册表 — 声明式、可扩展、模块化. 设计目标:加一个新模型 = 加一条声明(或纯改 config),不改 get_provider 逻辑。 | ProviderSpec, register_provider, get_spec, registered_names, _build_anthropic, _build_openai_compat, _spec_for, get_provider | 密钥字样 | 未见直接阻断项 |
| `codev_platform/agent/brain/types.py` | 64 | 中性类型 — agent loop 与 provider 之间的通用货币. | ToolCall, ToolResult, Message, AssistantTurn, StreamEvent | 密钥字样 | 未见直接阻断项 |
| `codev_platform/agent/config.py` | 43 | agent 配置读取(复用 core. | agent_cfg, provider_name, model_name, max_steps, resolve_key, base_url, configured_provider_names | 密钥字样 | 未见直接阻断项 |
| `codev_platform/agent/deps.py` | 186 | 依赖注入 — 进程级单例 + provider 解析. 集中在此便于:① 测试时替换(注入假 provider / 空 registry)② 路由层只依赖这些 getter,不自己 new 单例。 | _build_session_store, get_sessions, get_registry, get_provider, get_memory_store, get_rbac_store, get_recall_service, get_memory_maintenance | 通配异常 | 未见直接阻断项 |
| `codev_platform/agent/loop.py` | 112 | AgentLoop — 循环引擎(plan -> tool -> observe -> act -> stop). | Step, AgentResult, _summarize, AgentLoop | 密钥字样 | 未见直接阻断项 |
| `codev_platform/agent/memory_maintenance.py` | 81 | 记忆生命周期维护(memory M4):TTL 自动归档 + 压缩摘要融合。 | MemoryMaintenance, make_llm_fuse | - | 未见直接阻断项 |
| `codev_platform/agent/memory_recall.py` | 54 | 记忆冲突消解(memory M3 的确定性核心,纯逻辑)。 | _rank, resolve_conflicts | - | 未见直接阻断项 |
| `codev_platform/agent/memory_store.py` | 68 | MemoryStore —— 记忆条目的结构化存储(memory M2 核心)。 | MemoryEntry, MemoryStore | - | 未见直接阻断项 |
| `codev_platform/agent/memory_store_pg.py` | 190 | SqlMemoryStore —— MemoryStore 的 PostgreSQL 实现(memory M2 核心)。 | _row_to_entry, SqlMemoryStore | - | 未见直接阻断项 |
| `codev_platform/agent/prompts.py` | 84 | agent prompt 文案. 独立成文件:prompt 调优频繁,且后续可能 per-provider 微调, 与 loop 引擎逻辑分开便于迭代。 | _format_memories, build_code_understanding_system | - | 未见直接阻断项 |
| `codev_platform/agent/rbac_store_pg.py` | 219 | RbacStore —— RBAC 身份/成员/作用域表的 PostgreSQL 实现(memory M5 ACL 底座)。 | RbacStore, _highest_role | - | 未见直接阻断项 |
| `codev_platform/agent/recall_service.py` | 101 | 记忆召回服务(memory M3)。 | visible_scopes, _score, _rank_for_query, RecallService, LocalRecallService | - | 未见直接阻断项 |
| `codev_platform/agent/routes/__init__.py` | 2 | HTTP 路由. | - | - | 未见直接阻断项 |
| `codev_platform/agent/routes/chat.py` | 88 | 问答路由:POST /chat(A 只读能力主入口). | _to_response, _resolve_project_id, _resolve_identity, chat | 密钥字样 | 未见直接阻断项 |
| `codev_platform/agent/routes/memory.py` | 126 | memory 路由(M2):POST /memory 写记忆 / GET /memory 列作用域记忆. | _scope_decision, _resolve_identity, _to_out, write_memory, list_memory | 通配异常、密钥字样 | 未见直接阻断项 |
| `codev_platform/agent/routes/meta.py` | 23 | 元信息路由:/health /providers. | health, providers | - | 未见直接阻断项 |
| `codev_platform/agent/schemas.py` | 66 | HTTP 请求/响应模型(pydantic). | ChatRequest, StepOut, ChatResponse, ProviderOut, HealthOut, MemoryWriteRequest, MemoryEntryOut | - | 未见直接阻断项 |
| `codev_platform/agent/service.py` | 38 | FastAPI 应用工厂. | create_app | 密钥字样 | 未见直接阻断项 |
| `codev_platform/agent/services/__init__.py` | 8 | application 层 — 业务编排. | - | - | 未见直接阻断项 |
| `codev_platform/agent/services/chat_service.py` | 86 | ChatService — 问答编排(application 层). 职责:解析会话 -> 取 provider -> 跑 loop -> 持久化 -> 返回 domain 结果。 | ChatOutcome, ChatService | 通配异常 | 未见直接阻断项 |
| `codev_platform/agent/session.py` | 64 | 会话存储 — 抽象接口 + 内存实现. | SessionStore, InMemorySessionStore | - | 未见直接阻断项 |
| `codev_platform/agent/session_pg.py` | 152 | SqlSessionStore —— SessionStore 的 PostgreSQL 实现(memory M2:会话跨重启持久化)。 | _msg_to_payload, _row_to_msg, SqlSessionStore | - | 未见直接阻断项 |
| `codev_platform/agent/tools/__init__.py` | 19 | Tool 抽象 + 平台能力封装. | build_default_registry | - | 未见直接阻断项 |
| `codev_platform/agent/tools/_project.py` | 49 | 工具层的 project 上下文解析(P2 多租户)。 | resolve_project_id, _platform_meta_dir, repo_path_of | - | 未见直接阻断项 |
| `codev_platform/agent/tools/base.py` | 48 | Tool 抽象 + 注册表. | Tool, ToolRegistry | - | 未见直接阻断项 |
| `codev_platform/agent/tools/codegraph.py` | 196 | codegraph 工具(直读 . | _fts_query, _find_db, _connect, _node_brief, CodegraphSearchTool, _relations, CodegraphCallersTool, CodegraphCalleesTool | 通配异常、密钥字样 | 未见直接阻断项 |
| `codev_platform/agent/tools/cross_link.py` | 96 | cross-link 工具(in-process via CrossLayerDB). | _open_db, CrossLinkTableRefsTool, CrossLinkEndpointTool, register_into | 通配异常 | 未见直接阻断项 |
| `codev_platform/agent/tools/search_docs.py` | 91 | search_docs 工具(SSE MCP client → 已在跑的 chroma daemon). | _daemon_url, _resolve_project_id, _call_search, SearchDocsTool, register_into | 通配异常 | 未见直接阻断项 |
| `codev_platform/agent/trace.py` | 50 | 每步 trace 落 jsonl(可观测 + 未来 eval 输入). 落在 data_root/agent_trace/<date>.jsonl。 | _trace_dir, Trace | 通配异常 | 未见直接阻断项 |

## codev_platform/chroma

| 文件 | 行数 | 用途 | 关键符号 | 风险命中 | 审计结论 |
|---|---:|---|---|---|---|
| `codev_platform/chroma/__init__.py` | 35 | codev_platform.chroma — chroma 多租户 MCP daemon + BM25 hybrid recall。 | ensure_wal | 通配异常 | 未见直接阻断项 |
| `codev_platform/chroma/audit_patterns.py` | 79 | Audit: 检查 docs/ 顶级所有子目录都被 DOC_PATTERNS 白名单覆盖 防止 silent failure:加了新顶级 docs 子目录(如 docs/plans/), 但忘了同步加 | find_uncovered_docs_subdirs, main | - | 未见直接阻断项 |
| `codev_platform/chroma/bm25.py` | 163 | BM25 索引 — chroma 向量 RAG 的精确匹配伴侣。 | tokenize, BM25Index, _match_where, rrf_fuse | 密钥字样 | 未见直接阻断项 |
| `codev_platform/chroma/chunking.py` | 94 | 三级切分 chunk 策略 (markdown -> chunks). 抽自 platform/tools/chroma/index_docs.py 通用部分。 | split_by_heading, split_by_paragraph, hard_split, chunk_text, file_sha256 | - | 未见直接阻断项 |
| `codev_platform/chroma/indexer.py` | 705 | Chroma 平台文档全量索引器 将 CLAUDE. | is_excluded, _load_project_index_config, discover_files, infer_category, infer_module, _rel_path, iter_chunks, _empty_manifest | 通配异常、大文件 | 可运行，但建议拆分/收窄边界 |
| `codev_platform/chroma/launcher.py` | 309 | platform-docs MCP daemon launcher (uses mcp-proxy for stdio<->SSE bridge). | _resolve_venv, _flog, _check_daemon, _fetch_daemon_health, _spawn_daemon, _acquire_spawn_lock, _release_spawn_lock, _wait_daemon_ready | subprocess、Popen、通配异常、外部网络、密钥字样 | 可接受，发布前复核命令边界 |
| `codev_platform/chroma/server.py` | 1287 | Claude Code MCP server — 暴露 Chroma 文档检索给 Claude Code 提供 3 个 tools： - search_docs 语义搜索 platform markd | _torch_dtype, _maybe_rotate_log, _flog, _log_recall, _ProjectState, _record_stat, _record_stat_error, _process_info | 通配异常、密钥字样、超大文件 | 可运行，但建议拆分/收窄边界 |
| `codev_platform/chroma/verify.py` | 77 | 一次性验证脚本：直接打 Chroma 测语义检索质量 跑法： tools/chroma/. | main | - | 未见直接阻断项 |

## codev_platform/codegraph

| 文件 | 行数 | 用途 | 关键符号 | 风险命中 | 审计结论 |
|---|---:|---|---|---|---|
| `codev_platform/codegraph/__init__.py` | 3 | codegraph 多租户代理: 把外部 `codegraph serve --mcp` (per-repo stdio) 包成平台统一的 多租户 SSE MCP server (?project_i | - | - | 未见直接阻断项 |
| `codev_platform/codegraph/server.py` | 391 | codegraph 多租户代理 MCP server —— 把外部 `codegraph serve --mcp` (per-repo stdio, 无 HTTP) 包成平台统一的多租户 SSE 端点 | _resolve_codegraph_cmd, _flog, _log_usage, _resolve_default_project, _repo_for, _Backend, _active_pid, _backend_for | 通配异常、密钥字样 | 未见直接阻断项 |

## codev_platform/core

| 文件 | 行数 | 用途 | 关键符号 | 风险命中 | 审计结论 |
|---|---:|---|---|---|---|
| `codev_platform/core/__init__.py` | 10 | Internal shared infrastructure for multi-project AI tooling. | - | - | 未见直接阻断项 |
| `codev_platform/core/_smoke_test.py` | 37 | codev_platform multi-project namespace 集成 smoke test。 | main | - | 未见直接阻断项 |
| `codev_platform/core/acl.py` | 67 | 项目级访问控制 — 身份能否访问某 project_id 的唯一真值源。 | AccessDecision, can_access, memory_scope_access | 密钥字样 | 未见直接阻断项 |
| `codev_platform/core/audit.py` | 53 | 结构化访问审计日志 (jsonl) —— 项目访问授权判定留痕。 | audit_log_path, audit_access | 通配异常、密钥字样 | 未见直接阻断项 |
| `codev_platform/core/config.py` | 165 | User-level config loader for machine-specific paths. | config_path, load_config, get, env_or_config, save_config | 密钥字样 | 未见直接阻断项 |
| `codev_platform/core/identity.py` | 70 | user 身份解析 — 与 project_id 对称(memory 权限模型 M1 地基). agent 请求上下文 = (user_id, project_id)。 | validate, resolve_local, resolve_from_request, resolve_org_from_request | - | 未见直接阻断项 |
| `codev_platform/core/obslog.py` | 100 | Observability-log redaction helpers — dev vs prod 两套日志规则。 | logging_mode, _sha8, redact_text, redact_args | 密钥字样 | 未见直接阻断项 |
| `codev_platform/core/paths.py` | 114 | path conventions for multi-project AI tooling. | _business_repo_root, business_repo_root, data_root, chroma_dir, chroma_collection_name, codegraph_db_path, codegraph_index_dir, cross_lin... | 通配异常 | 未见直接阻断项 |
| `codev_platform/core/project_id.py` | 115 | project_id resolver for multi-project AI tooling. | ProjectIdError, validate, _read_repo_config, resolve_local, resolve_from_request | 通配异常 | 未见直接阻断项 |
| `codev_platform/core/ratelimit.py` | 64 | 纯滑动窗口限流器 —— 无 IO, now 由调用方传入(可测 / 确定性)。 | SlidingWindowLimiter, default_key_from_scope | - | 未见直接阻断项 |
| `codev_platform/core/rbac.py` | 115 | 作用域 RBAC 纯权限逻辑 — 角色 × 作用域 × 动作判定的唯一真值源。 | Membership, role_allows, compute_visible_scopes, memory_scope_decision | - | 未见直接阻断项 |
| `codev_platform/core/spawn_lock.py` | 86 | 跨进程 spawn lock — 防 N 个 user / 脚本同时跑长时间操作互踩。 | LockHeld, acquire_lock | 通配异常 | 未见直接阻断项 |

## codev_platform/cross_link

| 文件 | 行数 | 用途 | 关键符号 | 风险命中 | 审计结论 |
|---|---:|---|---|---|---|
| `codev_platform/cross_link/__init__.py` | 11 | codev_platform. | - | - | 未见直接阻断项 |
| `codev_platform/cross_link/linker.py` | 119 | 按 URL path + HTTP method 精确匹配 frontend_api ↔ java_endpoint，建 calls_api 边。 | link_api | 通配异常 | 未见直接阻断项 |
| `codev_platform/cross_link/query.py` | 215 | Cross-layer KG 便捷查询 API。 | CrossLayerDB | - | 未见直接阻断项 |
| `codev_platform/cross_link/schema.py` | 195 | Cross-layer KG sqlite schema + 初始化工具。 | open_db, upsert_node, upsert_edge, set_meta, get_meta | - | 未见直接阻断项 |
| `codev_platform/cross_link/server.py` | 624 | Claude Code MCP server — 暴露 cross-layer KG 给 AI。 | _resolve_default_project, _db_path_for, _flog, _log_usage, _active_pid, _error_for, _ensure_conn_for, _current_conn | 通配异常、密钥字样、大文件 | 可运行，但建议拆分/收窄边界 |

## codev_platform/cross_link_ts

| 文件 | 行数 | 用途 | 关键符号 | 风险命中 | 审计结论 |
|---|---:|---|---|---|---|
| `codev_platform/cross_link_ts/package.json` | 20 | JSON 配置文件。 | - | - | 未见直接阻断项 |
| `codev_platform/cross_link_ts/pnpm-lock.yaml` | 523 | YAML 配置文件。 | - | 大文件 | 可运行，但建议拆分/收窄边界 |
| `codev_platform/cross_link_ts/src/scan_frontend_calls.ts` | 257 | TypeScript 扫描/分析代码。 | - | - | 未见直接阻断项 |
| `codev_platform/cross_link_ts/tsconfig.json` | 18 | JSON 配置文件。 | - | - | 未见直接阻断项 |

## codev_platform/gateway

| 文件 | 行数 | 用途 | 关键符号 | 风险命中 | 审计结论 |
|---|---:|---|---|---|---|
| `codev_platform/gateway/__init__.py` | 38 | 平台 HTTP 网关层 —— 认证 + 统一请求拦截。 | - | 密钥字样 | 未见直接阻断项 |
| `codev_platform/gateway/auth.py` | 226 | 认证策略(可插拔)—— credential → Identity。 | token_hash, Unauthorized, token_expired, Identity, Authenticator, _header, _bearer, PassthroughAuthenticator | 通配异常、外部网络、密钥字样 | 未见直接阻断项 |
| `codev_platform/gateway/middleware.py` | 138 | 统一请求拦截中间件 —— **纯 ASGI**(不是 BaseHTTPMiddleware)。 | _normalize_public_paths, _path_is_public, AuthMiddleware, maybe_rate_limit_middleware, RateLimitMiddleware | 通配异常 | 未见直接阻断项 |

## codev_platform/ops

| 文件 | 行数 | 用途 | 关键符号 | 风险命中 | 审计结论 |
|---|---:|---|---|---|---|
| `codev_platform/ops/__init__.py` | 24 | codev_platform. | register_all | 通配异常 | 未见直接阻断项 |
| `codev_platform/ops/_common.py` | 175 | Shared helpers for codev_platform. | out, err, config, cfg_get, codev_root, resolve_repo, project_id_of, meta_health | subprocess、通配异常 | 可接受，发布前复核命令边界 |
| `codev_platform/ops/agent.py` | 66 | `codev-platform agent serve` -- 起 agent HTTP 服务(FastAPI via uvicorn). | cmd_agent, register | 通配异常、密钥字样 | 未见直接阻断项 |
| `codev_platform/ops/backup.py` | 208 | codev_platform. | _out, _err, _redact_dsn, BackupItem, plan_backup, prune_old, _archive_tree, run_backup | subprocess、通配异常、递归删除、外部网络、密钥字样 | 可接受，发布前复核命令边界 |
| `codev_platform/ops/bootstrap.py` | 172 | `codev-platform bootstrap [--dry-run]` —— 换机器 / 重 clone 后按 config 一键拉起平台。 | _out, _err, BootstrapStep, plan_bootstrap, _check_venv, run_bootstrap, load_config, cmd_bootstrap | subprocess、通配异常 | 可接受，发布前复核命令边界 |
| `codev_platform/ops/codegraph.py` | 257 | codev-platform codegraph —— 把业务项目的 codegraph 索引集中到平台 data/(目录联接)。 | _is_link, _link_target, _make_link, _remove_link, _same_path, link_state, _iter_projects, _stop_endpoint | subprocess、通配异常 | 可接受，发布前复核命令边界 |
| `codev_platform/ops/gateway.py` | 293 | codev_platform.ops.gateway —— gateway 鉴权管理 CLI 薄壳。 | _out, _err, parse_duration, _expires_disp, cmd_gateway, register | 外部网络、密钥字样 | 未见直接阻断项 |
| `codev_platform/ops/health.py` | 1120 | codev_platform. | Report, _expand, _count_files, _latest_mtime, _run_py, _git, _parse_dt, _iter_jsonl | subprocess、通配异常、外部网络、密钥字样、超大文件 | 可运行，但建议拆分/收窄边界 |
| `codev_platform/ops/hooks.py` | 87 | install-hooks -- cross-platform git hook installer (replaces install-git-hooks. | _write_hook, cmd_install_hooks, register | - | 未见直接阻断项 |
| `codev_platform/ops/logs.py` | 117 | codev_platform.ops.logs -- 集中查看平台侧日志末 N 行。 | log_sources, serve_log_dir, tail_file, _print_block, run_logs, cmd_logs, register | 密钥字样 | 未见直接阻断项 |
| `codev_platform/ops/memory_db.py` | 172 | `codev-platform memory <init-db\|doctor>` —— memory PG 库的 schema 初始化 + 体检。 | _out, _err, _require_dsn, _try_import_store, _cmd_init_db, _cmd_doctor, load_config, cmd_memory | 通配异常 | 未见直接阻断项 |
| `codev_platform/ops/metrics.py` | 354 | codev_platform. | parse_since, parse_lines, _record_ts, within_since, MetricsSummary, _agg_audit, _agg_usage, _agg_recall | 通配异常、外部网络 | 未见直接阻断项 |
| `codev_platform/ops/org.py` | 200 | `codev-platform org <action>` —— 组织 / 用户 / 团队 / 项目管理 CLI 薄壳(D13)。 | _out, _err, _get_store, cmd_org, _cmd_list, register | 通配异常 | 未见直接阻断项 |
| `codev_platform/ops/reindex.py` | 596 | codev_platform. | _reindex_log, _append_log, _now, cmd_reindex, _git_out, classify_scopes, _dispatch_reindex, cmd_post_commit | subprocess、Popen、通配异常、大文件 | 可运行，但建议拆分/收窄边界 |
| `codev_platform/ops/reindex_queue.py` | 96 | codev_platform.ops.reindex_queue —— reindex 写队列的 CLI 薄壳。 | _out, _err, _resolve_kinds, cmd_reindex_queue, register | - | 未见直接阻断项 |
| `codev_platform/ops/webhook.py` | 49 | codev_platform.ops.webhook —— webhook 接收器 CLI 薄壳。 | _out, cmd_webhook, register | 通配异常、外部网络 | 未见直接阻断项 |

## codev_platform/reindex

| 文件 | 行数 | 用途 | 关键符号 | 风险命中 | 审计结论 |
|---|---:|---|---|---|---|
| `codev_platform/reindex/__init__.py` | 31 | reindex 任务队列 —— 写侧串行化 (读并发不变)。 | spool_dir, open_default_queue | - | 未见直接阻断项 |
| `codev_platform/reindex/git_sync.py` | 60 | reindex 前把 repo 工作树追到远端 —— webhook 模型下服务器 clone 常落后于 push。 | _git, sync_repo_to_remote | subprocess | 可接受，发布前复核命令边界 |
| `codev_platform/reindex/queue.py` | 125 | reindex 任务队列 —— 持久层 (协议 + file spool 实现)。 | Job, JobQueue, FileSpoolQueue | - | 未见直接阻断项 |
| `codev_platform/reindex/runners.py` | 88 | reindex 执行层 —— 每索引类型一个 runner (协议 + registry)。 | ReindexRunner, register, get_runner, kinds, _venv_python, CliReindexRunner, CodegraphReindexRunner | subprocess | 可接受，发布前复核命令边界 |
| `codev_platform/reindex/worker.py` | 137 | reindex worker —— 编排层。 | _log, _repo_for, ReindexWorker | subprocess、通配异常 | 可接受，发布前复核命令边界 |

## codev_platform/webhook

| 文件 | 行数 | 用途 | 关键符号 | 风险命中 | 审计结论 |
|---|---:|---|---|---|---|
| `codev_platform/webhook/__init__.py` | 15 | VCS webhook 接收 —— push 即 enqueue reindex (与本地 hook 同源, 都进写队列)。 | - | 密钥字样 | 未见直接阻断项 |
| `codev_platform/webhook/providers.py` | 111 | webhook 来源适配 —— 按 VCS 协议族分 provider (策略接口 + registry)。 | PushEvent, WebhookProvider, register, get_provider, names, _hdr, _union_changed, GiteaProvider | 密钥字样、不安全开关 | 未见直接阻断项 |
| `codev_platform/webhook/server.py` | 167 | webhook 接收器 —— 收 VCS push 事件 → 入 reindex 写队列 (push 即触发 reindex)。 | _log, _project_for_repo, _scopes_for, webhook_port, build_app, run_http | 通配异常、密钥字样、不安全开关 | 未见直接阻断项 |

## demo

| 文件 | 行数 | 用途 | 关键符号 | 风险命中 | 审计结论 |
|---|---:|---|---|---|---|
| `demo/.claude/project.json` | 5 | JSON 配置文件。 | - | - | 未见直接阻断项 |
| `demo/index_demo.py` | 80 | 把 demo/docs 索引成 project_id="demo" 的 Chroma collection。 | main | 通配异常 | 未见直接阻断项 |

## scripts

| 文件 | 行数 | 用途 | 关键符号 | 风险命中 | 审计结论 |
|---|---:|---|---|---|---|
| `scripts/ai-health.ps1` | 1115 | PowerShell 运维脚本。 | - | 通配异常、密钥字样、超大文件 | 可运行，但建议拆分/收窄边界 |
| `scripts/clean-local-artifacts.ps1` | 132 | PowerShell 运维脚本。 | - | 递归删除 | 可接受，发布前复核命令边界 |
| `scripts/dirty-index-check.ps1` | 129 | PowerShell 运维脚本。 | - | - | 未见直接阻断项 |
| `scripts/install-git-hooks.ps1` | 55 | PowerShell 运维脚本。 | - | - | 未见直接阻断项 |
| `scripts/migrate_memory_md.py` | 145 | 把 *.md 人肉记忆迁进 PG memory store(memory plan 难点 #7)。 | _parse_front_matter, _is_redline, _entries, main | - | 未见直接阻断项 |
| `scripts/post-commit.ps1` | 184 | PowerShell 运维脚本。 | - | - | 未见直接阻断项 |
| `scripts/run_memory_maintenance.py` | 75 | memory 维护 job(M4):TTL 归档 + 可选压缩融合。 | main, _run | 通配异常 | 未见直接阻断项 |
| `scripts/sync-memory.ps1` | 54 | PowerShell 运维脚本。 | - | - | 未见直接阻断项 |
| `scripts/update-local-ai.ps1` | 149 | PowerShell 运维脚本。 | - | - | 未见直接阻断项 |
| `scripts/verify_memory_pg.py` | 228 | memory PG 真机验证脚本(M2 闭环)。 | _ok, _fail, main | 通配异常 | 未见直接阻断项 |
| `scripts/verify_rbac_pg.py` | 159 | RBAC PG 真机验证脚本(M5 ACL 底座闭环)。 | _ok, _fail, main | 通配异常 | 未见直接阻断项 |
| `scripts/wait-for-reindex.ps1` | 141 | PowerShell 运维脚本。 | - | - | 未见直接阻断项 |

## tests

| 文件 | 行数 | 用途 | 关键符号 | 风险命中 | 审计结论 |
|---|---:|---|---|---|---|
| `tests/__init__.py` | 0 | 测试用例。 | - | - | 测试文件，通过 |
| `tests/test_acl.py` | 133 | 项目级 ACL 纯函数单测 (codev_platform.core.acl.can_access)。 | _ident, test_passthrough_mode_advisory_allow_any_project, test_passthrough_explicit_advisory, _token_cfg, test_token_mode_requires_authen... | 密钥字样 | 测试文件，通过 |
| `tests/test_acl_integration_routes.py` | 103 | ACL 入口级集成测试 — agent FastAPI 路由入口 (audit #3)。 | _token_auth, _app, token_cfg, test_chat_token_no_project_id_is_403, test_chat_token_project_not_in_allowlist_is_403, test_chat_token_whit... | 密钥字样 | 测试文件，通过 |
| `tests/test_acl_integration_sse.py` | 130 | ACL 入口级集成测试 — MCP SSE 入口 (audit #3)。 | _build_app, _token_auth, test_sse_passthrough_allows_any_project, test_sse_passthrough_no_project_id_falls_back_to_default, test_sse_toke... | 密钥字样 | 测试文件，通过 |
| `tests/test_agent_anthropic_translate.py` | 48 | Anthropic 适配器的中性->原生翻译测试. | test_assistant_tool_calls_become_tool_use_blocks, test_consecutive_tool_results_merge_into_one_user_message, test_plain_user_message_pass... | - | 测试文件，通过 |
| `tests/test_agent_chat_service.py` | 135 | ChatService(application 层)测试 — 注入假 provider/registry,不触网不依赖 HTTP. | _FakeProvider, _CapturingProvider, _FakeRecall, _RaisingRecall, _service, test_ask_returns_outcome_with_session, test_session_continuity,... | 密钥字样 | 测试文件，通过 |
| `tests/test_agent_loop.py` | 97 | AgentLoop 引擎测试 — 用 FakeProvider 脚本化模型行为,不依赖真 LLM / 网络。 | FakeProvider, EchoTool, _registry_with, test_loop_runs_tool_then_answers, test_loop_unknown_tool_is_reported_not_crash, test_loop_max_ste... | 密钥字样 | 测试文件，通过 |
| `tests/test_agent_loop_guard.py` | 42 | loop 硬护栏测试:重复调用拦截 + 倒数步收尾提示(弱模型防空转). | CountingTool, RepeatProvider, test_repeated_call_is_blocked_not_executed | - | 测试文件，通过 |
| `tests/test_agent_memory_maintenance.py` | 147 | MemoryMaintenance(M4 压缩 + TTL)编排测试 —— 用注入 fuse_fn,纯逻辑不需 PG/LLM。 | FakeStore, _e, test_compress_below_threshold_noop, test_compress_at_threshold_fuses_and_archives, test_compress_excludes_redline, test_co... | - | 测试文件，通过 |
| `tests/test_agent_memory_recall.py` | 92 | 冲突消解纯逻辑测试 —— 红线最高 + policy(personal_first / org_first)+ topic_key 去重。 | _e, test_no_topic_key_all_passthrough, test_redline_always_wins, test_personal_first_personal_wins_nonredline, test_org_first_org_wins_no... | - | 测试文件，通过 |
| `tests/test_agent_memory_route_acl.py` | 116 | memory 路由 ACL 统一闸 (审计 #2 interim) —— 路由层收敛到 memory_scope_access。 | _FakeStore, _client, _ident, _hdrs, test_write_org_scope_token_denied, test_write_team_scope_token_denied, test_write_project_not_in_allo... | 密钥字样 | 测试文件，通过 |
| `tests/test_agent_memory_store.py` | 46 | MemoryStore / SqlMemoryStore 测试 —— 纯逻辑(row->entry, 默认值, 读写分离接缝)。 | test_scopes_constant, test_entry_defaults, test_row_to_entry, test_row_to_entry_with_supersedes, test_memory_store_split_routing | - | 测试文件，通过 |
| `tests/test_agent_openai_translate.py` | 44 | OpenAI 兼容适配器(DeepSeek/GPT/Qwen)的中性->原生翻译测试. | test_system_prepended_and_user_passthrough, test_assistant_tool_calls_become_openai_tool_calls, test_tool_result_message, test_tools_nati... | - | 测试文件，通过 |
| `tests/test_agent_project_routing.py` | 39 | P2 多租户:工具按 project_id 路由的解析逻辑测试(不触真 DB/网络). | test_resolve_project_id_explicit_wins, test_repo_path_of_reads_meta, test_repo_path_of_missing_returns_none, test_build_registry_accepts_... | - | 测试文件，通过 |
| `tests/test_agent_prompt_context.py` | 26 | system prompt 上下文注入测试 —— 让 agent "知道"当前 org/user/project. | test_no_context_returns_base, test_project_injected, test_user_and_org_injected, test_unspecified_project_noted | - | 测试文件，通过 |
| `tests/test_agent_recall.py` | 151 | RecallService / visible_scopes / 召回注入 测试(memory M3,纯逻辑,不需 PG)。 | FakeStore, _e, test_visible_scopes_full_tuple, test_visible_scopes_no_project_no_user, test_recall_merges_visible_scopes, test_recall_con... | - | 测试文件，通过 |
| `tests/test_agent_registry.py` | 62 | provider 注册表的可扩展性测试 — 验证"加模型不改 get_provider 逻辑". | _Fake, test_builtin_providers_registered, test_register_new_provider_then_build, test_unregistered_with_base_url_falls_back_to_openai_com... | 密钥字样 | 测试文件，通过 |
| `tests/test_agent_session_inmemory.py` | 31 | InMemorySessionStore 隔离测试 —— 按 (org_id, user_id, session_id) 三层隔离。 | test_user_isolation, test_org_isolation, test_default_org_when_omitted | - | 测试文件，通过 |
| `tests/test_agent_session_pg.py` | 69 | SqlSessionStore 的纯逻辑测试 —— Message <-> JSONB payload 往返(不连真 PG)。 | test_plain_message_roundtrip, test_assistant_with_tool_calls_roundtrip, test_tool_result_message_roundtrip, test_row_to_msg_tolerates_emp... | - | 测试文件，通过 |
| `tests/test_agent_tools.py` | 36 | agent 工具注册 + spec 形状测试(不触网 / 不查真 db). | test_default_registry_has_expected_tools, test_specs_shape, test_registry_rejects_nameless_tool | - | 测试文件，通过 |
| `tests/test_audit.py` | 81 | 结构化访问审计日志单测 (codev_platform.core.audit.audit_access)。 | _ident, _dec, log_file, _lines, test_advisory_allow_skipped, test_deny_written_with_fields, test_token_allow_written, test_write_failure_... | 密钥字样 | 测试文件，通过 |
| `tests/test_backup.py` | 113 | Unit tests for codev_platform. | _cfg, test_plan_no_pg_when_dsn_empty, test_plan_has_pg_when_dsn_set, test_plan_data_subdir_existence, test_plan_gitea_arg_overrides_and_r... | 密钥字样 | 测试文件，通过 |
| `tests/test_bootstrap.py` | 88 | D15 bootstrap 纯 plan 单测 —— 只验"按 config 产出哪些有序步骤", 不真跑子命令。 | _names, _by_name, test_venv_check_always_first, test_serve_mcp_always_present, test_codegraph_link_absent_when_no_projects, test_codegrap... | subprocess | 可接受，发布前复核命令边界 |
| `tests/test_chroma_retry.py` | 91 | chroma server 模型/reranker 载入有界重试 —— 纯逻辑单测, 不碰 GPU / chromadb。 | test_retry_delays_default_3_attempts, test_retry_delays_caps_growth, test_retry_delays_no_retry_when_one_or_zero_attempts, test_retry_del... | - | 测试文件，通过 |
| `tests/test_cli_parser.py` | 61 | Regression tests for codev_platform. | _subcommand_choices, test_ops_subcommands_registered, test_core_subcommands_registered, test_parser_parses_dirty_check, test_parser_parse... | - | 测试文件，通过 |
| `tests/test_codegraph_ensure_link.py` | 77 | F18: reindex --codegraph 跑 sync 前自动幂等 ensure .codegraph junction(免手动 link --all)。 | test_ensure_link_skips_when_already_linked, test_ensure_link_triggers_when_not_linked, test_ensure_link_fail_soft_on_exception, test_code... | - | 测试文件，通过 |
| `tests/test_codegraph_link.py` | 85 | codegraph 索引集中到平台(junction)—— 状态判定 + link/unlink 机制。 | env, test_link_state_in_repo_then_missing, test_link_moves_to_platform_and_junctions, test_link_idempotent, test_unlink_moves_back, test_... | 递归删除 | 可接受，发布前复核命令边界 |
| `tests/test_config_redact.py` | 51 | redact_config 纯函数掩码测试 (config doctor/show --redact)。 | test_masks_api_key_and_password_and_token, test_masks_dsn_password_segment_only, test_does_not_mutate_original, test_non_sensitive_keys_u... | 密钥字样 | 测试文件，通过 |
| `tests/test_cross_link_server.py` | 158 | cross-link MCP server —— 多租户 per-project 路由 + 4 tools 烟测。 | _build_db, two_projects, _call, test_per_project_routing_isolates_data, test_stats_reports_active_project, test_search_nodes_scoped, test... | - | 测试文件，通过 |
| `tests/test_eval_memory.py` | 110 | eval memory suite 的纯单测 (不碰 PG)。 | _load_conflict_rows, test_accuracy_basic, test_accuracy_zero_total, test_dataset_has_conflict_cases, test_conflict_winner_matches_expect,... | - | 测试文件，通过 |
| `tests/test_eval_metrics.py` | 85 | eval/metrics.py 纯单测 (无 IO, 无后端)。 | test_recall_at_k_partial, test_recall_at_k_all_hit_and_k_truncation, test_recall_at_k_empty_relevant_and_no_hit, test_hit_at_k_true_withi... | - | 测试文件，通过 |
| `tests/test_gateway_auth.py` | 257 | gateway 认证策略 + 统一拦截中间件测试。 | test_passthrough_resolves_headers, test_passthrough_defaults_when_no_headers, test_passthrough_rejects_illegal_header, test_token_valid_b... | 密钥字样 | 测试文件，通过 |
| `tests/test_gateway_client_url.py` | 90 | gateway client-url —— 把 .mcp.json 各 sse server url 重写成远程反代地址。 | _mcp_json, _args, _servers, test_rewrites_sse_url_by_server_name, test_preserves_headers_and_skips_stdio, test_trailing_slash_base_normal... | 密钥字样 | 测试文件，通过 |
| `tests/test_gateway_projects.py` | 70 | gateway token-add --projects 白名单解析 + token-list 展示单测。 | tmp_cfg, _add, _tokens, test_projects_star_writes_star, test_projects_csv_writes_list, test_projects_absent_not_written_and_warns, test_p... | 密钥字样 | 测试文件，通过 |
| `tests/test_gpu_config.py` | 38 | 显卡参数配置化(2026-06-01): device / dtype / batch 不写死, 换显卡只改 config。 | _dtype, test_auto_cuda_is_fp16, test_auto_cpu_is_fp32, test_explicit_dtypes, test_unknown_falls_back_fp32 | - | 测试文件，通过 |
| `tests/test_health_cross_link_path.py` | 44 | 审计 #5 回归 — health 的 cross_layer DB 路径走 paths. | test_cross_link_db_path_respects_env_override, test_health_check_cross_layer_uses_paths | - | 测试文件，通过 |
| `tests/test_health_split_security.py` | 143 | 审计 #4 回归 — public /healthz 最小化 + 详情面 /platform/status 鉴权。 | _token_auth, _build_app, test_healthz_public_minimal_no_leak, test_platform_status_requires_auth_in_token_mode, test_webhook_healthz_mini... | 密钥字样 | 测试文件，通过 |
| `tests/test_identity.py` | 38 | core. | test_validate_ok, test_validate_rejects_bad, test_resolve_local_default, test_resolve_local_env, test_resolve_from_request_header, test_r... | - | 测试文件，通过 |
| `tests/test_logs.py` | 34 | ops.logs 纯定位 + tail 单测 —— log_sources 用包定位, tail_file 用 tmp 文件验。 | test_log_sources_keys_and_package_located, test_tail_file_missing_returns_empty, test_tail_file_returns_last_n, test_serve_log_dir_named_... | - | 测试文件，通过 |
| `tests/test_mcp_serve.py` | 96 | 平台 MCP 端点编排 (mcp_serve) —— 纯函数单测: 端点枚举 / 命令构造 / 探测。 | test_build_codegraph_cmd, test_build_cross_link_cmd, test_endpoint_health_url_for_all_kinds, test_iter_endpoints_always_has_chroma_and_cr... | - | 测试文件，通过 |
| `tests/test_memory_db.py` | 84 | Tests for `codev-platform memory <init-db\|doctor>` (ops.memory_db). 不连真 DB。 | _build, test_memory_subcommand_registered, test_parser_routes_init_db, test_parser_routes_doctor, _ns, test_init_db_no_dsn, test_doctor_n... | - | 测试文件，通过 |
| `tests/test_meta_health.py` | 77 | Regression tests for meta_health() and project_id_of() resilience. | test_meta_health_reads_health_section, test_meta_health_none_pid_returns_empty, test_meta_health_missing_file_returns_empty, test_meta_he... | - | 测试文件，通过 |
| `tests/test_metrics.py` | 180 | ops.metrics 纯聚合层单测 —— 喂 fake records, 不读真文件。 | test_parse_lines_skips_bad_and_nondict, test_parse_since, test_within_since_filters_by_ts, test_aggregate_audit_counts_and_deny_reasons, ... | 外部网络、密钥字样 | 测试文件，通过 |
| `tests/test_org_cli.py` | 151 | org CLI(ops/org.py)单测:argparse 路由正确性 + 缺 dsn/psycopg 优雅退出。 | _parse, test_route_create, test_route_add_user, test_route_add_member_role_default, test_route_team_create, test_route_team_add_member, t... | - | 测试文件，通过 |
| `tests/test_paths.py` | 68 | Regression tests for codev_platform. | test_cross_link_db_path_has_pid_subdir, test_cross_link_db_path_distinct_per_project, test_cross_link_db_path_not_legacy, test_chroma_col... | - | 测试文件，通过 |
| `tests/test_post_hooks.py` | 45 | post-merge / post-checkout hook 逻辑(双实例 plan P0:本地"获取新代码"也更新索引)。 | test_classify_scopes_buckets, test_classify_scopes_empty_when_no_match, test_post_checkout_skips_file_checkout, test_post_checkout_skips_... | - | 测试文件，通过 |
| `tests/test_ratelimit.py` | 65 | SlidingWindowLimiter + default_key_from_scope 纯单测(不起服务)。 | test_within_window_allows_then_denies, test_window_slides_and_reallows, test_keys_isolated, test_max_le_zero_always_allows, _Ident, test_... | - | 测试文件，通过 |
| `tests/test_ratelimit_middleware.py` | 105 | RateLimitMiddleware + maybe_rate_limit_middleware 工厂行为测试。 | _PassthroughAuth, _build_client, test_third_request_same_identity_rate_limited, test_healthz_exempt_from_rate_limit, test_distinct_users_... | - | 测试文件，通过 |
| `tests/test_rbac_core.py` | 126 | core/rbac.py 纯权限逻辑单测(无 psycopg / 无 IO)。 | test_role_allows_viewer_reads_not_writes, test_role_allows_member_writes_not_admin, test_role_allows_admin_all, test_role_allows_none_den... | - | 测试文件，通过 |
| `tests/test_rbac_wire.py` | 100 | RBAC 接线测试(M5):deps.get_rbac_store 回退 + LocalRecallService 双路 + memory 路由语法。 | _FakeMemoryStore, _FakeRbacStore, test_get_rbac_store_none_without_dsn, test_recall_visible_scopes_falls_back_to_stub, test_recall_visibl... | - | 测试文件，通过 |
| `tests/test_reindex_patterns.py` | 83 | Regression tests for reindex scope pattern matching. | _scope_of, test_sql_matches_cross_link_when_declared, test_mapper_matches_cross_link_when_declared, test_py_matches_codegraph_when_declar... | - | 测试文件，通过 |
| `tests/test_reindex_pull.py` | 112 | sync_repo_to_remote 单测 —— 纯, monkeypatch subprocess, 不碰真 git / 网络。 | _CP, _mk_git_repo, test_not_git_repo, test_no_upstream, test_pull_success, test_pull_non_ff, test_pull_timeout, test_pull_oserror | subprocess | 可接受，发布前复核命令边界 |
| `tests/test_resolve_argv.py` | 78 | Regression tests for codev_platform. | _which_factory, test_cmd_routed_through_comspec, test_bat_routed_through_comspec, test_ps1_routed_through_powershell, test_exe_runs_direc... | - | 测试文件，通过 |
| `tests/test_resources_packaging.py` | 32 | audit-0601 #2: rules/skills 真值源 relocate 进 codev_platform/resources/ 后的可达性。 | test_resources_rules_accessible_via_importlib, test_resources_skills_accessible_via_importlib, test_cli_sync_sources_resolve_to_existing_... | - | 测试文件，通过 |
| `tests/test_serve_mcp_diagnose.py` | 104 | serve-mcp DOWN 原因诊断 + start --wait 轮询判定的纯函数单测 (P1 fullchain-audit-2026-06-01)。 | _ep, test_port_closed_unit_inactive, test_port_closed_unit_active, test_port_closed_unit_unknown, test_dep_missing_cross_link, test_dep_m... | - | 测试文件，通过 |
| `tests/test_systemd_agent_clock.py` | 55 | E16 + B8: 断言 agent 常驻 unit 与时钟重同步 service/timer 的渲染与安装纳入。 | _cfg, test_agent_unit_self_healing_and_execstart, test_agent_unit_respects_config_port, test_clock_resync_service_has_hwclock, test_clock... | - | 测试文件，通过 |
| `tests/test_systemd_restart.py` | 44 | B9: 断言每个 systemd unit 都含 Restart=always —— 防未来有人移除自愈策略。 | _cfg, test_all_mcp_units_have_restart_always, test_reindex_unit_has_restart_always, test_webhook_unit_has_restart_always, test_codegraph_... | - | 测试文件，通过 |
| `tests/test_token_expiry.py` | 84 | #8(部分): token 过期 + 轮换 —— 纯函数 + TokenAuthenticator 过期拒绝(不起服务)。 | test_token_expired_no_field_never_expires, test_token_expired_future_not_expired, test_token_expired_past_expired, test_token_expired_iso... | 密钥字样 | 测试文件，通过 |
| `tests/test_webhook_body_limit.py` | 35 | webhook 请求体大小上限 (审计 #7) —— 超 _MAX_BODY 返回 413, 不进 reindex 队列。 | _client, test_oversized_body_rejected_413, test_normal_size_passes_body_limit, test_unknown_provider_404_before_body_check | 密钥字样 | 测试文件，通过 |

## 需要优先人工复核的文件

- `codev_platform/chroma/indexer.py`: 可运行，但建议拆分/收窄边界；风险命中: 通配异常、大文件
- `codev_platform/chroma/launcher.py`: 可接受，发布前复核命令边界；风险命中: subprocess、Popen、通配异常、外部网络、密钥字样
- `codev_platform/chroma/server.py`: 可运行，但建议拆分/收窄边界；风险命中: 通配异常、密钥字样、超大文件
- `codev_platform/cli.py`: 可运行，但建议拆分/收窄边界；风险命中: subprocess、通配异常、强杀进程、密钥字样、大文件
- `codev_platform/cross_link/server.py`: 可运行，但建议拆分/收窄边界；风险命中: 通配异常、密钥字样、大文件
- `codev_platform/cross_link_ts/pnpm-lock.yaml`: 可运行，但建议拆分/收窄边界；风险命中: 大文件
- `codev_platform/mcp_serve.py`: 可运行，但建议拆分/收窄边界；风险命中: subprocess、Popen、通配异常、外部网络、密钥字样、大文件
- `codev_platform/ops/_common.py`: 可接受，发布前复核命令边界；风险命中: subprocess、通配异常
- `codev_platform/ops/backup.py`: 可接受，发布前复核命令边界；风险命中: subprocess、通配异常、递归删除、外部网络、密钥字样
- `codev_platform/ops/bootstrap.py`: 可接受，发布前复核命令边界；风险命中: subprocess、通配异常
- `codev_platform/ops/codegraph.py`: 可接受，发布前复核命令边界；风险命中: subprocess、通配异常
- `codev_platform/ops/health.py`: 可运行，但建议拆分/收窄边界；风险命中: subprocess、通配异常、外部网络、密钥字样、超大文件
- `codev_platform/ops/reindex.py`: 可运行，但建议拆分/收窄边界；风险命中: subprocess、Popen、通配异常、大文件
- `codev_platform/reindex/git_sync.py`: 可接受，发布前复核命令边界；风险命中: subprocess
- `codev_platform/reindex/runners.py`: 可接受，发布前复核命令边界；风险命中: subprocess
- `codev_platform/reindex/worker.py`: 可接受，发布前复核命令边界；风险命中: subprocess、通配异常
- `scripts/ai-health.ps1`: 可运行，但建议拆分/收窄边界；风险命中: 通配异常、密钥字样、超大文件
- `scripts/clean-local-artifacts.ps1`: 可接受，发布前复核命令边界；风险命中: 递归删除
- `tests/test_bootstrap.py`: 可接受，发布前复核命令边界；风险命中: subprocess
- `tests/test_codegraph_link.py`: 可接受，发布前复核命令边界；风险命中: 递归删除
- `tests/test_reindex_pull.py`: 可接受，发布前复核命令边界；风险命中: subprocess

## 边界 bug 专项追加

本轮继续按边界输入审计，发现并修复一个 dev 单机模式下的真实 ACL 误判：`/memory` 未挂 gateway 中间件时，路由能解析 `X-User-Id`，但 ACL 使用的 identity 为 `None`，导致 personal memory “本人写本人”返回 403。

修复位置：

- `codev_platform/agent/routes/memory.py`: 增加 `_effective_identity()`，gateway 存在时使用可信 identity；dev 单机时合成 passthrough advisory identity。
- `tests/test_agent_memory_route_acl.py`: 增加 `test_write_personal_self_allowed_without_gateway`。

验证：

- ACL/Memory 边界专项测试: `31 passed, 1 warning`
- 全量测试: `428 passed, 5 skipped, 1 warning`

剩余边界风险：

- `health --mode full` 与 `serve-mcp status` 语义不完全一致，后续建议加 `health --require-services`。
- `SqlMemoryStore.forget/archive` 未来开放 HTTP 删除/归档端点前，必须补 `org_id/user_id` 权限闸。
- webhook `/platform/status` 当前公开 provider 清单，正式部署建议鉴权或继续最小信息化。
- 核心链路 `except Exception` 仍较多，需逐步收窄，避免边界错误被 fail-soft 掩盖。

## 最终判断

逐文件扫描没有发现直接阻断当前本机运行和测试的代码问题。当前主要风险不是某个文件已经坏掉，而是平台型项目自然形成的复杂度集中、后台进程治理、Docker 私有化交付、授权升级机制和真实客户项目插件化适配。
