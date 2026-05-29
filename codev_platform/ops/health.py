r"""codev_platform.ops.health -- cross-platform tool-stack health check.

Port of scripts/ai-health.ps1 to a config-driven, cross-platform Python CLI
subcommand (`codev-platform health`). All machine-specific paths come from
~/.codev-platform/config.json via codev_platform.ops._common / core (no hardcoded
D:\models etc). Windows-only PowerShell plumbing (CIM process probes, nvidia-smi
parsing) is reimplemented portably or degraded to INFO on non-supported platforms.

Checks (parity with the .ps1):
  - chroma venv python present
  - embed model dir + load probe (Full only)
  - reranker model dir
  - torch + CUDA probe (Full only)
  - chroma data dir + collection probe (Full) / .last_build stamp (Light)
  - chroma index freshness vs latest docs/rules mtime
  - platform-docs daemon /health (HTTP)
  - platform-docs server process count
  - mcp-proxy presence
  - rules vs incident freshness
  - cross_layer KG freshness (project-namespaced)
  - cross-link mcp process diagnostics
  - codegraph db (integrity + journal_mode + counts) + locks
  - codegraph-api jar
  - post-commit hook missed-fire detection
  - git tools/ status
  - usage stats: search_recall / reindex 7d / platform-docs usage+adopt /
    cross-link usage / codegraph usage

ExitCode: 0 all green / 2 only WARN / 1 any FAIL (same as .ps1).
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sqlite3
import subprocess
import sys
import tempfile
import urllib.request
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

from codev_platform.ops._common import (
    cfg_get,
    chroma_python,
    codev_root,
    config as load_cfg,
    matches_any,
    meta_health,
    out,
    project_id_of,
    reindex_patterns,
    resolve_repo,
    run,
)


# ----------------------------------------------------------------------
# report buffer (parity with .ps1 Line/Section + top banner)
# ----------------------------------------------------------------------
class Report:
    def __init__(self) -> None:
        self.rows: list[dict[str, Any]] = []
        self.red = 0
        self.amber = 0

    def line(self, tag: str, status: str, msg: str) -> None:
        if status == "WARN":
            self.amber += 1
        elif status == "FAIL":
            self.red += 1
        self.rows.append({"tag": tag, "status": status, "msg": msg})

    def section(self, text: str) -> None:
        self.rows.append({"section": text})

    def flush(self) -> None:
        for row in self.rows:
            if "section" in row:
                out(row["section"])
            else:
                out("[" + row["status"].ljust(4) + "] " + row["tag"].ljust(20) + " " + row["msg"])


# ----------------------------------------------------------------------
# helpers
# ----------------------------------------------------------------------
def _expand(p: str | None) -> Path | None:
    if not p:
        return None
    return Path(str(p)).expanduser()


def _count_files(d: Path, recurse: bool = False) -> int:
    if not d.is_dir():
        return 0
    it = d.rglob("*") if recurse else d.iterdir()
    return sum(1 for f in it if f.is_file())


def _latest_mtime(d: Path, suffixes: tuple[str, ...] | None = None) -> tuple[Path, float] | None:
    """(path, mtime) of newest file under d (optionally filtered by suffix)."""
    if not d.is_dir():
        return None
    best: tuple[Path, float] | None = None
    for f in d.rglob("*"):
        try:
            if not f.is_file():
                continue
            if suffixes and f.suffix.lower() not in suffixes:
                continue
            m = f.stat().st_mtime
        except OSError:
            continue
        if best is None or m > best[1]:
            best = (f, m)
    return best


def _run_py(py: Path, code: str, timeout: int = 60) -> tuple[int, str]:
    """Run a probe script with the given interpreter; return (rc, combined output).

    Uses _common.run for cross-platform launching; merges stderr into stdout so a
    crash message surfaces (mirrors the .ps1 `cmd /c "... 2>&1"`).
    """
    tmp = Path(tempfile.gettempdir()) / f"cdv_health_{os.getpid()}_{abs(hash(code)) & 0xffffff:x}.py"
    tmp.write_text(code, encoding="utf-8")
    try:
        cp = run(
            [str(py), str(tmp)],
            capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=timeout,
        )
        return cp.returncode, ((cp.stdout or "") + (cp.stderr or "")).strip()
    except Exception as exc:  # noqa: BLE001 - probe failures are non-fatal
        return 2, f"ERR {exc!r}"
    finally:
        try:
            tmp.unlink()
        except OSError:
            pass


def _git(repo: Path, *args: str) -> tuple[int, str]:
    try:
        cp = subprocess.run(
            ["git", "-C", str(repo), *args],
            capture_output=True, text=True, encoding="utf-8", errors="replace",
        )
        return cp.returncode, (cp.stdout or "").strip()
    except FileNotFoundError:
        return 127, ""


def _parse_dt(text: str) -> datetime | None:
    """Tolerant ISO-ish parser for jsonl ts / build_meta last_build_at."""
    text = text.strip().replace("Z", "+00:00")
    for fmt in (None, "%Y-%m-%d %H:%M:%S", "%Y-%m-%dT%H:%M:%S"):
        try:
            if fmt is None:
                dt = datetime.fromisoformat(text)
            else:
                dt = datetime.strptime(text, fmt)
            return dt.replace(tzinfo=None)
        except ValueError:
            continue
    return None


def _iter_jsonl(path: Path):
    try:
        with path.open(encoding="utf-8") as fh:
            for ln in fh:
                ln = ln.strip()
                if not ln:
                    continue
                try:
                    yield json.loads(ln)
                except json.JSONDecodeError:
                    continue
    except OSError:
        return


def _list_processes() -> list[dict[str, str]]:
    """Best-effort cross-platform process list: [{pid, ppid, name, cmdline}].

    Prefers psutil; falls back to `ps` on POSIX / `wmic` on Windows. Returns [] if
    nothing works (callers then emit INFO 'process probe skipped').
    """
    try:
        import psutil  # type: ignore

        rows = []
        for p in psutil.process_iter(["pid", "ppid", "name", "cmdline"]):
            info = p.info
            rows.append({
                "pid": str(info.get("pid") or ""),
                "ppid": str(info.get("ppid") or ""),
                "name": (info.get("name") or ""),
                "cmdline": " ".join(info.get("cmdline") or []),
            })
        return rows
    except Exception:
        pass
    if os.name != "nt":
        try:
            cp = subprocess.run(
                ["ps", "-eo", "pid=,ppid=,comm=,args="],
                capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=10,
            )
            rows = []
            for ln in cp.stdout.splitlines():
                parts = ln.split(None, 3)
                if len(parts) >= 3:
                    rows.append({
                        "pid": parts[0], "ppid": parts[1],
                        "name": parts[2], "cmdline": parts[3] if len(parts) > 3 else "",
                    })
            return rows
        except Exception:
            return []
    # Windows without psutil: PowerShell CIM -> JSON (wmic is removed on recent
    # Win11; CIM is the reliable path). Emit one object per process.
    ps_script = (
        "Get-CimInstance Win32_Process | "
        "Select-Object ProcessId,ParentProcessId,Name,CommandLine | "
        "ConvertTo-Json -Compress -Depth 2"
    )
    try:
        cp = subprocess.run(
            ["powershell", "-NoProfile", "-NonInteractive", "-Command", ps_script],
            capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=20,
        )
        data = json.loads(cp.stdout) if cp.stdout.strip() else []
        if isinstance(data, dict):
            data = [data]
        return [{
            "pid": str(d.get("ProcessId") or ""),
            "ppid": str(d.get("ParentProcessId") or ""),
            "name": d.get("Name") or "",
            "cmdline": d.get("CommandLine") or "",
        } for d in data]
    except Exception:
        return []


# ----------------------------------------------------------------------
# individual checks
# ----------------------------------------------------------------------
def _check_chroma_venv(r: Report, chroma_py: Path | None) -> None:
    if chroma_py and chroma_py.exists():
        r.line("chroma venv", "OK", str(chroma_py))
    else:
        r.line("chroma venv", "FAIL", f"missing: {chroma_py} (config runtime.chroma_venv)")


def _resolve_model_dir(cfg: dict, repo: Path) -> Path | None:
    env = os.environ.get("PLATFORM_EMBED_MODEL_PATH")
    if env:
        return Path(env).expanduser()
    shared = _expand(cfg_get("models.embed_path", cfg=cfg))
    if shared and shared.exists():
        return shared
    minilm = repo / "models" / "paraphrase-multilingual-MiniLM-L12-v2"
    if minilm.exists():
        return minilm
    return shared  # report missing shared path


def _check_embed_model(r: Report, model_dir: Path | None) -> None:
    if model_dir and model_dir.exists():
        r.line("embed model", "OK", f"{model_dir} ({_count_files(model_dir, recurse=True)} files)")
    else:
        r.line("embed model", "FAIL", f"missing: {model_dir}")


def _check_embed_load(r: Report, light: bool, chroma_py: Path | None, model_dir: Path | None) -> None:
    if light:
        r.line("embed load", "OK", "(skipped in Light mode)")
        return
    if not (chroma_py and chroma_py.exists() and model_dir and model_dir.exists()):
        return
    code = (
        "import os,sys\n"
        "try:\n"
        "    from sentence_transformers import SentenceTransformer\n"
        f"    mp=r'''{model_dir}'''\n"
        "    m=SentenceTransformer(mp, device='cpu')\n"
        "    g=m.get_embedding_dimension if hasattr(m,'get_embedding_dimension') else m.get_sentence_embedding_dimension\n"
        "    dim=g()\n"
        "    ms=getattr(m,'max_seq_length',None)\n"
        "    pr=getattr(m,'prompts',None) or {}\n"
        "    qp='query' in pr and bool(pr.get('query'))\n"
        "    print('model='+os.path.basename(mp)+' dim='+str(dim)+' max_seq='+str(ms)+' query_prompt='+str(qp))\n"
        "except Exception as e:\n"
        "    print('ERR '+repr(e)); sys.exit(2)\n"
    )
    rc, o = _run_py(chroma_py, code, timeout=120)
    pick = next((ln for ln in reversed(o.splitlines()) if "model=" in ln), o)
    r.line("embed load", "OK" if rc == 0 else "FAIL", pick)


def _check_reranker(r: Report, cfg: dict) -> None:
    env = os.environ.get("PLATFORM_RERANKER_MODEL_PATH")
    rer = Path(env).expanduser() if env else _expand(cfg_get("models.reranker_path", cfg=cfg))
    en_env = os.environ.get("PLATFORM_RERANKER_ENABLED")
    if en_env is not None:
        enabled = en_env != "false"
    else:
        enabled = bool(cfg_get("models.reranker_enabled", True, cfg=cfg))
    if rer and rer.exists():
        required = ("config.json", "tokenizer.json", "tokenizer_config.json", "model.safetensors")
        missing = [f for f in required if not (rer / f).exists()]
        n = _count_files(rer)
        if missing:
            r.line("reranker model", "WARN", f"missing required: {','.join(missing)} in {rer}")
        else:
            tag = "ENABLED" if enabled else "ENABLED=false"
            r.line("reranker model", "OK", f"{rer} ({n} files, {tag})")
    else:
        r.line("reranker model", "OK", "not configured (PLATFORM_RERANKER_MODEL_PATH unset)")


def _check_torch(r: Report, light: bool, chroma_py: Path | None) -> None:
    if light:
        r.line("torch cuda", "OK", "(skipped in Light mode)")
        return
    if not (chroma_py and chroma_py.exists()):
        return
    code = (
        "import sys\n"
        "try:\n"
        "    import torch\n"
        "    ok=torch.cuda.is_available()\n"
        "    name=torch.cuda.get_device_name(0) if ok else 'cpu-only'\n"
        "    print('torch='+torch.__version__+' cuda='+str(ok)+' gpu='+name)\n"
        "except Exception as e:\n"
        "    print('ERR '+repr(e)); sys.exit(2)\n"
    )
    rc, o = _run_py(chroma_py, code, timeout=60)
    o1 = o.strip().splitlines()
    line = next((ln for ln in o1 if "torch=" in ln), o)
    if rc == 0 and "cuda=True" in o:
        r.line("torch cuda", "OK", line)
    elif rc == 0:
        r.line("torch cuda", "WARN", line)
    else:
        r.line("torch cuda", "FAIL", line)


def _check_chroma_data(
    r: Report, light: bool, chroma_py: Path | None, chroma_data: Path,
    chroma_pkg: Path, project_id: str,
) -> None:
    if not chroma_data.is_dir():
        r.line("chroma data dir", "FAIL", f"missing: {chroma_data} (run index_docs)")
        return
    seg = sum(1 for d in chroma_data.iterdir() if d.is_dir())
    r.line("chroma data dir", "OK", f"{chroma_data} ({seg} segments)")
    coll = f"{project_id}__platform_docs" if project_id else "platform_docs"

    if light:
        stamp = chroma_data / f".last_build.{project_id}.json"
        if not stamp.is_file():
            stamp = chroma_data / ".last_build.json"
        if stamp.is_file():
            try:
                s = json.loads(stamp.read_text(encoding="utf-8"))
                r.line("chroma collection", "OK",
                       f"chunks={s.get('chunks')} dim={s.get('embed_dim')} "
                       f"model={s.get('embed_model')} [{coll}]")
            except Exception:
                r.line("chroma collection", "WARN", "cannot parse .last_build stamp")
        else:
            r.line("chroma collection", "WARN",
                   f".last_build.{project_id}.json missing (run reindex/update-local-ai)")
        return

    if not (chroma_py and chroma_py.exists()):
        return
    code = (
        "import sys,os\n"
        f"sys.path.insert(0, r'''{chroma_pkg}''')\n"
        "os.environ.setdefault('PLATFORM_EMBED_DEVICE','cpu')\n"
        "try:\n"
        "    import chromadb\n"
        f"    c=chromadb.PersistentClient(path=r'''{chroma_data}''')\n"
        f"    col=c.get_collection('{coll}')\n"
        "    total=col.count(); meta=col.metadata or {}; dim='empty'\n"
        "    if total:\n"
        "        sm=col.get(limit=1, include=['embeddings']); emb=sm.get('embeddings')\n"
        "        if emb is not None and len(emb)>0: dim=str(len(emb[0]))\n"
        "    bits=['chunks='+str(total),'dim='+str(dim)]\n"
        "    if meta.get('embed_model_name'): bits.append('model='+str(meta.get('embed_model_name')))\n"
        "    if meta.get('max_seq_length'): bits.append('max_seq='+str(meta.get('max_seq_length')))\n"
        "    if meta.get('query_prompt_enabled') is not None: bits.append('query_prompt='+str(meta.get('query_prompt_enabled')))\n"
        "    print(' '.join(bits))\n"
        "except Exception as e:\n"
        "    print('ERR '+repr(e)); sys.exit(2)\n"
    )
    rc, o = _run_py(chroma_py, code, timeout=120)
    line = next((ln for ln in o.splitlines() if ln.startswith("chunks=") or ln.startswith("ERR")), o)
    if rc == 0 and "dim=1024" in o:
        r.line("chroma collection", "OK", line)
    elif rc == 0:
        r.line("chroma collection", "WARN", line)
    else:
        r.line("chroma collection", "FAIL", line)


def _check_chroma_freshness(r: Report, chroma_data: Path, repo: Path) -> None:
    if not chroma_data.is_dir():
        return
    idx = _latest_mtime(chroma_data)
    doc_dirs = [repo / ".claude" / "rules", repo / ".claude" / "skills", repo / "docs"]
    latest_doc: tuple[Path, float] | None = None
    for d in doc_dirs:
        cand = _latest_mtime(d, suffixes=(".md",))
        if cand and (latest_doc is None or cand[1] > latest_doc[1]):
            latest_doc = cand
    if not (idx and latest_doc):
        return
    delta_days = round((latest_doc[1] - idx[1]) / 86400.0)
    name = latest_doc[0].name
    if delta_days <= 0:
        r.line("chroma freshness", "OK", f"index newer than latest doc ({name})")
    elif delta_days <= 7:
        r.line("chroma freshness", "OK", f"lag {delta_days}d vs {name}")
    else:
        r.line("chroma freshness", "WARN", f"lag {delta_days}d vs {name} (run reindex)")


def _daemon_port(cfg: dict) -> str:
    env = os.environ.get("PLATFORM_DOCS_DAEMON_PORT")
    if env:
        return env
    return str(cfg_get("daemon.port", 18083, cfg=cfg))


def _check_daemon(r: Report, port: str) -> None:
    url = f"http://127.0.0.1:{port}/health"
    try:
        with urllib.request.urlopen(url, timeout=2) as resp:
            body = resp.read().decode("utf-8", "replace")
        h = json.loads(body)
        ptag = (" project=" + h["project_id"]) if h.get("project_id") else " project=<legacy daemon>"
        r.line("platform-docs daemon", "OK",
               f"port={port} model={h.get('model')} reranker={h.get('reranker')} "
               f"collection={h.get('collection')}{ptag}")
    except urllib.error.HTTPError as e:  # type: ignore[attr-defined]
        if e.code == 503:
            r.line("platform-docs daemon", "INFO",
                   f"port={port} starting (prewarming Qwen models, 30-60s typical)")
        else:
            r.line("platform-docs daemon", "INFO", f"port={port} http {e.code}")
    except Exception:
        r.line("platform-docs daemon", "INFO",
               f"port={port} not running (auto-spawn on first Claude Code session)")


def _check_pd_servers(r: Report, port: str, procs: list[dict[str, str]]) -> None:
    if not procs:
        r.line("platform-docs servers", "INFO", "process probe skipped (no psutil/ps/wmic)")
        return
    pd = [p for p in procs if p["cmdline"] and (
        re.search(r"codev_platform[\\/.]chroma[\\/.]server", p["cmdline"])
        or re.search(r"chroma[\\/]mcp_server\.py", p["cmdline"])
    )]
    if not pd:
        r.line("platform-docs servers", "OK", "no mcp_server processes (daemon spawns on first session)")
    elif len(pd) == 1:
        mode = "daemon mode --http" if "--http" in pd[0]["cmdline"] else "stdio mode (legacy per-session)"
        r.line("platform-docs servers", "OK", f"1 server pid={pd[0]['pid']} ({mode})")
    else:
        pids = ",".join(p["pid"] for p in pd)
        http = [p for p in pd if "--http" in p["cmdline"]]
        stdio = len(pd) - len(http)
        if stdio > 0:
            r.line("platform-docs servers", "WARN",
                   f"count={len(pd)} pids={pids} ({stdio} legacy stdio still running; kill to reclaim GPU)")
            return
        # Multiple --http procs are NORMAL: the daemon runs as a bootstrap parent
        # that re-spawns the real HTTP server as a child (different interpreters),
        # so 2+ procs in ONE lineage == one logical daemon. Count independent
        # roots (procs whose parent is not itself a daemon proc); only 2+ roots is
        # a genuine duplicate. Counting bare --http occurrences (the old logic)
        # false-flagged the normal bootstrap+server chain as a duplicate daemon.
        http_pids = {p["pid"] for p in http}
        roots = [p for p in http if p.get("ppid", "") not in http_pids]
        if len(roots) <= 1:
            r.line("platform-docs servers", "OK",
                   f"process-chain pids={pids} (bootstrap+server chain, single active daemon)")
        else:
            r.line("platform-docs servers", "WARN",
                   f"count={len(pd)} pids={pids} ({len(roots)} independent daemons; possible duplicate)")


def _check_mcp_proxy(r: Report, cfg: dict) -> None:
    venv = _expand(cfg_get("runtime.chroma_venv", cfg=cfg))
    if not venv:
        r.line("mcp-proxy", "WARN", "runtime.chroma_venv unset; cannot locate mcp-proxy")
        return
    exe = venv / ("Scripts/mcp-proxy.exe" if os.name == "nt" else "bin/mcp-proxy")
    if exe.exists():
        kb = int(exe.stat().st_size / 1024)
        r.line("mcp-proxy", "OK", f"{exe} ({kb} KB)")
    elif os.environ.get("PLATFORM_DOCS_DAEMON_MODE") == "false":
        r.line("mcp-proxy", "OK", "not needed (PLATFORM_DOCS_DAEMON_MODE=false)")
    else:
        r.line("mcp-proxy", "WARN", f"missing: {exe} -- daemon launcher will fail (uv pip install mcp-proxy)")


def _check_rules_vs_incident(r: Report, repo: Path) -> None:
    inc_dir = repo / "docs" / "operations"
    rules_dir = repo / ".claude" / "rules"
    if not (inc_dir.is_dir() and rules_dir.is_dir()):
        r.line("rules vs incident", "INFO", "not configured")
        return
    incs = sorted(inc_dir.glob("incident-*.md"), key=lambda p: p.stat().st_mtime, reverse=True)
    rules = sorted(rules_dir.glob("*.md"), key=lambda p: p.stat().st_mtime, reverse=True)
    if not incs:
        r.line("rules vs incident", "OK", "no incident files (clean)")
        return
    if not rules:
        return
    gap = round((incs[0].stat().st_mtime - rules[0].stat().st_mtime) / 86400.0)
    if gap <= 0:
        r.line("rules vs incident", "OK", f"rules newer than latest incident ({incs[0].name})")
    elif gap <= 7:
        r.line("rules vs incident", "OK", f"incident {gap}d ahead of rules, within window")
    else:
        r.line("rules vs incident", "WARN", f"incident {gap}d ahead of rules ({incs[0].name}) - sync rules")


def _has_cross_link(repo: Path) -> bool:
    mcp = repo / ".mcp.json"
    if not mcp.is_file():
        return False
    try:
        d = json.loads(mcp.read_text(encoding="utf-8"))
        return bool(d.get("mcpServers", {}).get("cross-link"))
    except Exception:
        return False


def _check_cross_layer(
    r: Report, repo: Path, cdv_root: Path, chroma_py: Path | None,
    project_id: str, health: dict,
) -> None:
    has_cl = _has_cross_link(repo)
    db = cdv_root / "data" / "codegraph_ext" / project_id / "cross_layer.sqlite"
    if has_cl and db.is_file():
        if not (chroma_py and chroma_py.exists()):
            return
        try:
            conn = sqlite3.connect(str(db))
            cur = conn.cursor()
            n = cur.execute("select count(*) from nodes").fetchone()[0]
            e = cur.execute("select count(*) from edges").fetchone()[0]
            row = cur.execute("select value from build_meta where key='last_build_at'").fetchone()
            last = row[0] if row else "?"
            conn.close()
        except Exception as exc:  # noqa: BLE001
            r.line("cross_layer", "FAIL", f"query failed: {exc!r}")
            return
        base = f"nodes={n} edges={e} last={last}"
        src_dirs = [repo / rel.replace("/", os.sep) for rel in (health.get("cross_layer_source_dirs") or [])]
        latest_src: tuple[Path, float] | None = None
        for d in src_dirs:
            cand = _latest_mtime(d)
            if cand and (latest_src is None or cand[1] > latest_src[1]):
                latest_src = cand
        build_at = _parse_dt(last) if last != "?" else None
        if build_at and latest_src:
            lag = round((latest_src[1] - build_at.timestamp()) / 86400.0)
            if lag <= 0:
                r.line("cross_layer", "OK", base)
            elif lag <= 1:
                r.line("cross_layer", "OK", base + " (lag <=1d)")
            else:
                r.line("cross_layer", "WARN", base + f" (lag {lag}d vs {latest_src[0].name})")
        else:
            r.line("cross_layer", "OK", base)
    elif not has_cl:
        r.line("cross_layer", "INFO", "not configured")
    else:
        r.line("cross_layer", "INFO", "configured, index not built")


def _check_cross_link_mcp(r: Report, procs: list[dict[str, str]]) -> None:
    if not procs:
        r.line("cross-link mcp", "INFO", "process probe skipped (no psutil/ps/wmic)")
        return
    cl = [p for p in procs if p["cmdline"] and re.search(r"cross_link[\\/]mcp_server\.py", p["cmdline"])]
    if not cl:
        r.line("cross-link mcp", "OK", "no running cross-link MCP server")
        return
    pidset = {p["pid"] for p in cl}
    roots = [p for p in cl if p["ppid"] not in pidset]
    chain = len(roots) or len(cl)
    pids = ",".join(p["pid"] for p in cl)
    if chain == 1:
        r.line("cross-link mcp", "INFO", f"stdio chain count=1 process-chain pids={pids}")
    else:
        r.line("cross-link mcp", "INFO",
               f"stdio chain count={chain} process-chain pids={pids} (expected with multiple sessions)")


def _check_codegraph_db(r: Report, repo: Path, chroma_py: Path | None) -> None:
    lock = repo / ".codegraph" / ".rebuild.lock"
    db = repo / ".codegraph" / "codegraph.db"
    if lock.is_file():
        try:
            lk = json.loads(lock.read_text(encoding="utf-8"))
            st = _parse_dt(lk.get("started_at", ""))
            elapsed = int((datetime.now() - st).total_seconds()) if st else -1
            r.line("codegraph db", "WARN",
                   f"rebuild in progress (mode={lk.get('mode')}, pid={lk.get('pid')}, {elapsed}s) - retry later")
        except Exception as exc:  # noqa: BLE001
            r.line("codegraph db", "WARN", f"rebuild lock present but unreadable: {exc!r}")
        return
    if not db.is_file():
        r.line("codegraph db", "FAIL", f"missing: {db} (run codegraph rebuild)")
        return
    size = round(db.stat().st_size / (1024 * 1024), 1)
    try:
        conn = sqlite3.connect(str(db))
        cur = conn.cursor()
        ic = cur.execute("PRAGMA integrity_check").fetchone()[0]
        jm = cur.execute("PRAGMA journal_mode").fetchone()[0]
        n = cur.execute("select count(*) from nodes").fetchone()[0]
        e = cur.execute("select count(*) from edges").fetchone()[0]
        conn.close()
    except Exception as exc:  # noqa: BLE001
        r.line("codegraph db", "FAIL", f"{db} present but query failed (corrupt/locked?): {exc!r}")
        return
    info = f"integrity={ic} journal_mode={jm} nodes={n} edges={e}"
    if ic != "ok":
        r.line("codegraph db", "FAIL", f"{db} ({size} MB) INTEGRITY BROKEN: {info}")
    elif jm in ("delete", "memory"):
        r.line("codegraph db", "WARN", f"{db} ({size} MB) on WASM fallback: {info}")
    else:
        r.line("codegraph db", "OK", f"{db} ({size} MB, {info})")


def _check_codegraph_mcp(r: Report, repo: Path, procs: list[dict[str, str]]) -> None:
    if procs:
        cg = [p for p in procs if p["cmdline"]
              and "codegraph" in p["cmdline"] and "serve" in p["cmdline"] and "--mcp" in p["cmdline"]]
        if len(cg) > 1:
            pids = ",".join(p["pid"] for p in cg)
            r.line("codegraph mcp", "INFO", f"stdio servers count={len(cg)} pids={pids} (multiple sessions)")
        elif len(cg) == 1:
            r.line("codegraph mcp", "INFO", f"server pid={cg[0]['pid']} (DB may be locked for CLI status)")
        else:
            r.line("codegraph mcp", "OK", "no running codegraph MCP server")
    cli_lock = repo / ".codegraph" / "codegraph.db.lock"
    if cli_lock.is_file():
        try:
            age_min = round((datetime.now().timestamp() - cli_lock.stat().st_mtime) / 60.0, 1)
            status = "WARN" if age_min >= 10 else "INFO"
            r.line("codegraph cli lock", status, f"path={cli_lock} age_min={age_min}")
        except OSError as exc:
            r.line("codegraph cli lock", "WARN", f"probe failed: {exc!r}")


def _check_codegraph_api(r: Report, repo: Path) -> None:
    api_dir = repo / "apps" / "codegraph-api"
    target = api_dir / "target"
    jars = sorted(target.glob("codegraph-api-*.jar")) if target.is_dir() else []
    if jars:
        r.line("codegraph-api", "OK", jars[0].name)
    elif not api_dir.is_dir():
        r.line("codegraph-api", "INFO", "not configured")
    else:
        r.line("codegraph-api", "WARN", f"no jar in {target} (run mvn package if needed)")


def _check_hook_missed(r: Report, repo: Path, health: dict) -> None:
    rc, head = _git(repo, "rev-parse", "HEAD")
    if rc != 0 or not head:
        r.line("hook missed?", "WARN", "git not available / no HEAD")
        return
    rc2, files_txt = _git(repo, "diff-tree", "--no-commit-id", "--name-only", "-r", "HEAD")
    files = [f for f in files_txt.splitlines() if f.strip()]
    # default indexable scope = doc + codegraph patterns (parity with .ps1 defaults)
    pats = reindex_patterns(health)
    indexable = pats["doc"] + pats["codegraph"] + pats["cross_link"]
    should = any(matches_any(f, indexable) for f in files)
    reindex_log = repo / "tools" / "chroma" / "reindex.log"
    short = head[:7]
    if not should:
        r.line("hook missed?", "OK", f"HEAD {short} touches no indexable file")
    elif reindex_log.is_file():
        try:
            found = head in reindex_log.read_text(encoding="utf-8", errors="replace")
        except OSError:
            found = False
        if found:
            r.line("hook missed?", "OK", f"HEAD {short} found in reindex.log")
        else:
            r.line("hook missed?", "WARN",
                   f"HEAD {short} touches indexable files but NOT in reindex.log - retry post-commit")
    else:
        r.line("hook missed?", "WARN", "reindex.log missing - hook may have never run")


def _check_git_tools(r: Report, repo: Path) -> None:
    rc, o = _git(repo, "status", "--short", "tools/")
    if rc == 127:
        r.line("git tools/", "WARN", "git not available")
    elif not o.strip():
        r.line("git tools/", "OK", "clean")
    else:
        r.line("git tools/", "WARN", f"{len(o.splitlines())} uncommitted file(s)")


# ---- usage stats (last 7 days) --------------------------------------
def _usage_search_recall(r: Report, recall_file: Path) -> None:
    if not recall_file.is_file():
        r.line("search_recall", "WARN", "search_recall.jsonl not found")
        return
    cutoff = datetime.now() - timedelta(days=7)
    recent = []
    for o in _iter_jsonl(recall_file):
        ts = _parse_dt(str(o["ts"])) if o.get("ts") else None
        if ts is None or ts >= cutoff:
            recent.append(o)
    total = len(recent)
    if total == 0:
        r.line("search_recall", "WARN", "no recent queries (last 7d)")
        return
    with_hits = sum(1 for o in recent if (o.get("hit") or 0) > 0)
    hit_rate = round(100.0 * with_hits / total, 1)
    top1 = sorted(o["top5"][0]["distance"] for o in recent if o.get("top5"))
    med = round(top1[len(top1) // 2], 3) if top1 else "n/a"
    if hit_rate < 80:
        status = "WARN"
    elif total < 30:
        status = "INFO"
    else:
        status = "OK"
    sample = " / baseline=small(<30)" if total < 30 else ""
    r.line("search_recall", status,
           f"{total} queries / hit_rate={hit_rate}% / median_top1_dist={med}{sample}")


def _usage_reindex(r: Report, repo: Path) -> None:
    log = repo / "tools" / "chroma" / "reindex.log"
    if not log.is_file():
        r.line("reindex 7d", "INFO", "no reindex.log yet (freshness/hook checks cover health)")
        return
    cutoff = datetime.now() - timedelta(days=7)
    runs = 0
    try:
        for ln in log.read_text(encoding="utf-8", errors="replace").splitlines():
            m = re.search(r"reindex started at (\d{4}-\d{2}-\d{2})", ln)
            if m:
                d = _parse_dt(m.group(1))
                if d and d >= cutoff:
                    runs += 1
    except OSError:
        pass
    r.line("reindex 7d", "OK", f"{runs} runs (post-commit + manual)")


def _usage_platform_docs(r: Report, repo: Path, recall_file: Path, health: dict) -> None:
    # candidate / strict MCP-path patterns (defaults + project meta extensions)
    default_cand = [
        r"^apps/[^/]+/src/.*\.(java|ts|tsx|less)$",
        r"^\.claude/(rules|skills)/.*\.md$",
        r"^apps/[^/]+/\.claude/rules/.*\.md$",
        r"^docs/.*\.md$",
        r"^tools/(dev|chroma|cross_link)/",
        r"^scripts/.*\.(ps1|cmd|bat)$",
    ]
    default_strict = [r"^\.claude/(rules|skills)/", r"^tools/(dev|chroma|cross_link)/"]
    cand = default_cand + list(health.get("mcp_candidate_patterns") or [])
    strict = default_strict + list(health.get("mcp_strict_patterns") or [])

    rc, oneline = _git(repo, "log", "--since=7.days.ago", "--oneline")
    commit_count = len([x for x in oneline.splitlines() if x.strip()]) if rc == 0 else 0

    rc2, raw = _git(repo, "log", "--since=7.days.ago", "--name-only", "--format=__COMMIT__%H")
    cand_commits = strict_commits = 0
    seen = has_c = has_s = False
    for line in raw.splitlines():
        t = line.strip()
        if not t:
            continue
        if t.startswith("__COMMIT__"):
            if seen:
                cand_commits += int(has_c)
                strict_commits += int(has_s)
            seen, has_c, has_s = True, False, False
            continue
        if matches_any(t, cand):
            has_c = True
        if matches_any(t, strict):
            has_s = True
    if seen:
        cand_commits += int(has_c)
        strict_commits += int(has_s)

    cutoff = datetime.now() - timedelta(days=7)
    query_count = 0
    if recall_file.is_file():
        for o in _iter_jsonl(recall_file):
            if o.get("ts"):
                ts = _parse_dt(str(o["ts"]))
                if ts and ts >= cutoff:
                    query_count += 1

    if query_count == 0 and cand_commits > 0:
        r.line("platform-docs usage", "WARN",
               f"0 search_docs / {cand_commits} L2L3 candidate commits; adoption missing")
    else:
        detail = f"{query_count} search_docs (last 7d)"
        if commit_count > 0:
            detail += f" / {commit_count} commits = {round(1.0 * query_count / commit_count, 2)}"
        r.line("platform-docs usage", "INFO", detail)
    if cand_commits > 0:
        ratio = round(1.0 * query_count / cand_commits, 2)
        r.line("platform-docs adopt", "INFO",
               f"L2L3_candidate_commits={cand_commits} strict_MCP_candidate_commits={strict_commits} "
               f"search_docs_per_candidate={ratio} (platform-docs only)")
    elif commit_count > 0:
        r.line("platform-docs adopt", "INFO", "no L2/L3 candidate commits detected in last 7d")


def _usage_cross_link(r: Report, cl_usage: Path) -> None:
    if not cl_usage.is_file():
        r.line("cross-link usage", "INFO", "cross_link_usage.jsonl not found (no calls yet)")
        return
    cutoff = datetime.now() - timedelta(days=7)
    rows = []
    for o in _iter_jsonl(cl_usage):
        if o.get("ts"):
            ts = _parse_dt(str(o["ts"]))
            if ts and ts < cutoff:
                continue
        rows.append(o)
    if not rows:
        r.line("cross-link usage", "INFO", "no cross-link calls (last 7d)")
        return
    by_tool: dict[str, int] = {}
    for o in rows:
        by_tool[o.get("tool", "?")] = by_tool.get(o.get("tool", "?"), 0) + 1
    ok = sum(1 for o in rows if o.get("ok"))
    ok_rate = round(100.0 * ok / len(rows))
    lat = sorted(float(o["elapsed_ms"]) for o in rows if o.get("elapsed_ms") is not None)
    med = f"{round(lat[len(lat) // 2])}ms" if lat else "n/a"
    tools = ",".join(f"{k}={v}" for k, v in by_tool.items())
    r.line("cross-link usage", "INFO",
           f"{len(rows)} calls / ok={ok_rate}% / median={med} / {tools} last 7d")


# ----------------------------------------------------------------------
# main command
# ----------------------------------------------------------------------
def cmd_health(args: argparse.Namespace) -> int:
    if getattr(args, "all", False):
        return cmd_health_all(args)
    light = (args.mode == "light")
    cfg = load_cfg()
    cdv_root = codev_root()

    try:
        repo = resolve_repo(args.repo)
    except RuntimeError as exc:
        out(f"error: {exc}")
        return 1

    project_id = args.project or project_id_of(repo) or "unknown"
    health = meta_health(args.project or project_id_of(repo))

    chroma_py = chroma_python()
    chroma_data = cdv_root / "data" / "chroma"
    chroma_pkg = cdv_root / "codev_platform" / "chroma"

    out(f"=== codev-platform health (mode={args.mode}) ===")
    out(f"repo: {repo}")
    out(f"project_id: {project_id}")
    if args.project:
        reg = cdv_root / "platform_meta" / "projects" / args.project
        if not reg.exists():
            out(f"project override: {args.project} (WARNING: not registered in platform_meta/projects)")
    out("")

    r = Report()
    procs = _list_processes()
    port = _daemon_port(cfg)
    model_dir = _resolve_model_dir(cfg, repo)

    _check_chroma_venv(r, chroma_py)
    _check_embed_model(r, model_dir)
    _check_embed_load(r, light, chroma_py, model_dir)
    _check_reranker(r, cfg)
    _check_torch(r, light, chroma_py)
    _check_chroma_data(r, light, chroma_py, chroma_data, chroma_pkg, project_id)
    _check_chroma_freshness(r, chroma_data, repo)
    _check_daemon(r, port)
    _check_pd_servers(r, port, procs)
    _check_mcp_proxy(r, cfg)
    _check_rules_vs_incident(r, repo)
    _check_cross_layer(r, repo, cdv_root, chroma_py, project_id, health)
    _check_cross_link_mcp(r, procs)
    _check_codegraph_db(r, repo, chroma_py)
    _check_codegraph_mcp(r, repo, procs)
    _check_codegraph_api(r, repo)
    _check_hook_missed(r, repo, health)
    _check_git_tools(r, repo)

    r.section("")
    r.section("--- usage stats (last 7 days) ---")
    recall_file = cdv_root / "codev_platform" / "chroma" / "search_recall.jsonl"
    cl_usage = cdv_root / "codev_platform" / "cross_link" / "cross_link_usage.jsonl"
    _usage_search_recall(r, recall_file)
    _usage_reindex(r, repo)
    _usage_platform_docs(r, repo, recall_file, health)
    _usage_cross_link(r, cl_usage)
    r.line("codegraph usage", "INFO", "not logged by project scripts yet; process/db health only")

    # top banner (parity with .ps1 P6)
    if r.red > 0:
        out(f">>> BROKEN <<<    {r.red} FAIL / {r.amber} WARN (fix critical items below)")
    elif r.amber > 0:
        out(f">>> ATTENTION <<< all critical OK, {r.amber} WARN (degraded, still usable)")
    else:
        out(">>> READY <<<     all checks green")
    out("")

    r.flush()

    # Optional widget snapshot (parity with ai-health.ps1 -JsonOut). Runs after
    # all checks so red/amber are final; never affects the exit code below.
    if getattr(args, "json_out", None) is not None:
        if args.json_out == "":
            if project_id:
                snap = cdv_root / "platform_meta" / "health" / f"{project_id}.json"
            else:
                out("[json] WARN no project_id resolved; pass --json-out <path> explicitly")
                snap = None
        else:
            snap = Path(args.json_out).expanduser()
        if snap is not None:
            _write_json_snapshot(r, snap, project_id, args.mode)

    out("")
    if r.red > 0:
        out(f"SUMMARY: {r.red} FAIL / {r.amber} WARN")
        return 1
    if r.amber > 0:
        out(f"SUMMARY: all critical OK, {r.amber} WARN")
        return 2
    out("SUMMARY: all green")
    return 0


def _verdict(red: int, amber: int) -> str:
    if red > 0:
        return "BROKEN"
    if amber > 0:
        return "ATTENTION"
    return "READY"


def _write_json_snapshot(r: Report, path: Path, project_id: str | None, mode: str) -> None:
    """Serialise the report to a widget-readable JSON snapshot (parity with
    ai-health.ps1 -JsonOut). Non-fatal: any failure is logged, never raised."""
    try:
        checks = [
            {"tag": row["tag"], "status": row["status"], "msg": row["msg"]}
            for row in r.rows
            if "status" in row
        ]
        payload = {
            "schema_version": 1,
            "project_id": project_id,
            "mode": mode.capitalize(),
            "generated_at": datetime.now().astimezone().isoformat(timespec="seconds"),
            "verdict": _verdict(r.red, r.amber),
            "fail_count": r.red,
            "warn_count": r.amber,
            "ok_count": sum(1 for c in checks if c["status"] == "OK"),
            "checks": checks,
        }
        path.parent.mkdir(parents=True, exist_ok=True)
        # UTF-8 WITHOUT BOM -- Node's JSON.parse on the widget side chokes on a BOM.
        path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
        out(f"[json] wrote {path}")
    except Exception as exc:  # noqa: BLE001 - snapshot write must never fail health
        out(f"[json] WARN failed to write {path}: {exc}")


# ----------------------------------------------------------------------
# platform-wide aggregate (--all): HTTP client of daemon /platform/status
# 原则: 访问平台数据走 HTTP/HTTPS。本函数只 HTTP GET, 不读任何本地文件路径;
# 服务端 (daemon) 跑在平台主机上聚合本机 data/+PG, 见 codev_platform/platform_status.py。
# ----------------------------------------------------------------------
def _platform_url(cfg: dict) -> str:
    base = cfg_get("platform.url", cfg=cfg) or f"http://127.0.0.1:{_daemon_port(cfg)}"
    return base.rstrip("/") + "/platform/status"


def cmd_health_all(args: argparse.Namespace) -> int:
    cfg = load_cfg()
    url = _platform_url(cfg)
    try:
        with urllib.request.urlopen(url, timeout=20) as resp:
            data = json.loads(resp.read().decode("utf-8", "replace"))
    except Exception as exc:  # noqa: BLE001
        out(f"[FAIL] 连不上平台服务: {url}")
        out(f"       {type(exc).__name__}: {exc}")
        out("       平台数据一律走 HTTP。请确认 daemon 在跑(首个 Claude Code 会话自动起,")
        out("       或在任一仓 `codev-platform reindex` 触发);远程平台则配 config.platform.url。")
        return 1
    if isinstance(data, dict) and data.get("error"):
        out(f"[FAIL] 平台服务内部错误: {data['error']}")
        return 1

    projects = data.get("projects", {})
    mem_org = data.get("memory_org", 0)
    out("=== codev-platform health --all (平台全局视图 · via HTTP) ===")
    out(f"平台服务: {url}")
    out(f"data root: {data.get('data_root')}  |  registered: {len(data.get('registered', []))}  "
        f"|  org 共享记忆: {mem_org} 条(全项目通用)")
    out("")

    tot_chroma = 0
    for pid in sorted(projects):
        p = projects[pid]
        ch = p.get("chroma_chunks", 0)
        tot_chroma += ch
        cg = p.get("codegraph")
        if isinstance(cg, dict):
            src = cg.get("source")
            tag = " (via codegraph-api HTTP)" if src == "http" else " (本地 sqlite)" if src == "local" else ""
            cg_s = f"nodes={cg.get('nodes', 0)} edges={cg.get('edges', 0)}{tag}"
        elif cg == "no_repo_path":
            cg_s = "?(仓路径未在平台登记)"
        elif cg == "no_db":
            cg_s = "无 .codegraph db"
        elif cg == "api_down":
            cg_s = "codegraph-api 未响应(启动 :18082 / 查 config.projects.<id>.codegraph_api_url)"
        elif cg == "api_error":
            cg_s = "codegraph-api 返回错误"
        else:
            cg_s = str(cg)
        xl = p.get("cross_link")
        if isinstance(xl, dict):
            xsrc = xl.get("source")
            xtag = " (via codegraph-api HTTP)" if xsrc == "http" else " (本地)" if xsrc == "local" else ""
            xl_s = f"nodes={xl.get('nodes', 0)}{xtag}"
        elif xl == "not_built":
            xl_s = "未建(不适用/未建)"
        elif xl == "api_down":
            xl_s = "codegraph-api 未响应"
        else:
            xl_s = "未建/不可用"
        u = p.get("usage_7d", {})
        reg_tag = "" if p.get("registered") else "  (未注册 platform_meta)"
        out(f"[{pid}]{reg_tag}")
        out(f"    chroma 文档 = {ch} chunks")
        out(f"    codegraph 代码 = {cg_s}")
        out(f"    cross-link 链路 = {xl_s}")
        out(f"    memory 项目专属 = {p.get('memory_project', 0)} 条  (+ org 共享 {mem_org})")
        out(f"    使用率(7d) = search_docs {u.get('search_docs', 0)} / cross-link {u.get('cross_link', 0)}  (codegraph 未计数)")
        out("")

    proj_mem = sum(p.get("memory_project", 0) for p in projects.values())
    out(f"合计: chroma {tot_chroma} chunks / {len(projects)} 项目 ; memory {mem_org} org + {proj_mem} project")
    leg = data.get("usage_legacy")
    if leg:
        out(f"[INFO] 旧日志未带 project_id(daemon 重启后新查询才分项目): "
            f"search_docs {leg.get('search_docs', 0)} / cross-link {leg.get('cross_link', 0)}")
    for e in data.get("errors", []):
        out(f"[WARN] 服务端: {e}")
    return 0


def register(subparsers) -> None:
    sp = subparsers.add_parser("health", help="工具栈体检")
    sp.add_argument("--repo")
    sp.add_argument("--project")
    sp.add_argument("--all", action="store_true", help="平台全局视图: 所有项目 x 三库 + 记忆(不限当前仓)")
    sp.add_argument("--mode", choices=["light", "full"], default="full")
    # --json-out: write a widget-readable snapshot. Bare flag => canonical path
    # platform_meta/health/<project_id>.json; explicit path => that file.
    sp.add_argument(
        "--json-out", nargs="?", const="", default=None, dest="json_out",
        help="写健康快照 JSON (widget 读); 省略路径=写规范位置 platform_meta/health/<pid>.json",
    )
    sp.set_defaults(func=cmd_health)
