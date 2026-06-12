"""health 子包 —— 各项体检 (从 ops/health.py 抽出, file-discipline §1)。

每个 _check_* 接收 Report, 自行 r.line(tag, status, msg); 不打印 (展示交给 Report.flush)。
纯探测 + 判定, 复用 _util 的文件/进程/git helper。
"""
from __future__ import annotations

import json
import os
import re
import sqlite3
import urllib.error
import urllib.request
from datetime import datetime
from pathlib import Path

from codev_platform.ops._common import cfg_get, matches_any, reindex_patterns

from ._util import (
    Report,
    _count_files,
    _expand,
    _git,
    _latest_mtime,
    _parse_dt,
    _run_py,
)


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
    # platform_docs 每项目独立库 docs/<pid>/ (隔离 chromadb 多 collection compaction 损坏);
    # 回退根库兼容尚未迁移的 legacy 布局。
    proj_data = (chroma_data / "docs" / project_id) if project_id else chroma_data
    if not proj_data.is_dir():
        proj_data = chroma_data
    chroma_data = proj_data
    seg = sum(1 for d in chroma_data.iterdir() if d.is_dir())
    r.line("chroma data dir", "OK", f"{chroma_data} ({seg} segments)")
    coll = f"{project_id}__platform_docs" if project_id else "platform_docs"

    if light:
        stamp = chroma_data / ".last_build.json"
        if not stamp.is_file():
            stamp = chroma_data / f".last_build.{project_id}.json"
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
        return env                                      # 运行态真值(systemd 注入 bind 口)优先
    from codev_platform.mcp_serve import _bind_port     # 端口统一: canonical 键 + daemon.port 别名
    return str(_bind_port(cfg, "chroma"))


def _check_daemon(r: Report, port: str) -> None:
    # 审计 #4: PUBLIC /healthz 只给最小存活/就绪 (200 ready / 503 prewarming);
    # 详情 (model/reranker/collection/project) 在鉴权后的 /platform/health
    # (passthrough 模式本机 ai-health 可直读; token 模式无 Bearer 会 401 → 退回最小存活展示)。
    url = f"http://127.0.0.1:{port}/platform/health"
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
        elif e.code == 401:
            # token 模式: 详情面需鉴权, ai-health 不带 token → 退回 PUBLIC /healthz 探存活。
            r.line("platform-docs daemon", "OK",
                   f"port={port} up (detail /platform/health needs auth in token mode)")
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


def _check_graph_store(r: Report, project_id: str) -> None:
    """统一图谱 store (graph_store/<pid>.sqlite) 节点/边 + 新鲜度。替代退役的 cross_layer 检查。"""
    from codev_platform.graph.store import graph_store_path, open_store
    db = graph_store_path(project_id)
    if not db.is_file():
        r.line("graph store", "INFO", "未建 (跑 reindex --ingest 生成统一图谱)")
        return
    try:
        with open_store(project_id, mode="ro") as store:
            data = store.stats(project_id)
        t = data["totals"]
        n, e = t["nodes"], t["edges"]
        ings = [p["ingested_at"] for p in data["plugins"] if p.get("ingested_at")]
        last = max(ings) if ings else "?"
    except Exception as exc:  # noqa: BLE001
        r.line("graph store", "FAIL", f"query failed: {exc!r}")
        return
    if n == 0:
        r.line("graph store", "WARN", f"0 nodes (ingest 未跑/失败); last={last}")
    else:
        r.line("graph store", "OK", f"nodes={n} edges={e} last={last}")


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


def _check_hook_missed(r: Report, repo: Path, health: dict) -> None:
    rc, head = _git(repo, "rev-parse", "HEAD")
    if rc != 0 or not head:
        r.line("hook missed?", "WARN", "git not available / no HEAD")
        return
    rc2, files_txt = _git(repo, "diff-tree", "--no-commit-id", "--name-only", "-r", "HEAD")
    files = [f for f in files_txt.splitlines() if f.strip()]
    # default indexable scope = doc + codegraph patterns (parity with .ps1 defaults)
    pats = reindex_patterns(health)
    indexable = pats["doc"] + pats["codegraph"]
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
