# codev-platform Codex 工作流

本文档只约束 codev-platform 本仓的 Codex 工作。`.claude/**` 是迁移期兼容副本；新的规则入口和本仓私有规则以 `.codex/**` 为准。

---

## 1. 默认不读取

除非排查目标直接指向，不主动读取这些路径：

- 运行态 / 索引产物：`data/`、`.codegraph/`、`codev_platform/mcp_serve_logs/`
- 环境 / 缓存：`.venv/`、`__pycache__/`、`.pytest_cache/`、`.ruff_cache/`
- 构建产物：`build/`、`dist/`、`codev_platform.egg-info/`、`web-ui/dist/`、`web-ui/.umi/`、`web-ui/.umi-production/`
- 前端依赖：`web-ui/node_modules/`
- 大型或临时输出：`output/`、`*.png`、`*.zip`、`*.sqlite*`
- 锁文件：默认不读 `web-ui/pnpm-lock.yaml`，排查依赖时再定向读

---

## 2. 可读但默认禁改

| 范围 | 规则 |
|---|---|
| `.venv/`、`data/`、`.codegraph/` | 本地运行态，不手改；需要重建走 CLI / 脚本 |
| `~/.codev-platform/config.json` | 用户级配置，不主动覆盖 |
| `.mcp.json` | Claude 兼容 MCP 配置，改前说明影响面和回退 |
| `.codex/config.toml` | Codex MCP 配置，改前确认 token env 和重启要求 |
| `platform_meta/projects/*/meta.json` | 项目登记表，改前确认 project_id / repo / 端口 |
| `codev_platform/resources/{rules,skills,hooks}/` | 跨项目分发真值源，不塞本仓专属规则；改后验证 sync/package |
| `.codex/rules/` | 本仓 Codex 规则，可按本仓需要优化 |
| `.claude/**` | 迁移期兼容副本，除非用户明确要求，否则不改 |
| `web-ui/src/services/**` | 生成或半生成 API 层，优先走生成脚本 |

---

## 3. 任务分级

| 等级 | 适用范围 | 必做动作 |
|---|---|---|
| L1 小改 | 文案、注释、单文件测试、局部样式，不改接口/配置/分发资源 | `git status -s` + 定向读文件 + 最小验证 |
| L2 单层 | `codev_platform` 某模块、CLI 子命令、单个 MCP 服务、单个 web-ui 页面/组件 | `codegraph_context` 或定向搜索 + 目标测试 |
| L3 跨层 | CLI ↔ resources 分发、MCP server ↔ web/API、graph/chroma/codegraph 数据链、web-ui ↔ FastAPI 契约 | `platform-docs` + `codegraph` + 必要时 `graph` + 端到端验证 |
| L4 高风险 | MCP 配置、索引重建、依赖、认证/RBAC、长驻服务、跨仓 shim、发布/打包、Codex/Claude 迁移 | 先列影响面、回退方式、禁改项和串行资源 |

L1 命中 §2 默认禁改范围时，升级到 L3/L4。

---

## 4. MCP-first

Codex 已接入四套项目 MCP：

- `mcp__platform_docs__`：规则、设计、事故、日报、skill 文档检索
- `mcp__codegraph__`：符号、调用方、调用链、代码上下文
- `mcp__graph__`：统一图谱、跨层影响、前后端/表/业务域链路
- `mcp__agent_memory__`：跨会话项目记忆

优先级：

| 场景 | 首选 |
|---|---|
| “X 怎么工作 / 怎么改 / bug 可能在哪” | `codegraph_context` |
| 找类、函数、组件、调用方 | `codegraph_search` / `codegraph_callers` / `codegraph_callees` |
| 找规则、设计、事故复盘、历史决策 | `platform-docs search_docs` |
| 查 endpoint / 表 / 页面 / 组件跨层影响 | `graph find_impact` / `find_table_usage` / `find_api_callers` / `find_page_dependencies` |
| 开工或换主题 | `agent-memory recall` |

本地搜索/读取仅在这些场景兜底：

- 未提交改动可能未进索引
- 索引滞后或 MCP 不可用
- 需要核对刚编辑后的真实文件
- 需要查看 git diff / git log / 测试输出

