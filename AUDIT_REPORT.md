# codev-platform 后端/测试平台深度审计报告

审计时间：2026-06-05  
审计方式：只读扫描、静态审计、全量测试、重点边界测试复核  
审计范围：`codev_platform/`、`tests/`、`scripts/`、`tools/`、`eval/`  
未纳入范围：`web-ui/` 前端目录

## 1. 执行摘要

本轮审计未修改业务代码。全量测试结果为：

- `1076 passed`
- `5 skipped`
- `7 failed`

失败集中在两个测试文件：

- `tests/test_agent_vector_recall.py`：5 个失败，根因是轻量测试环境缺少 `jieba`，但召回排序路径间接导入了 BM25 重依赖模块。
- `tests/test_serve_mcp_diagnose.py`：2 个失败，根因是 `cross_link` 退役/替换为 `graph` 后，诊断提示和旧测试期望不一致。

排除上述已定位失败文件后：

- `1061 passed`
- `5 skipped`
- `22 deselected`

结论：主干回归面整体稳定，但存在 4 个需要优先处理的高风险问题：

1. 向量召回轻量环境依赖耦合导致测试/运行失败。
2. 用户角色变更接口存在跨组织边界漏洞。
3. 生产模式下 PG 账户存储缺依赖会静默回退内存。
4. 创建用户不传 role 时不会写 org membership，PG 重启后可能丢失 org 归属。

## 2. 扫描范围与基础结果

文件统计：

- 文件总数：1720
- Python 源码：398
- Python AST 语法错误：0
- 类定义：312
- 函数/方法定义：2868

目录分布：

- `codev_platform`：1051 个文件，含 pyc/cache 等生成物
- `tests`：617 个文件，含 pyc/cache 等生成物
- `scripts`：25 个文件
- `tools`：10 个文件
- `eval`：17 个文件

当前工作区状态：

- 只有未跟踪项：
  - `linux-venv`
  - `scripts/bench_memory.py`
  - `tests/test_bench_memory.py`

## 3. 测试结果

### 3.1 全量测试

命令：

```powershell
python -m pytest -q
```

结果：

```text
7 failed, 1076 passed, 5 skipped, 1 warning
```

失败项：

```text
tests/test_agent_vector_recall.py::test_redline_top_and_vector_reorders_rest
tests/test_agent_vector_recall.py::test_redline_never_demoted_even_if_vector_ranks_others
tests/test_agent_vector_recall.py::test_conflict_resolution_preserved_before_ranking
tests/test_agent_vector_recall.py::test_vector_id_outside_pool_ignored
tests/test_agent_vector_recall.py::test_index_query_gets_visible_scopes
tests/test_serve_mcp_diagnose.py::test_dep_missing_cross_link
tests/test_serve_mcp_diagnose.py::test_db_missing
```

### 3.2 排除已定位失败文件后的回归结果

命令：

```powershell
python -m pytest -q -k "not agent_vector_recall and not serve_mcp_diagnose"
```

结果：

```text
1061 passed, 5 skipped, 22 deselected, 1 warning
```

### 3.3 重点安全/工具抽测

命令：

```powershell
python -m pytest -q tests\test_webhook_body_limit.py tests\test_agent_tools.py tests\test_health_split_security.py
```

结果：

```text
11 passed, 1 warning
```

## 4. P0 高优先级问题

### P0-1. 向量召回轻量环境必崩

位置：

- `codev_platform/agent/recall_service.py:160`
- `codev_platform/chroma/bm25.py:24`

现象：

`VectorRecallService._rank()` 中注释说明是 lazy import，避免 Local 用户拉 `jieba/rank_bm25`，但实际代码：

```python
from codev_platform.chroma.bm25 import rrf_fuse
```

而 `codev_platform/chroma/bm25.py` 顶层直接执行：

```python
import jieba
from rank_bm25 import BM25Okapi
```

当前 dev extra 不安装 runtime 重依赖，因此轻量环境运行召回排序时会抛：

```text
ModuleNotFoundError: No module named 'jieba'
```

影响：

- 5 个向量召回测试失败。
- Local/dev 环境中只要走向量召回融合路径就可能失败。
- 注释与实际行为不一致，容易误判为已做懒加载保护。

落地建议：

1. 将纯函数 `rrf_fuse` 拆到无第三方依赖模块，例如 `codev_platform/core/ranking.py`。
2. `recall_service.py` 从无依赖模块导入 `rrf_fuse`。
3. `bm25.py` 继续保留 BM25Index，但 `jieba/rank_bm25` 只服务 BM25，不影响 RRF。
4. 增加回归测试：在未安装 `jieba` 的环境中，`VectorRecallService` 仍可完成向量+关键词融合排序。

### P0-2. 用户角色变更存在跨组织边界漏洞

位置：

- `codev_platform/web/services/user_service.py:125-135`

问题代码逻辑：

```python
user = self._require_user(username)
org_id = org_id.strip()
if not caller_is_admin and org_id != caller_org_id:
    raise PlatformError(...)
role = self._coerce_role(role)
get_member_store().upsert(OrgMember(org_id=org_id, username=username, role=role))
```

