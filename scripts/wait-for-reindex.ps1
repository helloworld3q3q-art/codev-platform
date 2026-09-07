# --------------------------------------------------------------------
# wait-for-reindex.ps1
# Poll tools/chroma/reindex.log until target commit's reindex finishes or timeout.
#
# Why: post-commit hook spawns reindex in background (~30s). AI sessions
# need to verify it finished before querying MCP (else hit §2.1 codegraph
# staleness window). This helper avoids ad-hoc while loops in transcripts
# that trigger Claude Code's sleep-chain safety blocks.
#
# Usage:
#   powershell -File tools\dev\wait-for-reindex.ps1
#   powershell -File tools\dev\wait-for-reindex.ps1 -Commit abc1234
#   powershell -File tools\dev\wait-for-reindex.ps1 -TimeoutSec 60
#
# 2026-05-25 fix (two bugs):
#   1. Old impl used -Tail 30 which dropped target trigger line when
#      multiple commits were pushed in rapid succession (commit A's trigger
#      scrolled out of the 30-line window before A's reindex finished).
#   2. Old impl looked for "reindex finished" anywhere after target trigger,
#      which falsely matched a later commit's finished line if commit A had
#      not actually completed yet.
#   New impl: read whole log, locate target commit's trigger block bounded
#   by the next trigger line, only accept finished marker within that block.
#
# ExitCode: 0 found / 1 timeout / 2 log missing
# Pure ASCII only (PowerShell 5.1 GBK parser safety).
# --------------------------------------------------------------------

param(
    [string]$Commit,
    [int]$TimeoutSec = 120
)

$ErrorActionPreference = 'Stop'

$RepoRoot   = (Resolve-Path (Join-Path $PSScriptRoot '..\..')).Path
$ReindexLog = Join-Path $RepoRoot 'tools\chroma\reindex.log'

if (-not (Test-Path $ReindexLog)) {
    Write-Host "[FAIL] reindex.log not found at $ReindexLog"
    exit 2
}

if (-not $Commit) {
    $Commit = (& git -C $RepoRoot rev-parse HEAD 2>&1).Trim()
}

$shortSha = $Commit.Substring(0, [Math]::Min(7, $Commit.Length))

# Pre-check: mirror post-commit.ps1's doc + codegraph patterns. If this commit touches NO
# indexable file, the post-commit hook actively skips reindex, so reindex.log
# will never write a trigger line -- waiting will always timeout. Exit 0 early.
$changed = & git -C $RepoRoot diff-tree --no-commit-id --name-only -r $Commit 2>$null
if ($changed) {
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
    # (cross_link scope retired 2026-06-05; its .java/.ts/.py files fall under
    # codegraphPattern below, migration .sql no longer triggers a post-commit reindex.)
    $codegraphPattern = '^(' +
        'apps/stock-admin-api/src/main/java/.*\.java$' +
        '|apps/stock-admin-web/src/.*\.(ts|tsx)$' +
        '|python/stock-pipeline/.*\.py$' +
        ')'
    $hasIndexable = $changed | Where-Object {
        $_ -match $docPattern -or $_ -match $codegraphPattern
    }
    if (-not $hasIndexable) {
        Write-Host ('[OK] ' + $shortSha + ' touches no indexable file, skip wait')
        exit 0
    }
}

Write-Host ('[INFO] waiting for reindex of ' + $shortSha + ' (timeout ' + $TimeoutSec + 's)')

$deadline = (Get-Date).AddSeconds($TimeoutSec)
$pollIntervalSec = 3
$targetTrigger = 'trigger commit: ' + $Commit

while ((Get-Date) -lt $deadline) {
    # Read whole log; even after months of commits this stays in the MB range,
    # cheap compared to disk I/O caching. Avoids the -Tail edge case.
    $lines = Get-Content $ReindexLog -Encoding UTF8 -ErrorAction SilentlyContinue
    if ($lines) {
        # Locate the line that triggers OUR commit's reindex
        $triggerLine = -1
        for ($i = 0; $i -lt $lines.Count; $i++) {
            if ($lines[$i] -like ('*' + $targetTrigger + '*')) {
                $triggerLine = $i
                break
            }
        }
        if ($triggerLine -ge 0) {
            # Find the next trigger line (which would belong to a later commit)
            # to bound our commit's reindex output block
            $blockEnd = $lines.Count
            for ($j = $triggerLine + 1; $j -lt $lines.Count; $j++) {
                if ($lines[$j] -like '*trigger commit: *') {
                    $blockEnd = $j
                    break
                }
            }
            # Look for "reindex finished" ONLY within [triggerLine, blockEnd)
            # to avoid matching a newer commit's finished line
            $finishedFound = $false
            $finishedStatus = ''
            for ($k = $triggerLine; $k -lt $blockEnd; $k++) {
                if ($lines[$k] -match 'reindex finished at .* \[(ok|warn exit=\d+|failed exit=\d+)\]') {
                    $finishedFound = $true
                    $finishedStatus = $Matches[1]
                    break
                }
            }
            if ($finishedFound) {
                $elapsed = [int]($TimeoutSec - ($deadline - (Get-Date)).TotalSeconds)
                Write-Host ('[OK] reindex finished for ' + $shortSha + ' status=' + $finishedStatus + ' (took ~' + $elapsed + 's)')
                exit 0
            }
        }
    }
    Start-Sleep -Seconds $pollIntervalSec
}

Write-Host ('[TIMEOUT] reindex for ' + $shortSha + ' did not finish within ' + $TimeoutSec + 's')
Write-Host '  Check tools\chroma\reindex.log tail for errors,'
Write-Host '  or run tools\dev\post-commit.ps1 manually to retry.'
exit 1
