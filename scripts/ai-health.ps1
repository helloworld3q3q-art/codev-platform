# --------------------------------------------------------------------
# ai-health.ps1
# Health check for local AI dev toolchain:
#   - Python venv + torch CUDA availability
#   - Embedding model present + loadable
#   - Chroma collection exists + chunk count
#   - CodeGraph SQLite DB exists + node count
#   - codegraph-api jar present (optional start probe)
#   - Git status of tools/ tree
#
# Pure ASCII (PowerShell 5.1 GBK parser safety, see windows-powershell.md).
# ExitCode: 0 all green / 1 any RED / 2 only YELLOW warnings
# --------------------------------------------------------------------

[CmdletBinding()]
param(
    # Full: 完整体检(默认,加载模型 + GPU 探测 + Chroma collection 查询),~10-15s
    # Light: 跳过模型加载 / torch / Chroma collection 加载,只查文件 mtime/sqlite 行数,~1-2s
    #        用于 post-commit 后台 reindex 后兜底校验,避免重复加载 Qwen3 模型
    [ValidateSet('Full', 'Light')]
    [string]$Mode = 'Full',
    # Optional: write structured JSON to this path for programmatic consumers (e.g. Electron widget).
    # Default empty = no JSON written; existing text output and exit code are unaffected.
    [string]$JsonOut = '',
    # Which repo's tool-stack to inspect. Default empty = derive from this script's
    # own location ($PSScriptRoot\..\..), preserving the original in-place behavior.
    # Pass an explicit path so a copy living OUTSIDE the target repo (e.g. the
    # canonical copy in codev-platform/scripts) can health-check any business repo.
    [string]$Repo = '',
    # Optional: override which project's shared index is audited (chroma collection /
    # cross_layer), independent of -Repo. Default empty = resolve from -Repo's
    # .claude/project.json. Lets codev-platform inspect any project's index from one place.
    [string]$Project = ''
)

$ErrorActionPreference = 'Stop'
$IsLight = ($Mode -eq 'Light')

# User-level config (~/.codev-platform/config.json) is the single source of truth
# for machine-specific paths (model dirs / venv / daemon port). Machine moves =>
# only edit config, never these scripts. Read once; field-missing => $null fallback.
$script:codevCfg = $null
$cfgPath = Join-Path $env:USERPROFILE '.codev-platform\config.json'
if (Test-Path $cfgPath) {
    try { $script:codevCfg = Get-Content $cfgPath -Encoding UTF8 -Raw | ConvertFrom-Json } catch { }
}
function Get-CfgPath {
    # Dotted lookup into config (e.g. 'models.embed_path'); returns $null if any
    # segment missing. Callers supply a hardcoded-path-free fallback.
    param([string]$DottedKey)
    if (-not $script:codevCfg) { return $null }
    $node = $script:codevCfg
    foreach ($seg in ($DottedKey -split '\.')) {
        if ($null -eq $node) { return $null }
        $node = $node.$seg
    }
    if ($node) { return [string]$node } else { return $null }
}

if ($Repo) {
    $RepoRoot = (Resolve-Path $Repo).Path
} else {
    $RepoRoot = (Resolve-Path (Join-Path $PSScriptRoot '..\..')).Path
}
# codev-platform repo root: this canonical script lives in codev-platform\scripts,
# so its parent is the codev-platform root. MCP recall/usage logs are written next
# to their Python modules in the codev-platform package dir (not the audited -Repo).
$CodevRoot  = Split-Path -Parent $PSScriptRoot
# Shared AI infra now lives in codev-platform (platform ownership inversion 2026-05-28),
# NOT in the audited -Repo: venv at codev-platform root .venv; chroma persist data in
# codev-platform\data\chroma; module logs (reindex/recall) in the package dir.
$ChromaDir  = Join-Path $CodevRoot 'codev_platform\chroma'
$ChromaPy   = Join-Path $CodevRoot '.venv\Scripts\python.exe'
$ChromaData = Join-Path $CodevRoot 'data\chroma'
# Embedding model dir: env override > config.models.embed_path > repo-local MiniLm.
# Machine-specific path comes from ~/.codev-platform/config.json (not hardcoded).
$Qwen3Shared = Get-CfgPath 'models.embed_path'
$MiniLmRepo  = Join-Path $RepoRoot 'models\paraphrase-multilingual-MiniLM-L12-v2'
if ($env:PLATFORM_EMBED_MODEL_PATH) {
    $ModelDir = $env:PLATFORM_EMBED_MODEL_PATH
} elseif ($Qwen3Shared -and (Test-Path $Qwen3Shared)) {
    $ModelDir = $Qwen3Shared
} else {
    $ModelDir = $MiniLmRepo
}
# Reranker (optional 2-stage rerank,Claude/Codex 共用环境变量)
# env override > config.models.reranker_path > unset.
$RerankerDefault = Get-CfgPath 'models.reranker_path'
if ($env:PLATFORM_RERANKER_MODEL_PATH) {
    $RerankerDir = $env:PLATFORM_RERANKER_MODEL_PATH
} elseif ($RerankerDefault -and (Test-Path $RerankerDefault)) {
    $RerankerDir = $RerankerDefault
} else {
    $RerankerDir = $null
}
# env override > config.models.reranker_enabled > default enabled.
$RerankerEnabled = if ($env:PLATFORM_RERANKER_ENABLED) { $env:PLATFORM_RERANKER_ENABLED -ne 'false' }
                   elseif ($script:codevCfg -and $null -ne $script:codevCfg.models.reranker_enabled) { [bool]$script:codevCfg.models.reranker_enabled }
                   else { $true }
$CgDb       = Join-Path $RepoRoot '.codegraph\codegraph.db'
$CgApiJar   = Join-Path $RepoRoot 'apps\codegraph-api\target'

$red   = 0
$amber = 0

# Buffer all check output so we can print a one-line health overview at the TOP
# (P6 UX: user sees READY/ATTENTION/BROKEN at a glance without reading the full
# log). Checks still run first; their output is flushed after the banner.
$script:report = New-Object System.Collections.ArrayList

function Line {
    param([string]$tag, [string]$status, [string]$msg)
    $color = 'Gray'
    switch ($status) {
        'OK'   { $color = 'Green' }
        'WARN' { $color = 'Yellow'; $script:amber++ }
        'FAIL' { $color = 'Red';    $script:red++ }
    }
    $pad = $tag.PadRight(20)
    [void]$script:report.Add([pscustomobject]@{
        Text   = '[' + $status.PadRight(4) + '] ' + $pad + ' ' + $msg
        Color  = $color
        Tag    = $tag
        Status = $status
        Msg    = $msg
    })
}

# Buffer a free-form section line (e.g. the usage-stats sub-header) so it stays
# in order with the buffered Line output when flushed below the banner.
function Section {
    param([string]$text, [string]$color = 'Cyan')
    [void]$script:report.Add([pscustomobject]@{ Text = $text; Color = $color })
}

Write-Host ('=== ai-health check (mode=' + $Mode + ') ===') -ForegroundColor Cyan
Write-Host ('repo: ' + $RepoRoot)

