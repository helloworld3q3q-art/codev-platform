# --------------------------------------------------------------------
# update-local-ai.ps1
# One-shot refresh for local AI dev toolchain:
#   1. Rebuild CodeGraph index (scripts/codegraph/rebuild_index.ps1)        ~60s
#   2. Reindex Chroma docs (tools/chroma/index_docs.py)                     incremental, full ~90s
#   3. Print final node / chunk / CUDA summary via ai-health.ps1
#
# NOTE: the former cross-layer KG stage (python -m cross_link.build_index) was
# retired 2026-06-05 (cross-link MCP removed; graph unified-graph took over).
#
# Pure ASCII (PowerShell 5.1 GBK parser safety).
# Flags:
#   -SkipCodeGraph   skip step 1
#   -SkipChroma      skip step 2
#   -ChromaForce     pass --force to index_docs.py (drop + rebuild collection)
# ExitCode: 0 success / non-zero on first failure
# --------------------------------------------------------------------

param(
    [switch]$SkipCodeGraph,
    [switch]$SkipChroma,
    [switch]$ChromaForce,
    # Health 模式: Light(默认,~1-2s,只查 mtime/sqlite) / Full(~10s, 加载模型 + GPU)
    # 后台 post-commit reindex 用 Light 节省时间(模型已加载过一次没意义再来一次);
    # 手动 /ai-health 走 Full 完整体检
    [ValidateSet('Light', 'Full')]
    [string]$HealthMode = 'Light'
)

$ErrorActionPreference = 'Stop'

$RepoRoot   = (Resolve-Path (Join-Path $PSScriptRoot '..\..')).Path

# P8 hint: a full/forced rebuild (codegraph ~60s + chroma full ~90s) is long.
# Run it in the FOREGROUND so you can watch per-stage progress and catch errors.
# Incremental refreshes after a commit are handled automatically in the
# background by tools\dev\post-commit.ps1 -- no need to launch this by hand for
# routine edits. Use wait-for-reindex.ps1 to block on that background run.
if ($ChromaForce -or (-not $SkipCodeGraph -and -not $SkipChroma)) {
    Write-Host '[update-local-ai] full rebuild: run in foreground to watch progress (post-commit handles incremental in background)' -ForegroundColor DarkGray
}

$CgScript   = Join-Path $RepoRoot 'scripts\codegraph\rebuild_index.ps1'
# venv now owned by codev-platform (platform ownership inversion): read from
# ~/.codev-platform/config.json runtime.chroma_venv; fall back to legacy repo-local.
# (indexer is the codev_platform.chroma.indexer package module.)
$ChromaVenv = $null
$cfgPath = Join-Path $env:USERPROFILE '.codev-platform\config.json'
if (Test-Path $cfgPath) {
    try { $ChromaVenv = (Get-Content $cfgPath -Encoding UTF8 -Raw | ConvertFrom-Json).runtime.chroma_venv } catch { }
}
if ($ChromaVenv) {
    $ChromaPy = Join-Path $ChromaVenv 'Scripts\python.exe'
} else {
    $ChromaPy = Join-Path $RepoRoot 'tools\chroma\.venv\Scripts\python.exe'
}
$HealthPs1  = Join-Path $PSScriptRoot 'ai-health.ps1'

function Stage {
    param([string]$name)
    Write-Host ''
    Write-Host ('=== ' + $name + ' ===') -ForegroundColor Cyan
}

$startedAt = Get-Date

# 1. CodeGraph rebuild
if (-not $SkipCodeGraph) {
    Stage 'step 1/3: codegraph rebuild'
    if (-not (Test-Path $CgScript)) {
        Write-Host ('SKIP: missing ' + $CgScript) -ForegroundColor Yellow
    } else {
        & powershell -NoProfile -ExecutionPolicy Bypass -File $CgScript
        $rc = $LASTEXITCODE
        if ($rc -eq 2) {
            Write-Host 'WARN: codegraph rebuild skipped because MCP holds DB; continuing other indexes' -ForegroundColor Yellow
        } elseif ($rc -ne 0) {
            Write-Host ('FAIL: codegraph rebuild exit=' + $rc) -ForegroundColor Red
            exit $rc
        }
    }
} else {
    Write-Host 'step 1/3: codegraph rebuild  -- skipped' -ForegroundColor DarkGray
}

# 2. Chroma reindex
if (-not $SkipChroma) {
    Stage 'step 2/3: chroma reindex'
    if (-not (Test-Path $ChromaPy)) {
        Write-Host ('FAIL: missing ' + $ChromaPy) -ForegroundColor Red
        exit 1
    }
    $env:PLATFORM_ROOT = $RepoRoot
    $argList = @('-m', 'codev_platform.chroma.indexer')
    if ($ChromaForce) { $argList += '--force' }
    & $ChromaPy @argList
    $rc = $LASTEXITCODE
    if ($rc -ne 0) {
        Write-Host ('FAIL: chroma reindex exit=' + $rc) -ForegroundColor Red
        exit $rc
    }
} else {
    Write-Host 'step 2/3: chroma reindex     -- skipped' -ForegroundColor DarkGray
}

# 3. Health summary
Stage 'step 3/3: health summary'
if (Test-Path $HealthPs1) {
    & powershell -NoProfile -ExecutionPolicy Bypass -File $HealthPs1 -Mode $HealthMode
    # ai-health exit codes: 0 green / 1 fail / 2 warn -- preserve degraded state.
    $rc = $LASTEXITCODE
} else {
    Write-Host ('WARN: ai-health.ps1 not found at ' + $HealthPs1) -ForegroundColor Yellow
    $rc = 0
}

$dur = (New-TimeSpan -Start $startedAt -End (Get-Date)).TotalSeconds
Write-Host ''
Write-Host ('total: ' + [int]$dur + 's, health exit=' + $rc) -ForegroundColor Cyan
exit $rc
