# Platform Docs 过滤零命中诊断优化计划（2026-07-29）

> 状态：✅ 已完成

## 目标

修复 `search_docs` 在显式 `category` / `module` 过滤导致零命中时只返回空数组、容易被误判为
“索引未同步”的可观测性缺口。保持过滤语义和现有成功响应兼容，不静默放宽过滤，不触发任何索引重建。

## 范围

- `codev_platform/chroma/`：查询结果与 MCP 工具说明。
- `tests/`：过滤零命中、兼容响应和提示内容的定向回归。
- `.codex/rules/ai-tools-mcp.md`：补充 roadmap / 子模块查询边界及空结果复核门禁。
- `codev_platform/resources/rules/ai-tools-mcp.md`：已确认属于跨项目通用语义，同步更新分发真值源；
  本轮不执行对消费仓的 `sync-rules`。

## 适用规则

- `.codex/rules/workflow.md`
- `.codex/rules/ai-tools-mcp.md`
- `.codex/rules/code-quality-discipline.md`
- `.codex/rules/file-discipline.md`
- `.codex/rules/verification-checklist.md`

## 关键约束

1. `module` 继续表示物理路径归属，不把内容语义猜测混入现有单值字段。
2. 过滤零命中时给出显式、机器可辨识的 `filter_miss` 诊断，但不把放宽过滤后的文档冒充正式结果。
3. 保持正常命中结果和第一内容块契约兼容；不增加未接线参数或重复真值源。
4. 首次实现阶段不执行 reindex、数据库修改、服务重启或运行时发布；用户授权的续做只允许官方薄发布和
   目标 scope 增量恢复，仍禁止全量重建。
5. 不修改 `.claude/**` 兼容副本，除非后续证明确有必要并另行登记。

## 有序步骤

1. 核对 `search_docs` schema、查询实现、日志和现有测试契约。
2. 选定最小兼容的空结果诊断载体，并补单元测试锁定行为。
3. 实现诊断与工具描述，确保显式过滤仍严格生效。
4. 在本仓 MCP 规则中补充路径归属语义和空结果复核流程。
5. 运行定向 pytest、静态检查和必要的 MCP 级小样本验证。
6. 回写验证证据和完成状态。

## 验证命令

```powershell
python -m pytest tests/test_chroma_search_schema.py tests/test_chroma_filter_miss.py tests/test_chroma_gpu_op_timeout.py
python -m pytest tests -k "chroma and (search or filter)"
python -m pytest tests -k chroma
python -m pytest tests/test_resources_packaging.py tests/test_cli_parser.py
python -m compileall -q codev_platform/chroma
python -m ruff check codev_platform/chroma/_schema.py codev_platform/chroma/_tools.py
git diff --check
```

## 当前证据

- `openclaw-stock` 目标 roadmap 文档已存在于 Chroma；初始诊断时精确 `get_by_file` 返回 6 chunks，
  证明文档并非缺失。最终重启后的实时读取返回 3 chunks；chunk 数不是本任务兼容契约，非空才是门禁。
- WSL 数据 owner 侧 manifest 显示 Chroma 对齐 HEAD，排除索引滞后。
- 2026-07-29 21:31 的召回日志显示：
  - `category=dev_log,module=stock-pipeline` → 0；
  - `category=design,module=stock-pipeline` → 0。
- 当前分类实现把 `docs/architecture/roadmap-*` 标为 `category=design,module=platform`；现有 where
  构造对两个字段执行精确 `$and`。

## 实现结果

- 正常命中继续只返回原有 JSON 数组内容块。
- 显式过滤后零命中时，第一个内容块仍为 `[]`，第二个内容块追加：
  - `code=filter_miss`；
  - 原始 `requested_filters`；
  - 逐步放宽过滤的 `suggested_retries`；
  - `list_collections` / `get_by_file` 复核提示。
- 诊断不执行第二次查询、不返回放宽过滤后的文档；召回日志增加
  `diagnostic=filter_miss`，便于后续统计误用。
- MCP schema、内置 Agent 工具说明、本仓规则和跨项目分发规则源均已明确：
  `module` 是物理路径归属，不是内容主题。

## 验证结果

- `test_chroma_search_schema.py + test_chroma_filter_miss.py + test_chroma_gpu_op_timeout.py`：
  **11 passed**。
- `tests -k "chroma and (search or filter)"`：**6 passed，2 skipped**。
- `tests -k chroma`：**162 passed，3 skipped**。
- `test_resources_packaging.py + test_cli_parser.py`：**21 passed**。
- `compileall`、目标文件 Ruff、`git diff --check`：全部通过。
- 回归测试明确断言：过滤错配只执行一次查询；正常命中和无过滤空结果保持原响应契约。

## 首次实现阶段的运行态边界

