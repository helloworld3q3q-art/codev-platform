# codev-platform

Multi-project AI 协作工具栈基础设施。从 `helloworld3q3q-art/platform` 仓内嵌的 `tools/_platform/` + `tools/claude-platform/` + `platform-meta/` 抽出独立化,**目标**:让多个业务项目(量化 / Widget UI / 未来项目)共享同一套 project_id 命名空间 + chroma / cross-link 多租户索引,而不必各自重复造轮子。

## 状态:本地原型

- ✅ project_id resolver(env / `.claude/project.json` / 硬失败)
- ✅ paths 约定(chroma collection 前缀 / per-project DB 子目录)
- ✅ CLI:`init` / `current` / `list-projects` / `validate`
- ✅ platform_meta 跨项目登记表骨架
- ⏸️ chroma daemon multi-tenant(代码在 platform 仓 `tools/chroma/`,渐进抽出中)
- ⏸️ cross-link / codegraph 多租户(同上)
- ⏸️ pip 包发布(暂用 `pip install -e .` 本地装)

## 用法

### 安装

```powershell
pip install -e D:\WorkSpace\codev-platform
```

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
└── codegraph_ext/<project_id>/cross_layer.sqlite  # cross-link KG, per-project
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
| `tools/cross_link/` | ⏸️ platform 仓内, 待抽 |
| `.claude/rules/` 跨项目通用 (workflow / commit / windows-ps 等) | ⏸️ platform 仓内, 待抽 |
| `.claude/skills/` 跨项目通用 | ⏸️ platform 仓内, 待抽 |

抽离原则:**已抽模块的 platform 仓副本暂不删**,双份共存,直至本仓验证稳定再清理。
