# --------------------------------------------------------------------
# install-git-hooks.ps1
# Copy tools/dev/git-hooks/* into .git/hooks/ for this repo.
# Run once after `git clone` or whenever git-hooks/ source changes.
#
# Idempotent: overwrites existing hooks (back up first if you have custom ones).
# Pure ASCII (PowerShell 5.1 GBK parser safety).
# --------------------------------------------------------------------

$ErrorActionPreference = 'Stop'
$RepoRoot = (Resolve-Path (Join-Path $PSScriptRoot '..\..')).Path
$SrcDir   = Join-Path $PSScriptRoot 'git-hooks'
$DstDir   = Join-Path $RepoRoot '.git\hooks'

if (-not (Test-Path $SrcDir)) {
    Write-Host ('FAIL: source dir not found: ' + $SrcDir) -ForegroundColor Red
    exit 1
}
if (-not (Test-Path $DstDir)) {
    Write-Host ('FAIL: not a git repo (no .git/hooks): ' + $DstDir) -ForegroundColor Red
    exit 1
}

$installed = 0
Get-ChildItem -Path $SrcDir -File | ForEach-Object {
    $dst = Join-Path $DstDir $_.Name
    Copy-Item -Path $_.FullName -Destination $dst -Force
    Write-Host ('  installed: ' + $_.Name + ' -> ' + $dst) -ForegroundColor Green
    $installed++
}

Write-Host ''
Write-Host ('SUMMARY: ' + $installed + ' hook(s) installed') -ForegroundColor Cyan
Write-Host 'Test with: git commit --allow-empty -m "test hook"'
Write-Host 'Reindex log: tools\chroma\reindex.log'
exit 0
