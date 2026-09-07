"""正式运行服务的连续稳定性与 Webhook 延迟开放测试。"""

from __future__ import annotations

from dataclasses import replace

import pytest

from codev_platform.runtime_deployment_contract import RuntimeDeploymentError
from codev_platform.runtime_systemd_acceptance import (
    STABLE_RUNTIME_UNITS,
    SystemdRuntimeState,
    SystemdStabilityPorts,
    verify_ingress_closed,
    verify_ingress_stability,
    verify_systemd_stability,
)


def _state(unit: str) -> SystemdRuntimeState:
    index = STABLE_RUNTIME_UNITS.index(unit) + 1
    return SystemdRuntimeState(
        active_state="active",
        sub_state="running",
        main_pid=100 + index,
        invocation_id=f"{index:032x}",
        restart_count=0,
    )


def test_连续两次身份完全一致才接受服务稳定() -> None:
    events: list[str] = []

    def read(unit: str) -> SystemdRuntimeState:
        events.append(f"read:{unit}")
        return _state(unit)

    result = verify_systemd_stability(
        ports=SystemdStabilityPorts(read_state=read, wait=lambda seconds: events.append(f"wait:{seconds}")),
        stability_sec=3.0,
        platform_name="linux",
    )

    assert len(result.evidence_sha256) == 64
    assert events == [
        *(f"read:{unit}" for unit in STABLE_RUNTIME_UNITS),
        "wait:3.0",
        *(f"read:{unit}" for unit in STABLE_RUNTIME_UNITS),
    ]


@pytest.mark.parametrize("field", ("main_pid", "invocation_id", "restart_count"))
def test_窗口内进程身份或重启次数漂移即失败(field: str) -> None:
    calls = {unit: 0 for unit in STABLE_RUNTIME_UNITS}

    def read(unit: str) -> SystemdRuntimeState:
        calls[unit] += 1
        state = _state(unit)
        if unit == STABLE_RUNTIME_UNITS[0] and calls[unit] == 2:
            value = {
                "main_pid": state.main_pid + 1,
                "invocation_id": "f" * 32,
                "restart_count": state.restart_count + 1,
            }[field]
            return replace(state, **{field: value})
        return state

    with pytest.raises(RuntimeDeploymentError, match="稳定性"):
        verify_systemd_stability(
            ports=SystemdStabilityPorts(read_state=read, wait=lambda _seconds: None),
            stability_sec=1.0,
            platform_name="linux",
        )


def test_webhook_只有完全停止且无调用身份才算关闭() -> None:
    closed = SystemdRuntimeState("inactive", "dead", 0, "", 0)

    evidence = verify_ingress_closed(
        ports=SystemdStabilityPorts(read_state=lambda _unit: closed, wait=lambda _seconds: None),
        platform_name="linux",
    )

    assert len(evidence.evidence_sha256) == 64

    with pytest.raises(RuntimeDeploymentError, match="Webhook"):
        verify_ingress_closed(
            ports=SystemdStabilityPorts(
                read_state=lambda _unit: replace(closed, main_pid=7),
                wait=lambda _seconds: None,
            ),
            platform_name="linux",
        )


def test_webhook开放后必须跨窗口保持同一调用身份() -> None:
    events: list[str] = []
    running = SystemdRuntimeState("active", "running", 777, "f" * 32, 2)

    evidence = verify_ingress_stability(
        ports=SystemdStabilityPorts(
            read_state=lambda unit: events.append(f"read:{unit}") or running,
            wait=lambda seconds: events.append(f"wait:{seconds}"),
        ),
        stability_sec=2.0,
        platform_name="linux",
    )

    assert len(evidence.evidence_sha256) == 64
    assert events == [
        "read:codev-webhook.service",
        "wait:2.0",
        "read:codev-webhook.service",
    ]

    calls = 0

    def drifting(_unit: str) -> SystemdRuntimeState:
        nonlocal calls
        calls += 1
        return running if calls == 1 else replace(running, restart_count=3)

    with pytest.raises(RuntimeDeploymentError, match="Webhook 稳定性"):
        verify_ingress_stability(
            ports=SystemdStabilityPorts(read_state=drifting, wait=lambda _seconds: None),
            stability_sec=1.0,
            platform_name="linux",
        )
