"""退役部署协调器只保留 schema 边界与零副作用门禁测试。"""

from __future__ import annotations

import pytest

from codev_platform.runtime_deployment_contract import (
    DeploymentPlan,
    RuntimeDeploymentError,
)
from codev_platform.runtime_deployment_coordinator import DeploymentCoordinator
from tests.runtime_legacy_deployment_fixtures import legacy_plan_audit


def _plan() -> DeploymentPlan:
    return DeploymentPlan(
        schema_version=2,
        target_revision="a" * 40,
        source_repo="/home/helloworld/work/codev-platform",
        runtime_root="/var/lib/codev-platform/runtime",
        requirements_lock="/home/helloworld/artifacts/wsl-runtime.lock",
        approved_requirements=(
            "/home/helloworld/work/codev-platform/requirements/wsl-runtime.freeze"
        ),
        wheelhouse="/home/helloworld/artifacts/wheelhouse",
        candidate_root="/var/lib/codev-platform/runtime/candidates",
        config_source="/home/helloworld/.codev-platform/config.json",
        environment_source="/etc/codev-platform/platform.env",
        service_user="helloworld",
        project_id="codev-platform",
        require_cuda=True,
    )


def _legacy_plan():
    return legacy_plan_audit()


def _coordinator(events: list[str]) -> DeploymentCoordinator:
    class Store:
        def lock(self, _plan):
            events.append("lock")
            raise AssertionError("退役协调器不得加锁")

        def load(self, _plan):
            events.append("load")
            raise AssertionError("退役协调器不得读取")

        def save(self, _plan, _receipt):
            events.append("save")
            raise AssertionError("退役协调器不得写入")

    return DeploymentCoordinator(
        store=Store(),  # type: ignore[arg-type]
        steps=(),
        settle_failure=lambda *_args: events.append("settle") is None,
        verify_boundary=lambda *_args: events.append("boundary"),
        now=lambda: events.append("clock") or "2026-07-19T10:00:00Z",
        progress=lambda *_args: events.append("progress"),
    )


@pytest.mark.parametrize(
    ("plan_factory", "message"),
    (
        (_plan, "schema 2.*旧部署"),
        (_legacy_plan, "旧 schema 1.*只允许审计"),
    ),
)
def test_退役协调器拒绝v2与v1审计且全部端口零调用(
    plan_factory,
    message: str,
) -> None:
    events: list[str] = []

    with pytest.raises(RuntimeDeploymentError, match=message):
        _coordinator(events).run(plan_factory())  # type: ignore[arg-type]

    assert events == []
