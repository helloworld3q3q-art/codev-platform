---
name: git-commit
description: 标准 commit 流程 —— 合并 commit + 兜底 post-commit.ps1 + 校验 reindex.log,最少 token。用于用户说"提交代码"/"commit"/"提交 git"
---

# Git Commit (精简版)

**触发**:"提交代码" / "commit" / "提交 git" / "commit 一下"

## 流程

### 用户给了 `-m "xxx"` → 2 步: 同步 commit + 后台监听 reindex 完成

#### Step 1: 同步 PowerShell — commit + scope 检测 (~1-3s)

```powershell
git add -A; git commit -m "<用户给的>"
$hash=(git rev-parse --short HEAD)
$hashFull=(git rev-parse HEAD)

# 等最多 3s 确认 post-commit hook 已写 reindex.log;没写就手动补触发(避免锁冲突)
$hookSeen=$false
$hookDeadline=(Get-Date).AddSeconds(3)
while ((Get-Date) -lt $hookDeadline) {
  if (Test-Path tools\chroma\reindex.log) {
    $hookProbe=Get-Content tools\chroma\reindex.log -Tail 80 -Encoding UTF8
    if ($hookProbe | Where-Object { $_ -like "*trigger commit: $hashFull*" }) { $hookSeen=$true; break }
  }
  Start-Sleep -Milliseconds 500
}
if (-not $hookSeen) { powershell -NoProfile -File tools\dev\post-commit.ps1 }

$log=Get-Content tools\chroma\reindex.log -Tail 30 -Encoding UTF8
$scopesLine=$null
$idx=-1
for ($i=0; $i -lt $log.Count; $i++) { if ($log[$i] -like "*trigger commit: $hashFull*") { $idx=$i } }
if ($idx -ge 0 -and $idx+8 -lt $log.Count) {
  $scopesLine=$log[$idx..([Math]::Min($idx+8,$log.Count-1))] | Where-Object { $_ -match '^scopes:' } | Select-Object -Last 1
}
if (-not $scopesLine) { $scopesLine=$log | Where-Object { $_ -match '^scopes:' } | Select-Object -Last 1 }

Write-Host "===hash===$hash"
Write-Host "===hashFull===$hashFull"
Write-Host "===scopes===$scopesLine"
```

#### Step 2: PowerShell `run_in_background=true` — 调既有 wait-for-reindex.ps1

把 Step 1 输出的 `===hashFull===` 值代入下面命令,**用 PowerShell 工具 + `run_in_background: true`** 启动:

```powershell
powershell -NoProfile -File tools\dev\wait-for-reindex.ps1 -Commit <HASHFULL> -TimeoutSec 300
```

任务完成时 harness 自动通知 AI;**AI 在下次回复里给用户报告 reindex 状态**。

⚠️ **必须用 PowerShell 工具**(不要 Bash 工具)。原因:
- Bash 工具会先启动 `bash.exe`,Git for Windows 偶发 errno 1 启动失败 → 任务直接 exit 5
- Bash 内联 `-Command "..."` 还会把 `$xxx` 当 bash 变量展开 → PowerShell 解析错
- PowerShell 工具直接 invoke,无中间层

skill 立刻返回 commit hash + scope,**不阻塞用户**。

### 用户没给 message → 2 步

```powershell
# 步骤 1: 看改了啥(用于生成 message)
git status --short; git diff --stat HEAD
```

按改动推断 message(见下 §message 规则),然后用步骤 1 的 PowerShell 块。

## 输出格式(强制)

### commit 完成时的同步输出 (Step 1 后立刻给用户)

```
<hash> ok | chroma+codegraph spawned | N 文件 (reindex 后台跑,完成会通知)
```

scope 单一时简写:`chroma spawned` / `codegraph spawned`。
不命中任何 scope:`<hash> ok | no reindex | N 文件`。

### 后台 Bash 任务完成时 (harness 通知 AI,AI 下次回复带这行)

成功:`✓ <hash> reindex 完成 — [reindex] finished at YYYY-MM-DD HH:MM:SS [ok]`
warn:`⚠ <hash> reindex 完成但 ai-health WARN — 跑 /ai-health 看详情`
失败:`❌ <hash> reindex 失败 [failed exit=N] — 跑 /ai-health 排查`
超时:`⚠ <hash> reindex 超时 >5min 未见 finished 行 — 跑 /ai-health 看 daemon`

### commit 本身失败

```
❌ <git 报错原文>
```

判定规则:
- scope 触发 → 看 Step 1 输出的 `===scopes===` 行
- 后台任务退出码 0 = ok/warn,>0 = failed/timeout
- 解析后台 stdout 第一行 `[reindex] finished at ... [<status>]`

**禁止输出**:
- 复述 git diff
- "我看到你..." 废话
- reindex.log 完整内容
- 解释 sh hook errno 1(已知现象)
- 同步 polling 等 reindex 完成

## commit message 规则(用户没给 -m 时推断)

遵循 `.claude/rules/commit-pr-conventions.md` — **禁 AI 痕迹**(no `Co-Authored-By: Claude` / 🤖 / `noreply@anthropic.com`)。

按改动文件类型:

| 改动 | 模板 |
|---|---|
| `.claude/rules/*.md` | `docs(rules): <一句>` |
| `AGENTS.md` / `CLAUDE.md` | `docs(agents\|claude): <一句>` |
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
| reindex.log 没出现刚才 hash | 提示 "hook 未补上",再跑 `powershell -File tools\dev\post-commit.ps1` |
| 想确认 reindex 成功 | 主动跑 `/ai-health`(不在 commit 流程内同步等) |

sh hook 偶发 `errno 1` 是已知 Git for Windows 问题 — **不要解释**,手动补触发就行(skill 已默认补)。

## 关联

- post-commit 工作原理:`tools/dev/post-commit.ps1`
- commit 规范:`.claude/rules/commit-pr-conventions.md`
- 2 个 scope: `chroma`(docs/.md) / `codegraph`(.java/.py/.tsx)。(cross_link scope 已随 cross-link 退役删除,2026-06-05)
