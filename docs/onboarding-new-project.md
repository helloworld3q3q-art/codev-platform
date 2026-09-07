# 接入新项目到 codev-platform

> 把**任意一个你自己的项目**接进 codev-platform 的代码智能 MCP(代码图谱 / 文档检索 / 全栈链路 / 代码向量召回)。
> **一条 `onboard` 命令搞定**,不用手敲那 8 步。
> 前提:codev-platform 本体已在本机/服务器装好(`.venv` 装了 `[runtime]`、嵌入模型在、`codegraph` 已装)。

---

## 模型(30 秒)

codev-platform 是**装一次、多项目共用**的平台,按 `project_id` **多租户隔离**:

- 每个项目仓根放 `.claude/project.json`(一个 `project_id`),各项目索引/图谱互不串。
- 平台 MCP 走 **SSE 服务**(`serve-mcp` 常驻拉起 4 端点),业务仓 `.mcp.json` 用 `type:sse` 连端点 —— 多机/多人共享同一平台。
- 索引(chroma/codegraph/code_vec/graph)由平台 **reindex worker** 后台串行构建。

---

## 一键接入

在平台机器上(能读到你的项目仓路径)跑:

```bash
codev-platform onboard <project-id> --repo <你的项目仓路径>
```

`onboard` **一条命令自动完成 8 步**(每步幂等,失败可重跑;软步失败 warn 不阻断):

| 步 | 做什么 |
|---|---|
| 1 | `config.projects.<id>` 写 repo_path + org_id(reindex worker 据此解析仓) |
| 2 | `<repo>/.claude/project.json`(project_id 真值源) |
| 3 | `platform_meta/projects/<id>/meta.json`(list-projects / web 列表可见) |
| 4 | RBAC:project 挂 org + 授 owner admin(无 PG 单机自动跳过) |
| 5 | **sync rules/skills/hooks → 业务仓 `.claude/`**(平台规则/skill/MCP-first 护栏) |
| 6 | codegraph:写 `.codegraph/config.json`(排噪声)+ gitignore db + `codegraph init` |
| 7 | **生成 `.mcp.json`**(指向平台 SSE 端点,4 套多租户)|
| 8 | reindex 入队(graph ingest + codegraph + code_vec + chroma,worker 后台跑)|

**可选参数**:
- `--org <org>` / `--owner <user>` / `--name "<显示名>"`
- `--mcp-source platform|local`(默认 `platform`=平台服务器端点;`local`=本机本地实例)
- `--no-index`(只登记不入队索引)

---

## 接入后(3 件事 + 验证)

1. **提交配置进仓**(随 git 走,别人/重 clone 也带得上):
   ```bash
   git add .claude/project.json .claude/rules .claude/skills .claude/hooks .claude/settings.json \
           .codegraph/config.json .gitignore .mcp.json
   ```
2. **看索引进度**:`codev-platform reindex-queue status`(worker 串行消费;codegraph/code_vec 大仓需几分钟)。
3. **起平台 MCP 端点**(首次 / 重启电脑后跑一次):
   ```bash
   codev-platform serve-mcp start      # 拉起 4 端点(chroma 预热 ~30-60s)
   codev-platform serve-mcp status     # 确认全 OK
   ```
4. **token 模式**(多人/远程):给 `.mcp.json` 各 server 补 `headers.Authorization: "Bearer <token>"`
   (token 由 `codev-platform gateway pg-token-add` 签发;**header 形式,不要只用 `?token=`** —— 见记忆/排错)。
5. **验证**:重启 Claude Code(Reload Window,不是 /clear)→ `/mcp` 4 套应 **connected**,检索的是你这个项目的内容。

---

## 说明 / 边界

- **doc_patterns**:默认只扫 `CLAUDE.md` / `README.md` / `docs/**`。设计文档在别处(`PRD.md` / `prompts/*.md`)→ 建 `<仓>/.claude/index.json`:
  ```json
  { "doc_patterns": ["*.md", "docs/**/*.md", "prompts/*.md", ".claude/rules/*.md"] }
  ```
- **graph(统一图谱)**:提供前端↔接口↔表跨层链路(`find_table_usage`/`find_api_callers`/`search_nodes` 等),适用全栈项目(Java+Flyway+前端 / Python+FastAPI)。纯文档项目用不上,调了会报图谱数据不存在 —— onboard 仍会建,空着无害。
- **数据隔离**:`~/.codev-platform/config.json` 的 `data.platform_data_dir` 决定索引落哪;多项目按 `project_id` 隔离。
- **日常维护**:改完 commit 自动重建(装了 post-commit hook),或手动 `reindex-queue enqueue <id> --kind all`。
- **多仓项目**:前端/后端分仓的项目,主仓 `meta.json` 加 `extra_repos: ["<另一仓 pid>"]`(graph 跨仓连边;codegraph/code_vec 多根 fan-out 见 roadmap-2026-06-14 P3)。

---

## 排错

| 现象 | 处理 |
|---|---|
| `/mcp` 全红(连不上端点)| 平台端点没常驻 → `codev-platform serve-mcp start`(重启电脑后必跑一次)|
| 单独某 server 红 | `serve-mcp status` 看哪个 down;codegraph 端点需 ~2-5s 起 |
| `/mcp` 401(token 模式)| `.mcp.json` 各 server 缺 `headers.Authorization`(Bearer),`?token=` 不够 |
| `current` 报无法解析 project_id | 不在仓根 / 没跑 onboard → 确认 `<repo>/.claude/project.json` 在 |
| search 返回空 | 索引还没建完(`reindex-queue status`)/ doc_patterns 没覆盖(建 `.claude/index.json`)|
| codegraph 索引空 | `which codegraph` 确认已装;onboard 的 `[6/8]` 看 init 是否 OK |

> **从零装平台机器**见 [onboarding-mac.md](onboarding-mac.md) / [ai-toolchain-guide.md](ai-toolchain-guide.md)。本文只讲"平台已就绪后,接入一个新项目"。
