# --------------------------------------------------------------------
# install-git-hooks.ps1 (canonical, project-agnostic)
# Installs git hooks into the CURRENT repo (resolved via `git rev-parse`), so any
# business repo can run it -- not just the one this script lives in. Run from
# INSIDE the target repo (or anywhere whose cwd is in that repo):
#   powershell -File <path-to-this>
# Hook stubs are byte-copied (preserve LF; CRLF breaks sh.exe) from scripts/git-hooks/.
# They are thin sh stubs that exec the repo's own tools/dev/*.ps1 wrappers.
#
# pre-push is installed ONLY if the repo has tools/dev/pre-push-audit.ps1
# (openclaw-specific gates); repos without it (e.g. widget) get post-commit only.
#
# Idempotent: overwrites existing hooks. Pure ASCII (PowerShell 5.1 GBK safety).
# --------------------------------------------------------------------

$ErrorActionPreference = 'Stop'

$repoRoot = (& git rev-parse --show-toplevel 2>$null)
if (-not $repoRoot -or $LASTEXITCODE -ne 0) {
    Write-Host 'FAIL: not a git repo. Run this from inside the target repo.' -ForegroundColor Red
    exit 1
}
$repoRoot = $repoRoot.Trim()

$SrcDir = Join-Path $PSScriptRoot 'git-hooks'
$DstDir = Join-Path $repoRoot '.git\hooks'

if (-not (Test-Path $SrcDir)) {
    Write-Host ('FAIL: hook source dir not found: ' + $SrcDir) -ForegroundColor Red
    exit 1
}
if (-not (Test-Path $DstDir)) {
    New-Item -ItemType Directory -Path $DstDir -Force | Out-Null
}

Write-Host ('Installing git hooks into: ' + $repoRoot) -ForegroundColor Cyan

$installed = 0
Get-ChildItem -Path $SrcDir -File | ForEach-Object {
    # pre-push runs tools/dev/pre-push-audit.ps1; skip for repos that lack it.
    if ($_.Name -eq 'pre-push' -and -not (Test-Path (Join-Path $repoRoot 'tools\dev\pre-push-audit.ps1'))) {
        Write-Host ('  skip: pre-push (no tools/dev/pre-push-audit.ps1 in this repo)') -ForegroundColor DarkGray
        return
    }
    $dst = Join-Path $DstDir $_.Name
    Copy-Item -Path $_.FullName -Destination $dst -Force
    Write-Host ('  installed: ' + $_.Name + ' -> ' + $dst) -ForegroundColor Green
    $installed++
}

Write-Host ''
Write-Host ('SUMMARY: ' + $installed + ' hook(s) installed into ' + $repoRoot) -ForegroundColor Cyan
Write-Host 'Test with: git commit --allow-empty -m "test hook"'
exit 0