# Resolve project_id (multi-project namespace, 2026-05-27)
# $script:projectId is also consumed by the -JsonOut serialiser below.
$script:projectId = 'unknown'
$projectIdFile = Join-Path $RepoRoot '.claude\project.json'
if (Test-Path $projectIdFile) {
    try {
        $pjData = Get-Content $projectIdFile -Encoding UTF8 -Raw | ConvertFrom-Json
        if ($pjData.project_id) {
            $script:projectId = $pjData.project_id
            Write-Host ('project_id: ' + $pjData.project_id + ' (from .claude/project.json)')
        } else {
            Write-Host 'project_id: <missing field in .claude/project.json>' -ForegroundColor Yellow
        }
    } catch {
        Write-Host ('project_id: <parse failed: ' + $_.Exception.Message + '>') -ForegroundColor Yellow
    }
} else {
    Write-Host 'project_id: <none, .claude/project.json missing>' -ForegroundColor Yellow
}
# -Project override: audit a specific project's shared index regardless of -Repo.
if ($Project) {
    $projReg = Join-Path $CodevRoot ('platform_meta\projects\' + $Project)
    if (-not (Test-Path $projReg)) {
        Write-Host ('project override: ' + $Project + ' <WARNING: not registered in platform_meta/projects; auditing anyway>') -ForegroundColor Yellow
    } else {
        Write-Host ('project override: ' + $Project + ' (was ' + $script:projectId + ', via -Project)') -ForegroundColor Cyan
    }
    $script:projectId = $Project
}

# Per-project health config (config-driven, no hardcoded per-project paths in code).
# platform_meta/projects/<id>/meta.json may carry an optional "health" section, e.g.:
#   "health": { "cross_layer_source_dirs": ["apps/.../migration", "apps/.../mapper"] }
# Absent / new projects => checks degrade gracefully (counts only, no project-specific probes).
$script:healthCfg = $null
$metaJsonPath = Join-Path $CodevRoot ('platform_meta\projects\' + $script:projectId + '\meta.json')
if (Test-Path $metaJsonPath) {
    try {
        $metaParsed = Get-Content $metaJsonPath -Encoding UTF8 -Raw | ConvertFrom-Json
        if ($metaParsed.health) { $script:healthCfg = $metaParsed.health }
    } catch { }
}
Write-Host ''

# 1. Chroma venv python
if (Test-Path $ChromaPy) {
    Line 'chroma venv'      'OK'   $ChromaPy
} else {
    Line 'chroma venv'      'FAIL' ('missing: ' + $ChromaPy)
}

# 2. Embedding model dir
if (Test-Path $ModelDir) {
    $files = (Get-ChildItem -Path $ModelDir -File -Recurse -ErrorAction SilentlyContinue | Measure-Object).Count
    Line 'embed model'      'OK'   ($ModelDir + ' (' + $files + ' files)')
} else {
    Line 'embed model'      'FAIL' ('missing: ' + $ModelDir)
}

# 3. Embedding model load probe (Full only — Light 跳过,省 ~10s 模型加载)
if ($IsLight) {
    Line 'embed load'      'OK'   '(skipped in Light mode)'
}
if ((-not $IsLight) -and (Test-Path $ChromaPy) -and (Test-Path $ModelDir)) {
    $probeModel = @'
import os
import sys
try:
    from sentence_transformers import SentenceTransformer
    model_path = r"__MODEL__"
    m = SentenceTransformer(model_path, device="cpu")
    get_dim = m.get_embedding_dimension if hasattr(m, "get_embedding_dimension") else m.get_sentence_embedding_dimension
    dim = get_dim()
    max_seq = getattr(m, "max_seq_length", None)
    prompts = getattr(m, "prompts", None) or {}
    query_prompt = "query" in prompts and bool(prompts.get("query"))
    print("model=" + os.path.basename(model_path) + " dim=" + str(dim) + " max_seq=" + str(max_seq) + " query_prompt=" + str(query_prompt))
except Exception as e:
    print("ERR " + repr(e))
    sys.exit(2)
'@
    $probeModel = $probeModel.Replace('__MODEL__', $ModelDir)
    $tmpModel = Join-Path $env:TEMP ('ai_health_model_' + [guid]::NewGuid().ToString('N') + '.py')
    Set-Content -Path $tmpModel -Value $probeModel -Encoding ASCII
    try {
        $outModelRaw = & cmd /c "`"$ChromaPy`" `"$tmpModel`" 2>&1"
        $rcModel = $LASTEXITCODE
        $outModel = $outModelRaw |
                    Where-Object { $_ -match 'model=' } |
                    ForEach-Object {
                        if ($_ -match '(model=.*)$') { $Matches[1] } else { $_ }
                    } |
                    Select-Object -Last 1
        if (-not $outModel) { $outModel = $outModelRaw }
        if ($rcModel -eq 0) {
            Line 'embed load'    'OK'   ($outModel -join ' ')
        } else {
            Line 'embed load'    'FAIL' ($outModel -join ' ')
        }
    } finally {
        Remove-Item -Path $tmpModel -Force -ErrorAction SilentlyContinue
    }
}

# 3b. Reranker model presence (optional 2-stage rerank)
if ($RerankerDir -and (Test-Path $RerankerDir)) {
    $rcount = (Get-ChildItem -Path $RerankerDir -File -ErrorAction SilentlyContinue | Measure-Object).Count
    $required = @('config.json', 'tokenizer.json', 'tokenizer_config.json', 'model.safetensors')
    $missing = @()
    foreach ($f in $required) {
        if (-not (Test-Path (Join-Path $RerankerDir $f))) { $missing += $f }
    }
    if ($missing.Count -gt 0) {
        Line 'reranker model'  'WARN' ('missing required: ' + ($missing -join ',') + ' in ' + $RerankerDir)
    } elseif (-not $RerankerEnabled) {
        Line 'reranker model'  'OK'   ($RerankerDir + ' (' + $rcount + ' files, ENABLED=false)')
    } else {
        Line 'reranker model'  'OK'   ($RerankerDir + ' (' + $rcount + ' files, ENABLED)')
    }
} else {
    Line 'reranker model'  'OK'   'not configured (PLATFORM_RERANKER_MODEL_PATH unset)'
}

# 4. Torch + CUDA (only if venv python present, Full only — Light 跳过)
if ($IsLight) {
    Line 'torch cuda'      'OK'   '(skipped in Light mode)'
}
if ((-not $IsLight) -and (Test-Path $ChromaPy)) {
    $probe = @'
import sys
try:
    import torch
    ok = torch.cuda.is_available()
    name = torch.cuda.get_device_name(0) if ok else "cpu-only"
    print("torch=" + torch.__version__ + " cuda=" + str(ok) + " gpu=" + name)
except Exception as e:
    print("ERR " + repr(e))
    sys.exit(2)
'@
    $tmp = Join-Path $env:TEMP ('ai_health_torch_' + [guid]::NewGuid().ToString('N') + '.py')
    Set-Content -Path $tmp -Value $probe -Encoding ASCII
    try {
        $out = & cmd /c "`"$ChromaPy`" `"$tmp`" 2>&1"
        $rc = $LASTEXITCODE
        if ($rc -eq 0 -and $out -match 'cuda=True') {
            Line 'torch cuda'    'OK'   ($out -join ' ')
        } elseif ($rc -eq 0) {
            Line 'torch cuda'    'WARN' ($out -join ' ')
        } else {
            Line 'torch cuda'    'FAIL' ($out -join ' ')
        }
    } finally {
        Remove-Item -Path $tmp -Force -ErrorAction SilentlyContinue
    }
}

# 5. Chroma data dir + collection probe
if (Test-Path $ChromaData) {
    $sub = (Get-ChildItem -Path $ChromaData -Directory -ErrorAction SilentlyContinue | Measure-Object).Count
    Line 'chroma data dir'  'OK'   ($ChromaData + ' (' + $sub + ' segments)')

    # Collection is project-namespaced: <project_id>__platform_docs (multi-tenant daemon).
    # Audit THIS repo's project, not the legacy unprefixed or last-built collection.
    $collName = if ($script:projectId) { $script:projectId + '__platform_docs' } else { 'platform_docs' }

    # Light 模式跳过 collection probe(要加载 chromadb 模块,~1-2s)— 用 per-project .last_build 替代
    if ($IsLight) {
        $stampJson = Join-Path $ChromaData ('.last_build.' + $script:projectId + '.json')
        if (-not (Test-Path $stampJson)) { $stampJson = Join-Path $ChromaData '.last_build.json' }
        if (Test-Path $stampJson) {
            try {
                $stamp = Get-Content $stampJson -Raw -Encoding UTF8 | ConvertFrom-Json
                Line 'chroma collection' 'OK' ('chunks=' + $stamp.chunks + ' dim=' + $stamp.embed_dim + ' model=' + $stamp.embed_model + ' [' + $collName + ']')
            } catch {
                Line 'chroma collection' 'WARN' 'cannot parse .last_build stamp'
            }
        } else {
            Line 'chroma collection' 'WARN' ('.last_build.' + $script:projectId + '.json missing (run update-local-ai.ps1)')
        }
    }
    if ((-not $IsLight) -and (Test-Path $ChromaPy)) {
        $probe2 = @'
import sys, os
sys.path.insert(0, r"__CHROMA__")
os.environ.setdefault("PLATFORM_EMBED_DEVICE", "cpu")
try:
    import chromadb
    c = chromadb.PersistentClient(path=r"__DATA__")
    col = c.get_collection("__COLLECTION__")
    total = col.count()
    meta = col.metadata or {}
    dim = "empty"
    if total:
        sample = col.get(limit=1, include=["embeddings"])
        emb = sample.get("embeddings")
        if emb is not None and len(emb) > 0:
            first = emb[0]
            dim = str(len(first))
    bits = ["chunks=" + str(total), "dim=" + str(dim)]
    if meta.get("embed_model_name"):
        bits.append("model=" + str(meta.get("embed_model_name")))
    if meta.get("max_seq_length"):
        bits.append("max_seq=" + str(meta.get("max_seq_length")))
    if meta.get("query_prompt_enabled") is not None:
        bits.append("query_prompt=" + str(meta.get("query_prompt_enabled")))
    print(" ".join(bits))
except Exception as e:
    print("ERR " + repr(e))
    sys.exit(2)
'@
        $probe2 = $probe2.Replace('__CHROMA__', $ChromaDir).Replace('__DATA__', $ChromaData).Replace('__COLLECTION__', $collName)
        $tmp2 = Join-Path $env:TEMP ('ai_health_chroma_' + [guid]::NewGuid().ToString('N') + '.py')
        Set-Content -Path $tmp2 -Value $probe2 -Encoding ASCII
        try {
            $out2 = & cmd /c "`"$ChromaPy`" `"$tmp2`" 2>&1"
            $rc2 = $LASTEXITCODE
            if ($rc2 -eq 0 -and $out2 -match 'dim=1024') {
                Line 'chroma collection' 'OK'   ($out2 -join ' ')
            } elseif ($rc2 -eq 0) {
                Line 'chroma collection' 'WARN' ($out2 -join ' ')
            } else {
                Line 'chroma collection' 'FAIL' ($out2 -join ' ')
            }
        } finally {
            Remove-Item -Path $tmp2 -Force -ErrorAction SilentlyContinue
        }
    }
} else {
    Line 'chroma data dir'  'FAIL' ('missing: ' + $ChromaData + ' (run index_docs.py)')
}

# 4b. Chroma index freshness vs latest docs/rules mtime
if (Test-Path $ChromaData) {
    $indexMtime = (Get-ChildItem -Path $ChromaData -Recurse -File -ErrorAction SilentlyContinue |
                   Sort-Object LastWriteTime -Descending | Select-Object -First 1).LastWriteTime
    $docDirs = @(
        (Join-Path $RepoRoot '.claude\rules'),
        (Join-Path $RepoRoot '.claude\skills'),
        (Join-Path $RepoRoot 'docs')
    ) | Where-Object { Test-Path $_ }
    $latestDoc = $null
    foreach ($d in $docDirs) {
        $candidate = Get-ChildItem -Path $d -Recurse -Filter '*.md' -File -ErrorAction SilentlyContinue |
                     Sort-Object LastWriteTime -Descending | Select-Object -First 1
        if ($candidate -and ($null -eq $latestDoc -or $candidate.LastWriteTime -gt $latestDoc.LastWriteTime)) {
            $latestDoc = $candidate
        }
    }
    if ($indexMtime -and $latestDoc) {
        $deltaDays = [int]([math]::Round(($latestDoc.LastWriteTime - $indexMtime).TotalDays))
        if ($deltaDays -le 0) {
            Line 'chroma freshness'  'OK'   ('index newer than latest doc (' + $latestDoc.Name + ')')
        } elseif ($deltaDays -le 7) {
            Line 'chroma freshness'  'OK'   ('lag ' + $deltaDays + 'd vs ' + $latestDoc.Name)
        } else {
            Line 'chroma freshness'  'WARN' ('lag ' + $deltaDays + 'd vs ' + $latestDoc.Name + ' (run update-local-ai.ps1 -SkipCodeGraph)')
        }
    }
}

# 4b2. platform-docs daemon (multi-session GPU sharing, 2026-05-24)
# Audit #4: public /healthz returns minimal liveness only; detail (model/reranker/
# collection/project) moved to authenticated /platform/health (passthrough mode lets
# local ai-health read it; token mode without Bearer returns 401 -> show liveness only).
$DaemonPort = if ($env:PLATFORM_DOCS_DAEMON_PORT) { $env:PLATFORM_DOCS_DAEMON_PORT } elseif (Get-CfgPath 'daemon.port') { Get-CfgPath 'daemon.port' } else { '18083' }
try {
    $req = [System.Net.WebRequest]::Create('http://127.0.0.1:' + $DaemonPort + '/platform/health')
    $req.Timeout = 2000
    $req.Method = 'GET'
    $resp = $req.GetResponse()
    try {
        $reader = New-Object System.IO.StreamReader($resp.GetResponseStream())
        $body = $reader.ReadToEnd()
        $reader.Close()
        $health = $body | ConvertFrom-Json
        $projectTag = if ($health.project_id) { ' project=' + $health.project_id } else { ' project=<legacy daemon, no project_id>' }
        Line 'platform-docs daemon' 'OK' ('port=' + $DaemonPort + ' model=' + $health.model + ' reranker=' + $health.reranker + ' collection=' + $health.collection + $projectTag)
    } finally {
        $resp.Close()
    }
} catch {
    $we = $_.Exception
    $code = if ($we.InnerException -and $we.InnerException.Response) { [int]$we.InnerException.Response.StatusCode } else { 0 }
    if ($code -eq 503) {
        Line 'platform-docs daemon' 'INFO' ('port=' + $DaemonPort + ' starting (prewarming Qwen models, 30-60s typical)')
    } elseif ($code -eq 401) {
        Line 'platform-docs daemon' 'OK' ('port=' + $DaemonPort + ' up (detail /platform/health needs auth in token mode)')
    } else {
        Line 'platform-docs daemon' 'INFO' ('port=' + $DaemonPort + ' not running (auto-spawn on first Claude Code session via launcher)')
    }
}

# 4b3. platform-docs server process count (catch real duplicate GPU daemons)
try {
    # Match chroma daemon process (new module form `codev_platform.chroma.server`
    # or legacy `tools\chroma\mcp_server.py`). cross_link MCP uses same chroma
    # venv so we filter explicitly on chroma server module / script.
    $pdServers = @(Get-CimInstance Win32_Process -Filter "Name = 'python.exe' OR Name = 'pythonw.exe'" |
        Where-Object {
            $_.CommandLine -and (
                $_.CommandLine -match 'codev_platform[\\/.]chroma[\\/.]server' -or
                $_.CommandLine -match 'chroma[\\/]mcp_server\.py'
            )
        })
    $listenerOwners = @()
    try {
        $listenerOwners = @(Get-NetTCPConnection -LocalPort ([int]$DaemonPort) -ErrorAction SilentlyContinue |
            Where-Object { $_.State -eq 'Listen' } |
            Select-Object -ExpandProperty OwningProcess -Unique)
    } catch {
        $listenerOwners = @()
    }
    $gpuPids = @()
    try {
        $gpuLines = @(nvidia-smi --query-compute-apps=pid --format=csv,noheader,nounits 2>$null)
        foreach ($gpuLine in $gpuLines) {
            $pidText = ($gpuLine -as [string]).Trim()
            if ($pidText -match '^\d+$') {
                $gpuPids += [int]$pidText
            }
        }
    } catch {
        $gpuPids = @()
    }
    if ($pdServers.Count -eq 0) {
        Line 'platform-docs servers' 'OK' 'no mcp_server processes (daemon will spawn on first session)'
    } elseif ($pdServers.Count -eq 1) {
        $cmd = $pdServers[0].CommandLine
        $modeTag = if ($cmd -match '--http') { 'daemon mode --http' } else { 'stdio mode (legacy per-session)' }
        Line 'platform-docs servers' 'OK' ('1 server pid=' + $pdServers[0].ProcessId + ' (' + $modeTag + ')')
    } else {
        $serverPids = ($pdServers | ForEach-Object { $_.ProcessId }) -join ','
        $httpCount = @($pdServers | Where-Object { $_.CommandLine -match '--http' }).Count
        $stdioCount = $pdServers.Count - $httpCount
        $pdPidSet = @($pdServers | ForEach-Object { [int]$_.ProcessId })
        $pdGpuPids = @($gpuPids | Where-Object { $pdPidSet -contains $_ })
        if ($stdioCount -gt 0) {
            Line 'platform-docs servers' 'WARN' ('count=' + $pdServers.Count + ' pids=' + $serverPids + ' (' + $stdioCount + ' legacy stdio still running; kill the legacy stdio pids to reclaim GPU)')
        } elseif ($listenerOwners.Count -eq 1 -and $pdGpuPids.Count -le 1) {
            $listenerPid = $listenerOwners[0]
            $gpuText = if ($pdGpuPids.Count -eq 1) { ' gpu pid=' + $pdGpuPids[0] } else { ' gpu pid=none' }
            Line 'platform-docs servers' 'OK' ('listener pid=' + $listenerPid + $gpuText + ' process-chain pids=' + $serverPids + ' (multi-session proxy/shim chain, single active daemon)')
        } elseif ($httpCount -eq 1) {
            Line 'platform-docs servers' 'WARN' ('count=' + $pdServers.Count + ' pids=' + $serverPids + ' (1 daemon + extra process; listener count=' + $listenerOwners.Count + ')')
        } else {
            $gpuList = if ($pdGpuPids.Count -gt 0) { ($pdGpuPids -join ',') } else { 'none' }
            Line 'platform-docs servers' 'WARN' ('count=' + $pdServers.Count + ' pids=' + $serverPids + ' gpu_pids=' + $gpuList + ' listener_count=' + $listenerOwners.Count + ' (possible duplicate daemon)')
        }
    }
} catch {
    Line 'platform-docs servers' 'INFO' ('process probe skipped: ' + $_.Exception.Message)
}

# 4b4. mcp-proxy availability (required by daemon-mode launcher)
# Without mcp-proxy.exe, platform_docs_launcher.py exits 1 -> Claude Code MCP
# connection error. Disabled only if PLATFORM_DOCS_DAEMON_MODE=false.
$McpProxyExe = Join-Path $CodevRoot '.venv\Scripts\mcp-proxy.exe'
if (Test-Path $McpProxyExe) {
    Line 'mcp-proxy' 'OK' ($McpProxyExe + ' (' + [int]((Get-Item $McpProxyExe).Length / 1KB) + ' KB)')
} elseif ($env:PLATFORM_DOCS_DAEMON_MODE -eq 'false') {
    Line 'mcp-proxy' 'OK' 'not needed (PLATFORM_DOCS_DAEMON_MODE=false, daemon mode disabled)'
} else {
    Line 'mcp-proxy' 'WARN' ('missing: ' + $McpProxyExe + ' -- daemon mode launcher will fail; install: uv pip install --python ' + (Join-Path $CodevRoot '.venv\Scripts\python.exe') + ' mcp-proxy')
}

# 4c. Incident vs rules sync freshness (knowledge debt signal)
# Heuristic: latest incident-YYYY-MM-DD-*.md vs latest .claude/rules/*.md mtime.
# If incident is >7d newer than newest rule -> WARN "incident after rules untouched".
$IncidentDir = Join-Path $RepoRoot 'docs\operations'
$RulesDir    = Join-Path $RepoRoot '.claude\rules'
if ((Test-Path $IncidentDir) -and (Test-Path $RulesDir)) {
    $latestIncident = Get-ChildItem -Path $IncidentDir -Filter 'incident-*.md' -File -ErrorAction SilentlyContinue |
                      Sort-Object LastWriteTime -Descending | Select-Object -First 1
    $latestRule = Get-ChildItem -Path $RulesDir -Filter '*.md' -File -ErrorAction SilentlyContinue |
                  Sort-Object LastWriteTime -Descending | Select-Object -First 1
    if ($latestIncident -and $latestRule) {
        $gap = [int]([math]::Round(($latestIncident.LastWriteTime - $latestRule.LastWriteTime).TotalDays))
        if ($gap -le 0) {
            Line 'rules vs incident'  'OK'   ('rules newer than latest incident (' + $latestIncident.Name + ')')
        } elseif ($gap -le 7) {
            Line 'rules vs incident'  'OK'   ('incident ' + $gap + 'd ahead of rules, within window')
        } else {
            Line 'rules vs incident'  'WARN' ('incident ' + $gap + 'd ahead of rules (' + $latestIncident.Name + ') - sync rules or archive incident')
        }
    } elseif (-not $latestIncident) {
        Line 'rules vs incident'      'OK'   'no incident files (clean)'
    }
} else {
    # Uniform category for every project: show 'not configured' instead of silently
    # skipping, so all projects display the same check list (no docs/operations dir).
    Line 'rules vs incident'      'INFO' 'not configured'
}

# 4d. Cross-layer KG freshness.
# Data lives in shared codev-platform\data\codegraph_ext, namespaced PER PROJECT.
# NO legacy unprefixed fallback: the unprefixed cross_layer.sqlite is openclaw's
# historical data, so falling back would make every project show openclaw's numbers
# (the bug spotted 2026-05-28). Each project shows ONLY its own per-project DB; a
# toolstack repo with no full-stack chains (e.g. codev-platform) correctly shows
# 'not built'. Repos without a cross-link MCP (e.g. widget) skip cleanly.
$hasCrossLink = $false
$mcpFile = Join-Path $RepoRoot '.mcp.json'
if (Test-Path $mcpFile) {
    try {
        $mcpData = Get-Content $mcpFile -Encoding UTF8 -Raw | ConvertFrom-Json
        if ($mcpData.mcpServers -and $mcpData.mcpServers.'cross-link') { $hasCrossLink = $true }
    } catch { }
}
$CrossLayerDb = Join-Path $CodevRoot ('data\codegraph_ext\' + $script:projectId + '\cross_layer.sqlite')
if ($hasCrossLink -and (Test-Path $CrossLayerDb)) {
    $size = [math]::Round((Get-Item $CrossLayerDb).Length / 1KB, 1)
    if (Test-Path $ChromaPy) {
        $probe3 = @'
import sys, sqlite3
try:
    conn = sqlite3.connect(r"__DB__")
    cur = conn.cursor()
    cur.execute("select count(*) from nodes")
    n = cur.fetchone()[0]
    cur.execute("select count(*) from edges")
    e = cur.fetchone()[0]
    cur.execute("select value from build_meta where key='last_build_at'")
    row = cur.fetchone()
    last = row[0] if row else "?"
    print("nodes=" + str(n) + " edges=" + str(e) + " last=" + last)
except Exception as exc:
    print("ERR " + repr(exc))
    sys.exit(2)
'@
        $probe3 = $probe3.Replace('__DB__', $CrossLayerDb)
        $tmp3 = Join-Path $env:TEMP ('ai_health_xlayer_' + [guid]::NewGuid().ToString('N') + '.py')
        Set-Content -Path $tmp3 -Value $probe3 -Encoding ASCII
        try {
            $out3 = & cmd /c "`"$ChromaPy`" `"$tmp3`" 2>&1"
            $rc3 = $LASTEXITCODE
            if ($rc3 -eq 0) {
                # Compare last_build_at vs latest source mtime. Source dirs are
                # project-declared in meta.json health.cross_layer_source_dirs
                # (no hardcoded per-project paths). Absent => skip lag, show counts only.
                $srcDirs = @()
                if ($script:healthCfg -and $script:healthCfg.cross_layer_source_dirs) {
                    foreach ($rel in $script:healthCfg.cross_layer_source_dirs) {
                        $srcDirs += (Join-Path $RepoRoot ($rel -replace '/', '\'))
                    }
                }
                $latestSrc = $null
                foreach ($d in $srcDirs) {
                    if (Test-Path $d) {
                        $c = Get-ChildItem -Path $d -Recurse -File -ErrorAction SilentlyContinue |
                             Sort-Object LastWriteTime -Descending | Select-Object -First 1
                        if ($c -and ($null -eq $latestSrc -or $c.LastWriteTime -gt $latestSrc.LastWriteTime)) {
                            $latestSrc = $c
                        }
                    }
                }
                $buildAt = $null
                if ($out3 -match 'last=(\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2})') {
                    $buildAt = [datetime]::ParseExact($Matches[1], 'yyyy-MM-dd HH:mm:ss', $null)
                }
                if ($buildAt -and $latestSrc) {
                    $lagDays = [int]([math]::Round(($latestSrc.LastWriteTime - $buildAt).TotalDays))
                    if ($lagDays -le 0) {
                        Line 'cross_layer' 'OK'   ($out3 -join ' ')
                    } elseif ($lagDays -le 1) {
                        Line 'cross_layer' 'OK'   (($out3 -join ' ') + ' (lag <=1d)')
                    } else {
                        Line 'cross_layer' 'WARN' (($out3 -join ' ') + ' (lag ' + $lagDays + 'd vs ' + $latestSrc.Name + ' - run python -m cross_link.build_index)')
                    }
                } else {
                    Line 'cross_layer' 'OK'   ($out3 -join ' ')
                }
            } else {
                Line 'cross_layer' 'FAIL' ($out3 -join ' ')
            }
        } finally {
            Remove-Item -Path $tmp3 -Force -ErrorAction SilentlyContinue
        }
    }
} elseif (-not $hasCrossLink) {
    Line 'cross_layer' 'INFO' 'not configured'
} else {
    Line 'cross_layer' 'INFO' 'configured, index not built'
}

# 4e. cross-link MCP process diagnostics
# cross-link is still per-session stdio. Multiple sessions are expected, but
# report process-chain roots so uv/venv shim children do not look like extra
# independent sessions.
try {
    $clServers = @(Get-CimInstance Win32_Process -Filter "Name = 'python.exe' OR Name = 'pythonw.exe'" |
        Where-Object {
            $_.CommandLine -and
            $_.CommandLine -match 'cross_link[\\/]mcp_server\.py'
        })
    if ($clServers.Count -eq 0) {
        Line 'cross-link mcp' 'OK' 'no running cross-link MCP server'
    } else {
        $clPidSet = @($clServers | ForEach-Object { [int]$_.ProcessId })
        $clRootServers = @($clServers | Where-Object { -not ($clPidSet -contains [int]$_.ParentProcessId) })
        $chainCount = $clRootServers.Count
        if ($chainCount -le 0) { $chainCount = $clServers.Count }
        $pids = ($clServers | ForEach-Object { $_.ProcessId }) -join ','
        if ($chainCount -eq 1) {
            Line 'cross-link mcp' 'INFO' ('stdio chain count=1 process-chain pids=' + $pids)
        } else {
            Line 'cross-link mcp' 'INFO' ('stdio chain count=' + $chainCount + ' process-chain pids=' + $pids + ' (expected with multiple AI sessions; read-only queries share cross_layer db)')
        }
    }
} catch {
    Line 'cross-link mcp' 'WARN' ('process probe failed: ' + $_.Exception.Message)
}

# 5. CodeGraph DB + node count
# Check .rebuild.lock first - if rebuild in progress, DB may show half-truncated
# state (race condition where ai-health probes during TRUNCATE+REPOPULATE window).
$CgLockPath = Join-Path $RepoRoot '.codegraph\.rebuild.lock'
if (Test-Path $CgLockPath) {
    try {
        $lockRaw = Get-Content $CgLockPath -Raw
        $lock = $lockRaw | ConvertFrom-Json
        $startTime = [datetime]::ParseExact($lock.started_at, 'yyyy-MM-dd HH:mm:ss', $null)
        $elapsed = [int]((Get-Date) - $startTime).TotalSeconds
        Line 'codegraph db'  'WARN' ('rebuild in progress (mode=' + $lock.mode + ', pid=' + $lock.pid + ', ' + $elapsed + 's elapsed) - retry ai-health later')
    } catch {
        Line 'codegraph db'  'WARN' ('rebuild lock present but unreadable: ' + $_.Exception.Message)
    }
} elseif (Test-Path $CgDb) {
    $size = [math]::Round((Get-Item $CgDb).Length / 1MB, 1)
    $py = if (Test-Path $ChromaPy) { $ChromaPy } else { 'python' }
    # Combined probe: integrity_check + journal_mode + node/edge counts.
    # Why: counts alone hide two failure modes
    #   (a) integrity_check != ok -> DB silently corrupt
    #   (b) journal_mode = delete -> codegraph CLI is on WASM fallback
    #       (native better-sqlite3 not installed) -> 5-10x slower + much
    #       higher multi-process lock/corruption risk
    $cgProbe = "import sqlite3,sys" + [char]10 +
               "c=sqlite3.connect(sys.argv[1])" + [char]10 +
               "cur=c.cursor()" + [char]10 +
               "cur.execute('PRAGMA integrity_check')" + [char]10 +
               "ic=cur.fetchone()[0]" + [char]10 +
               "cur.execute('PRAGMA journal_mode')" + [char]10 +
               "jm=cur.fetchone()[0]" + [char]10 +
               "cur.execute('select count(*) from nodes')" + [char]10 +
               "n=cur.fetchone()[0]" + [char]10 +
               "cur.execute('select count(*) from edges')" + [char]10 +
               "e=cur.fetchone()[0]" + [char]10 +
               "print('integrity=' + str(ic) + ' journal_mode=' + str(jm) + ' nodes=' + str(n) + ' edges=' + str(e))"
    $tmpProbe = Join-Path $env:TEMP ('cg_probe_' + [guid]::NewGuid().ToString('N') + '.py')
    [System.IO.File]::WriteAllText($tmpProbe, $cgProbe, [System.Text.UTF8Encoding]::new($false))
    try {
        $out3 = & cmd /c ('"' + $py + '" "' + $tmpProbe + '" "' + $CgDb + '" 2>&1')
        $rc3 = $LASTEXITCODE
    } finally {
        Remove-Item -Path $tmpProbe -Force -ErrorAction SilentlyContinue
    }
    $joined3 = ($out3 -join ' ')
    if ($rc3 -ne 0) {
        Line 'codegraph db'  'FAIL' ($CgDb + ' present but query failed (corrupt/locked?): ' + $joined3 + ' -- run scripts/codegraph/rebuild_index.ps1 -Full')
    } elseif ($joined3 -notmatch 'integrity=ok') {
        Line 'codegraph db'  'FAIL' ($CgDb + ' (' + $size + ' MB) INTEGRITY BROKEN: ' + $joined3 + ' -- run scripts/codegraph/rebuild_index.ps1 -Full')
    } elseif ($joined3 -match 'journal_mode=delete' -or $joined3 -match 'journal_mode=memory') {
        Line 'codegraph db'  'WARN' ($CgDb + ' (' + $size + ' MB) on WASM fallback: ' + $joined3 + ' -- run scripts/codegraph/install_native_sqlite.ps1')
    } else {
        Line 'codegraph db'  'OK'   ($CgDb + ' (' + $size + ' MB, ' + $joined3 + ')')
    }
} else {
    Line 'codegraph db'      'FAIL' ('missing: ' + $CgDb + ' (run scripts/codegraph/rebuild_index.bat)')
}

# 5b. CodeGraph lock diagnostics
try {
    $cgMcp = @(Get-CimInstance Win32_Process -Filter "Name = 'node.exe'" |
        Where-Object {
            $_.CommandLine -and
            $_.CommandLine -match 'codegraph' -and
            $_.CommandLine -match 'serve' -and
            $_.CommandLine -match '--mcp'
        })
    if ($cgMcp.Count -gt 1) {
        $pids = ($cgMcp | ForEach-Object { $_.ProcessId }) -join ','
        Line 'codegraph mcp' 'INFO' ('stdio servers count=' + $cgMcp.Count + ' pids=' + $pids + ' (expected with multiple AI sessions; watch codegraph db/lock status)')
    } elseif ($cgMcp.Count -eq 1) {
        Line 'codegraph mcp' 'INFO' ('server pid=' + $cgMcp[0].ProcessId + ' (DB may be locked for codegraph CLI status/sync)')
    } else {
        Line 'codegraph mcp' 'OK' 'no running codegraph MCP server'
    }
} catch {
    Line 'codegraph mcp' 'WARN' ('process probe failed: ' + $_.Exception.Message)
}

$CgCliLockPath = Join-Path $RepoRoot '.codegraph\codegraph.db.lock'
if (Test-Path $CgCliLockPath) {
    try {
        $lockItem = Get-Item $CgCliLockPath -Force
        $ageMin = [math]::Round(((Get-Date) - $lockItem.LastWriteTime).TotalMinutes, 1)
        $status = if ($ageMin -ge 10) { 'WARN' } else { 'INFO' }
        Line 'codegraph cli lock' $status ('path=' + $CgCliLockPath + ' age_min=' + $ageMin)
    } catch {
        Line 'codegraph cli lock' 'WARN' ('probe failed: ' + $_.Exception.Message)
    }
}

# 6. codegraph-api jar (optional Java component; absent in most repos)
$CgApiDir = Join-Path $RepoRoot 'apps\codegraph-api'
$jar = Get-ChildItem -Path $CgApiJar -Filter 'codegraph-api-*.jar' -ErrorAction SilentlyContinue | Select-Object -First 1
if ($jar) {
    Line 'codegraph-api'     'OK'   $jar.Name
} elseif (-not (Test-Path $CgApiDir)) {
    Line 'codegraph-api'     'INFO' 'not configured'
} else {
    Line 'codegraph-api'     'WARN' ('no jar in ' + $CgApiJar + ' (run mvn package if needed)')
}

# 7. post-commit hook missed-fire detection
# If HEAD commit touches indexable files but its SHA isn't in reindex.log,
# the sh stub likely hit errno 1 and the hook silently failed. WARN + offer fix.
$ReindexLog = Join-Path $RepoRoot 'tools\chroma\reindex.log'
try {
    $headSha = (& git -C $RepoRoot rev-parse HEAD 2>$null).Trim()
    if ($headSha) {
        $headFiles = & git -C $RepoRoot diff-tree --no-commit-id --name-only -r HEAD 2>$null
        # Indexable-scope patterns: project-name-free generics, EXTENDED per project via
        # meta.json health.indexable_patterns. Detects "this commit should have triggered
        # a reindex". Adding a project needs no code edit.
        $defaultIndexable = @(
            '^docs/.*\.md$',
            '^\.claude/(rules|skills)/.*\.md$',
            '^apps/[^/]+/\.claude/rules/.*\.md$',
            '^tools/.*\.md$',
            '.*CLAUDE\.md$',
            '.*AGENTS\.md$',
            'README\.md$',
            '^apps/[^/]+/src/.*\.(java|ts|tsx)$'
        )
        # Reuse the same per-project reindex-scope patterns post-commit.ps1 uses
        # (single source of truth in meta.json health.reindex_*_patterns).
        $metaIndexable = @()
        foreach ($k in @('reindex_doc_patterns', 'reindex_cross_link_patterns', 'reindex_codegraph_patterns')) {
            if ($script:healthCfg -and $script:healthCfg.$k) { $metaIndexable += @($script:healthCfg.$k) }
        }
        $indexablePatterns = $defaultIndexable + $metaIndexable
        $shouldTrigger = $false
        foreach ($f in $headFiles) {
            $p = $f -replace '\\', '/'
            foreach ($pat in $indexablePatterns) { if ($p -match $pat) { $shouldTrigger = $true; break } }
            if ($shouldTrigger) { break }
        }
        if (-not $shouldTrigger) {
            Line 'hook missed?'  'OK'   ('HEAD ' + $headSha.Substring(0,7) + ' touches no indexable file')
        } elseif (Test-Path $ReindexLog) {
            $found = Select-String -Path $ReindexLog -Pattern $headSha -SimpleMatch -Quiet
            if ($found) {
                Line 'hook missed?'  'OK'   ('HEAD ' + $headSha.Substring(0,7) + ' found in reindex.log')
            } else {
                Line 'hook missed?'  'WARN' ('HEAD ' + $headSha.Substring(0,7) + ' touches indexable files but NOT in reindex.log - run powershell -File tools/dev/post-commit.ps1 to retry')
            }
        } else {
            Line 'hook missed?'  'WARN' ('reindex.log missing - hook may have never run')
        }
    }
} catch {
    Line 'hook missed?'      'WARN' ('detection error: ' + $_.Exception.Message)
}

# 8. Git status of tools/ tree
try {
    $gitOut = & git -C $RepoRoot status --short tools/ 2>&1
    if ([string]::IsNullOrWhiteSpace(($gitOut -join ''))) {
        Line 'git tools/'    'OK'   'clean'
    } else {
        $cnt = ($gitOut | Measure-Object -Line).Lines
        Line 'git tools/'    'WARN' ($cnt.ToString() + ' uncommitted file(s)')
    }
} catch {
    Line 'git tools/'        'WARN' 'git not available'
}

# 9. Usage stats (Chroma hit rate / reindex frequency / MCP usage proxy)
Section ''
Section '--- usage stats (last 7 days) ---' 'Cyan'

# 9.1 search_recall hit rate + top1 distance
# Log lives in codev-platform package dir (chroma module writes next to itself),
# shared across projects; not in the audited -Repo's tools\chroma.
$recallFile = Join-Path $CodevRoot 'codev_platform\chroma\search_recall.jsonl'
try {
    if (Test-Path $recallFile) {
        $cutoff = (Get-Date).AddDays(-7)
        $recent = @()
        Get-Content $recallFile -Encoding UTF8 | ForEach-Object {
            if ($_ -and $_.Trim()) {
                try {
                    $obj = $_ | ConvertFrom-Json
                    $ts = $null
                    if ($obj.ts) { $ts = [datetime]$obj.ts }
                    if ($ts -eq $null -or $ts -ge $cutoff) { $recent += $obj }
                } catch { }
            }
        }
        $total = $recent.Count
        if ($total -eq 0) {
            Line 'search_recall'  'WARN' 'no recent queries (last 7d); run more search_docs to accumulate baseline'
        } else {
            $withHits = @($recent | Where-Object { $_.hit -gt 0 }).Count
            $hitRate = [math]::Round(100.0 * $withHits / $total, 1)
            # top1 distance median (lower = more relevant in Chroma cosine)
            $top1Dists = @($recent | Where-Object { $_.top5 -and $_.top5.Count -gt 0 } | ForEach-Object { $_.top5[0].distance })
            $medDist = if ($top1Dists.Count -gt 0) {
                [math]::Round(($top1Dists | Sort-Object)[[math]::Floor($top1Dists.Count / 2)], 3)
            } else { 'n/a' }
            $status = if ($hitRate -lt 80) { 'WARN' } elseif ($total -lt 30) { 'INFO' } else { 'OK' }
            $sampleText = if ($total -lt 30) { ' / baseline=small(<30)' } else { '' }
            Line 'search_recall'  $status ($total.ToString() + ' queries / hit_rate=' + $hitRate + '% / median_top1_dist=' + $medDist + $sampleText)
        }
    } else {
        Line 'search_recall'      'WARN' 'search_recall.jsonl not found'
    }
} catch {
    Line 'search_recall'          'WARN' ('parse error: ' + $_.Exception.Message)
}

# 9.2 reindex frequency (last 7d)
# reindex.log is per-repo: each repo's post-commit hook writes its own tools\chroma\reindex.log.
$reindexLog2 = Join-Path $RepoRoot 'tools\chroma\reindex.log'
try {
    if (Test-Path $reindexLog2) {
        $cutoff = (Get-Date).AddDays(-7)
        $matches = Select-String -Path $reindexLog2 -Pattern 'reindex started at' -SimpleMatch
        $recentRuns = 0
        foreach ($m in $matches) {
            if ($m.Line -match 'reindex started at (\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2})') {
                try {
                    $ts = [datetime]$matches[0].Matches[0].Groups[1].Value
                    if (([datetime]$Matches[1]) -ge $cutoff) { $recentRuns++ }
                } catch { }
            }
        }
        # Simpler: just count last 7d lines
        $recentRuns = (Select-String -Path $reindexLog2 -Pattern 'reindex started at' -SimpleMatch | ForEach-Object {
            if ($_.Line -match 'reindex started at (\d{4}-\d{2}-\d{2})') {
                $d = [datetime]$Matches[1]
                if ($d -ge $cutoff) { 1 } else { 0 }
            } else { 0 }
        } | Measure-Object -Sum).Sum
        if (-not $recentRuns) { $recentRuns = 0 }
        Line 'reindex 7d'       'OK'  ($recentRuns.ToString() + ' runs (post-commit + manual)')
    } else {
        # Informational only: chroma freshness + 'hook missed?' cover real health.
        # Absence just means no reindex logged in this repo's tools\chroma yet.
        Line 'reindex 7d'       'INFO' 'no reindex.log yet (freshness/hook checks cover health)'
    }
} catch {
    Line 'reindex 7d'           'WARN' ('parse error: ' + $_.Exception.Message)
}

# 9.3 MCP usage stats: search_docs last 7d.
# Commit count alone is too noisy because many commits are L1. Also compute
# a rough L2/L3 candidate denominator from changed paths.
try {
    # Candidate / strict MCP-path patterns drive the "platform-docs adopt" metric
    # (which commits should have used MCP). Defaults below are project-name-free
    # generics; a project EXTENDS them via meta.json health.mcp_candidate_patterns /
    # mcp_strict_patterns (regex strings). Adding a project needs no code edit.
    $defaultCandidatePatterns = @(
        '^apps/[^/]+/src/.*\.(java|ts|tsx|less)$',
        '^\.claude/(rules|skills)/.*\.md$',
        '^apps/[^/]+/\.claude/rules/.*\.md$',
        '^docs/.*\.md$',
        '^tools/(dev|chroma|cross_link)/',
        '^scripts/.*\.(ps1|cmd|bat)$'
    )
    $defaultStrictPatterns = @(
        '^\.claude/(rules|skills)/',
        '^tools/(dev|chroma|cross_link)/'
    )
    $metaCandidate = if ($script:healthCfg -and $script:healthCfg.mcp_candidate_patterns) { @($script:healthCfg.mcp_candidate_patterns) } else { @() }
    $metaStrict    = if ($script:healthCfg -and $script:healthCfg.mcp_strict_patterns) { @($script:healthCfg.mcp_strict_patterns) } else { @() }
    $candidatePatterns = $defaultCandidatePatterns + $metaCandidate
    $strictPatterns    = $defaultStrictPatterns + $metaStrict

    function Test-McpCandidatePath {
        param([string]$PathText, [string[]]$Patterns)
        $p = $PathText -replace '\\', '/'
        foreach ($pat in $Patterns) { if ($p -match $pat) { return $true } }
        return $false
    }

    function Test-StrictMcpPath {
        param([string]$PathText, [string[]]$Patterns)
        $p = $PathText -replace '\\', '/'
        foreach ($pat in $Patterns) { if ($p -match $pat) { return $true } }
        return $false
    }

    $sinceArg = '--since=7.days.ago'
    $commitCount = (& git -C $RepoRoot log $sinceArg --oneline 2>&1 | Measure-Object -Line).Lines
    if (-not $commitCount) { $commitCount = 0 }
    $candidateCommitCount = 0
    $strictCommitCount = 0
    $rawLog = @(& git -C $RepoRoot log $sinceArg --name-only --format='__COMMIT__%H' 2>$null)
    $seenCommit = $false
    $hasCandidate = $false
    $hasStrict = $false
    foreach ($line in $rawLog) {
        $text = ($line -as [string]).Trim()
        if (-not $text) { continue }
        if ($text -match '^__COMMIT__') {
            if ($seenCommit) {
                if ($hasCandidate) { $candidateCommitCount++ }
                if ($hasStrict) { $strictCommitCount++ }
            }
            $seenCommit = $true
            $hasCandidate = $false
            $hasStrict = $false
            continue
        }
        if (Test-McpCandidatePath $text $candidatePatterns) { $hasCandidate = $true }
        if (Test-StrictMcpPath $text $strictPatterns) { $hasStrict = $true }
    }
    if ($seenCommit) {
        if ($hasCandidate) { $candidateCommitCount++ }
        if ($hasStrict) { $strictCommitCount++ }
    }

    $queryCount = 0
    if (Test-Path $recallFile) {
        $cutoff7 = (Get-Date).AddDays(-7)
        Get-Content $recallFile -Encoding UTF8 | ForEach-Object {
            if ($_ -and $_.Trim()) {
                try {
                    $rec = $_ | ConvertFrom-Json
                    if ($rec.ts) {
                        $ts = [datetime]$rec.ts
                        if ($ts -ge $cutoff7) { $queryCount++ }
                    }
                } catch { }
            }
        }
    }
    if ($queryCount -eq 0 -and $candidateCommitCount -gt 0) {
        Line 'platform-docs usage' 'WARN' ('0 search_docs / ' + $candidateCommitCount.ToString() + ' L2L3 candidate commits; platform-docs adoption is missing')
    } else {
        $detail = $queryCount.ToString() + ' search_docs (last 7d)'
        if ($commitCount -gt 0) {
            $ratio = [math]::Round(1.0 * $queryCount / $commitCount, 2)
            $detail += ' / ' + $commitCount.ToString() + ' commits = ' + $ratio.ToString()
        }
        Line 'platform-docs usage' 'INFO' $detail
    }
    if ($candidateCommitCount -gt 0) {
        $candidateRatio = [math]::Round(1.0 * $queryCount / $candidateCommitCount, 2)
        Line 'platform-docs adopt' 'INFO' ('L2L3_candidate_commits=' + $candidateCommitCount.ToString() + ' strict_MCP_candidate_commits=' + $strictCommitCount.ToString() + ' search_docs_per_candidate=' + $candidateRatio.ToString() + ' (platform-docs only)')
    } elseif ($commitCount -gt 0) {
        Line 'platform-docs adopt' 'INFO' 'no L2/L3 candidate commits detected in last 7d'
    }
} catch {
    Line 'mcp usage 7d'         'WARN' ('compute error: ' + $_.Exception.Message)
}

# 9.4 cross-link usage stats from structured cross_link_usage.jsonl (per-tool telemetry).
# The log is written by codev_platform.cross_link.server next to its module file
# (codev-platform package dir), shared across all projects; records carry project_id.
# Locate via the canonical script root ($PSScriptRoot\.. = codev-platform root),
# which is correct regardless of the -Repo business repo being audited.
try {
    $ClUsageLog = Join-Path $CodevRoot 'codev_platform\cross_link\cross_link_usage.jsonl'
    if (Test-Path $ClUsageLog) {
        $cutoff7b = (Get-Date).AddDays(-7)
        $clRows = @()
        Get-Content $ClUsageLog -Encoding UTF8 | ForEach-Object {
            if ($_ -and $_.Trim()) {
                try {
                    $o = $_ | ConvertFrom-Json
                    $keep = $true
                    if ($o.ts) { $t = [datetime]$o.ts; if ($t -lt $cutoff7b) { $keep = $false } }
                    if ($keep) { $clRows += $o }
                } catch { }
            }
        }
        $clTotal = $clRows.Count
        if ($clTotal -eq 0) {
            Line 'cross-link usage' 'INFO' 'no cross-link calls (last 7d)'
        } else {
            $byTool = @($clRows | Group-Object tool | ForEach-Object { $_.Name + '=' + $_.Count })
            $okCount = @($clRows | Where-Object { $_.ok }).Count
            $okRate = [math]::Round(100.0 * $okCount / $clTotal, 0)
            $lat = @($clRows | Where-Object { $_.elapsed_ms -ne $null } | ForEach-Object { [double]$_.elapsed_ms } | Sort-Object)
            $medLat = if ($lat.Count -gt 0) { [math]::Round($lat[[math]::Floor($lat.Count / 2)], 0).ToString() + 'ms' } else { 'n/a' }
            $clDetail = $clTotal.ToString() + ' calls / ok=' + $okRate.ToString() + '% / median=' + $medLat + ' / ' + ($byTool -join ',')
            Line 'cross-link usage' 'INFO' ($clDetail + ' last 7d')
        }
    } else {
        Line 'cross-link usage' 'INFO' 'cross_link_usage.jsonl not found (no calls yet)'
    }
} catch {
    Line 'cross-link usage' 'WARN' ('compute error: ' + $_.Exception.Message)
}

Line 'codegraph usage' 'INFO' 'not logged by project scripts yet; ai-health reports process/db health only'

# --- Top overview banner (P6) ---
# All checks have run; $red / $amber now reflect the whole run. Print a single
# one-line verdict ABOVE the detailed report so the user knows overall health
# at a glance:
#   >>> READY <<<      all critical OK, no warnings
#   >>> ATTENTION <<<  critical OK but >=1 WARN (degraded, usable)
#   >>> BROKEN <<<     >=1 FAIL (daemon down / venv missing / model missing)
if ($red -gt 0) {
    $overview = '>>> BROKEN <<<    ' + $red + ' FAIL / ' + $amber + ' WARN (fix critical items below)'
    $overviewColor = 'Red'
} elseif ($amber -gt 0) {
    $overview = '>>> ATTENTION <<< all critical OK, ' + $amber + ' WARN (degraded, still usable)'
    $overviewColor = 'Yellow'
} else {
    $overview = '>>> READY <<<     all checks green'
    $overviewColor = 'Green'
}
Write-Host $overview -ForegroundColor $overviewColor
Write-Host ''

# Flush buffered check output below the banner
foreach ($row in $script:report) {
    Write-Host $row.Text -ForegroundColor $row.Color
}

# --- Optional JSON output (-JsonOut) ---
# Serialise buffered check results to a structured JSON file for programmatic
# consumers (e.g. Electron widget).  This block runs AFTER the verdict is known
# so $red/$amber are final.  Any failure here is non-fatal: we catch, warn, and
# let the normal exit-code path proceed unchanged.
if ($JsonOut) {
    try {
        $verdict = if ($red -gt 0) { 'BROKEN' } elseif ($amber -gt 0) { 'ATTENTION' } else { 'READY' }
        # Filter to rows that have a Status field (Line calls); Section rows lack it.
        $checks = @($script:report |
            Where-Object { $_.PSObject.Properties.Name -contains 'Status' } |
            ForEach-Object {
                @{ tag = $_.Tag; status = $_.Status; msg = $_.Msg }
            })
        $okCount = @($checks | Where-Object { $_['status'] -eq 'OK' }).Count
        $payload = [ordered]@{
            schema_version = 1
            project_id     = $script:projectId
            mode           = $Mode
            generated_at   = (Get-Date).ToString('yyyy-MM-ddTHH:mm:sszzz')
            verdict        = $verdict
            fail_count     = $red
            warn_count     = $amber
            ok_count       = $okCount
            checks         = $checks
        }
        $jsonText = $payload | ConvertTo-Json -Depth 5
        $outDir = Split-Path $JsonOut -Parent
        if ($outDir -and -not (Test-Path $outDir)) {
            New-Item -ItemType Directory -Force -Path $outDir | Out-Null
        }
        # Write UTF-8 WITHOUT BOM. PS5.1 Out-File -Encoding UTF8 prepends a BOM
        # (EF BB BF) that breaks Node's JSON.parse on the consuming widget side.
        [System.IO.File]::WriteAllText($JsonOut, $jsonText, (New-Object System.Text.UTF8Encoding($false)))
        Write-Host ('[json] wrote ' + $JsonOut)
    } catch {
        Write-Warning ('[json] failed to write ' + $JsonOut + ': ' + $_.Exception.Message)
    }
}

Write-Host ''
if ($red -gt 0) {
    Write-Host ('SUMMARY: ' + $red + ' FAIL / ' + $amber + ' WARN') -ForegroundColor Red
    exit 1
} elseif ($amber -gt 0) {
    Write-Host ('SUMMARY: all critical OK, ' + $amber + ' WARN') -ForegroundColor Yellow
    exit 2
} else {
    Write-Host 'SUMMARY: all green' -ForegroundColor Green
    exit 0
}