使用本地搜索兜底时，在说明中写清楚原因，例如：`[本地搜索例外: 未提交改动未进索引]`。

---

## 5. 改前影响面

L2/L3/L4 修改前先压缩影响面：

```markdown
合并影响面:
- 等级:
- 文件范围:
- 规则/skill:
- MCP 结论:
- dirty-index / 真实文件:
- 禁改项 / 生成项:
- 验证:
```

首次修改前说一句：

`本次触及 <层>; 适用规则 <文件名>; 关键约束 <一句话>; 验证方式 <命令>`

---

## 6. 本仓任务映射

| 改动类型 | 必读 / 先查 | 最小验证 |
|---|---|---|
| CLI 子命令 / parser | `codegraph_context` + `codev_platform/cli.py` / `cli_cmds/*` | `python -m pytest tests/test_cli_parser.py` 或目标 CLI 测试 |
| `sync-rules` / `sync-skills` / `sync-hooks` | 本文件 + `cli_cmds/sync.py` + resources 目标 | dry-run + `tests/test_resources_packaging.py` / `tests/test_sync_hooks.py` |
| `.codex/rules` 本仓规则 | 本文件 + 相关规则 | 关键字残留扫描 |
| `codev_platform/resources/*` 分发真值源 | 确认是否跨项目通用 | 对应 `sync-*` + package-data 测试 |
| MCP 服务启动 / serve-mcp | `.codex/rules/ai-tools-mcp.md` + `codev_platform/ops` / `core` / `cli_cmds` | `codex mcp list` + 目标 tool 实调 |
| platform-docs / chroma | `ai-tools-mcp.md` + chroma 模块调用链 | 目标 pytest；涉及索引再跑小样本/dry-run |
| codegraph 集成 | `ai-tools-mcp.md` + codegraph 模块调用链 | codegraph 相关 pytest + status |
| graph 统一图谱 | `ai-tools-mcp.md` + graph/store/plugins/analyzers | graph 相关 pytest；必要时小仓样本扫描 |
| agent / memory / RBAC | `security.md` + agent/memory 调用链 | 目标 `tests/test_agent_*` / ACL 测试 |
| FastAPI / web 后端 | route/service 调用链 + security | 目标 pytest；契约改动补 web-ui 调用点检查 |
| web-ui 页面 / 组件 | `web-ui/AGENTS.md` + 目标组件上下游 | `npm --prefix web-ui run tsc` 或 `lint:js` |
| package / wheel / resources | `pyproject.toml` + importlib.resources 测试 | `python -m pytest tests/test_resources_packaging.py` |
| PowerShell / Windows 脚本 | `windows-powershell.md` | ASCII/UTF-8 检查 + dry-run |
| 文档 / 审计 | `file-discipline.md` + 目标 docs 目录规则 | 路径归位检查 + 引用检查 |
| Codex 迁移 | `AGENTS.md` + 本文件 + `ai-tools-mcp.md` | `codex mcp list/get` + 残留扫描 |

---

## 7. Codex / Claude 迁移边界

- 新入口：`AGENTS.md`
- 新规则：`.codex/rules/`
- 新 skill 副本：`.codex/skills/`
- 新 MCP 配置：`.codex/config.toml` 或用户级 `~/.codex/config.toml`
- 兼容入口：`CLAUDE.md`
- 兼容规则：`.claude/**`

迁移期只做双写/双读，不删除 `.claude/**`。新增规则优先放 `.codex/rules/`；确需支持旧 Claude 时，再同步到 `.claude/`。

---

## 8. 验证口径

优先跑目标测试。只有涉及共享底座、认证、图谱、索引、分发资源、包发布时，才扩大测试面。

常用命令：

```powershell
python -m pytest tests/test_cli_parser.py
python -m pytest tests/test_sync_hooks.py tests/test_resources_packaging.py
python -m pytest tests/test_onboard.py
python -m pytest tests/test_graph_impact.py
npm --prefix web-ui run tsc
codex mcp list
```

测试无法运行时，必须说明原因和剩余风险。
