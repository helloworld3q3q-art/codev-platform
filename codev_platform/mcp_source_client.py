"""Read-only MCP source probing and owner-managed start orchestration.

This client-side module deliberately stays outside ``mcp_serve``: selecting a
remote source must not alter the WSL server or systemd rendering contract.
"""

from __future__ import annotations

import http.client
import time
import urllib.error
import urllib.request
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from codev_platform.mcp_endpoint_catalog import (
    MCP_SOURCE_TOOLS,
    mcp_source_endpoint,
    mcp_source_kind,
)
from codev_platform.mcp_systemd_unit_registry import mcp_systemd_unit_for_kind

_SOURCE_PROBE_TIMEOUT_SEC = 2.0


@dataclass(frozen=True, slots=True)
class MCPSourceEndpoint:
    """A non-spawning endpoint selected by a client source profile."""

    name: str
    kind: str
    host: str
    port: int
    project_id: str | None = None

    @property
    def sse_url(self) -> str:
        return f"http://{self.host}:{self.port}/sse"

    @property
    def health_url(self) -> str:
        return f"http://{self.host}:{self.port}/healthz"


def iter_source_endpoints(cfg: dict, target: str) -> list[MCPSourceEndpoint]:
    """Map service declarations to a non-spawning client source."""
    endpoints: list[MCPSourceEndpoint] = []
    for tool in MCP_SOURCE_TOOLS:
        host, port = mcp_source_endpoint(cfg, target, tool)
        endpoints.append(
            MCPSourceEndpoint(
                name=tool,
                kind=mcp_source_kind(tool),
                host=host,
                port=port,
            )
        )
    return endpoints


def _http_health(url: str, timeout: float = 2.0) -> bool:
    try:
        with urllib.request.urlopen(url, timeout=timeout) as response:
            return response.status == 200
    except (
        urllib.error.URLError,
        http.client.HTTPException,
        ConnectionRefusedError,
        TimeoutError,
        OSError,
    ):
        return False


def probe_source(endpoint: MCPSourceEndpoint, *, timeout: float = 2.0) -> str:
    """Probe a managed remote source through HTTP within one shared budget."""
    deadline = time.monotonic() + min(_SOURCE_PROBE_TIMEOUT_SEC, max(0.0, timeout))
    if endpoint.health_url:
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            return "down"
        if _http_health(
            endpoint.health_url,
            timeout=remaining,
        ):
            return "ok"
    legacy_url = f"http://{endpoint.host}:{endpoint.port}/health"
    if legacy_url != endpoint.health_url:
        remaining = deadline - time.monotonic()
        if remaining > 0 and _http_health(
            legacy_url,
            timeout=remaining,
        ):
            return "ok"
    return "down"


def probe_source_all(
    cfg: dict,
    target: str,
    *,
    assumed_healthy_kinds: frozenset[str] = frozenset(),
) -> list[dict[str, Any]]:
    """Probe a client source without reading local dependency or data paths."""
    rows: list[dict[str, Any]] = []
    for endpoint in iter_source_endpoints(cfg, target):
        status = "ok" if endpoint.kind in assumed_healthy_kinds else probe_source(endpoint)
        rows.append(
            {
                "name": endpoint.name,
                "kind": endpoint.kind,
                "port": endpoint.port,
                "project_id": endpoint.project_id,
                "status": status,
                "sse_url": endpoint.sse_url,
                "self_spawned": False,
                "reason": "" if status == "ok" else f"{target} source unreachable",
            }
        )
    return rows


def wait_until_source_serving(
    cfg: dict,
    target: str,
    *,
    timeout: float = 60.0,
    interval: float = 2.0,
) -> list[dict[str, Any]]:
    """Wait until every endpoint is HTTP-healthy in the same probe round."""
    endpoints = iter_source_endpoints(cfg, target)
    statuses = {endpoint.name: "down" for endpoint in endpoints}
    started_at = time.monotonic()
    deadline = started_at + max(0.0, timeout)
    while True:
        full_round = True
        round_statuses: dict[str, str] = {}
        for endpoint in endpoints:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                full_round = False
                break
            round_statuses[endpoint.name] = probe_source(
                endpoint,
                timeout=min(_SOURCE_PROBE_TIMEOUT_SEC, remaining),
            )
        statuses = {
            endpoint.name: round_statuses.get(endpoint.name, "down") for endpoint in endpoints
        }
        all_ok = full_round and all(status == "ok" for status in statuses.values())
        remaining = deadline - time.monotonic()
        if all_ok or remaining <= 0:
            break
        time.sleep(min(interval, remaining))
    return [
        {
            "name": endpoint.name,
            "kind": endpoint.kind,
            "port": endpoint.port,
            "status": "ok" if statuses[endpoint.name] == "ok" else "timeout",
            **(
                {}
                if statuses[endpoint.name] == "ok"
                else {"reason": f"{target} source unreachable"}
            ),
        }
        for endpoint in endpoints
    ]


def ensure_source_serving(
    cfg: dict,
    target: str,
    *,
    start_unit: Callable[[str], dict[str, Any]],
) -> list[dict[str, Any]]:
    """Ensure a managed source through an injected service-manager strategy."""
    results: list[dict[str, Any]] = []
    for endpoint in iter_source_endpoints(cfg, target):
        if probe_source(endpoint) == "ok":
            results.append({"name": endpoint.name, "action": "already-up", "status": "ok"})
            continue
        unit = mcp_systemd_unit_for_kind(endpoint.kind)
        try:
            started = dict(start_unit(unit))
        except Exception as exc:  # noqa: BLE001 - CLI reports bounded adapter failure
            started = {"action": "failed", "error": type(exc).__name__}
        action = str(started.get("action") or "failed")
        results.append(
            {
                "name": endpoint.name,
                **started,
                "action": action,
                "status": "starting" if action == "started" else "down",
            }
        )
    return results


__all__ = [
    "MCPSourceEndpoint",
    "ensure_source_serving",
    "iter_source_endpoints",
    "probe_source",
    "probe_source_all",
    "wait_until_source_serving",
]
