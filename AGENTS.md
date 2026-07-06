# AGENTS.md - codev-platform

> Codex 协作入口。`CLAUDE.md` 和 `.claude/**` 仅作为迁移期兼容副本保留；新的 Codex 工作流以本文件、`.codex/rules/`、`.codex/skills/` 为准。
>
> 不要在入口文件内展开全部规则。改动前按任务类型读取 `.codex/rules/workflow.md`，再按映射读取对应规则。

---

## 核心协议

1. **改文件前**：先读 `.codex/rules/workflow.md` 的任务分级和任务映射，按本次任务读取对应规则。
2. **先说明门禁**：首次修改前说清楚触及层、适用规则、关键约束、验证方式。
3. **不回滚用户改动**：发现未知 dirty 文件时，先判断是否相关；相关且冲突再问。
4. **MCP-first**：查代码结构先用 `codegraph`，查规则/设计/事故先用 `platform-docs`，查跨层影响先用 `graph`，跨会话记忆用 `agent-memory`。
5. **本地搜索是兜底**：只有未提交改动、索引滞后、MCP 不可用、需要精确核对真实文件时，才用本地搜索/读取兜底，并说明原因。
6. **禁改默认范围**：`.venv/`、`data/`、`.codegraph/`、`node_modules/`、构建产物、用户级 `~/.codev-platform/config.json` 默认不手改。
7. **生成物纪律**：`web-ui/src/services/**` 优先由 `pnpm run api` 生成；确需手改先说明原因。
8. **安全纪律**：不输出明文 token/API key/DSN；commit message 不带 AI 痕迹。
9. **PowerShell 编码**：读中文 MD/JSON/log 使用 `-Encoding UTF8`；`.ps1` 保持 ASCII。
10. **Claude 兼容层**：不要删除 `.claude/**` 或 `CLAUDE.md`；它们不是新的维护入口。

---

## 规则索引

| 规则 | 用途 |
|---|---|
| `.codex/rules/workflow.md` | 本仓任务分级、MCP-first、改前影响面、验证矩阵 |
| `.codex/rules/ai-tools-mcp.md` | MCP 工具选型、Codex `/mcp` 接入、故障排查 |
| `.codex/rules/agent-provider-architecture.md` | Agent provider/loop 架构 |
| `.codex/rules/code-quality-discipline.md` | 代码质量红线 |
| `.codex/rules/commit-pr-conventions.md` | commit/PR 约定与 AI 痕迹禁令 |
| `.codex/rules/file-discipline.md` | 文件规模、目录归位、文档归档 |
| `.codex/rules/verification-checklist.md` | 改后验证清单 |
| `.codex/rules/security.md` | 敏感信息、权限、令牌安全 |
| `.codex/rules/windows-powershell.md` | Windows/PowerShell 注意事项 |
| `.codex/rules/weekly-iteration-cadence.md` | roadmap / 日报 / 迭代节奏 |

`.codex/skills/` 是项目内 Codex skill 副本。若当前会话未自动列出某个 skill，就把对应 `SKILL.md` 当作普通流程文档按需读取。`.codex/hooks/` 仅保留迁移说明；Codex 当前不自动执行 Claude hook。

---

## 项目定位

`codev-platform` 是多项目 AI 协作工具栈基础设施：

- `project_id` 解析与多租户隔离
- Chroma / platform-docs 文档检索
- codegraph 符号与调用图
- graph 统一图谱和跨层影响分析
- agent-memory 分层记忆
- FastAPI Web 后端和 `web-ui` 管理控制台
- `codev-platform` CLI、onboard、sync、reindex、serve-mcp、ops 工具

---

## 主要目录

| 路径 | 说明 |
|---|---|
| `codev_platform/core/` | config、paths、project_id、ACL/RBAC、HTTP 基础件 |
| `codev_platform/cli.py` / `cli_cmds/` | CLI parser 和子命令 |
| `codev_platform/chroma/` | platform-docs / Chroma daemon / embedding / rerank |
| `codev_platform/codegraph/` | codegraph MCP/服务封装 |
| `codev_platform/graph/` | 统一图谱、ingest、impact、store、MCP |
| `codev_platform/agent/` | Agent loop、provider、tools、memory、session |
| `codev_platform/web/` | FastAPI 管理后台 API |
| `codev_platform/ops/` | onboard、health、backup、metrics、gateway、reindex ops |
| `codev_platform/resources/` | 跨项目分发真值源，改动需格外谨慎 |
| `web-ui/` | React/Umi Max/Ant Design 管理控制台 |
| `.codex/` | Codex 入口规则、skills、hooks 副本 |
| `.claude/` | Claude 迁移期兼容副本，不作为新维护入口 |

---

## 常用验证

```powershell
python -m pytest tests/test_cli_parser.py
python -m pytest tests/test_sync_hooks.py tests/test_resources_packaging.py
python -m pytest tests/test_onboard.py
python -m pytest tests/test_graph_impact.py tests/test_web_token_service.py
npm --prefix web-ui run tsc
npm --prefix web-ui run lint:js
codex mcp list
```

按变更面选择最小验证，不默认跑全量长任务。
