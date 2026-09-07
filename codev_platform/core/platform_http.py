"""Small authenticated HTTP client shared by platform control-plane readers.

The client owns only transport concerns: endpoint selection, bearer-token discovery,
bounded requests, and JSON decoding. Domain response validation stays with each caller.
"""

from __future__ import annotations

import json
import os
import urllib.error
import urllib.request
from collections.abc import Callable
from pathlib import Path
from typing import Any

from codev_platform.core.config import get
from codev_platform.core.wsl_data_owner import wsl_data_owner


DEFAULT_PLATFORM_WEB_PORT = 18088
_TOKEN_ENV_FALLBACKS = ("PLATFORM_TOKEN", "CODEV_PLATFORM_MCP_TOKEN")
UrlOpen = Callable[..., Any]


def platform_token_env_names(cfg: dict) -> list[str]:
    """Return configured and compatibility token variable names in priority order."""
    names: list[str] = []
    configured = get(cfg, "platform.token_env")
    if configured:
        names.append(str(configured))
    names.extend(_TOKEN_ENV_FALLBACKS)
    return list(dict.fromkeys(name for name in names if name))


def read_env_file_values(path: str | None, allowed_names: set[str]) -> dict[str, str]:
    """Read allow-listed literal values from an EnvironmentFile without evaluation."""
    if not path:
        return {}
    env_path = Path(str(path)).expanduser()
    try:
        lines = env_path.read_text(encoding="utf-8").splitlines()
    except OSError:
        return {}
    values: dict[str, str] = {}
    for raw in lines:
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        if line.startswith("export "):
            line = line[len("export ") :].strip()
        key, separator, value = line.partition("=")
        key = key.strip()
        if (
            not separator
            or not key
            or not key.replace("_", "").isalnum()
            or key[0].isdigit()
            or key not in allowed_names
        ):
            continue
        value = value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in {"'", '"'}:
            value = value[1:-1]
        values[key] = value
    return values


def platform_token_candidates(cfg: dict) -> list[tuple[str, str]]:
    """Resolve bearer tokens without ever returning them in diagnostics."""
    names = platform_token_env_names(cfg)
    candidates = [
        (name, token)
        for name in names
        if (token := os.environ.get(name))
    ]
    if candidates:
        return candidates
    env_values = read_env_file_values(get(cfg, "systemd.env_file"), set(names))
    return [(name, token) for name in names if (token := env_values.get(name))]


def authenticated_requests(
    url: str,
    cfg: dict,
    *,
    method: str = "GET",
    headers: dict[str, str] | None = None,
) -> list[tuple[str | None, urllib.request.Request]]:
    """Build one request per unique token, or one anonymous request as fallback."""
    base_headers = dict(headers or {})
    requests: list[tuple[str | None, urllib.request.Request]] = []
    seen_tokens: set[str] = set()
    for env_name, token in platform_token_candidates(cfg):
        if token in seen_tokens:
            continue
        seen_tokens.add(token)
        request = urllib.request.Request(
            url,
            headers=dict(base_headers),
            method=method.upper(),
        )
        # urllib copies normal headers across redirects. Mark credentials as
        # unredirected so a 30x can never forward the bearer to another origin.
        request.add_unredirected_header("Authorization", f"Bearer {token}")
        requests.append(
            (
                env_name,
                request,
            )
        )
    if requests:
        return requests
    return [(None, urllib.request.Request(url, headers=base_headers, method=method.upper()))]


def authenticated_json_request(
    url: str,
    cfg: dict,
    *,
    method: str = "GET",
    headers: dict[str, str] | None = None,
    timeout: float = 20,
    opener: UrlOpen | None = None,
) -> Any:
    """Read JSON with bounded token rotation; only HTTP 401 advances a candidate."""
    selected_opener = urllib.request.urlopen if opener is None else opener
    last_unauthorized: urllib.error.HTTPError | None = None
    for _env_name, request in authenticated_requests(
        url,
        cfg,
        method=method,
        headers=headers,
    ):
        try:
            with selected_opener(request, timeout=timeout) as response:
                return json.loads(response.read().decode("utf-8", "replace"))
        except urllib.error.HTTPError as exc:
            if exc.code != 401:
                raise
            last_unauthorized = exc
    if last_unauthorized is not None:
        raise last_unauthorized
    raise RuntimeError("no platform HTTP request attempted")


def platform_web_url(cfg: dict) -> str:
    """Resolve the web control-plane base URL without deriving it from a data path."""
    explicit = get(cfg, "platform.web_url") or get(cfg, "web.url")
    if explicit:
        return str(explicit).rstrip("/")

    host = "127.0.0.1"
    if wsl_data_owner(cfg) is not None:
        source = get(cfg, "mcp_sources.platform") or {}
        if isinstance(source, dict):
            host = str(source.get("host") or host)
    else:
        configured_host = str(get(cfg, "web.host", host) or host)
        host = "127.0.0.1" if configured_host in {"0.0.0.0", "::"} else configured_host
    if ":" in host and not host.startswith("["):
        host = f"[{host}]"
    port = int(get(cfg, "web.port", DEFAULT_PLATFORM_WEB_PORT))
    return f"http://{host}:{port}"


__all__ = [
    "DEFAULT_PLATFORM_WEB_PORT",
    "authenticated_json_request",
    "authenticated_requests",
    "platform_token_candidates",
    "platform_token_env_names",
    "platform_web_url",
    "read_env_file_values",
]
