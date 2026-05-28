# sync-memory.ps1 - SessionStart hook: sync docs/memory/*.md to Claude Code autoload dir
#
# Why: Claude Code harness autoload path is hardcoded at
#      ~/.claude/projects/<cwd-encoded>/memory/, not configurable via settings.json.
#      Put memory source of truth in repo docs/memory/ (git-tracked) and this script
#      copies them over at SessionStart, preserving autoload + enabling cross-machine
#      sync + Chroma indexing.
#
# Behavior:
#   1. Remove existing *.md from autoload dir (clean stale)
#   2. Copy docs/memory/*.md over
#   3. Silent failure (hook must not block session start)
#
# Trigger: .claude/settings.json hooks.SessionStart
#
# ASCII-only file (per windows-powershell.md rule: no Chinese / em-dash in .ps1)

$ErrorActionPreference = "SilentlyContinue"

# Repo root: this canonical script lives in <repo>/scripts/, go up 2 levels.
# git rev-parse is preferred (works regardless of where the copy lives), with the
# $PSScriptRoot derivation as fallback when not in a git context.
$repoRoot = (& git -C $PSScriptRoot rev-parse --show-toplevel 2>$null)
if ($repoRoot -and $LASTEXITCODE -eq 0) {
    $repoRoot = $repoRoot.Trim().Replace('/', '\')
} else {
    $repoRoot = Split-Path -Parent (Split-Path -Parent $PSScriptRoot)
}
$srcDir = Join-Path $repoRoot "docs\memory"

# Claude Code autoload path: ~/.claude/projects/<cwd-encoded>/memory/
# cwd encoding: drive + path with every '\', '/', ':' replaced by '-'
# (e.g. D:\WorkSpace\codev-platform -> D--WorkSpace-codev-platform).
# Derived from the actual repo root so a machine / repo move needs no edit.
$cwdEncoded = $repoRoot -replace '[\\/:]', '-'
$dstDir = Join-Path $env:USERPROFILE ".claude\projects\$cwdEncoded\memory"

# Source missing (e.g. first clone before memory committed) - skip silently
if (-not (Test-Path $srcDir)) { exit 0 }

# Create dst if missing
if (-not (Test-Path $dstDir)) {
    New-Item -ItemType Directory -Force -Path $dstDir | Out-Null
}

# Clear old md (keep non-md files like .DS_Store)
Get-ChildItem -Path $dstDir -Filter "*.md" -File -ErrorAction SilentlyContinue |
    Remove-Item -Force -ErrorAction SilentlyContinue

# Copy all md from src
Copy-Item -Path (Join-Path $srcDir "*.md") -Destination $dstDir -Force -ErrorAction SilentlyContinue

exit 0
