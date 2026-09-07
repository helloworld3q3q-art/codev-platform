"""旧生产部署组合根的显式退役栅栏测试。"""

from __future__ import annotations

import pytest

import codev_platform.runtime_production_deployment as production
from codev_platform.runtime_deployment_contract import (
    DeploymentPlan,
    RuntimeDeploymentError,
)
from tests.runtime_legacy_deployment_fixtures import legacy_plan_audit


def _plan() -> DeploymentPlan:
    return DeploymentPlan(
        schema_version=2,
        target_revision="a" * 40,
        source_repo="/home/helloworld/work/codev-platform",
        runtime_root="/var/lib/codev-platform/runtime",
        requirements_lock="/srv/codev-artifacts/wsl-runtime.lock",
        approved_requirements="/srv/codev-artifacts/wsl-runtime.freeze",
        wheelhouse="/srv/codev-artifacts/wheelhouse",
        candidate_root="/var/lib/codev-platform/runtime/candidates",
        config_source="/home/helloworld/.codev-platform/config.json",
        environment_source="/etc/codev-platform/platform.source.env",
        service_user="helloworld",
        project_id="codev-platform",
        require_cuda=True,
    )


def _legacy_plan():
    return legacy_plan_audit()


def test_schema2在依赖根存储时钟与进度端口前拒绝(monkeypatch) -> None:
    events: list[str] = []
    monkeypatch.setattr(
        production,
        "_default_dependencies",
        lambda: events.append("dependencies"),
    )

    with pytest.raises(RuntimeDeploymentError, match="schema 2.*旧部署"):
        production.run_production_deployment(
            _plan(),
            progress=lambda *_args: events.append("progress"),
        )
    assert events == []


def test_v1审计对象不能伪装为生产计划且依赖零调用(monkeypatch) -> None:
    events: list[str] = []
    monkeypatch.setattr(
        production,
        "_default_dependencies",
        lambda: events.append("dependencies"),
    )

    with pytest.raises(RuntimeDeploymentError, match="生产部署计划无效"):
        production.run_production_deployment(_legacy_plan())  # type: ignore[arg-type]
    assert events == []


def test_schema2退役栅栏先于旧进度端口校验(monkeypatch) -> None:
    events: list[str] = []
    monkeypatch.setattr(
        production,
        "_default_dependencies",
        lambda: events.append("dependencies"),
    )

    with pytest.raises(RuntimeDeploymentError, match="schema 2.*旧部署"):
        production.run_production_deployment(_plan(), progress=object())  # type: ignore[arg-type]
    assert events == []
