# --------------------------------------------------------------------
# post-commit.ps1
# Auto-trigger chroma reindex + cross_link rebuild after commits that
# changed docs / rules / agents OR code that feeds the cross-layer KG.
#
# Invoked from .git/hooks/post-commit (sh stub) -> exec powershell -File post-commit.ps1
# Rationale: Git for Windows sh.exe sometimes errors (add_item / errno 1)
# on PATH conflicts; sh stub exec's PowerShell immediately so we never run
# real logic in sh.
#
# Two trigger categories, dispatched separately:
#   DOC paths (-> chroma reindex, usually incremental):
#     docs/**/*.md, .claude/rules/*.md, .claude/skills/**/*.md,
#     submodule .claude/rules/*.md, tools/**/*.md,
#     **/CLAUDE.md, **/AGENTS.md, root README.md
#
#   CODE paths (-> cross_link rebuild, ~2s background):
#     apps/stock-admin-api/src/main/resources/db/migration/V*.sql
#     apps/stock-admin-api/src/main/java/**/*Mapper.java
#     apps/stock-admin-api/src/main/java/**/controller/*.java
#     apps/stock-admin-web/src/services/apis/*.ts
#     python/stock-pipeline/stock_pipeline/repositories/*.py
#
# When both match: spawn one bg job calling update-local-ai -SkipCodeGraph (both)
# When only doc:   spawn chroma reindex only (skip cross_link save 2s)
# When only code:  spawn cross_link rebuild only (skip chroma save ~90s)
# When neither:    silent no-op
# Never fails the commit even if reindex spawn errors.
#
# Log: tools/chroma/reindex.log (UTF-8, append, wrapped in start/finish markers)
# Pure ASCII (PowerShell 5.1 GBK parser safety, see windows-powershell.md).
# --------------------------------------------------------------------

$ErrorActionPreference = 'Continue'

