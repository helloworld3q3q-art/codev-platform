"""正式运行服务连续稳定性与 Webhook 延迟开放证明。"""

from __future__ import annotations

import hashlib
import re
import subprocess
import sys
import time
from collections.abc import Callable
from dataclasses import dataclass

from codev_platform.runtime_deployment_contract import RuntimeDeploymentError


STABLE_RUNTIME_UNITS = (
    "codev-mcp-platform-docs.service",
    "codev-mcp-codegraph.service",
    "codev-mcp-agent-memory.service",
    "codev-mcp-graph.service",
    "codev-reindex.service",
    "codev-agent.service",
    "codev-web.service",
)
_WEBHOOK_UNIT = "codev-webhook.service"
_INVOCATION_ID = re.compile(r"[0-9a-f]{32}\Z")
_SYSTEMCTL = "/usr/bin/systemctl"


@dataclass(frozen=True, slots=True)
class SystemdRuntimeState:
    active_state: str
    sub_state: str
    main_pid: int
    invocation_id: str
    restart_count: int


@dataclass(frozen=True, slots=True)
class SystemdAcceptanceEvidence:
    evidence_sha256: str


@dataclass(frozen=True, slots=True)
class SystemdStabilityPorts:
    read_state: Callable[[str], SystemdRuntimeState]
    wait: Callable[[float], None]

    def validate(self) -> SystemdStabilityPorts:
        if (
            type(self) is not SystemdStabilityPorts
            or not callable(self.read_state)
            or not callable(self.wait)
        ):
            raise RuntimeDeploymentError("systemd 稳定性端口不可用")
        return self


def verify_systemd_stability(
    *,
    ports: SystemdStabilityPorts | None = None,
    stability_sec: float = 5.0,
    platform_name: str | None = None,
) -> SystemdAcceptanceEvidence:
    """在有界窗口两端证明全部长驻服务身份与重启次数完全一致。"""
    _require_linux(platform_name)
    window = _require_stability_window(stability_sec)
    active = default_ports() if ports is None else ports.validate()
    try:
        before = tuple(_require_running(unit, active.read_state(unit)) for unit in STABLE_RUNTIME_UNITS)
        active.wait(window)
        after = tuple(_require_running(unit, active.read_state(unit)) for unit in STABLE_RUNTIME_UNITS)
    except (KeyboardInterrupt, SystemExit, MemoryError):
        raise
    except RuntimeDeploymentError:
        raise
    except Exception:
        raise RuntimeDeploymentError("systemd 稳定性无法证明") from None
    if before != after:
        raise RuntimeDeploymentError("systemd 稳定性窗口内进程身份发生漂移")
    evidence = hashlib.sha256(
        "".join(
            f"{unit}:{state.main_pid}:{state.invocation_id}:{state.restart_count}\n"
            for unit, state in zip(STABLE_RUNTIME_UNITS, after, strict=True)
        ).encode("ascii")
    ).hexdigest()
    return SystemdAcceptanceEvidence(evidence)


def verify_ingress_stability(
    *,
    ports: SystemdStabilityPorts | None = None,
    stability_sec: float = 5.0,
    platform_name: str | None = None,
) -> SystemdAcceptanceEvidence:
    """Webhook 开放后必须跨有界窗口保持进程身份和重启次数不变。"""
    _require_linux(platform_name)
    window = _require_stability_window(stability_sec)
    active = default_ports() if ports is None else ports.validate()
    try:
        before = _require_running(_WEBHOOK_UNIT, active.read_state(_WEBHOOK_UNIT))
        active.wait(window)
        after = _require_running(_WEBHOOK_UNIT, active.read_state(_WEBHOOK_UNIT))
    except (KeyboardInterrupt, SystemExit, MemoryError):
        raise
    except RuntimeDeploymentError:
        raise
    except Exception:
        raise RuntimeDeploymentError("Webhook 稳定性无法证明") from None
    if before != after:
        raise RuntimeDeploymentError("Webhook 稳定性窗口内进程身份发生漂移")
    evidence = hashlib.sha256(
        (
            f"{_WEBHOOK_UNIT}:{after.main_pid}:{after.invocation_id}:"
            f"{after.restart_count}\n"
        ).encode("ascii")
    ).hexdigest()
    return SystemdAcceptanceEvidence(evidence)


