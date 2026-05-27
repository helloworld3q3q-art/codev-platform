# CLAUDE.md — codev-platform

> AI 协作工具栈本体仓。和量化业务仓 `platform` 平行,通过 `pip install -e .` 被各业务仓引用。

---

## 0. 核心协议(不可漂移)

1. **改文件前**:任务分级 + MCP 选型见 `.claude/rules/workflow.md`(从 platform 同步过来,跨项目通用)
2. **门禁声明**:首次修改前一句话说明 — 触及层 / 适用规则 / 关键约束 / 验证方式
3. **commit 不带 AI 痕迹**:禁 `Co-Authored-By: Claude` / `Generated with`(详见 `.claude/rules/commit-pr-conventions.md`)
4. **PowerShell 脚本**:`.ps1` 禁含非 ASCII;读中文 MD/JSON 必须 `-Encoding UTF8`
5. **MCP 优先 grep+Read**:找代码 / 文档 / 业务链路按 `.claude/rules/workflow.md §3.2` 任务映射
6. **本仓改完务必同步 platform 仓的 shim**:platform/tools/_platform/、tools/chroma/{mcp_server,bm25_index}.py、tools/cross_link/{schema,mcp_server}.py 是本仓的薄壳 re-export,新增 symbol 要在 shim 加导出

---

## 一、定位

codev-platform = 多项目 AI 协作工具栈基础设施。

- **真值源仓** — `tools/_platform/`、`tools/claude-platform/`、`tools/chroma/{mcp_server,bm25_index}.py`、`tools/cross_link/{schema,mcp_server}.py` 这些在 platform 业务仓内全是 shim re-export 自本仓
- **pip 安装** — `pip install -e D:\WorkSpace\codev-platform` 让 chroma .venv / system python 都能 `import codev_platform.*`
- **CLI 入口** — `codev-platform` console script,6 子命令(init / current / list-projects / validate / sync-rules / sync-skills / config)
- **跨项目共享数据** — `~/.codev-platform/config.json` 单一用户级配置驱动(模型 / 数据基目录 / daemon port / search 参数)

---

## 二、模块组织

```
codev_platform/
├── core/
│   ├── project_id.py   project_id resolver (env > .claude/project.json > 硬失败 + X-Project-Id header)
│   ├── paths.py        data_root / chroma_dir / chroma_collection_name / cross_link_db_path
│   └── config.py       ~/.codev-platform/config.json 加载 (env > config > default)
├── chroma/
│   ├── server.py       multi-tenant MCP daemon (870+ 行, contextvar 路由 + GPU semaphore)
│   ├── bm25.py         BM25 倒排索引 (jieba + rank_bm25 + RRF)
│   └── __init__.py
├── cross_link/
│   ├── schema.py       sqlite schema + open_db / upsert_node / set_meta
│   ├── server.py       MCP server (find_endpoint_link / find_table_refs / search_nodes / cross_link_stats)
│   ├── query.py        full-stack chain 查询 Python API
│   └── __init__.py
├── cli.py              codev-platform <subcommand>
└── __init__.py

platform_meta/projects/   各 project 登记表 (meta.json)
rules/                    跨项目通用规则 (11 条, 真值源)
skills/                   跨项目通用 skill (3 个, 真值源)
docs/plans/               team-deploy 系列 design / pr-body / 原 plan
config.example.json       用户级 config 样板 + 字段说明
pyproject.toml            pip 包定义
```

---

## 三、当前 project 接入清单

`platform_meta/projects/` 现登记 3 项目:

| project_id | 仓 | 状态 |
|---|---|---|
| `openclaw-stock` | helloworld3q3q-art/platform | 真业务,4427 chunks chroma |
| `codev-platform` | helloworld3q3q-art/codev-platform(本仓)| 工具栈本体,290 chunks |
| `codev-platform-widget` | helloworld3q3q-art/codev-platform-widget | Tray UI 开发中 |

---

## 四、规则 + Skill 同步策略

`.claude/rules/` 和 `.claude/skills/` 是 sync 后副本(真值源在仓根 `rules/` 和 `skills/`)。

改规则 / skill 时:
1. **改真值源** — 改 `rules/<name>.md` 或 `skills/<name>/SKILL.md`
2. **本仓 sync** — `codev-platform sync-rules` + `sync-skills` 重生 `.claude/` 副本
3. **推送其它业务仓** — 各业务仓跑同样 sync 命令 / 或后续机制(submodule / chroma 双扫)

---

## 五、跨仓改动协议

改 codev-platform 影响 platform 业务仓时:

1. **本仓改完 commit + push**
2. **platform 仓 chroma .venv `uv pip install -e D:\WorkSpace\codev-platform`** 拉新版(自动用 -e 软链,改源码即生效,不需重装)
3. **platform 仓 shim 文件**(tools/_platform/、tools/chroma/{mcp_server,bm25_index}.py 等)— 若加了 symbol 要同步加 re-export 行
4. **重启 daemon**:`Stop-Process -Id (Get-NetTCPConnection -LocalPort 18083).OwningProcess -Force`(否则 daemon 持旧代码)

---

## 六、常用命令

```powershell
# CLI 自测
codev-platform current
codev-platform list-projects
codev-platform config show

# 业务仓接入演练
cd <new-project-repo>
codev-platform init <new-project-id>
codev-platform sync-rules
codev-platform sync-skills

# 索引自身仓 (codev-platform__platform_docs collection)
$env:PLATFORM_ROOT = $pwd
D:\WorkSpace\platform\tools\chroma\.venv\Scripts\python.exe D:\WorkSpace\platform\tools\chroma\index_docs.py --force
```

---

## 七、配置真值源

`~/.codev-platform/config.json`(用户级)— 字段全集见 `config.example.json`。

env > config > 代码默认。换机器只改 config,代码不动。

---

## 八、不要改

- `tools/chroma/.venv/`(platform 仓内,heavy ML 依赖,不在本仓 — 本仓 0 deps)
- 各业务仓 `.claude/project.json`(各仓自己写,本仓 CLI 只 init / 不远程改)
- 用户 `~/.codev-platform/config.json`(用户主权,本仓代码不主动覆盖,只通过 CLI `config init` 或 `--force`)
