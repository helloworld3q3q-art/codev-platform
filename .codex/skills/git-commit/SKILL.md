---
name: git-commit
description: 标准 Git 提交流程：检查改动、生成中文提交信息、通过已安装 Git hook/WSL relay 增量入队，并按需用 manifest 等待索引完成。用于用户说“提交代码”“commit”“提交 git”。
---

# Git Commit (精简版)

**触发**:"提交代码" / "commit" / "提交 git" / "commit 一下"

## 流程

1. 用 PowerShell 执行 `git status --short` 和 `git diff --stat HEAD`，确认提交范围且不混入未知改动。
2. 用户未给 message 时，按下方规则生成中文 subject；不确定则询问。
3. 执行 `git add -A`、`git commit -m "<message>"`，再读取完整 hash。Git 自己运行已安装的 post-commit hook；Windows 仓由该 hook 校验分支/远端并 relay 到 WSL 队列 owner。
4. 默认不阻塞等待索引。用户要求“确认 MCP 可见/等索引完成”时运行：

```powershell
codev-platform wait-for-reindex --commit <HASHFULL> --timeout-sec 300
```

完成真值是目标 commit 的 manifest/result；`reindex.log` 只用于诊断，缺少 `finished` 不能判定失败。

## 手工恢复硬门禁

- post-commit 需要补触发时只运行 `git hook run post-commit`，复用已安装的 WSL relay。
- 禁止在 Windows 直接运行 `codev-platform post-commit` 或 `tools/dev/post-commit.ps1` 写 WSL file queue。
- enqueue 失败、pending、平台 HTTP 服务停止、日志缺少 `finished`、一次等待超时都不授权 full rebuild。
- Windows + WSL owner 用 `serve-mcp status` 查服务 owner，不在 Windows 运行 `reindex-queue status` 直读 UNC；随后用 `wait-for-reindex` 经 HTTP 核对 manifest。仅 `/update-local-ai` 的严格条件可进入手工重建。

## 输出

- 提交成功：`<hash> ok | 增量索引已交给 Git hook | N 文件`。
- 等待成功：`✓ <hash> manifest 已覆盖目标提交`。
- 等待失败/超时：报告命令原始摘要和下一步诊断，不声称索引损坏。
- commit 失败：返回 Git 错误原文；不使用 `--no-verify`。

## commit message 规则(用户没给 -m 时推断)

遵循当前客户端 surface 下的 `rules/commit-pr-conventions.md` — **subject 必须中文**，并且**禁 AI 痕迹**(no `Co-Authored-By: Claude` / 🤖 / `contact@example.invalid`)。

按改动文件类型:

| 改动 | 模板 |
|---|---|
| 客户端规则目录下的 `*.md` | `docs(rules): <一句>` |
| `AGENTS.md` | `docs(agents): <一句>` |
| `apps/stock-admin-api/**` | `feat\|fix(api): <一句>` |
| `apps/stock-admin-web/**` | `feat\|fix(web): <一句>` |
| `python/stock-pipeline/**` | `feat\|fix(pipeline): <一句>` |
| `tools/dev/**` / `scripts/**` | `chore(tools): <一句>` |
| Flyway `V*.sql` | `feat(db): <一句>` |
| 测试残留 / 垃圾文件清理 | `chore: 清理 xxx` |

type: `feat / fix / chore / docs / refactor / test / perf`,一句话 ≤ 60 字。

**没把握就反问** "commit message 写什么?",不要瞎编。

## 异常处理(精简)

| 现象 | 处理 |
|---|---|
| `git status` 干净 | 输出 `无变更,无需 commit` 退出 |
| pre-commit hook 失败 | 把错误原文给用户看,不 `--no-verify`,等指示 |
| hook 输出入队失败 | 运行 `git hook run post-commit`，再查 queue + manifest |
| 想确认 reindex 成功 | 运行 `wait-for-reindex --commit <HASH>`；需要全栈信息再跑 `/ai-health` |

## 关联

- post-commit 恢复入口：`git hook run post-commit`
- commit 规范：当前客户端 surface 下的 `rules/commit-pr-conventions.md`
- 2 个 scope: `chroma`(docs/.md) / `codegraph`(.java/.py/.tsx)。(cross_link scope 已随 cross-link 退役删除,2026-06-05)
