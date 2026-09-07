"""MCP 端点声明、端口真值、启动命令和客户端源配置。"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from codev_platform.core.config import get as _cfg_get


_log = logging.getLogger("codev_platform.mcp_serve")

DEFAULT_CHROMA_PORT = 18083
DEFAULT_CODEGRAPH_PORT = 18091
DEFAULT_AGENT_MEMORY_PORT = 18087
DEFAULT_GRAPH_PORT = 18092

_SERVICE_PORTS: dict[str, tuple[str, list[str], int]] = {
    "chroma": ("mcp.platform_docs_sse_port", ["daemon.port"], DEFAULT_CHROMA_PORT),
    "codegraph": ("mcp.codegraph_sse_port", [], DEFAULT_CODEGRAPH_PORT),
    "agent_memory": ("mcp.agent_memory_sse_port", [], DEFAULT_AGENT_MEMORY_PORT),
    "graph": ("mcp.graph_sse_port", [], DEFAULT_GRAPH_PORT),
}
_KIND_TO_TOOL = {
    "chroma": "platform-docs",
    "codegraph": "codegraph",
    "agent_memory": "agent-memory",
    "graph": "graph",
}
_warned_deprecated: set[str] = set()


def _warn_deprecated(old_key: str, canonical: str) -> None:
    """旧别名命中时只记录一次迁移提示。"""
    if old_key not in _warned_deprecated:
        _warned_deprecated.add(old_key)
        _log.warning(
            "[mcp] config 键 %r 已废弃,请改用 %r(仍兼容可读,后续版本移除)", old_key, canonical
        )


def mcp_bind_host(cfg: dict) -> str:
    """返回 MCP 服务唯一 bind host；空值失败回落到 loopback。"""
    raw = str(_cfg_get(cfg, "mcp.bind_host", "127.0.0.1")).strip()
    return raw or "127.0.0.1"


def _bind_port(cfg: dict, kind: str) -> int:
    """按 canonical 键、兼容别名、默认值的顺序解析服务端口。"""
    canonical, aliases, default = _SERVICE_PORTS[kind]
    value = _cfg_get(cfg, canonical)
    if value is not None:
        return int(value)
    for alias in aliases:
        value = _cfg_get(cfg, alias)
        if value is not None:
            _warn_deprecated(alias, canonical)
            return int(value)
    return default


@dataclass
class MCPEndpoint:
    """一个 MCP 服务端点。"""

    name: str
    kind: str
    port: int
    project_id: str | None = None
    cmd: list[str] | None = None
    cwd: str | None = None
    self_spawned: bool = False

    @property
    def host(self) -> str:
        return "127.0.0.1"

    @property
    def sse_url(self) -> str:
        return f"http://{self.host}:{self.port}/sse"

    @property
    def health_url(self) -> str | None:
        return f"http://{self.host}:{self.port}/healthz"


def build_codegraph_cmd(python: str | Path, port: int) -> list[str]:
    """构造 CodeGraph 多租户 HTTP 代理启动命令。"""
    return [
        str(python),
        "-I",
        "-m",
        "codev_platform.codegraph.server",
        "--http",
        "--port",
        str(port),
    ]


def build_agent_memory_cmd(python: str | Path, port: int) -> list[str]:
    """构造 Agent Memory MCP HTTP 端点启动命令。"""
    return [
        str(python),
        "-I",
        "-m",
        "codev_platform.agent.memory_mcp",
        "--http",
        "--port",
        str(port),
    ]


def build_graph_cmd(python: str | Path, port: int) -> list[str]:
    """构造统一图谱 MCP HTTP 端点启动命令。"""
    return [
        str(python),
        "-I",
        "-m",
        "codev_platform.graph.mcp_server",
        "--http",
        "--port",
        str(port),
    ]


MCP_SOURCE_TOOLS = ("platform-docs", "codegraph", "agent-memory", "graph")
_TOOL_TO_KIND = {tool: kind for kind, tool in _KIND_TO_TOOL.items()}
DEFAULT_MCP_SOURCES: dict[str, dict[str, Any]] = {
    "local": {"host": "127.0.0.1"},
    "platform": {
        "host": "127.0.0.1",
        "platform-docs": 19083,
        "codegraph": 19091,
        "agent-memory": 19087,
        "graph": 19092,
    },
}


def mcp_source_kind(tool: str) -> str:
    """Return the stable service kind for a public MCP source tool name."""
    try:
        return _TOOL_TO_KIND[tool]
    except KeyError:
        raise ValueError(f"未知 MCP source tool: {tool}") from None


def mcp_source_endpoint(cfg: dict, target: str, tool: str) -> tuple[str, int]:
    """解析客户端源 target/tool 的 host 与 port。"""
    source = dict(DEFAULT_MCP_SOURCES.get(target) or {})
    override = _cfg_get(cfg, f"mcp_sources.{target}") or {}
    if isinstance(override, dict):
        source.update(override)
    host = str(source.get("host", "127.0.0.1"))
    port = source.get(tool)
    if port is None and target == "local" and tool in _TOOL_TO_KIND:
        return host, _bind_port(cfg, _TOOL_TO_KIND[tool])
    if port is None:
        raise ValueError(
            f"源 '{target}' 未定义 tool '{tool}' 的端口 (config.mcp_sources.{target}.{tool})"
        )
    return host, int(port)


def check_port_consistency(cfg: dict) -> list[str]:
    """报告显式 local 源端口与同机 bind 端口的不一致。"""
    warnings: list[str] = []
    override = _cfg_get(cfg, "mcp_sources.local") or {}
    if not isinstance(override, dict):
        return warnings
    for tool, kind in _TOOL_TO_KIND.items():
        local_port = override.get(tool)
        if local_port is None:
            continue
        bind = _bind_port(cfg, kind)
        if int(local_port) != bind:
            warnings.append(
                f"端口不一致: mcp_sources.local.{tool}={local_port} ≠ {kind} bind 口 {bind} "
                f"(派生默认即可对齐; 如非反代有意错开, 检查是否只改了一边)"
            )
    return warnings


def mcp_source_url(cfg: dict, target: str, tool: str, project_id: str) -> str:
    """生成业务仓使用的多租户 SSE URL。"""
    host, port = mcp_source_endpoint(cfg, target, tool)
    return f"http://{host}:{port}/sse?project_id={project_id}"


def build_mcp_servers(cfg: dict, target: str, project_id: str) -> dict:
    """生成 `.mcp.json` 的四端点 `mcpServers` 块。"""
    return {
        tool: {"type": "sse", "url": mcp_source_url(cfg, target, tool, project_id)}
        for tool in MCP_SOURCE_TOOLS
    }


__all__ = [
    "DEFAULT_AGENT_MEMORY_PORT",
    "DEFAULT_CHROMA_PORT",
    "DEFAULT_CODEGRAPH_PORT",
    "DEFAULT_GRAPH_PORT",
    "DEFAULT_MCP_SOURCES",
    "MCPEndpoint",
    "MCP_SOURCE_TOOLS",
    "_bind_port",
    "build_agent_memory_cmd",
    "build_codegraph_cmd",
    "build_graph_cmd",
    "build_mcp_servers",
    "check_port_consistency",
    "mcp_bind_host",
    "mcp_source_endpoint",
    "mcp_source_kind",
    "mcp_source_url",
]
