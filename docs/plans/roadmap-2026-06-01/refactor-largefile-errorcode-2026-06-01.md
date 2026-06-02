# 大文件拆分 + 错误码结构化 —— 技术方案 & Plan (2026-06-01)

> 来源: `docs/audits/fullchain-audit-2026-06-01.md` §2 代码级审计「需要继续加强」。
> 两条独立工作流, 可分批落地, 互不阻塞:
> - **A. 拆大文件** —— `chroma/server.py` / `ops/health.py` / `mcp_serve.py` 超/近 600 行硬约束。
> - **B. 错误码结构化** —— 对外 HTTP/MCP 返回收窄 `except Exception`, 区分 5 类错误。
>
> 定位: 纯可维护性重构, **行为不变 (除 B 的对外错误体新增字段)**, 不引入新能力。是 `roadmap-2026-06-01` 插件化平台的前置整理 ("新增能力前先拆边界")。

---

## 0. 现状盘点 (已实测)

| 文件 | 行数 | 软/硬阈值 | `except Exception` 数 | 说明 |
|---|---:|---|---:|---|
| `codev_platform/chroma/server.py` | 1286 | 跨模块兜底 1000 硬 | 29 | chroma MCP daemon, 最复杂 (模型+路由+工具+HTTP 四合一) |
| `codev_platform/ops/health.py` | 1119 | 同上 | 15 | health 体检, 函数已天然按 `_check_*`/`_usage_*` 分组 |
| `codev_platform/mcp_serve.py` | 701 | 600 软 | 2 | 端点探活 + systemd 渲染两职责, 只超一点 |
| 全 `codev_platform` | — | — | **132** | 多数是 CLI/daemon/launcher 的**有意 fail-soft**, B 只动对外 HTTP 边界 |

对外错误现状 (B 的改造面):

| 出口 | 现状 | 问题 |
|---|---|---|
| chroma `call_tool` / cross_link / codegraph MCP | `{"error": "<中文 msg>"}` (TextContent / JSONResponse) | 无机器可读 code, 客户端只能字符串匹配 |
| agent `routes/chat.py` `routes/memory.py` | `HTTPException(status_code, detail=str(e))` | 直接把 `str(e)` 透出, 泄漏内部异常文本 + 无 code |
| webhook `server.py` | `{"error": "..."}` + 正确 status_code | 已较规范, 缺 code |

**向后兼容硬约束 (实测)**: `tests/test_cross_link_server.py:104,143` 断言 `r["error"]` **子串**。故 `error` 字段**必须保留为字符串**, code 只能**并排新增** (`{"error": "<msg>", "code": "<machine>"}`), 不可改成嵌套对象。其余对外测试只断言 `status_code`, 不受影响。

---

## A. 拆大文件

### A.1 原则

1. **零行为变更**: 纯搬迁 + import 重连, 不改逻辑、不改函数签名、不改对外符号路径 (外部 `import codev_platform.chroma.server` 仍可用)。
2. **共享态先抽离**: server.py 有 module-level 全局 (`_projects` / `_current_project_id` contextvar / `PROJECT_ID` / `_global_init_error` / 模型句柄等)。拆前先把这些挪进一个被各子模块 import 的 `_state.py`, 否则跨文件全局会断。**这是 A 组唯一高风险点**。
3. **拆完原文件变 facade**: 旧文件保留为 `from ._xxx import *` re-export, 保证 `tools/_platform` shim 和外部引用不破。

### A.2 chroma/server.py (1286 → ~6 文件)

把单文件升级成 `chroma/` 子包内部模块 (对外仍 `chroma.server`):

