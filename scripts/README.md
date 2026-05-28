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