问题：

`set_roles()` 只校验请求参数里的 `org_id` 是否等于调用者 org，没有校验目标用户 `user.org_id` 是否也属于调用者 org。

可触发场景：

1. `boss` 是 `orgA` admin。
2. `outsider` 是 `orgB` 用户。
3. `boss` 调 `/home/user/project`，请求体传：

```json
{
  "username": "outsider",
  "orgId": "orgA",
  "role": "admin"
}
```

当前逻辑会允许写入：

```text
OrgMember(org_id="orgA", username="outsider", role="admin")
```

影响：

- 跨组织用户可被错误加入本组织。
- 可能造成 RBAC、用户列表、审计归属混乱。
- 属于权限边界漏洞。

落地建议：

1. `set_roles()` 在 upsert 前调用：

```python
self._guard_same_org(user, caller_org_id, caller_is_admin)
```

2. 补测试：

```text
test_org_admin_cannot_set_roles_for_other_org_user
```

3. 测试断言：

- HTTP 403
- `member_store.get("orgA", "outsider") is None`

### P0-3. 生产 PG 缺依赖会静默回退内存

位置：

- `codev_platform/web/repositories/account_store.py:122-125`
- `tests/test_account_store_pg_failfast.py:39`

当前逻辑：

```python
except ImportError:
    _active.update(org=org_store, user=user_store, member=member_store)
    return "memory"
```

问题：

即使 `deployment.mode = "prod"` 且配置了 `memory.pg_dsn`，只要缺 `psycopg/sqlalchemy` 相关依赖，账户存储就会静默回退到内存。

影响：

- 生产环境用户、组织、成员、RBAC 状态无法持久化。
- 重启后账户状态丢失。
- 运维以为使用 PG，实际使用内存，属于危险的 fail-open。

落地建议：

1. prod 下 `ImportError` 也应 fail-fast。
2. dev/test 才允许 fallback memory。
3. 修改 `tests/test_account_store_pg_failfast.py::test_prod_psycopg_missing_falls_back_to_memory` 的预期。

建议策略：

```text
prod + dsn + PG 初始化失败或依赖缺失 => raise
dev/test + dsn + PG 初始化失败或依赖缺失 => fallback memory + warning
无 dsn => memory
```

### P0-4. 创建用户不传 role 时不会写 org membership

位置：

- `codev_platform/web/services/user_service.py:70`
- `codev_platform/web/services/user_service.py:76-77`
- `codev_platform/web/repositories/account_store_pg.py:121-127`

当前逻辑：

```python
role = self._coerce_role(role) if role else None
...
if role:
    get_member_store().upsert(OrgMember(org_id=org_id, username=username, role=role))
```

PG 读取用户时：

```python
org_id=(m[0] if m else "default")
```

问题：

用户创建时如果不传 role，则只写 users，不写 org_members。PG 模式下再次读取用户时，org_id 会从 org_members 推导；缺 membership 时会落到 `"default"`。

影响：

- 用户 org 归属在 PG 模式下可能丢失。
- list/detail/auth/session 的 org 判断可能异常。
- 内存 store 与 PG store 行为不一致。

落地建议：

1. 创建用户时默认 role 为 `member`。
2. 或者 PG users 表持久化 `org_id`，不要依赖 org_members 反推归属。
3. 补测试：
   - 创建用户不传 role 后，member_store 中应存在 `member`。
   - PG round-trip 后 `User.org_id` 仍为创建时 org。

## 5. P1 中高风险问题

### P1-1. MCP diagnose 与 cross_link 退役状态不一致

位置：

- `codev_platform/mcp_serve.py:253-264`
- `tests/test_serve_mcp_diagnose.py:39-57`

当前 `mcp_serve.py` 的 `_DEP_HINT/_DB_HINT` 不再包含 `cross_link`，但旧测试仍要求：

- 缺依赖提示包含 `sqlglot`
- 缺数据提示包含 `cross_layer.sqlite`

失败：

```text
AssertionError: assert ('依赖缺失' in '依赖缺失 (cross_link)' and 'sqlglot' in ...)
AssertionError: assert ('数据缺失' in '数据缺失 (cross_link)' and 'cross_layer.sqlite' in ...)
```

判断：

这是 `cross_link` 退役到 `graph` 后的兼容策略未统一。

落地建议：

二选一：

1. 如果 `cross_link` 已正式退役：更新/删除旧 cross_link diagnose 测试。
2. 如果仍需兼容旧 kind：恢复 `cross_link` 对 `sqlglot` 和 `cross_layer.sqlite` 的 hint。

### P1-2. 组织成员可添加不存在用户

位置：

- `codev_platform/web/services/org_service.py:91-96`
- `tests/test_web_orgs.py:91-104`

当前 `OrgService.add_member()` 只校验 org 存在和 role 合法，不校验 username 是否存在。

影响：

- 可产生 orphan membership。
- 用户列表、权限计算、审计归属可能出现幽灵成员。
- 当前测试允许 `carol` 未创建即加入成员表，说明需求未定清楚。

