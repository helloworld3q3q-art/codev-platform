"""Presentation adapter for health checks whose data owner is another runtime."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from codev_platform.core.wsl_data_owner import WslDataOwner

from ._util import Report


_REQUIRED_ENDPOINTS = frozenset({"platform-docs", "codegraph", "agent-memory", "graph"})


@dataclass(frozen=True, slots=True)
class RemoteHealthContext:
    owner: WslDataOwner
    project_id: str
    project: dict[str, Any] | None = None
    endpoints: tuple[dict[str, Any], ...] = field(default_factory=tuple)
    error_type: str | None = None


def _report_codegraph(r: Report, value: Any) -> None:
    if isinstance(value, dict):
        nodes, edges = value.get("nodes", 0), value.get("edges", 0)
        status = "OK" if nodes else "WARN"
        r.line("codegraph db", status, f"nodes={nodes} edges={edges} via HTTP")
    elif value == "no_db":
        r.line("codegraph db", "FAIL", "platform owner reports no database")
    elif value == "no_repo_path":
        r.line("codegraph db", "WARN", "platform owner has no registered repo path")
    else:
        r.line("codegraph db", "WARN", "platform owner status unavailable")


def _report_graph(r: Report, value: Any) -> None:
    if isinstance(value, dict):
        nodes, edges = value.get("nodes", 0), value.get("edges", 0)
        status = "OK" if nodes else "WARN"
        r.line("graph store", status, f"nodes={nodes} edges={edges} via HTTP")
    elif value in {"not_built", None}:
        r.line("graph store", "INFO", "not built (reported by platform owner)")
    else:
        r.line("graph store", "WARN", "platform owner status unavailable")


def _report_endpoints(r: Report, endpoints: tuple[dict[str, Any], ...]) -> None:
    selected = [
        row
        for row in endpoints
        if row.get("name") in {"platform-docs", "codegraph", "graph", "agent-memory"}
    ]
    present = {str(row.get("name")) for row in selected}
    missing = sorted(_REQUIRED_ENDPOINTS - present)
    down = sorted(str(row.get("name")) for row in selected if row.get("status") != "ok")
    if missing or down:
        details = []
        if missing:
            details.append("MISSING: " + ", ".join(missing))
        if down:
            details.append("DOWN: " + ", ".join(down))
        r.line("MCP endpoints", "FAIL", "; ".join(details))
    else:
        ports = ", ".join(
            f"{row.get('name')}:{row.get('port')}" for row in selected
        )
        r.line("MCP endpoints", "OK", ports + " via HTTP")


def report_remote_stack(r: Report, context: RemoteHealthContext) -> None:
    """Render remote-owned platform data without touching its filesystem."""
    r.line(
        "platform data owner",
        "OK",
        f"WSL distro={context.owner.distro}; via HTTP (local data probes disabled)",
    )
    if context.error_type:
        r.line(
            "platform status",
            "FAIL",
            f"HTTP unavailable ({context.error_type}); run `codev-platform serve-mcp status`",
        )
        return
    if context.project is None:
        r.line(
            "platform project",
            "FAIL",
            f"{context.project_id} missing from platform HTTP status",
        )
        return

    chunks = context.project.get("chroma_chunks")
    if isinstance(chunks, int):
        r.line(
            "chroma collection",
            "OK" if chunks > 0 else "WARN",
            f"chunks={chunks} via HTTP",
        )
    else:
        r.line("chroma collection", "WARN", "platform owner status unavailable")
    _report_codegraph(r, context.project.get("codegraph"))
    _report_graph(r, context.project.get("graph"))
    _report_endpoints(r, context.endpoints)
    r.line(
        "reindex worker",
        "INFO",
        f"managed by WSL systemd ({context.owner.distro}); local queue probe disabled",
    )
    usage = context.project.get("usage_7d") or {}
    r.line(
        "platform usage 7d",
        "INFO",
        f"search_docs={usage.get('search_docs', 0)} codegraph={usage.get('codegraph', 0)} via HTTP",
    )


__all__ = ["RemoteHealthContext", "report_remote_stack"]
