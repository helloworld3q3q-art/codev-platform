"""退役部署协调器的零副作用兼容入口。"""

from __future__ import annotations

from dataclasses import dataclass
from typing import NoReturn

from codev_platform.runtime_deployment_contract import (
    DeploymentPlan,
    reject_legacy_deployment_pipeline,
)


@dataclass(frozen=True, slots=True)
class DeploymentCoordinator:
    """保留旧构造签名；任何计划均在端口调用前受控拒绝。"""

    store: object
    steps: tuple[object, ...]
    settle_failure: object
    verify_boundary: object
    now: object
    progress: object = lambda _phase, _state: None

    def run(self, plan: DeploymentPlan) -> NoReturn:
        reject_legacy_deployment_pipeline(plan)


__all__ = ["DeploymentCoordinator"]
