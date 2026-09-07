"""部署门禁交接崩溃后的规范维护态恢复测试。"""

from __future__ import annotations

from dataclasses import replace
from types import SimpleNamespace

import pytest

from codev_platform.runtime_deployment_contract import (
    DeploymentPlan,
    RuntimeDeploymentError,
)


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


def _dependencies(events: list[str], *, ingress_active: bool = False):
    from codev_platform.runtime_deployment_recovery import (
        MaintenanceRecoveryDependencies,
    )

    first_ingress_probe = True

    def evidence(name: str):
        def run(_plan: DeploymentPlan):
            events.append(name)
            return SimpleNamespace(evidence_sha256="1" * 64)

        return run

    def verify_ingress(_plan: DeploymentPlan):
        nonlocal first_ingress_probe
        events.append("ingress:verify-absent")
        if ingress_active and first_ingress_probe:
            first_ingress_probe = False
            raise RuntimeDeploymentError("入口门禁仍处于激活状态")
        return SimpleNamespace(evidence_sha256="2" * 64)

    return MaintenanceRecoveryDependencies(
        activate_deployment_guard=evidence("deployment:activate"),
        quiesce_writers=lambda: events.append("writers:quiesce"),
        verify_deployment_guard=evidence("deployment:verify"),
        verify_writers=lambda: events.append("writers:verify"),
        verify_ingress_marker_absent=verify_ingress,
        deactivate_ingress_gate=evidence("ingress:deactivate"),
    )


def test_进入维护态复用幂等恢复服务并完整复验() -> None:
    from codev_platform.runtime_deployment_recovery import restore_maintenance_closed

    events: list[str] = []

    evidence = restore_maintenance_closed(
        _plan(),
        dependencies=_dependencies(events),
    )

    assert events == [
        "deployment:activate",
        "writers:quiesce",
        "ingress:verify-absent",
        "deployment:verify",
        "writers:verify",
        "ingress:verify-absent",
    ]
    assert len(evidence.evidence_sha256) == 64


def test_systemd门禁交接后崩溃先恢复总门禁再撤销入口门禁() -> None:
    from codev_platform.runtime_deployment_recovery import restore_maintenance_closed

    events: list[str] = []

    restore_maintenance_closed(
        _plan(),
        dependencies=_dependencies(events, ingress_active=True),
    )

    assert events == [
        "deployment:activate",
        "writers:quiesce",
        "ingress:verify-absent",
        "ingress:deactivate",
        "deployment:verify",
        "writers:verify",
        "ingress:verify-absent",
    ]


def test_未知入口门禁无法安全撤销时拒绝宣称恢复成功() -> None:
    from codev_platform.runtime_deployment_recovery import restore_maintenance_closed

    dependencies = _dependencies([], ingress_active=True)
    broken = replace(
        dependencies,
        deactivate_ingress_gate=lambda _plan: (_ for _ in ()).throw(
            RuntimeDeploymentError("token=秘密")
        ),
    )

    with pytest.raises(RuntimeDeploymentError, match="维护安全态恢复失败") as captured:
        restore_maintenance_closed(_plan(), dependencies=broken)

    assert "token" not in str(captured.value)
