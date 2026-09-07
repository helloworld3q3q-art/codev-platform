# --------------------------------------------------------------------
# post-commit.ps1
# Auto-trigger chroma reindex + codegraph sync after commits that
# changed docs / rules / agents OR indexable source code.
#
# Invoked from .git/hooks/post-commit (sh stub) -> exec powershell -File post-commit.ps1
# Rationale: Git for Windows sh.exe sometimes errors (add_item / errno 1)
# on PATH conflicts; sh stub exec's PowerShell immediately so we never run
# real logic in sh.
#
# Trigger categories (-> chroma / codegraph reindex), dispatched separately.
# Match patterns are PROJECT-DECLARED in meta.json health.* (project-name-free
# generic defaults live in code; each project EXTENDS via its meta):
#   DOC       -> chroma   : generic doc globs + health.reindex_doc_patterns
#   CODEGRAPH -> codegraph : generic source glob + health.reindex_codegraph_patterns
# Adding a project needs no code edit here -- declare its paths in meta.json.
# (The former CROSS_LINK -> cross_link category was retired 2026-06-05; cross-link
# MCP removed, graph unified-graph took over.)
#
# When both match: spawn one bg job (chroma + codegraph)
# When only doc:   spawn chroma reindex only (skip codegraph)
# When only code:  spawn codegraph sync only (skip chroma save ~90s)
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

    # Reindex-scope patterns are PROJECT-DECLARED in meta.json health.* so adding a
    # project needs no code edit here. Code carries only project-name-free generics;
    # a project EXTENDS them via meta. Resolve this repo's project_id + codev-platform
    # root (this canonical script lives in codev-platform\scripts).
    $codevRoot = Split-Path -Parent $PSScriptRoot
    $projectId = $null
    $pjFile = Join-Path $repoRoot '.claude\project.json'
    if (Test-Path $pjFile) {
        try { $projectId = (Get-Content $pjFile -Encoding UTF8 -Raw | ConvertFrom-Json).project_id } catch { }
    }
    $healthCfg = $null
    if ($projectId) {
        $metaFile = Join-Path $codevRoot ('platform_meta\projects\' + $projectId + '\meta.json')
        if (Test-Path $metaFile) {
            try { $mj = Get-Content $metaFile -Encoding UTF8 -Raw | ConvertFrom-Json; if ($mj.health) { $healthCfg = $mj.health } } catch { }
        }
    }
    function Get-MetaPatterns($key) {
        if ($healthCfg -and $healthCfg.$key) { return @($healthCfg.$key) }
        return @()
    }
    function Test-AnyPattern($text, $patterns) {
        $t = $text -replace '\\', '/'
        foreach ($pat in $patterns) { if ($t -match $pat) { return $true } }
        return $false
    }

    # DOC -> chroma reindex. Generic doc defaults + project's reindex_doc_patterns.
    $docPatterns = @(
        '^docs/.*\.md$', '^\.claude/(rules|skills)/.*\.md$', '^apps/[^/]+/\.claude/rules/.*\.md$',
        '^tools/.*\.md$', '.*CLAUDE\.md$', '.*AGENTS\.md$', '^README\.md$'
    ) + (Get-MetaPatterns 'reindex_doc_patterns')
    # CODEGRAPH -> codegraph sync. Generic source default + reindex_codegraph_patterns.
    $codegraphPatterns = @('^apps/[^/]+/src/.*\.(java|ts|tsx)$') + (Get-MetaPatterns 'reindex_codegraph_patterns')

    $docMatched       = @($changed | Where-Object { Test-AnyPattern $_ $docPatterns })
    $codegraphMatched = @($changed | Where-Object { Test-AnyPattern $_ $codegraphPatterns })

    if ($docMatched.Count -eq 0 -and $codegraphMatched.Count -eq 0) { exit 0 }

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
    if ($codegraphMatched.Count -gt 0) { $scopes += 'codegraph' }

    # update-local-ai takes inverse flags. Default = run all stages.
    $extraFlags = @()
    if ($scopes -notcontains 'codegraph')  { $extraFlags += '-SkipCodeGraph' }
    if ($scopes -notcontains 'chroma')     { $extraFlags += '-SkipChroma' }

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
    # e.g. tools/**/*.md hitting docPattern and codegraph rarely).
    $allMatched = @($docMatched + $codegraphMatched) |
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