def verify_ingress_closed(
    *,
    ports: SystemdStabilityPorts | None = None,
    platform_name: str | None = None,
) -> SystemdAcceptanceEvidence:
    """最终验收阶段要求 Webhook 仍未开放，避免健康探针接收真实流量。"""
    _require_linux(platform_name)
    active = default_ports() if ports is None else ports.validate()
    try:
        state = active.read_state(_WEBHOOK_UNIT)
    except (KeyboardInterrupt, SystemExit, MemoryError):
        raise
    except Exception:
        raise RuntimeDeploymentError("Webhook 关闭状态无法证明") from None
    if type(state) is not SystemdRuntimeState or state != SystemdRuntimeState(
        "inactive",
        "dead",
        0,
        "",
        state.restart_count,
    ):
        raise RuntimeDeploymentError("Webhook 未保持完全停止")
    if type(state.restart_count) is not int or state.restart_count < 0:
        raise RuntimeDeploymentError("Webhook 重启次数无效")
    evidence = hashlib.sha256(
        f"{_WEBHOOK_UNIT}:inactive:dead:0:{state.restart_count}\n".encode("ascii")
    ).hexdigest()
    return SystemdAcceptanceEvidence(evidence)


def default_ports() -> SystemdStabilityPorts:
    return SystemdStabilityPorts(read_state=_read_state, wait=time.sleep)


def _require_running(unit: str, state: SystemdRuntimeState) -> SystemdRuntimeState:
    if (
        type(state) is not SystemdRuntimeState
        or state.active_state != "active"
        or state.sub_state != "running"
        or type(state.main_pid) is not int
        or state.main_pid <= 0
        or _INVOCATION_ID.fullmatch(state.invocation_id) is None
        or type(state.restart_count) is not int
        or state.restart_count < 0
    ):
        raise RuntimeDeploymentError(f"受管服务未处于稳定运行态：{unit}")
    return state


def _read_state(unit: str) -> SystemdRuntimeState:
    if unit not in {*STABLE_RUNTIME_UNITS, _WEBHOOK_UNIT}:
        raise RuntimeDeploymentError("systemd 稳定性单元不在固定注册表")
    try:
        result = subprocess.run(
            (
                _SYSTEMCTL,
                "show",
                unit,
                "--property=ActiveState",
                "--property=SubState",
                "--property=MainPID",
                "--property=InvocationID",
                "--property=NRestarts",
            ),
            stdin=subprocess.DEVNULL,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="strict",
            check=False,
            timeout=10.0,
        )
    except (OSError, subprocess.SubprocessError):
        raise RuntimeDeploymentError("systemd 稳定性状态无法读取") from None
    if result.returncode != 0 or len(result.stdout) > 16 * 1024:
        raise RuntimeDeploymentError("systemd 稳定性状态无法读取")
    expected = {"ActiveState", "SubState", "MainPID", "InvocationID", "NRestarts"}
    values: dict[str, str] = {}
    for line in result.stdout.splitlines():
        key, separator, value = line.partition("=")
        if not separator or key not in expected or key in values or "\x00" in value:
            raise RuntimeDeploymentError("systemd 稳定性状态格式无效")
        values[key] = value
    if set(values) != expected or not values["MainPID"].isdigit() or not values["NRestarts"].isdigit():
        raise RuntimeDeploymentError("systemd 稳定性状态字段不完整")
    return SystemdRuntimeState(
        values["ActiveState"],
        values["SubState"],
        int(values["MainPID"]),
        values["InvocationID"],
        int(values["NRestarts"]),
    )


def _require_linux(platform_name: str | None) -> None:
    selected = sys.platform if platform_name is None else platform_name
    if type(selected) is not str or not selected.startswith("linux"):
        raise RuntimeDeploymentError("systemd 验收只允许在 Linux 执行")


def _require_stability_window(value: object) -> float:
    if type(value) not in {int, float} or not 1.0 <= float(value) <= 60.0:
        raise RuntimeDeploymentError("systemd 稳定性窗口无效")
    return float(value)


__all__ = [
    "STABLE_RUNTIME_UNITS",
    "SystemdAcceptanceEvidence",
    "SystemdRuntimeState",
    "SystemdStabilityPorts",
    "verify_ingress_closed",
    "verify_ingress_stability",
    "verify_systemd_stability",
]