| 新模块 | 收纳 (按实测函数) | 约行 |
|---|---|---|
| `chroma/_state.py` | 共享全局 + contextvar + `_ProjectState` 数据类 + 常量 | ~120 |
| `chroma/_models.py` | `_torch_dtype` `_ensure_model` `_get_client` `_encode_query` `_get_gpu_sem` `_ensure_reranker` `_rerank_scores` `_is_gpu_error` `_gpu_*` `_load_with_retry` `retry_delays` `should_retry` | ~340 |
| `chroma/_projects.py` | `_load_project_state` `_maybe_reload_project` `_ensure_project` `_project_last_indexed_iso` | ~180 |
| `chroma/_obs.py` | `_maybe_rotate_log` `_flog` `_log_recall` `_record_stat*` `_process_info` `_to_iso` | ~120 |
| `chroma/_tools.py` | `list_tools` `call_tool` `_err` `_ok` `_build_where` (call_tool 自身 ~250 行, 可内部再按 tool 分 `_do_search` / `_do_get_by_file`) | ~330 |
| `chroma/server.py` (facade) | `server` 对象装配 + `_run_stdio` `_run_http` (SSE/health/platform_status handler) + `main` + re-export | ~220 |

**落地顺序 (每步独立可测)**:
1. 抽 `_state.py` (全局/常量/dataclass) → 跑全量测试。
2. 抽 `_obs.py` (无状态日志/统计) → 测。
3. 抽 `_models.py` → 测 (重点: embed/reranker 冷启动路径)。
4. 抽 `_projects.py` → 测 (重点: 多租户路由 + reload)。
5. 抽 `_tools.py` → 测 (重点: search_docs/get_by_file 召回)。
6. server.py 收尾成 facade → 测 + 手动起 daemon `serve-mcp start --wait` 验 SSE。

### A.2b 议定的无环分层 DAG + 进度 (2026-06-02)

原 A.2 把"常量"塞进 `_state.py`,但**配置常量留在 server 会导致 reranker 循环**(server 又要 reranker 给 health/main)。修正:**配置(`_config`)与可变状态(`_state`)各抽一个叶子模块**,所有逻辑模块只向下依赖叶子 → DAG 无环。

```
core.* (config/paths/obslog/project_id)
  ↑
_config  _state  _obslog  _stats  _schema     ← 5 叶子 (只依赖 core)
  ↑
_helpers           (→_obslog,_stats)
  ↑
_models  _reranker (→_config,_state,_helpers,_obslog,_stats)
  ↑
_projects          (→_config,_state,_models,_obslog)
  ↑
_tools  _http      (→_config,_state,_models,_reranker,_projects,...)
  ↑
server.py          ← 瘦装配器: Server()+list_tools wrapper+_run_stdio+main+注册 import (~120-150 行)
```

**铁律**:rebound 标量(`_model`/`_global_init_error`/`_reranker_load_err`...)真值源在 `_state.py`,所有读写走 `import _state as st; st.X`。已在 `_tools.py` 用 `srv._global_init_error` 验证可行(早返回烟测返回 `{"error":"SMOKE_ERR"}`)。

**进度**:
- ✅ `_obslog` / `_stats` / `_helpers` / `_schema` 已抽 (`3ca9715`)
- ✅ `_tools`(call_tool)已抽 (`364133f`),server.py **1286→674**(过 1000 硬限)
- ⏳ `_config`(叶子,常量按值,低风险)— **下一步,解 reranker 循环的钥匙**
- ⏳ `_state`(可变全局→`st.X`,高风险机械大改)— **上线前必须过 WSL search_docs 烟测**(改了所有热路径全局访问,Windows CI 测不到 GPU 段)
- ⏳ `_models` / `_reranker` / `_projects` / `_http` — 各只依赖 _config+_state,抽完即 ≤600

**降风险**:`_state` 转换配 deeper mock 烟测(mock `_ensure_project` 返回带 collection 的假 state + mock `_encode_query`/`col.query`,让 call_tool 跑过早返回触到 `st._reranker_load_err` 行,抓"漏改引用"的 AttributeError)。

### A.3 ops/health.py (1119 → 子包)

函数已天然分组, 风险最低 (大多 `(r: Report, ...)` 入参, 无共享可变全局):

| 新模块 | 收纳 | 约行 |
|---|---|---|
| `ops/health/_util.py` | `_expand` `_count_files` `_latest_mtime` `_run_py` `_git` `_parse_dt` `_iter_jsonl` `_list_processes` | ~200 |
| `ops/health/_checks.py` | 全部 `_check_*` (chroma/embed/torch/daemon/cross-link/codegraph/hook ~18 个) | ~520 |
| `ops/health/_usage.py` | `_usage_search_recall` `_usage_reindex` `_usage_platform_docs` `_usage_cross_link` | ~170 |
| `ops/health/__init__.py` | `Report` `cmd_health` `cmd_health_all` `_verdict` `_write_json_snapshot` `_platform_url` `register` | ~230 |

