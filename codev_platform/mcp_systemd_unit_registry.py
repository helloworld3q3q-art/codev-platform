"""受管 systemd unit 的唯一只读注册表。"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import PurePosixPath


LEGACY_SYSTEMD_UNIT_DIRECTORY = PurePosixPath("/etc/systemd/system")
CANONICAL_SYSTEMD_UNIT_DIRECTORY = PurePosixPath("/usr/local/lib/systemd/system")
MULTI_USER_SYSTEMD_TARGET = "multi-user.target"
TIMERS_SYSTEMD_TARGET = "timers.target"
_ENABLE_TARGETS = frozenset({MULTI_USER_SYSTEMD_TARGET, TIMERS_SYSTEMD_TARGET})


@dataclass(frozen=True, slots=True)
class ManagedSystemdUnit:
    """单个 unit 的安装位置、生命周期与运行时归属。"""

    name: str
    enable: bool
    restart: bool
    activation_mode: str = "standard"
    runtime_bound: bool = True
    destination_directory: PurePosixPath = LEGACY_SYSTEMD_UNIT_DIRECTORY
    enable_target: str | None = MULTI_USER_SYSTEMD_TARGET

    def __post_init__(self) -> None:
        if self.enable:
            if type(self.enable_target) is not str or self.enable_target not in _ENABLE_TARGETS:
                raise ValueError("受管 systemd unit 启用目标无效")
        elif self.enable_target is not None:
            raise ValueError("无需启用的 systemd unit 不得声明启用目标")

    @property
    def destination(self) -> PurePosixPath:
        return self.destination_directory / self.name


_MCP_SYSTEMD_UNITS_BY_KIND = {
    "chroma": "codev-mcp-platform-docs.service",
    "codegraph": "codev-mcp-codegraph.service",
    "agent_memory": "codev-mcp-agent-memory.service",
    "graph": "codev-mcp-graph.service",
}
_REGISTRATIONS = (
    ManagedSystemdUnit(_MCP_SYSTEMD_UNITS_BY_KIND["chroma"], True, True),
    ManagedSystemdUnit(
        _MCP_SYSTEMD_UNITS_BY_KIND["codegraph"],
        True,
        True,
        activation_mode="codegraph_state_machine",
        destination_directory=CANONICAL_SYSTEMD_UNIT_DIRECTORY,
    ),
    ManagedSystemdUnit(_MCP_SYSTEMD_UNITS_BY_KIND["agent_memory"], True, True),
    ManagedSystemdUnit(_MCP_SYSTEMD_UNITS_BY_KIND["graph"], True, True),
    ManagedSystemdUnit(
        "codev-reindex.service",
        True,
        True,
        activation_mode="reindex_state_machine",
    ),
    ManagedSystemdUnit(
        "codev-webhook.service",
        True,
        True,
        activation_mode="ingress_state_machine",
    ),
    ManagedSystemdUnit("codev-agent.service", True, True),
    ManagedSystemdUnit("codev-web.service", True, True),
    ManagedSystemdUnit(
        "codev-clock-resync.service",
        False,
        False,
        runtime_bound=False,
        enable_target=None,
    ),
    ManagedSystemdUnit(
        "codev-clock-resync.timer",
        True,
        True,
        runtime_bound=False,
        enable_target=TIMERS_SYSTEMD_TARGET,
    ),
    ManagedSystemdUnit(
        "codev-memory-maintenance.service",
        False,
        False,
        enable_target=None,
    ),
    ManagedSystemdUnit(
        "codev-memory-maintenance.timer",
        True,
        True,
        runtime_bound=False,
        enable_target=TIMERS_SYSTEMD_TARGET,
    ),
)
MANAGED_SYSTEMD_UNITS = {item.name: item for item in _REGISTRATIONS}
MANAGED_SYSTEMD_UNIT_NAMES = frozenset(MANAGED_SYSTEMD_UNITS)
RUNTIME_BOUND_SYSTEMD_UNIT_NAMES = frozenset(
    name for name, item in MANAGED_SYSTEMD_UNITS.items() if item.runtime_bound
)
if (
    not RUNTIME_BOUND_SYSTEMD_UNIT_NAMES
    or not RUNTIME_BOUND_SYSTEMD_UNIT_NAMES < MANAGED_SYSTEMD_UNIT_NAMES
    or any(not name.endswith(".service") for name in RUNTIME_BOUND_SYSTEMD_UNIT_NAMES)
):
    raise RuntimeError("运行时绑定 unit 注册表无效")
DEPLOYMENT_GUARDED_SYSTEMD_UNITS = (
    "codev-webhook.service",
    "codev-memory-maintenance.timer",
    "codev-memory-maintenance.service",
    "codev-web.service",
    "codev-agent.service",
    "codev-mcp-platform-docs.service",
    "codev-mcp-agent-memory.service",
    "codev-mcp-graph.service",
    "codev-reindex.service",
    "codev-mcp-codegraph.service",
)
if (
    len(DEPLOYMENT_GUARDED_SYSTEMD_UNITS) != len(set(DEPLOYMENT_GUARDED_SYSTEMD_UNITS))
    or not set(DEPLOYMENT_GUARDED_SYSTEMD_UNITS) <= MANAGED_SYSTEMD_UNIT_NAMES
):
    raise RuntimeError("部署守卫 unit 注册表无效")
REINDEX_SYSTEMD_UNIT_NAME = "codev-reindex.service"
CODEGRAPH_SYSTEMD_UNIT_NAME = _MCP_SYSTEMD_UNITS_BY_KIND["codegraph"]
WEBHOOK_SYSTEMD_UNIT_NAME = "codev-webhook.service"
MAINTENANCE_DEFERRED_UNITS = (
    REINDEX_SYSTEMD_UNIT_NAME,
    CODEGRAPH_SYSTEMD_UNIT_NAME,
    WEBHOOK_SYSTEMD_UNIT_NAME,
)
TIMER_MANAGED_ONESHOT_SERVICES = frozenset(
    name
    for name, item in MANAGED_SYSTEMD_UNITS.items()
    if name.endswith(".service") and not item.enable
)


def managed_systemd_unit(name: str) -> ManagedSystemdUnit:
    """按固定名称读取注册项；未知 unit 失败关闭。"""
    try:
        return MANAGED_SYSTEMD_UNITS[name]
    except (KeyError, TypeError):
        raise ValueError("systemd unit 不在受管注册表中") from None


def mcp_systemd_unit_for_kind(kind: str) -> str:
    """按 MCP 端点种类返回唯一受管 unit，未知种类失败关闭。"""
    try:
        unit = _MCP_SYSTEMD_UNITS_BY_KIND[kind]
    except (KeyError, TypeError):
        raise ValueError("MCP 端点种类不在受管注册表中") from None
    if unit not in MANAGED_SYSTEMD_UNITS:
        raise ValueError("MCP 端点 unit 不在受管注册表中")
    return unit


__all__ = [
    "CANONICAL_SYSTEMD_UNIT_DIRECTORY",
    "CODEGRAPH_SYSTEMD_UNIT_NAME",
    "DEPLOYMENT_GUARDED_SYSTEMD_UNITS",
    "LEGACY_SYSTEMD_UNIT_DIRECTORY",
    "MAINTENANCE_DEFERRED_UNITS",
    "MANAGED_SYSTEMD_UNIT_NAMES",
    "MANAGED_SYSTEMD_UNITS",
    "ManagedSystemdUnit",
    "MULTI_USER_SYSTEMD_TARGET",
    "REINDEX_SYSTEMD_UNIT_NAME",
    "RUNTIME_BOUND_SYSTEMD_UNIT_NAMES",
    "TIMER_MANAGED_ONESHOT_SERVICES",
    "TIMERS_SYSTEMD_TARGET",
    "WEBHOOK_SYSTEMD_UNIT_NAME",
    "managed_systemd_unit",
    "mcp_systemd_unit_for_kind",
]
