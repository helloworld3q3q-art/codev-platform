# codev-platform

Multi-project AI 协作工具栈基础设施。让多个业务项目共享 project_id 命名空间 + chroma / graph(统一图谱)多租户索引 + GPU 模型 daemon,跨机器 portable。

## 新机器接入(零冷启 SOP)

> 🔒 **私有仓** — git clone 需 collaborator 邀请 + SSH/token 凭证。
>
> **前置工具**(需在 PATH):
> - `uv`(Python 包管理):`irm https://astral.sh/uv/install.ps1 | iex`(PowerShell)
> - Claude Code CLI(`claude` 命令):Anthropic 官方,自行安装
> - `mcp-proxy`(stdio↔SSE 桥接):由 chroma .venv 的 `uv sync` 自动装,无需手动
> - **三仓必须 clone 到同一父目录**(`.mcp.json` 用 `..\platform` 相对路径解析兄弟仓)
> - **模型自备**:Qwen3-Embedding/Reranker 放 `~/models/`,不放则自动走仓内 MiniLM fallback

```powershell
# 1. clone 三仓到【同一父目录】(路径任选名字, 但必须并列)
cd ~/Code            # 或 D:/WorkSpace, 任选
git clone <platform>.git
git clone <codev-platform>.git
git clone <codev-platform-widget>.git   # 可选, 仅 tray UI

# 2. 装 codev-platform CLI (system python)
pip install -e ./codev-platform

# 3. 一键接入 (preflight + 装 venv + pip install -e 进 venv + 写 config + sync rules/skills)
codev-platform setup --auto
#  耗时 ~8 分钟 (主要 torch / chromadb / sentence-transformers 装 venv)
#  缺 uv / claude CLI / venv 会明确报 MISSING + 修复命令, 不静默

# 4. (可选) 放 Qwen3 模型到 ~/models/Qwen3-Embedding-0.6B; 不放走 MiniLM fallback

# 5. 任一仓开 Claude Code, daemon 自动 spawn — END
```

## 状态

- ✅ project_id resolver + paths + config
- ✅ chroma daemon multi-tenant (本仓 codev_platform/chroma/)
- ✅ graph(统一图谱)engine + linker(原 cross-link 2026-06-05 退役并入)
- ✅ CLI 7 子命令: init / current / list-projects / validate / sync-rules / sync-skills / setup / config
- ✅ 三仓 .mcp.json 全用相对路径, 跨机 portable
- ✅ platform_meta 跨项目登记表

## 用法

### 一次性配置(用户级,跨项目共享)

机器级路径(GPU 模型 / 数据目录 / daemon 端口等)走 **`~/.codev-platform/config.json`**:

```powershell
codev-platform config init    # 写默认到 ~/.codev-platform/config.json
codev-platform config show    # 看当前生效配置
codev-platform config path    # 只打印文件位置
```

样板字段全集见仓根 [`config.example.json`](config.example.json)(每个字段含 `_comment` 说明)。**优先级:env var > config 文件 > 代码默认 hardcode**。

换机器场景:复制 codev-platform 仓后 → `pip install -e .` → `codev-platform config init` → 改 `~/.codev-platform/config.json` 里 model/data 路径。不再到处改 .cmd / mcp_server.py 里的 hardcode。

### 业务项目接入

```powershell
cd <your-business-repo>
codev-platform init <your-project-id>     # 写 <repo>/.claude/project.json
codev-platform current                     # 验证 resolver
codev-platform list-projects               # 看登记表
```

## project_id 解析顺序

1. 环境变量 `PLATFORM_PROJECT_ID`(显式覆盖)
2. `<cwd>/.claude/project.json`(向上查找仓根)
3. **硬失败 + 修复指引**(不静默 fallback)

## 路径约定

```
<PLATFORM_DATA_DIR or repo/data>/
├── chroma/                              # 单 DB 多 collection
│   └── <project_id>__platform_docs      # collection 前缀隔离
├── codegraph/<project_id>/codegraph.db  # per-project (第三方 MCP server 天然 per-repo)
└── codegraph_ext/<project_id>/                    # graph 统一图谱 KG, per-project
```

## 关联

- 量化业务仓:[helloworld3q3q-art/platform](https://github.com/helloworld3q3q-art/platform)(原宿主)
- Widget UI:[helloworld3q3q-art/codev-platform-widget](https://github.com/helloworld3q3q-art/codev-platform-widget)
- 架构 design doc:[`docs/plans/team-deploy-2026-05-27-design.md`](docs/plans/team-deploy-2026-05-27-design.md)

## 渐进抽离进展

| 模块 | 抽离状态 |
|---|---|
| `codev_platform/core/` (project_id + paths) | ✅ 已抽 |
| `codev_platform/cli.py` | ✅ 已抽 |
| `platform_meta/` | ✅ 已抽 |
| `docs/plans/team-deploy-*` | ✅ 已抽 |
| `tools/chroma/` (multi-tenant daemon) | ⏸️ platform 仓内, 待抽 |
| `codev_platform/graph_store/` (统一图谱) | ✅ 已抽 (原 tools/cross_link 退役并入) |
| `.claude/rules/` 跨项目通用 (workflow / commit / windows-ps 等) | ⏸️ platform 仓内, 待抽 |
| `.claude/skills/` 跨项目通用 | ⏸️ platform 仓内, 待抽 |

抽离原则:**已抽模块的 platform 仓副本暂不删**,双份共存,直至本仓验证稳定再清理。
