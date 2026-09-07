# --------------------------------------------------------------------
# pre-push-audit.ps1 -- codev-platform pre-push gate.
# Runs `graph audit --all`: blocks the push if ANY project's unified graph has
# structural ERRORS (dangling edges / cross-tenant leak / orphan soft plugin).
# Warnings (duplicate nodes / low-confidence edges / no-provenance / duplicate
# edges) do NOT block.
# Skips gracefully (exit 0) when no local graph store exists -- so machines
# without graph data are never blocked.
#
# Light by design: graph audit only opens sqlite (no torch / ML), memory-cheap.
# Emergency bypass (justified hotfix only):  $env:SKIP_PREPUSH_AUDIT=1; git push
#
# Source of truth: tools/dev/pre-push-audit.ps1   Install: scripts/install-git-hooks.ps1
# Pure ASCII (PowerShell 5.1 GBK safety -- no non-ASCII in this file).
# --------------------------------------------------------------------

if ($env:SKIP_PREPUSH_AUDIT -eq '1') {
    Write-Host '[pre-push] SKIP_PREPUSH_AUDIT=1 -> skipping graph audit gate' -ForegroundColor Yellow
    exit 0
}

$repoRoot = (& git rev-parse --show-toplevel 2>$null)
if (-not $repoRoot) { exit 0 }   # not resolvable -> do not block
$repoRoot = $repoRoot.Trim()

$wslReady = $false
$wslCommand = Get-Command wsl.exe -ErrorAction SilentlyContinue
if ($wslCommand) {
    $wslExe = $wslCommand.Source
    $wslDistro = if ($env:CODEV_WSL_DISTRO) { $env:CODEV_WSL_DISTRO } else { 'Ubuntu' }
    $wslUser = if ($env:CODEV_WSL_USER) { $env:CODEV_WSL_USER } else { 'helloworld' }
    $wslPython = if ($env:CODEV_WSL_PYTHON) {
        $env:CODEV_WSL_PYTHON
    } else {
        '/home/helloworld/work/codev-platform/.venv/bin/python'
    }
    $wslDataDir = if ($env:CODEV_WSL_DATA_DIR) {
        $env:CODEV_WSL_DATA_DIR
    } else {
        '/home/helloworld/work/codev-platform/data'
    }
    & $wslExe -d $wslDistro -u $wslUser -- test -x $wslPython *> $null
    $wslReady = ($LASTEXITCODE -eq 0)
}

# UTF-8 so the audit's Chinese output is readable in the hook console.
$env:PYTHONUTF8 = '1'
$env:PYTHONIOENCODING = 'utf-8'

if ($wslReady) {
    Write-Host '[pre-push] graph audit --all in WSL data owner...' -ForegroundColor Cyan
    & $wslExe -d $wslDistro -u $wslUser -- env "PLATFORM_DATA_DIR=$wslDataDir" `
        'PYTHONUTF8=1' 'PYTHONIOENCODING=utf-8' $wslPython -I -m codev_platform.cli graph audit --all
    $rc = $LASTEXITCODE
} else {
    $py = Join-Path $repoRoot '.venv\Scripts\python.exe'
    if (-not (Test-Path $py)) {
        Write-Host '[pre-push] no local runtime -> skipping graph audit gate' -ForegroundColor DarkGray
        exit 0
    }
    Write-Host '[pre-push] graph audit --all in local runtime...' -ForegroundColor Cyan
    & $py -m codev_platform.cli graph audit --all
    $rc = $LASTEXITCODE
}

if ($rc -ne 0) {
    Write-Host '[pre-push] graph audit FAILED -> push blocked.' -ForegroundColor Red
    Write-Host '           Fix the structural errors above, or bypass a justified hotfix with:' -ForegroundColor Red
    Write-Host '           $env:SKIP_PREPUSH_AUDIT=1; git push' -ForegroundColor Red
}
exit $rc
