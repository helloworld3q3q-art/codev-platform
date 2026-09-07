"""Health checks for stores owned by the current local runtime."""

from __future__ import annotations

import json
import sqlite3
from datetime import datetime
from pathlib import Path

from ._util import Report, _parse_dt


def _check_graph_store(r: Report, project_id: str) -> None:
    """Report unified graph store counts and freshness."""
    from codev_platform.graph.store import graph_store_path, open_store

    database = graph_store_path(project_id)
    if not database.is_file():
        r.line("graph store", "INFO", "未建 (跑 reindex --ingest 生成统一图谱)")
        return
    try:
        with open_store(project_id, mode="ro") as store:
            data = store.stats(project_id)
        totals = data["totals"]
        nodes, edges = totals["nodes"], totals["edges"]
        ingested = [
            plugin["ingested_at"]
            for plugin in data["plugins"]
            if plugin.get("ingested_at")
        ]
        latest = max(ingested) if ingested else "?"
    except Exception as exc:  # noqa: BLE001
        r.line("graph store", "FAIL", f"query failed: {exc!r}")
        return
    if nodes == 0:
        r.line("graph store", "WARN", f"0 nodes (ingest 未跑/失败); last={latest}")
    else:
        r.line("graph store", "OK", f"nodes={nodes} edges={edges} last={latest}")


def _check_codegraph_db(r: Report, repo: Path, chroma_py: Path | None) -> None:
    lock = repo / ".codegraph" / ".rebuild.lock"
    database = repo / ".codegraph" / "codegraph.db"
    if lock.is_file():
        try:
            value = json.loads(lock.read_text(encoding="utf-8"))
            started = _parse_dt(value.get("started_at", ""))
            elapsed = int((datetime.now() - started).total_seconds()) if started else -1
            r.line(
                "codegraph db",
                "WARN",
                f"rebuild in progress (mode={value.get('mode')}, pid={value.get('pid')}, "
                f"{elapsed}s) - retry later",
            )
        except Exception as exc:  # noqa: BLE001
            r.line("codegraph db", "WARN", f"rebuild lock present but unreadable: {exc!r}")
        return
    if not database.is_file():
        r.line("codegraph db", "FAIL", f"missing: {database} (run codegraph rebuild)")
        return
    size = round(database.stat().st_size / (1024 * 1024), 1)
    try:
        connection = sqlite3.connect(str(database))
        cursor = connection.cursor()
        integrity = cursor.execute("PRAGMA integrity_check").fetchone()[0]
        journal = cursor.execute("PRAGMA journal_mode").fetchone()[0]
        nodes = cursor.execute("select count(*) from nodes").fetchone()[0]
        edges = cursor.execute("select count(*) from edges").fetchone()[0]
        connection.close()
    except Exception as exc:  # noqa: BLE001
        r.line(
            "codegraph db",
            "FAIL",
            f"{database} present but query failed (corrupt/locked?): {exc!r}",
        )
        return
    info = f"integrity={integrity} journal_mode={journal} nodes={nodes} edges={edges}"
    if integrity != "ok":
        r.line("codegraph db", "FAIL", f"{database} ({size} MB) INTEGRITY BROKEN: {info}")
    elif journal in ("delete", "memory"):
        r.line("codegraph db", "WARN", f"{database} ({size} MB) on WASM fallback: {info}")
    else:
        r.line("codegraph db", "OK", f"{database} ({size} MB, {info})")


def _check_codegraph_mcp(r: Report, repo: Path, procs: list[dict[str, str]]) -> None:
    if procs:
        servers = [
            process
            for process in procs
            if process["cmdline"]
            and "codegraph" in process["cmdline"]
            and "serve" in process["cmdline"]
            and "--mcp" in process["cmdline"]
        ]
        if len(servers) > 1:
            pids = ",".join(process["pid"] for process in servers)
            r.line(
                "codegraph mcp",
                "INFO",
                f"stdio servers count={len(servers)} pids={pids} (multiple sessions)",
            )
        elif len(servers) == 1:
            r.line(
                "codegraph mcp",
                "INFO",
                f"server pid={servers[0]['pid']} (DB may be locked for CLI status)",
            )
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


__all__ = ["_check_codegraph_db", "_check_codegraph_mcp", "_check_graph_store"]