落地建议：

1. 若这是“添加已有用户为成员”：必须校验 user 存在。
2. 若这是“邀请用户”：需要显式 invitation 状态，而不是直接写 `OrgMember`。

### P1-3. SessionStore 仍是进程内实现

位置：

- `codev_platform/web/security/sessions.py:40`

当前实现说明：

```python
内存会话表。access_hash/refresh_hash → Session。TODO: PG 实现。
```

影响：

- 服务重启后登录态全部丢失。
- 多进程部署时 session 不共享。
- 禁用用户后的 revoke 只影响当前进程。

落地建议：

生产环境切 PG/Redis session store，并保持当前接口不变。

## 6. 静态扫描结果

### 6.1 Ruff

命令：

```powershell
ruff check codev_platform tests scripts tools eval
```

结果：

```text
Found 54 errors.
24 fixable with --fix
```

生产代码中优先清理：

- `codev_platform/graph/analyzers/brain_labeler.py:97`：B007 未使用循环变量 `short`
- `codev_platform/graph/mcp_server.py:24`：F401 未使用 import `Path`
- `eval/run_eval.py` 多处 `.format()` 可改 f-string

测试/脚本中主要是：

- UTF-8 encoding comment 冗余
- 分号多语句
- 未使用 import
- 测试 helper 默认参数调用

### 6.2 AST 规则扫描

统计：

- broad except：184
- subprocess-like call：117
- call default：100
- dynamic-code：52
- destructive fs：19
- open without encoding：15

说明：

- `dynamic-code` 大部分是 `re.compile()` 或模型 `.eval()` 被规则误报，不是直接安全漏洞。
- `destructive fs` 中高风险点主要需要确认路径约束是否明确，例如备份清理、chroma collection 删除、graph ingest 替换。
- `broad except` 大量用于降级逻辑，建议对核心路径逐步收窄异常类型，并补日志。

## 7. 覆盖薄弱区域

粗略按测试直接引用扫描，20 个生产文件未被测试直接引用。该结果不是精确覆盖率，但适合定位补测优先级。

优先补测文件：

```text
codev_platform/chroma/indexer.py
codev_platform/ops/health/_checks.py
codev_platform/web/services/user_service.py
codev_platform/cli_cmds/setup_cmd.py
codev_platform/web/services/org_service.py
codev_platform/chroma/bm25.py
codev_platform/chroma/_helpers.py
codev_platform/chroma/_reranker.py
codev_platform/web/db/alembic/versions/20260603_0001_baseline_rbac_account.py
codev_platform/chroma/_models.py
```

建议优先补：

1. `user_service.py`：跨 org、默认 membership、角色变更。
2. `org_service.py`：不存在用户加成员、成员移除幂等、跨 org 权限。
3. `chroma/bm25.py`：依赖缺失、tokenize 边界、RRF 独立性。
4. `ops/health/_checks.py`：缺依赖、缺数据、权限不足、命令超时。
5. `chroma/indexer.py`：collection 删除/重建、路径边界、失败回滚。

## 8. 安全正向观察

本轮未发现以下模块的明显高危问题：

- `codev_platform/agent/tools/fs.py`
  - 使用 `resolve()` + `is_relative_to(root)` 做路径沙箱。
  - 拒读 `.env`、key、pem 等敏感文件。

- `codev_platform/webhook/server.py`
  - body size 有上限。
  - secret 缺失默认 fail-closed。
  - `allow_insecure` 需要显式配置。

- `codev_platform/webhook/providers.py`
  - Gitea/GitLab 签名/Token 校验使用常量时间比较。

- `codev_platform/gateway/middleware.py`
  - public path 判断带边界。
  - 拒绝 `..` 路径。

## 9. 建议落地顺序

### 第一批：阻断当前失败与权限漏洞

1. 拆 `rrf_fuse` 到无依赖模块，修复 5 个 vector recall 失败。
2. `UserService.set_roles()` 增加目标用户 org 校验。
3. 明确 `cross_link` 是否退役，并同步 diagnose 测试/兼容 hint。

### 第二批：修生产持久化风险

1. prod 下 PG 依赖缺失 fail-fast。
2. 创建用户默认写 membership。
3. PG user/org 归属 round-trip 补测试。

### 第三批：补边界与质量

1. `OrgService.add_member()` 明确是添加已有用户还是邀请。
2. 清 Ruff 生产代码问题。
3. 对 health/check、indexer、BM25、reranker 补异常/边界测试。
4. SessionStore 生产持久化。

## 10. 最终结论

当前代码主干大部分测试通过，失败不是扩散型问题，而是集中在“依赖边界”和“模块退役兼容”两类。

真正需要优先处理的隐藏/边际 bug 是：

1. RRF 与 BM25 重依赖耦合。
2. `set_roles` 缺目标用户 org 校验。
3. prod PG 缺依赖静默回退内存。
4. 创建用户无默认 membership 导致 PG org 归属丢失。

这些问题都能用小范围改动和定向回归测试落地，不需要大改架构。
