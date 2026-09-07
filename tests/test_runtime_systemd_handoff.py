"""systemd stage 回执与部署门禁的计划绑定交接测试。"""

from __future__ import annotations

from contextlib import contextmanager

import pytest

from codev_platform.core.runtime_interpreter import ReleaseInterpreterIdentity
from codev_platform.runtime_deployment_contract import DeploymentPlan, RuntimeDeploymentError


def _plan() -> DeploymentPlan:
    return DeploymentPlan(
        schema_version=2,
        target_revision="a" * 40,
        source_repo="/home/helloworld/work/codev-platform",
        runtime_root="/var/lib/codev-platform/runtime",
        requirements_lock="/srv/codev-artifacts/wsl-runtime.lock",
        approved_requirements="/srv/codev-artifacts/wsl-runtime.freeze",
        wheelhouse="/srv/codev-artifacts/wheelhouse",
        candidate_root="/var/tmp/codev-platform-candidates/helloworld",
        config_source="/home/helloworld/.codev-platform/config.json",
        environment_source="/etc/codev-platform/platform.source.env",
        service_user="helloworld",
        project_id="codev-platform",
        require_cuda=True,
    )


def _release() -> ReleaseInterpreterIdentity:
    return ReleaseInterpreterIdentity(
        runtime_revision="a" * 40,
        release_id="2" * 64,
        interpreter_path=(
            "/var/lib/codev-platform/runtime/releases/" + "2" * 64 + "/venv/bin/python"
        ),
    )


def _ports(
    events: list[str],
    *,
    fail_ingress: bool = False,
    fail_stage: bool = False,
):
    from codev_platform.runtime_systemd_handoff import SystemdHandoffPorts

    @contextmanager
    def lock():
        events.append("lock-enter")
        try:
            yield
        finally:
            events.append("lock-exit")

    def ingress(_plan: DeploymentPlan) -> object:
        events.append("ingress-active")
        if fail_ingress:
            raise RuntimeDeploymentError("入口门禁缺失")
        return object()

    def build(proof):
        events.append("build-stage-ports")
        return proof

    def prove_stage(
        _plan: DeploymentPlan,
        _release: ReleaseInterpreterIdentity,
    ) -> None:
        events.append("stage-proven")
        if fail_stage:
            raise RuntimeDeploymentError("stage 已漂移")

    return SystemdHandoffPorts(
        transition_lock=lock,
        build_stage_ports=build,
        verify_deployment_guard=lambda _plan: events.append("guard-active"),
        verify_ingress_gate=ingress,
        deactivate_deployment_guard=lambda _plan: events.append("guard-remove"),
        verify_deployment_guard_inactive=lambda _plan: events.append("guard-inactive"),
        verify_staged_payload=prove_stage,
    )


def test_guarded_stage端口把总门禁和入口门禁绑定到事务内证明() -> None:
    from codev_platform.runtime_systemd_handoff import build_guarded_stage_install_ports

    events: list[str] = []
    proof = build_guarded_stage_install_ports(_plan(), ports=_ports(events))

    assert callable(proof)
    proof()
    assert events == ["build-stage-ports", "guard-active", "ingress-active"]


def test_撤销总门禁与前后证明持有同一systemd转换锁() -> None:
    from codev_platform.runtime_systemd_handoff import deactivate_systemd_handoff_guard

    events: list[str] = []

    deactivate_systemd_handoff_guard(
        _plan(),
        _release(),
        ports=_ports(events),
    )

    assert events == [
        "lock-enter",
        "guard-active",
        "ingress-active",
        "stage-proven",
        "guard-remove",
        "guard-inactive",
        "ingress-active",
        "lock-exit",
    ]


def test_入口门禁无法证明时锁内失败且不删除总门禁() -> None:
    from codev_platform.runtime_systemd_handoff import deactivate_systemd_handoff_guard

    events: list[str] = []

    with pytest.raises(RuntimeDeploymentError, match="入口门禁缺失"):
        deactivate_systemd_handoff_guard(
            _plan(),
            _release(),
            ports=_ports(events, fail_ingress=True),
        )

    assert events == ["lock-enter", "guard-active", "ingress-active", "lock-exit"]


def test_两次转换锁之间stage漂移时锁内复证失败且不删除总门禁() -> None:
    from codev_platform.runtime_systemd_handoff import deactivate_systemd_handoff_guard

    events: list[str] = []

    with pytest.raises(RuntimeDeploymentError, match="stage 已漂移"):
        deactivate_systemd_handoff_guard(
            _plan(),
            _release(),
            ports=_ports(events, fail_stage=True),
        )

    assert events == [
        "lock-enter",
        "guard-active",
        "ingress-active",
        "stage-proven",
        "lock-exit",
    ]
