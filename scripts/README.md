# codev-platform/scripts/

工具栈 PowerShell / sh 脚本模板。当前是 platform 仓 `tools/dev/` 的快照,**业务路径未完全参数化**,新项目接入前需自查 biz 假设。

## 文件

| 脚本 | 用途 | 参数化状态 |
|---|---|---|
| `ai-health.ps1` | 工具栈体检(12 项含 daemon /health + chroma chunks + cross-link nodes + codegraph + Qwen3 + GPU) | ✅ **已参数化 `-Repo`,本目录是真值源**;业务仓只留瘦 wrapper 转发 |
| `update-local-ai.ps1` | 三件套刷新(codegraph + chroma + cross-link) | ⚠️ 同上 |
| `wait-for-reindex.ps1` | polling reindex.log 等就绪 | ✅ 通用 |
| `dirty-index-check.ps1` | git status × AI 索引范围交叉 | ✅ 通用 |
| `clean-local-artifacts.ps1` | 清 logs / __pycache__ / temp | ✅ 通用,白名单可扩 |
| `sync-memory.ps1` | docs/memory/ → ~/.claude/projects/.../memory/ | ✅ 通用,cwdEncoded 用 git workdir |
| `install-git-hooks.ps1` | 装 .git/hooks/post-commit + pre-push sh stub | ✅ 通用 |
| `post-commit.ps1` | git hook 触发后台 reindex | ⚠️ DOC_PATTERN / CODE_PATTERN 业务正则,需各项目自配 |
| `git-hooks/post-commit` + `git-hooks/pre-push` | Git for Windows errno 1 规避 sh stub | ✅ 通用模板 |
| `init-memory-db.sql` | memory 持久化用的平台独立 PG 库一键建库脚本(role + database + 授权,不建表) | ✅ 通用 |

## 初始化 memory PG 库(会话/记忆持久化)

agent 会话默认存进程内,重启就丢。要跨重启持久化(`session_backend=pg`),需要一个**平台独立 PG 库** `codev_platform_memory`。codev-platform 代码没有 postgres 超管密码,**不会自动建库**,需你手动跑一次下面流程(只跑一次)。

> 红线:memory 数据严禁与任何业务库同 database(plan §3.2b)。本流程只在现有 PG server 里建独立库,不碰业务库。表由 app 首次连接幂等自动建,脚本只建 role + database。

前提:本机已有 PostgreSQL 在跑(如 localhost:5432)。若无原生 PG,改用仓根 `docker-compose.yml`(端口 5433),跳过步骤 1。

1. **改密码 + 建库**(postgres 超管身份跑一次):
   - 先编辑 `scripts/init-memory-db.sql`,把占位 `<CHANGE_ME>` 改成你的强密码。
   - 跑:`psql -U postgres -f scripts/init-memory-db.sql`
     (若报 `database already exists` 而前面成功,说明已就绪,忽略即可。)

2. **配 config.memory.pg_dsn**(把上一步设的密码原样填进去):
   编辑 `~/.codev-platform/config.json`:
   ```json
   "agent":  { "session_backend": "pg" },
   "memory": { "pg_dsn": "postgresql://codev_platform:<你的密码>@localhost:5432/codev_platform_memory" }
   ```
   更安全的做法:DSN 走 env,不进 config 文件 —— `setx CODEV_PLATFORM_MEMORY_DSN "postgresql://codev_platform:<密码>@localhost:5432/codev_platform_memory"`(env 优先于 config)。docker 方案端口改 `5433`。

3. **装 PG 驱动**(agent extra 含 psycopg,本仓 venv):
   `uv pip install --python .venv\Scripts\python.exe -e .[agent]`

4. **重启 agent**:`codev-platform agent serve`
   首次连接 `SqlSessionStore` 会幂等建出 `agent_sessions` / `agent_messages` 表,无需手动建表。

### 验证

- 连库通(会提示输入步骤 1 设的密码):
  ```
  psql -h localhost -U codev_platform -d codev_platform_memory -c "SELECT current_database(), current_user;"
  ```
- 表自动建出(agent serve 起来并发起过一次会话后):
  ```
  psql -h localhost -U codev_platform -d codev_platform_memory -c "\dt"
  ```
  应看到 `agent_sessions` 与 `agent_messages`。
- 持久化生效:发一轮对话 → 重启 `agent serve` → 同 session 历史仍在 = OK。

## 接入方式

业务仓:
1. `pip install -e <codev-platform-path>`(已含 codev-platform CLI)
2. 复制 `scripts/` 下需要的脚本到 `<your-repo>/tools/dev/`(或 `scripts/`)
3. 改 `RepoRoot` 推导 + 业务专属 pattern
4. 跑 `install-git-hooks.ps1` 装 git hook

后续 codev-platform 会:
- 抽 `codev_platform.config.get_business_repo_root()` 让 RepoRoot 通过 config 推导
- DOC_PATTERN / CODE_PATTERN 走业务仓 `.claude/index.json`
- 提供 `codev-platform ai-health` / `update-local-ai` 子命令直接调

## 当前状态(归属分两类,逐脚本翻转中)

- **`ai-health.ps1`**:✅ **已翻转** —— 本目录是真值源(参数化 `-Repo`),业务仓 `tools/dev/ai-health.ps1` 是瘦 wrapper,转发到这里并带 `-Repo <本仓>`。widget 的"重新体检"也直接调本目录 canonical。
- **`post-commit.ps1` / `update-local-ai.ps1`**:⏳ **仍是 snapshot** —— 业务仓 `tools/dev/` 为真值源,本目录是滞后快照。含业务专属 DOC/CODE 正则(post-commit 内联,非读 `.claude/index.json`),未参数化,翻转前需先把正则抽到业务仓 `.claude/index.json`。**别盲目同步覆盖**,先参数化再翻。
- **通用脚本**(wait-for-reindex / dirty-index-check / clean-local-artifacts / sync-memory / install-git-hooks):两处保持一致,改动同步即可。