注: `ops/health.py` → `ops/health/` 目录化, `register(subparsers)` 入口路径不变 (`from codev_platform.ops import health; health.register(...)` 仍生效)。确认 CLI 装配处 import 写法。

### A.4 mcp_serve.py (701 → 2 文件)

只需一次抽离即可过 600 软线, 低风险:

| 新模块 | 收纳 | 约行 |
|---|---|---|
| `mcp_systemd.py` (新) | `render_systemd_units` `render_reindex_unit` `render_webhook_unit` `render_agent_unit` `render_clock_resync_units` `install_systemd` `systemd_unit_name` `SYSTEMD_KINDS` | ~240 |
| `mcp_serve.py` (留) | `MCPEndpoint` `iter_endpoints` `probe*` `diagnose_down` `collect_facts` `wait_until_serving` `ensure_serving` `_spawn_detached` `build_*_cmd` `mcp_source_*` | ~460 |

`mcp_serve.py` 顶部 `from codev_platform.mcp_systemd import *` re-export, CLI `install-systemd` 子命令零改。

### A.5 A 组验证门

每个抽离步独立跑:
```
python -m compileall -q codev_platform
python -m pytest -q                       # 基线 427 passed, 5 skipped 不许下降
codev-platform serve-mcp start --wait     # A.2 收尾必跑: 真起 daemon 验 SSE OK
codev-platform health --mode full         # READY / all green
```
新增**静态断言** (防回潮): `tests/test_file_size_budget.py` —— 扫 `codev_platform/**/*.py`, 断言无文件 > 600 行 (chroma/health 拆后应全部达标; 例外白名单显式列出)。

---

## B. 错误码结构化

### B.1 目标

对外出口把笼统异常 → 5 类机器可读 code (对齐审计建议: 配置错误 / 依赖缺失 / 索引缺失 / 权限错误 / 下游不可用), 且**不再把 `str(e)` 原文透给客户端** (内部细节进日志, 对外给稳定 message)。

### B.2 错误模型 (新增 `core/errors.py`)

```python
class ErrorCode(str, Enum):
    INVALID_PARAMS   = "invalid_params"      # 400  入参非法/缺失
    ACCESS_DENIED    = "access_denied"       # 403  ACL/org 不匹配
    PROJECT_UNKNOWN  = "project_unknown"     # 404  project_id 未登记/未初始化
    DEPENDENCY_MISSING = "dependency_missing"# 503  torch/模型/psycopg 等缺失
    INDEX_MISSING    = "index_missing"       # 503  chroma/cross_layer DB 不存在
    UPSTREAM_UNAVAILABLE = "upstream_unavailable" # 503 下游 daemon/PG/LLM 不可用
    RATE_LIMITED     = "rate_limited"        # 429
    INTERNAL         = "internal"            # 500  兜底 (不泄漏 str(e))

class PlatformError(Exception):
    code: ErrorCode
    http_status: int
    message: str          # 对外稳定文案 (中文 OK)
    detail: str | None    # 仅进日志, 不进对外体
```

映射表 (单一真值源, code ↔ http_status), 提供:
- `to_mcp_error(err) -> dict` → `{"error": err.message, "code": err.code}` (**保留 error 字符串, 并排加 code**, 满足向后兼容)。
- `to_http_response(err) -> JSONResponse` → 同上 shape + status_code。
- FastAPI: 注册 `exception_handler(PlatformError)`, 统一出 `{"error": msg, "code": ...}`; 现有 `HTTPException` 暂保留 (兼容), 新代码抛 `PlatformError`。

### B.3 改造范围 (只动对外边界, 内部 fail-soft 不动)