首次实现阶段未部署、未重启 MCP 服务、未执行 reindex 或 `sync-rules`。随后用户明确授权 commit、push、
重启和真实 MCP 验收，续做结果记录如下。

## 2026-07-29 提交、发布与运行态验收续做

用户已明确授权 commit、push、重启并做真实 MCP 验证。本续做仍沿用本计划，不创建第二份任务真值。

### 影响面与回退

- 等级：L4；涉及 Git 远端、post-commit 增量队列、WSL 受管 runtime 和 platform-docs MCP。
- 推送范围：仅当前 `dev` 分支到既有 `origin/dev`；不推送其它远端或分支。
- 禁止项：不执行全量 reindex、数据库重建、base / 依赖重建或手工覆盖受管运行文件。
- 回退：Git 代码以提交前 `a41e47b` 为基线；运行态使用既有受管 release/controller 的正式回滚能力，
  不直接修改 current 链接、systemd unit 或数据目录。

### 有序步骤

1. 复核工作树和测试证据，生成中文且无 AI 痕迹的 commit。
2. 校验 post-commit hook、scope 和 reindex 状态。
3. 仅推送 `origin/dev`。
4. 按仓库既有 WSL 薄发布 / promotion 流程发布该提交并重启受影响 MCP 服务。
5. 从 WSL owner 侧核对 unit、runtime commit 和 Chroma manifest。
6. 真实调用 `openclaw-stock`：
   - `category=design,module=stock-pipeline` 必须返回首块 `[]` + `filter_miss`；
   - `category=design,module=platform` 必须正常命中且不追加诊断；
   - `get_by_file` 必须继续返回既有 chunks。
7. 回写 commit、push、release、服务与 MCP 验收证据后关闭计划。

## 2026-07-29 发布与运行态验收结果

### Git 与发布

- 实现提交：`1cfde28ed71cd9f170d66af2fe17cad89d344128`
  （`fix(chroma): 增加文档过滤空结果诊断`）。
- post-commit 正常登记 `chroma/code_vec/codegraph/ingest` scope；提交仅推送到 `origin/dev`，
  本地、远端和 WSL 服务仓目标提交一致。
- 官方 `runtime promote --target-revision` 成功：
  - active release：`a34853a5c3856ea4569697cd9f5c4c46d34aa98dcf907985615833da9fce8045`；
  - runtime revision：`1cfde28ed71cd9f170d66af2fe17cad89d344128`；
  - rollback release：`c529eb36789e51e9fd3dad2eccf426379d824c1c62e319a15d4a2dbea6ca7d93`；
  - 复用既有 base `1200f0e3edf1ba7205e0f6bda615b1923587bee0ee486fa086f16376c74b37e3`，
    未下载依赖、未重建 base 或数据库。
- 发布前严格门禁发现旧 root Python 调用遗留 21 个 `0755` `__pycache__` 目录和 258 个可证明
  生成的 `.pyc`。唯一允许的官方恢复链已成功执行：
  `reindex-maintenance prepare/status` → `runtime access-repair-current --yes` →
  `maintenance-stage` → `configure/restore/resume-codegraph`。未手工 `chmod/chown`、未裸启停 unit。

### 索引与健康

- 首次 hook 的 Chroma 增量任务在删除旧 chunk 时遇到瞬态 compaction purge 错误；既有 collection
  保持可用，其余 `code_vec/codegraph/ingest` 已成功。
- 薄发布重启后只重投 `chroma` scope；正常增量 runner 用时 `110.38s`，处理 231 个文档、
  写入 3738 chunks，最终 `chroma=ok/fresh=true`。未使用 force、未执行 `reindex all`，
  其余三个 scope 未重投。
- 新 release 下 8 个受管服务均为 active，四个 MCP endpoint 均为 OK，worker 空闲。
- 发布后 `health --mode light` 为 `all checks green`；`dirty-check --json` 返回
  `{"dirty": false, "affected": {}}`。

### 真实 MCP 传输层验收

- 通过 `openclaw-stock` 的 Streamable HTTP MCP 端点完成初始化，协议版本
  `2025-11-25`；线上 tool schema 已说明路径归属语义和 `filter_miss`。
- `category=design,module=stock-pipeline`：
  - 调用成功且非 MCP error；
  - 第一内容块严格为 `[]`；
  - 第二内容块 `code=filter_miss`，保留原始 filters；
  - 建议顺序严格为 `design/all` → `all/stock-pipeline` → `all/all`。
- 同一查询改为 `category=design,module=platform`：只返回一个正常结果块，命中 10 条且没有诊断；
  前三条覆盖评分影子观察和规则告警治理 roadmap。
- 精确 `get_by_file` 返回 3 chunks，证明正常读取链未被诊断扩展破坏。
- 全部实时断言通过；空结果根因已从“索引滞后”明确收敛为“显式路径过滤错配”。
