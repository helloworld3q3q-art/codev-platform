"""旧生产部署组合根的零副作用退役栅栏。"""

from __future__ import annotations

from typing import NoReturn

from codev_platform.runtime_deployment_contract import (
    DeploymentPlan,
    RuntimeDeploymentError,
    reject_legacy_deployment_pipeline,
)


def run_production_deployment(
    plan: DeploymentPlan,
    *,
    progress: object | None = None,
) -> NoReturn:
    """在依赖、I/O、锁、clock 和 progress 端口前拒绝退役管线。"""
    del progress
    if type(plan) is not DeploymentPlan:
        raise RuntimeDeploymentError("生产部署计划无效")
    reject_legacy_deployment_pipeline(plan)


def _default_dependencies() -> NoReturn:
    """兼容旧测试探针；退役入口绝不能调用此函数。"""
    raise RuntimeDeploymentError("旧生产部署依赖已退役")


__all__ = ["run_production_deployment"]
