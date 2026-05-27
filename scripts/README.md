# codev-platform/scripts/

工具栈 PowerShell / sh 脚本模板。当前是 platform 仓 `tools/dev/` 的快照,**业务路径未完全参数化**,新项目接入前需自查 biz 假设。

## 文件

| 脚本 | 用途 | 参数化状态 |
|---|---|---|
| `ai-health.ps1` | 工具栈体检(12 项含 daemon /health + chroma chunks + cross-link nodes + codegraph + Qwen3 + GPU) | ⚠️ 含 RepoRoot 推导 + 业务进程探测;新项目用前抽 BizConfig section |
| `update-local-ai.ps1` | 三件套刷新(codegraph + chroma + cross-link) | ⚠️ 同上 |
| `wait-for-reindex.ps1` | polling reindex.log 等就绪 | ✅ 通用 |
| `dirty-index-check.ps1` | git status × AI 索引范围交叉 | ✅ 通用 |
| `clean-local-artifacts.ps1` | 清 logs / __pycache__ / temp | ✅ 通用,白名单可扩 |
| `sync-memory.ps1` | docs/memory/ → ~/.claude/projects/.../memory/ | ✅ 通用,cwdEncoded 用 git workdir |
| `install-git-hooks.ps1` | 装 .git/hooks/post-commit + pre-push sh stub | ✅ 通用 |
| `post-commit.ps1` | git hook 触发后台 reindex | ⚠️ DOC_PATTERN / CODE_PATTERN 业务正则,需各项目自配 |
| `git-hooks/post-commit` + `git-hooks/pre-push` | Git for Windows errno 1 规避 sh stub | ✅ 通用模板 |

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

## 当前状态

platform 仓 `tools/dev/` 是真值源,本目录是 snapshot + 模板。改动先在 platform 测,稳定后回同步本目录。
