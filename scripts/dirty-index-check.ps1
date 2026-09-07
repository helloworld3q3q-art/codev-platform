# --------------------------------------------------------------------
# dirty-index-check.ps1
# 检查当前工作树是否有 dirty 文件命中 AI 索引范围(CodeGraph / Chroma)。
# 命中即提示"索引可能滞后",建议跑 update-local-ai.ps1 或允许 grep+Read 兜底。
#
# 用法:
#   dirty-index-check.ps1                # 人工查
#   dirty-index-check.ps1 -Quiet         # 只返回 ExitCode (0=clean / 1=dirty)
#   dirty-index-check.ps1 -Json          # 输出 JSON 给 AI / 其他工具
#
# Pure ASCII (PowerShell 5.1 GBK parser safety).
# --------------------------------------------------------------------

param(
    [switch]$Quiet,
    [switch]$Json
)

$ErrorActionPreference = 'Stop'

$repoRoot = (& git rev-parse --show-toplevel 2>$null)
if (-not $repoRoot -or $LASTEXITCODE -ne 0) {
    if (-not $Quiet) { Write-Host 'not a git repo' -ForegroundColor Red }
    exit 2
}
$repoRoot = $repoRoot.Trim()

# git status --porcelain -z safer than -s for files with spaces; -s OK here for our paths
$status = & git -C $repoRoot status --porcelain 2>$null
if (-not $status) {
    if ($Json) { Write-Output '{"dirty":false,"affected":{}}'; exit 0 }
    if (-not $Quiet) { Write-Host 'working tree clean' -ForegroundColor Green }
    exit 0
}

# Parse paths from status (skip status code prefix, handle rename)
$dirtyPaths = @()
foreach ($line in $status -split "`n") {
    if ($line.Length -lt 3) { continue }
    $path = $line.Substring(3).Trim()
    if ($path -match ' -> ') { $path = ($path -split ' -> ')[-1] }
    $path = $path.Trim('"').Replace('\', '/')
    if ($path) { $dirtyPaths += $path }
}

# Index scope patterns: same per-project source of truth as post-commit.ps1 /
# ai-health (meta.json health.reindex_*_patterns). Project-name-free generic
# defaults in code; each project EXTENDS via meta. No code edit to add a project.
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
    foreach ($pat in $patterns) { if ($text -match $pat) { return $true } }
    return $false
}
$codegraphPats = @('^apps/[^/]+/src/.*\.(java|ts|tsx)$') + (Get-MetaPatterns 'reindex_codegraph_patterns')
$chromaPats    = @('^docs/.*\.md$', '^\.claude/(rules|skills)/.*\.md$', '^apps/[^/]+/\.claude/rules/.*\.md$', '^tools/.*\.md$', '.*CLAUDE\.md$', '.*AGENTS\.md$', '^README\.md$') + (Get-MetaPatterns 'reindex_doc_patterns')

$affectedCg  = @($dirtyPaths | Where-Object { Test-AnyPattern $_ $codegraphPats })
$affectedCh  = @($dirtyPaths | Where-Object { Test-AnyPattern $_ $chromaPats })

$totalAffected = @($affectedCg + $affectedCh) | Sort-Object -Unique
$dirty = $totalAffected.Count -gt 0

if ($Json) {
    $payload = [ordered]@{
        dirty    = $dirty
        affected = [ordered]@{
            codegraph  = $affectedCg
            chroma     = $affectedCh
        }
        total_dirty_files = $dirtyPaths.Count
        recommendation    = if ($dirty) { 'allow grep/read fallback; run update-local-ai.ps1 before deciding' } else { 'MCP indexes are fresh; default to MCP tools' }
    }
    Write-Output (ConvertTo-Json $payload -Depth 4 -Compress)
    if ($dirty) { exit 1 } else { exit 0 }
}

if ($Quiet) {
    if ($dirty) { exit 1 } else { exit 0 }
}

# Human-readable output
Write-Host ''
if (-not $dirty) {
    Write-Host 'no dirty files in AI index scope' -ForegroundColor Green
    Write-Host ('working tree has ' + $dirtyPaths.Count + ' dirty file(s) but none in CodeGraph/Chroma scope') -ForegroundColor DarkGray
    exit 0
}

Write-Host 'WARN: dirty files in AI index scope - MCP results may be STALE' -ForegroundColor Yellow
Write-Host ''
if ($affectedCg.Count -gt 0) {
    Write-Host ('[CodeGraph  ] ' + $affectedCg.Count + ' file(s):') -ForegroundColor Cyan
    $affectedCg | ForEach-Object { Write-Host ('  ' + $_) -ForegroundColor DarkGray }
}
if ($affectedCh.Count -gt 0) {
    Write-Host ('[Chroma     ] ' + $affectedCh.Count + ' file(s):') -ForegroundColor Cyan
    $affectedCh | ForEach-Object { Write-Host ('  ' + $_) -ForegroundColor DarkGray }
}

Write-Host ''
Write-Host 'Next steps:' -ForegroundColor Yellow
Write-Host '  1. allow grep/read fallback for affected files' -ForegroundColor DarkGray
Write-Host '  2. or commit + let post-commit hook reindex' -ForegroundColor DarkGray
Write-Host '  3. or run tools/dev/update-local-ai.ps1 manually' -ForegroundColor DarkGray
exit 1
