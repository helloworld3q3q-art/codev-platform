# --------------------------------------------------------------------
# clean-local-artifacts.ps1
# Remove regenerable runtime junk (logs / temp dumps).
# Conservative by default - skips caches and never touches docs/rules/archive.
#
# Flags:
#   -DryRun       list what would be deleted, do nothing
#   -DeepClean    also remove __pycache__/ + .pytest_cache/ recursively
#   -KeepLogs N   keep N most recent logs in archive/logs/ (default: keep all)
#
# Never touches:
#   .claude/rules/  .claude/skills/  AGENTS.md  CLAUDE.md
#   models/  data/chroma/  .codegraph/codegraph.db
#   docs/  python/stock-pipeline/archive/incidents/
#
# Pure ASCII (PowerShell 5.1 GBK parser safety).
# ExitCode: 0 success / 1 any deletion failed
# --------------------------------------------------------------------

param(
    [switch]$DryRun,
    [switch]$DeepClean,
    [int]$KeepLogs = -1   # -1 means keep all
)

$ErrorActionPreference = 'Stop'
$RepoRoot = (Resolve-Path (Join-Path $PSScriptRoot '..\..')).Path

$mode = if ($DryRun) { 'DRY-RUN' } else { 'EXECUTE' }
Write-Host '=== clean-local-artifacts ===' -ForegroundColor Cyan
Write-Host ('mode: ' + $mode + ' | repo: ' + $RepoRoot)
Write-Host ''

$totalDeleted = 0
$totalBytes = 0
$failed = 0

function Remove-One {
    param([string]$path, [string]$label)
    if (-not (Test-Path $path)) { return }
    $size = 0
    try {
        $item = Get-Item -Path $path -ErrorAction Stop
        if ($item.PSIsContainer) {
            $size = (Get-ChildItem -Path $path -Recurse -File -ErrorAction SilentlyContinue |
                     Measure-Object -Property Length -Sum).Sum
        } else {
            $size = $item.Length
        }
    } catch { $size = 0 }
    $sizeStr = if ($size -gt 1048576) { ([math]::Round($size/1MB, 1)).ToString() + 'MB' }
               elseif ($size -gt 1024) { ([math]::Round($size/1KB, 1)).ToString() + 'KB' }
               else { $size.ToString() + 'B' }
    if ($DryRun) {
        Write-Host ('  WOULD DELETE [' + $label + '] ' + $path + ' (' + $sizeStr + ')') -ForegroundColor Yellow
    } else {
        try {
            Remove-Item -Path $path -Recurse -Force -ErrorAction Stop
            Write-Host ('  DELETED [' + $label + '] ' + $path + ' (' + $sizeStr + ')') -ForegroundColor Green
            $script:totalDeleted++
            $script:totalBytes += $size
        } catch {
            Write-Host ('  FAILED  [' + $label + '] ' + $path + ' : ' + $_.Exception.Message) -ForegroundColor Red
            $script:failed++
        }
    }
}

# Tier 1: explicit root-level ephemera
Write-Host '[tier 1] root-level runtime dumps' -ForegroundColor Cyan
$tier1 = @(
    '.tmp_backend.log',
    '.tmp_backend_err.log',
    'bat_e.txt',
    'bat_t.txt',
    'cg_index.log',
    'apps\stock-admin-web\frontend-dev.log',
    'apps\stock-admin-web\frontend-dev.err.log',
    'apps\stock-admin-web\tsc.log',
    'tools\chroma\mcp_server.log'
)
foreach ($rel in $tier1) {
    Remove-One -path (Join-Path $RepoRoot $rel) -label 'log'
}

# Tier 2: deep clean (caches)
if ($DeepClean) {
    Write-Host ''
    Write-Host '[tier 2] caches (DeepClean enabled)' -ForegroundColor Cyan
    # __pycache__ everywhere except inside .venv
    $pycacheDirs = Get-ChildItem -Path $RepoRoot -Recurse -Directory -Filter '__pycache__' -ErrorAction SilentlyContinue |
                   Where-Object { $_.FullName -notmatch '\\\.venv\\' -and $_.FullName -notmatch '\\node_modules\\' }
    foreach ($d in $pycacheDirs) {
        Remove-One -path $d.FullName -label 'pycache'
    }
    # .pytest_cache
    $pytestDirs = Get-ChildItem -Path $RepoRoot -Recurse -Directory -Filter '.pytest_cache' -ErrorAction SilentlyContinue |
                  Where-Object { $_.FullName -notmatch '\\\.venv\\' }
    foreach ($d in $pytestDirs) {
        Remove-One -path $d.FullName -label 'pytest-cache'
    }
} else {
    Write-Host ''
    Write-Host '[tier 2] caches skipped (pass -DeepClean to include __pycache__ / .pytest_cache)' -ForegroundColor DarkGray
}

# Tier 3: optional pipeline log rotation
if ($KeepLogs -ge 0) {
    Write-Host ''
    Write-Host ('[tier 3] rotate python/stock-pipeline/archive/logs (keep ' + $KeepLogs + ')') -ForegroundColor Cyan
    $logDir = Join-Path $RepoRoot 'python\stock-pipeline\archive\logs'
    if (Test-Path $logDir) {
        $logs = Get-ChildItem -Path $logDir -File -ErrorAction SilentlyContinue |
                Sort-Object LastWriteTime -Descending
        $toDelete = $logs | Select-Object -Skip $KeepLogs
        foreach ($f in $toDelete) {
            Remove-One -path $f.FullName -label 'old-log'
        }
    }
} else {
    Write-Host '[tier 3] log rotation skipped (pass -KeepLogs N to enable)' -ForegroundColor DarkGray
}

Write-Host ''
$totalMb = [math]::Round($totalBytes / 1MB, 2)
if ($DryRun) {
    Write-Host 'SUMMARY: dry-run, nothing actually deleted' -ForegroundColor Yellow
} else {
    Write-Host ('SUMMARY: deleted ' + $totalDeleted + ' item(s), ~' + $totalMb + ' MB freed, ' + $failed + ' failed') -ForegroundColor Green
}
if ($failed -gt 0) { exit 1 } else { exit 0 }