try {
    $repoRoot = (& git rev-parse --show-toplevel 2>$null)
    if (-not $repoRoot -or $LASTEXITCODE -ne 0) { exit 0 }
    $repoRoot = $repoRoot.Trim()

    $changed = & git diff-tree --no-commit-id --name-only -r HEAD 2>$null
    if (-not $changed -or $LASTEXITCODE -ne 0) { exit 0 }

    # DOC pattern: 必须严格对应 index_docs.py 的 DOC_PATTERNS,避免触发 reindex
    # 但 index_docs.py 实际不收的路径(如 .claude/settings.json / .claude/hooks/)
    # 2026-05-23 收窄:之前用 \.claude/ 整目录命中,误触发 settings.json / hooks/
    # 引发空跑(manifest 增量化后空跑也是几秒,但无意义占资源)
    $docPattern = '^(' +
        'docs/.*\.md$' +
        '|\.claude/rules/.*\.md$' +
        '|\.claude/skills/.*\.md$' +
        '|apps/[^/]+/\.claude/rules/.*\.md$' +
        '|python/stock-pipeline/\.claude/rules/.*\.md$' +
        '|tools/.*\.md$' +
        '|.*CLAUDE\.md$' +
        '|.*AGENTS\.md$' +
        '|README\.md$' +
        ')'

    # CROSS_LINK pattern: everything that feeds cross_link KG
    # - Flyway SQL: defines table/column nodes
    # - Java Mapper: SQL annotations -> reads/writes_table edges
    # - Java Controller: @Mapping -> java_endpoint nodes
    # - Frontend api client: generated typings -> frontend_api nodes
    # - Python repositories: SQL in repos -> python_method edges
    $crossLinkPattern = '^(' +
        'apps/stock-admin-api/src/main/resources/db/migration/V.*\.sql$' +
        '|apps/stock-admin-api/src/main/java/.*Mapper\.java$' +
        '|apps/stock-admin-api/src/main/java/.*/controller/.*\.java$' +
        '|apps/stock-admin-web/src/services/apis/.*\.ts$' +
        '|python/stock-pipeline/stock_pipeline/repositories/.*\.py$' +
        ')'

    # CODEGRAPH pattern: any source code feeds codegraph callgraph index
    # (2026-05-22 起 watcher 弃用 — 它会写崩 codegraph.db 出 SQLITE_CORRUPT，
    # 改用 hook 触发增量 sync。代码改动后台跑 codegraph index，~60s 无感)
    $codegraphPattern = '^(' +
        'apps/stock-admin-api/src/main/java/.*\.java$' +
        '|apps/stock-admin-web/src/.*\.(ts|tsx)$' +
        '|python/stock-pipeline/.*\.py$' +
        ')'

    $docMatched       = @($changed | Where-Object { $_ -match $docPattern })
    $crossLinkMatched = @($changed | Where-Object { $_ -match $crossLinkPattern })
    $codegraphMatched = @($changed | Where-Object { $_ -match $codegraphPattern })

    if ($docMatched.Count -eq 0 -and $crossLinkMatched.Count -eq 0 -and $codegraphMatched.Count -eq 0) { exit 0 }

    $updateScript = Join-Path $repoRoot 'tools\dev\update-local-ai.ps1'
    $logFile      = Join-Path $repoRoot 'tools\chroma\reindex.log'
    if (-not (Test-Path $updateScript)) {
        Write-Host '[post-commit] update-local-ai.ps1 not found, skipping reindex'
        exit 0
    }

    # Decide which scopes to refresh; build the update-local-ai argument set
    # 2026-05-22: codegraph watcher 弃用（写崩 db），改用 hook 触发增量 sync
    $scopes = @()
    if ($docMatched.Count       -gt 0) { $scopes += 'chroma' }
    if ($crossLinkMatched.Count -gt 0) { $scopes += 'cross_link' }
    if ($codegraphMatched.Count -gt 0) { $scopes += 'codegraph' }

    # update-local-ai takes inverse flags. Default = run all 3 stages.
    $extraFlags = @()
    if ($scopes -notcontains 'codegraph')  { $extraFlags += '-SkipCodeGraph' }
    if ($scopes -notcontains 'chroma')     { $extraFlags += '-SkipChroma' }
    if ($scopes -notcontains 'cross_link') { $extraFlags += '-SkipCrossLink' }

    $commitSha = (& git rev-parse HEAD 2>$null).Trim()
    Write-Host ('[post-commit] ' + ($scopes -join '+') + ' changed, spawning background reindex...')
    # P7 progress hint: the reindex runs detached, so this commit's changes are
    # NOT yet queryable via MCP. Tell the user how to wait / verify / re-run.
    Write-Host '[post-commit] index updates in background (~30-90s); MCP results lag until it finishes'
    Write-Host '[post-commit]   wait:   powershell -File tools\dev\wait-for-reindex.ps1'
    Write-Host '[post-commit]   verify: powershell -File tools\dev\ai-health.ps1 -Mode Light  (see hook missed? line)'
    Write-Host '[post-commit]   manual: powershell -File tools\dev\post-commit.ps1  (re-run if it missed)'

    # Sync header (lands before background output)
    $startStamp = (Get-Date).ToString('yyyy-MM-dd HH:mm:ss')
    # De-duplicate matched paths (a file may match both doc + code patterns,
    # e.g. tools/**/*.md hitting docPattern and codegraph rarely; or a Mapper.java
    # hitting both crossLink + codegraph).
    $allMatched = @($docMatched + $crossLinkMatched + $codegraphMatched) |
                  Sort-Object -Unique
    $headerLines = @(
        '',
        ('===== reindex started at ' + $startStamp + ' ====='),
        ('trigger commit: ' + $commitSha),
        ('scopes:         ' + ($scopes -join ', ')),
        'matched paths:'
    ) + $allMatched
    $headerText = ($headerLines -join [Environment]::NewLine) + [Environment]::NewLine
    [System.IO.File]::AppendAllText($logFile, $headerText, [System.Text.UTF8Encoding]::new($false))

    # Background wrapper: forward to update-local-ai with computed flags,
    # capture all output to reindex.log via .NET AppendAllText (UTF-8 no-BOM,
    # avoiding PS 5.1 `*>>` / Out-File -Append UTF-16 LE corruption).
    $flagsLiteral = ($extraFlags | ForEach-Object { "'$_'" }) -join ', '
    # 用 cmd /c "powershell ... >> log 2>&1" 让 OS 层合并 stderr,
    # 避免 PowerShell 5.1 把 native stderr 包装成 NativeCommandError 中断 wrapper
    # (windows-powershell.md §3 经典坑 — 之前 wrapper 5-10s 就 "finished" 但实际没跑完就是这个)
    $flagsForCmd = ($extraFlags -join ' ')
    $cmdLine = ('powershell -NoProfile -ExecutionPolicy Bypass -File "' + $updateScript + '" ' + $flagsForCmd + ' >> "' + $logFile + '" 2>&1')
    $logFileEsc = $logFile.Replace("'", "''")
    $cmdLineEsc = $cmdLine.Replace("'", "''")
    # finally 必须区分 cmd 退出码 — 不能只写 "finished",否则 update-local-ai 内部失败
    # 时 log 仍显示 "finished",误导调用方(skill / ai-health) 以为成功。
    # 写法:"ok" / "warn exit=2" / "failed exit=N" / "failed (wrapper exception)"
    $wrapper = @"
`$ErrorActionPreference = 'Continue'
[Console]::OutputEncoding = [System.Text.Encoding]::UTF8
`$cmdExit = -1
`$wrapperErr = `$null
try {
    & cmd /c '$cmdLineEsc' | Out-Null
    `$cmdExit = `$LASTEXITCODE
} catch {
    `$wrapperErr = `$_.Exception.Message
} finally {
    `$end = (Get-Date).ToString('yyyy-MM-dd HH:mm:ss')
    if (`$wrapperErr) {
        `$status = 'failed (wrapper exception: ' + `$wrapperErr + ')'
    } elseif (`$cmdExit -eq 0) {
        `$status = 'ok'
    } elseif (`$cmdExit -eq 2) {
        # ai-health exit=2 means warning-only; reindex itself completed and
        # critical checks passed, so do not label it as failed.
        `$status = 'warn exit=2'
    } else {
        `$status = 'failed exit=' + `$cmdExit
    }
    `$tail = '===== reindex finished at ' + `$end + ' [' + `$status + '] =====' + [Environment]::NewLine
    [System.IO.File]::AppendAllText('$logFileEsc', `$tail, [System.Text.UTF8Encoding]::new(`$false))
}
"@
    $tmpWrapper = Join-Path $env:TEMP ('post_commit_' + [guid]::NewGuid().ToString('N') + '.ps1')
    Set-Content -Path $tmpWrapper -Value $wrapper -Encoding ASCII

    Start-Process -FilePath 'powershell' `
        -ArgumentList @('-NoProfile', '-ExecutionPolicy', 'Bypass', '-WindowStyle', 'Hidden', '-File', $tmpWrapper) `
        -WindowStyle Hidden | Out-Null

    exit 0
}
catch {
    # Never fail the commit; log to stderr only
    Write-Host ('[post-commit] hook error (commit succeeded): ' + $_.Exception.Message)
    exit 0
}
