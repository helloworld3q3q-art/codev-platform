"""health 子包 —— 各项体检 (从 ops/health.py 抽出, file-discipline §1)。

每个 _check_* 接收 Report, 自行 r.line(tag, status, msg); 不打印 (展示交给 Report.flush)。
纯探测 + 判定, 复用 _util 的文件/进程/git helper。
"""
from __future__ import annotations

import json
import os
import re
import urllib.error
import urllib.request
from pathlib import Path

from codev_platform.chroma.document_manifest import valid_document_manifest
from codev_platform.core.index_handoff import resolve_current
from codev_platform.core.wsl_data_owner import wsl_data_owner
from codev_platform.reindex.index_status_client import (
    CoverageState,
    assess_index_coverage,
    read_platform_index_status,
)
from codev_platform.ops._common import cfg_get, reindex_patterns
from codev_platform.index_kind_contract import REQUIRED_INDEX_KINDS

from ._util import (
    Report,
    _count_files,
    _expand,
    _git,
    _latest_mtime,
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


def _read_chroma_build_summary(data_dir: Path, project_id: str) -> dict | None:
    """从当前 build 的 manifest 读取摘要，兼容旧布局的构建戳。"""
    for stamp in (data_dir / ".last_build.json", data_dir / f".last_build.{project_id}.json"):
        if stamp.is_file():
            return json.loads(stamp.read_text(encoding="utf-8"))

    manifest_path = data_dir / f"index_manifest.{project_id}.json"
    if manifest_path.is_file():
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        if not valid_document_manifest(manifest):
            raise ValueError("文档索引 manifest 结构无效")
        params = manifest["params"]
        return {
            "chunks": sum(entry["chunk_count"] for entry in manifest["files"].values()),
            "embed_dim": params["embed_dim"],
            "embed_model": params["embed_model"],
        }
    return None


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
    chroma_data = resolve_current(proj_data)
    seg = sum(1 for d in chroma_data.iterdir() if d.is_dir())
    r.line("chroma data dir", "OK", f"{chroma_data} ({seg} segments)")
    coll = f"{project_id}__platform_docs" if project_id else "platform_docs"

    if light:
        try:
            summary = _read_chroma_build_summary(chroma_data, project_id)
        except (OSError, TypeError, ValueError):
            r.line("chroma collection", "WARN", "cannot parse current build metadata")
            return
        if summary is None:
            r.line("chroma collection", "WARN", "current build metadata missing (run reindex/update-local-ai)")
            return
        r.line(
            "chroma collection", "OK",
            f"chunks={summary.get('chunks')} dim={summary.get('embed_dim')} "
            f"model={summary.get('embed_model')} [{coll}]",
        )
        return

    database = chroma_data / "chroma.sqlite3"
    if not database.is_file():
        r.line("chroma collection", "FAIL", f"chroma.sqlite3 missing: {database}")
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


def _expected_reindex_kinds(files: list[str], health: dict) -> set[str]:
    from codev_platform.ops.reindex import expected_reindex_kinds
    return set(expected_reindex_kinds(files, reindex_patterns(health)))


_MANIFEST_BACKED_KINDS = set(REQUIRED_INDEX_KINDS)


def _manifest_head_coverage(
    project_id: str | None,
    repo: Path,
    expected_kinds: set[str],
    *,
    cfg: dict | None = None,
    target_commit: str | None = None,
) -> tuple[bool, str]:
    if not expected_kinds:
        return False, ""
    if not project_id or project_id == "unknown":
        return False, "project_id unknown"
    if cfg is not None and wsl_data_owner(cfg) is not None:
        try:
            snapshot = read_platform_index_status(project_id, cfg)
            selected_commit = target_commit or snapshot.head_commit
            if not selected_commit:
                return False, "platform index status has no target commit"
            coverage = assess_index_coverage(
                snapshot,
                expected_kinds,
                selected_commit,
                commit_covers=lambda target, indexed: _health_commit_covers(
                    repo,
                    target,
                    indexed,
                ),
            )
        except Exception as exc:  # noqa: BLE001 - bounded HTTP diagnostic, no local fallback
            return False, f"platform index status unavailable: {type(exc).__name__}"
        return coverage.state is CoverageState.OK, coverage.detail

    try:
        from codev_platform.index_manifest import freshness
        rows = freshness(project_id, repo)
    except Exception as exc:  # noqa: BLE001 - health check should degrade to WARN
        return False, f"manifest unreadable: {type(exc).__name__}"

    by_kind = {str(row.get("kind")): row for row in rows if row.get("kind")}
    missing_or_stale: list[str] = []
    for kind in sorted(expected_kinds):
        row = by_kind.get(kind)
        if row is None:
            missing_or_stale.append(f"{kind}:missing")
        elif row.get("status") != "ok":
            missing_or_stale.append(f"{kind}:{row.get('status') or 'unknown'}")
        elif row.get("fresh") is not True:
            reason = str(row.get("reason") or "")
            if "dependency:" in reason:
                missing_or_stale.append(reason)
            else:
                missing_or_stale.append(f"{kind}:stale")
    if missing_or_stale:
        return False, ", ".join(missing_or_stale)
    unsupported = expected_kinds - _MANIFEST_BACKED_KINDS
    if unsupported:
        return False, "manifest fallback unsupported kinds; " + ",".join(sorted(unsupported))
    return True, ", ".join(sorted(expected_kinds))


def _health_commit_covers(repo: Path, target: str, indexed: str | None) -> bool:
    if not indexed:
        return False
    if target == indexed:
        return True
    rc, _output = _git(repo, "merge-base", "--is-ancestor", target, indexed)
    return rc == 0


def _latest_reindex_log_block(text: str, head: str) -> str:
    marker = f"trigger commit: {head}"
    idx = text.rfind(marker)
    if idx < 0:
        return ""
    next_block = text.find("\n===== reindex started at ", idx + len(marker))
    return text[idx:] if next_block < 0 else text[idx:next_block]


def _queued_reindex_log_block(text: str, head: str) -> bool:
    return "enqueued -> codev-reindex worker:" in _latest_reindex_log_block(text, head)


def _check_hook_missed(
    r: Report,
    repo: Path,
    health: dict,
    project_id: str | None = None,
    *,
    cfg: dict | None = None,
) -> None:
    rc, head = _git(repo, "rev-parse", "HEAD")
    if rc != 0 or not head:
        r.line("hook missed?", "WARN", "git not available / no HEAD")
        return
    rc2, files_txt = _git(repo, "diff-tree", "--no-commit-id", "--name-only", "-r", "HEAD")
    files = [f for f in files_txt.splitlines() if f.strip()]
    expected_kinds = _expected_reindex_kinds(files, health)
    reindex_log = repo / "tools" / "chroma" / "reindex.log"
    short = head[:7]
    if not expected_kinds:
        r.line("hook missed?", "OK", f"HEAD {short} touches no indexable file")
        return

    covered, detail = _manifest_head_coverage(
        project_id,
        repo,
        expected_kinds,
        cfg=cfg,
        target_commit=head,
    )
    log_text = ""
    log_exists = reindex_log.is_file()
    if log_exists:
        try:
            log_text = reindex_log.read_text(encoding="utf-8", errors="replace")
        except OSError:
            log_text = ""

    block = _latest_reindex_log_block(log_text, head)
    enqueue_failed = "reindex enqueue failed" in block
    enqueued = "enqueued -> codev-reindex worker:" in block
    legacy = re.search(
        r"reindex finished at .* \[(ok|warn exit=\d+|failed exit=\d+)\]",
        block,
    )

    if covered:
        if enqueue_failed:
            r.line(
                "hook missed?",
                "OK",
                f"HEAD {short} local enqueue failed, but covered by manifest ({detail})",
            )
        elif block:
            r.line(
                "hook missed?",
                "OK",
                f"HEAD {short} found in reindex.log and covered by manifest ({detail})",
            )
        elif log_exists:
            r.line(
                "hook missed?",
                "OK",
                f"HEAD {short} covered by manifest ({detail}); reindex.log lacks HEAD",
            )
        else:
            r.line(
                "hook missed?",
                "OK",
                f"HEAD {short} covered by manifest ({detail}); reindex.log absent in worker mode",
            )
        return

    suffix = f": {detail}" if detail else ""
    if legacy:
        legacy_status = legacy.group(1)
        legacy_detail = f"legacy completion ({legacy_status}) cannot override manifest"
        if legacy_status.startswith("failed"):
            legacy_detail = f"legacy completion failed ({legacy_status})"
        r.line(
            "hook missed?",
            "WARN",
            f"HEAD {short} {legacy_detail}; manifest not successful{suffix}",
        )
    elif enqueue_failed:
        r.line(
            "hook missed?",
            "WARN",
            f"HEAD {short} local enqueue failed and manifest not successful{suffix}; "
            "retry with `git hook run post-commit`",
        )
    elif enqueued:
        r.line(
            "hook missed?",
            "WARN",
            f"HEAD {short} enqueued in reindex.log but manifest not successful{suffix}",
        )
    elif block:
        r.line(
            "hook missed?",
            "WARN",
            f"HEAD {short} trigger recorded without successful enqueue or manifest{suffix}; "
            "retry with `git hook run post-commit`",
        )
    elif log_exists:
        r.line(
            "hook missed?",
            "WARN",
            f"HEAD {short} touches indexable files but NOT in reindex.log; "
            f"retry with `git hook run post-commit`{suffix}",
        )
    else:
        r.line(
            "hook missed?",
            "WARN",
            f"reindex.log missing and manifest not successful{suffix}; "
            "retry with `git hook run post-commit`",
        )


def _check_git_tools(r: Report, repo: Path) -> None:
    rc, o = _git(repo, "status", "--short", "tools/")
    if rc == 127:
        r.line("git tools/", "WARN", "git not available")
    elif not o.strip():
        r.line("git tools/", "OK", "clean")
    else:
        r.line("git tools/", "WARN", f"{len(o.splitlines())} uncommitted file(s)")


def _check_webhook_extra_repo_mapping(r: Report, cfg: dict) -> None:
    from codev_platform.core.repos import webhook_enabled_project_ids, webhook_extra_repo_mapping_issues
    issues = webhook_extra_repo_mapping_issues(cfg, project_ids=webhook_enabled_project_ids(cfg))
    if not issues:
        r.line("webhook extra repos", "OK", "all extra repos have webhook mapping or no extra_repos")
        return
    samples = "; ".join(
        f"{i['project_id']} -> {i['extra']} ({i['reason']})" for i in issues[:3]
    )
    more = f"; +{len(issues) - 3} more" if len(issues) > 3 else ""
    r.line("webhook extra repos", "WARN", samples + more)


def _check_reindex_worker(r: Report) -> None:
    from codev_platform.reindex.status import format_summary, summarize
    summary = summarize()
    r.line("reindex worker", summary.get("severity", "WARN"), format_summary(summary))
