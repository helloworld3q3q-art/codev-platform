# CLAUDE.md — codev-platform

> AI 协作工具栈本体仓。通过 `pip install -e .` 被各业务仓引用。

---

## 0. 核心协议(不可漂移)

1. **改文件前**:任务分级 + MCP 选型见 `.claude/rules/workflow.md`
2. **门禁声明**:首次修改前一句话说明 — 触及层 / 适用规则 / 关键约束 / 验证方式
3. **commit 不带 AI 痕迹**:禁 `Co-Authored-By: Claude` / `Generated with`(详见 `.claude/rules/commit-pr-conventions.md`)
4. **PowerShell 脚本**:`.ps1` 禁含非 ASCII;读中文 MD/JSON 必须 `-Encoding UTF8`
5. **MCP 优先 grep+Read**:按 `.claude/rules/workflow.md §3.2` 任务映射
6. **改完同步业务仓 shim**:业务仓内有 thin shim re-export 自本仓,新增 symbol 要在 shim 加导出

---

## 一、定位

codev-platform = 多项目 AI 协作工具栈基础设施。

- **真值源仓** — `tools/_platform/`、`tools/claude-platform/`、`tools/chroma/{mcp_server,bm25_index}.py` 这些在 platform 业务仓内全是 shim re-export 自本仓(cross_link 已退役删除; 统一图谱 graph 走 SSE 服务, 不经 shim)
- **pip 安装** — `pip install -e D:\WorkSpace\codev-platform` 让 chroma .venv / system python 都能 `import codev_platform.*`
- **CLI 入口** — `codev-platform` console script,6 子命令(init / current / list-projects / validate / sync-rules / sync-skills / config)
- **跨项目共享数据** — `~/.codev-platform/config.json` 单一用户级配置驱动(模型 / 数据基目录 / daemon port / search 参数)

---

## 二、模块组织

```
codev_platform/
├── core/
│   ├── project_id.py   project_id resolver (env > .claude/project.json > 硬失败 + X-Project-Id header)
│   ├── paths.py        data_root / chroma_dir / chroma_collection_name / codegraph_db_path
│   └── config.py       ~/.codev-platform/config.json 加载 (env > config > default)
├── chroma/
│   ├── server.py       multi-tenant MCP daemon (870+ 行, contextvar 路由 + GPU semaphore)
│   ├── bm25.py         BM25 倒排索引 (jieba + rank_bm25 + RRF)
│   └── __init__.py
├── graph/              统一图谱 (替代退役的 cross_link; 跨层血缘 + A1 业务域)
│   ├── store.py        per-project sqlite 存储 (nodes/edges/evidences/findings)
│   ├── impact.py       跨层 BFS + A1 查询 (find_table_usage/find_api_callers/search_nodes...)
│   ├── mcp_server.py   统一图谱 MCP server (第5端点 18092, 8 工具)
│   ├── plugins/        各栈扫描插件 (frontend/backend/sql → GraphNode/Edge)
│   └── analyzers/      A1 业务域软节点 (LLM labeler, second post-pass)
├── cli.py              codev-platform <subcommand>
└── __init__.py

platform_meta/projects/   各 project 登记表 (meta.json)
codev_platform/resources/rules/    跨项目通用规则 (真值源;2026-06-01 relocate 进包, wheel 可交付)
codev_platform/resources/skills/   跨项目通用 skill (真值源;同上)
docs/plans/               team-deploy 系列 design / pr-body / 原 plan
config.example.json       用户级 config 样板 + 字段说明 (仍在仓根, 纯样本)
pyproject.toml            pip 包定义 (package-data 含 resources/**)
```

> **2026-06-01 relocate(audit #2)**:`rules/` `skills/` 从仓根移入 `codev_platform/resources/`,
> 使普通 `pip install`(非 editable)的 wheel 也带得走 → `sync-rules`/`sync-skills` 在客户机可用。
> CLI 经 `importlib.resources` 定位(editable + wheel 通用),旧仓根布局仍作 fallback。

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

`.claude/rules/` 和 `.claude/skills/` 是 sync 后副本(真值源在 `codev_platform/resources/rules/` 和 `codev_platform/resources/skills/`,2026-06-01 relocate 进包)。

改规则 / skill 时:
1. **改真值源** — 改 `codev_platform/resources/rules/<name>.md` 或 `codev_platform/resources/skills/<name>/SKILL.md`
2. **本仓 sync** — `codev-platform sync-rules` + `sync-skills` 重生 `.claude/` 副本
3. **推送其它业务仓** — 各业务仓跑同样 sync 命令 / 或后续机制(submodule / chroma 双扫)

---

## 五、跨仓改动协议

> **2026-05-28 平台所有权翻正后**:venv + 数据 + launcher 全归本仓(`.venv` / `data/` / `tools/`)。daemon 直接从本仓 `.venv` 跑,改源码即生效(本仓 editable 装在本仓 venv)。

改 codev-platform 代码时:

1. **本仓改完 commit + push**
2. **本仓 venv 已是 editable** —— 改 `codev_platform/*.py` 源码即生效,无需重装。仅新增重依赖时才 `uv pip install --python .venv\Scripts\python.exe -r requirements-runtime.txt`
3. **业务仓 shim 文件**(tools/_platform/ 等)— 若加了 symbol 要同步加 re-export 行(业务仓仍 `pip install -e` 本仓到它自己的 venv)
4. **重启 daemon**(daemon 持旧代码时):`Stop-Process -Id (Get-NetTCPConnection -LocalPort 18083 -State Listen).OwningProcess -Force`,下个 Claude 会话自动从新 launcher 重起

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

# 索引自身仓 (codev-platform__platform_docs collection) — daemon 用本仓 .venv
.venv\Scripts\python.exe -m codev_platform.chroma.indexer --force
```

---

## 七、配置真值源

`~/.codev-platform/config.json`(用户级)— 字段全集见 `config.example.json`。

env > config > 代码默认。换机器只改 config,代码不动。

---

## 八、不要改

- `.venv/`(本仓根,heavy ML 依赖 4.66GB,gitignored;**2026-05-28 平台所有权翻正**后归本仓自有,从 `requirements-runtime.txt` 重建,见 `docs/plans/roadmap-2026-05-28/platform-ownership-inversion-2026-05-28.md`)
- `data/`(本仓根,chroma collection + graph_store/<pid>.sqlite 运行态,gitignored;多租户共享,按 project_id 隔离)
- 各业务仓 `.claude/project.json`(各仓自己写,本仓 CLI 只 init / 不远程改)
- 用户 `~/.codev-platform/config.json`(用户主权,本仓代码不主动覆盖,只通过 CLI `config init` 或 `--force`)