| 出口文件 | 动作 |
|---|---|
| `chroma/_tools.py` (`_err`) | `_err(msg, code=...)` 加可选 code; `call_tool` 顶层 `except Exception` → 捕获后归 `INTERNAL` + 日志原文, 已知分支 (空 query→INVALID_PARAMS, project 未初始化→PROJECT_UNKNOWN/INDEX_MISSING) 显式抛 `PlatformError` |
| `cross_link/server.py` | `invalid project_id`→INVALID_PARAMS, `forbidden`→ACCESS_DENIED, `DB 不存在`→INDEX_MISSING (注意保留 `error` 子串, test 依赖) |
| `codegraph/server.py` | 同上 |
| `agent/routes/chat.py` `memory.py` | `detail=str(e)` → 改抛 `PlatformError`; 503/403/400 归对应 code; **停止透出 raw `str(e)`** |
| `webhook/server.py` | 已有 status_code, 补 code 字段 (payload too large→INVALID_PARAMS, secret 未配→DEPENDENCY_MISSING, 验签失败→ACCESS_DENIED) |

**明确不动**: launcher / reindex worker / metrics / backup / CLI 的 `except Exception` fail-soft —— 它们是后台健壮性边界, 收窄反而降可用性。仅要求: 这些地方的 `except Exception` 至少 `_log` 结构化原因 (若尚未)。

### B.4 向后兼容策略 (additive only)

- MCP 返回: `{"error": "<原中文 msg>"}` → `{"error": "<原中文 msg>", "code": "<machine>"}`。`error` 文案与子串**不变**, 老客户端/测试不破。
- agent HTTP: 由 `{"detail": "..."}` → `{"error": "<稳定 msg>", "code": "..."}`。**这是行为变化** (FastAPI HTTPException 默认 `detail`)。需:
  1. 先确认无前端/脚本依赖 `detail` 字段 (grep playground/widget)。
  2. 过渡期同时保留 `detail` (= message) + 新增 `error` + `code`, 一两个版本后再去 `detail`。
- 文档: HTML §11 agent /chat 错误体示例补 code 表。

### B.5 B 组验证门

```
python -m pytest -q                        # 基线不降; 重点 test_cross_link_server (error 子串)
```
新增测试:
- `tests/test_error_model.py` —— code↔http_status 映射完整 + `to_mcp_error`/`to_http_response` shape (含 `error` 字符串保留断言)。
- `tests/test_agent_route_errors.py` —— chat/memory 各错误分支返回正确 code + status + **不含 raw `str(e)`**。
- 扩 `test_cross_link_server.py` —— 断言新 `code` 字段同时存在。

---

## 排期 & 优先级

| 批次 | 内容 | 风险 | 依赖 | 建议 |
|---|---|---|---|---|
| 1 | A.4 `mcp_serve.py` 抽 systemd | 低 | 无 | 先做, 练手 + 立刻达标一个文件 |
| 2 | A.3 `health.py` 目录化 | 低-中 | 无 | 函数已分组, 机械搬迁 |
| 3 | B `core/errors.py` + 边界改造 | 中 | 无 (与 A 正交) | 可与 1/2 并行, 但 chroma 出口待批次 4 |
| 4 | A.2 `chroma/server.py` 子包化 | **高** | 共享态抽离 | 单独一批, 每子步跑全量 + 真起 daemon |

**门禁声明 (本 plan 实施时首次改文件前需复述)**: 触及 chroma/ops/agent/webhook 多层 + 纯重构; 适用 `file-discipline.md §1` (≤600) + `agent-provider-architecture.md` (策略接口/registry 不破) + `commit-pr-conventions.md`; 关键约束 = 零行为变更 + `error` 字符串字段保留 + 共享态先抽离; 验证 = `pytest -q` 基线 427 不降 + `serve-mcp start --wait` + `health --mode full`。

## 回退

- A: 每批一个独立 commit, 出问题 `git revert` 单批; facade re-export 保证回退期间外部引用不破。
- B: additive 字段, 回退只需删 `code`; `core/errors.py` 是新增文件, 删除即净回退。

## 不做 (本轮边界)

- 不重写 call_tool 业务逻辑、不改召回算法、不动 GPU 信号量策略。
- 不收窄后台 fail-soft `except Exception` (仅补结构化日志)。
- 不引入第三方错误框架 (problem+json / RFC7807) —— 保持最小自描述 envelope。
