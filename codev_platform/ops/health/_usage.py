"""health 子包 —— 使用率统计 (近 7 天; 从 ops/health.py 抽出, file-discipline §1)。

search_recall 命中率 / reindex 运行数 / platform-docs L2L3 采纳率 / cross-link 调用统计。
"""
from __future__ import annotations

import re
from datetime import datetime, timedelta
from pathlib import Path

from codev_platform.ops._common import matches_any

from ._util import Report, _git, _iter_jsonl, _parse_dt


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


def _usage_codegraph(r: Report, cg_usage: Path) -> None:
    # 镜像 _usage_cross_link: 读 codegraph 自写代理 (server.py) 的 codegraph_usage.jsonl。
    if not cg_usage.is_file():
        r.line("codegraph usage", "INFO", "codegraph_usage.jsonl not found (no calls yet)")
        return
    cutoff = datetime.now() - timedelta(days=7)
    rows = []
    for o in _iter_jsonl(cg_usage):
        if o.get("ts"):
            ts = _parse_dt(str(o["ts"]))
            if ts and ts < cutoff:
                continue
        rows.append(o)
    if not rows:
        r.line("codegraph usage", "INFO", "no codegraph calls (last 7d)")
        return
    by_tool: dict[str, int] = {}
    for o in rows:
        by_tool[o.get("tool", "?")] = by_tool.get(o.get("tool", "?"), 0) + 1
    ok = sum(1 for o in rows if o.get("ok"))
    ok_rate = round(100.0 * ok / len(rows))
    lat = sorted(float(o["elapsed_ms"]) for o in rows if o.get("elapsed_ms") is not None)
    med = f"{round(lat[len(lat) // 2])}ms" if lat else "n/a"
    tools = ",".join(f"{k}={v}" for k, v in by_tool.items())
    r.line("codegraph usage", "INFO",
           f"{len(rows)} calls / ok={ok_rate}% / median={med} / {tools} last 7d")
