"""部署阶段崩溃后恢复到可续跑的规范维护关闭态。"""

from __future__ import annotations

import hashlib
from collections.abc import Callable
from dataclasses import dataclass

from codev_platform.core.runtime_models import require_sha256
from codev_platform.runtime_deployment_contract import (
    DeploymentPlan,
    RuntimeDeploymentError,
)


PlanAction = Callable[[DeploymentPlan], object]
VoidAction = Callable[[], object]


@dataclass(frozen=True, slots=True)
class MaintenanceRecoveryDependencies:
    """只注入门禁与停写叶子，不依赖协调器或 CLI。"""

    activate_deployment_guard: PlanAction
    quiesce_writers: VoidAction
    verify_deployment_guard: PlanAction
    verify_writers: VoidAction
    verify_ingress_marker_absent: PlanAction
    deactivate_ingress_gate: PlanAction

    def validate(self) -> MaintenanceRecoveryDependencies:
        if type(self) is not MaintenanceRecoveryDependencies or not all(
            callable(item)
            for item in (
                self.activate_deployment_guard,
                self.quiesce_writers,
                self.verify_deployment_guard,
                self.verify_writers,
                self.verify_ingress_marker_absent,
                self.deactivate_ingress_gate,
            )
        ):
            raise RuntimeDeploymentError("维护安全态恢复依赖不可用")
        return self


@dataclass(frozen=True, slots=True)
class MaintenanceClosedEvidence:
    """只保存规范维护态的聚合摘要。"""

    evidence_sha256: str

    def __post_init__(self) -> None:
        try:
            require_sha256(self.evidence_sha256, field="maintenance_closed_evidence")
        except ValueError:
            raise RuntimeDeploymentError("维护安全态证据无效") from None


def restore_maintenance_closed(
    plan: DeploymentPlan,
    *,
    dependencies: MaintenanceRecoveryDependencies | None = None,
) -> MaintenanceClosedEvidence:
    """先建立总门禁并停净写入者，再规范化入口门禁并完整复验。"""
    if type(plan) is not DeploymentPlan:
        raise RuntimeDeploymentError("维护安全态恢复计划无效")
    active = default_dependencies() if dependencies is None else dependencies.validate()
    try:
        active.activate_deployment_guard(plan)
        active.quiesce_writers()
        _ensure_ingress_marker_absent(plan, active)
        guard = _evidence_digest(active.verify_deployment_guard(plan))
        active.verify_writers()
        ingress = _evidence_digest(active.verify_ingress_marker_absent(plan))
    except (KeyboardInterrupt, SystemExit, MemoryError):
        raise
    except Exception:
        raise RuntimeDeploymentError("维护安全态恢复失败") from None
    digest = hashlib.sha256(
        f"{plan.digest}\n{guard}\n{ingress}\n".encode("ascii")
    ).hexdigest()
    return MaintenanceClosedEvidence(digest)


def default_dependencies() -> MaintenanceRecoveryDependencies:
    from codev_platform.runtime_deployment_guard import (
        activate_deployment_guard,
        verify_deployment_guard_active,
    )
    from codev_platform.runtime_deployment_quiesce import (
        quiesce_database_writers,
        verify_database_writers_quiesced,
    )
    from codev_platform.runtime_ingress_gate import (
        deactivate_ingress_gate,
        verify_ingress_gate_marker_absent,
    )

    return MaintenanceRecoveryDependencies(
        activate_deployment_guard=activate_deployment_guard,
        quiesce_writers=quiesce_database_writers,
        verify_deployment_guard=verify_deployment_guard_active,
        verify_writers=verify_database_writers_quiesced,
        verify_ingress_marker_absent=verify_ingress_gate_marker_absent,
        deactivate_ingress_gate=deactivate_ingress_gate,
    )


def _ensure_ingress_marker_absent(
    plan: DeploymentPlan,
    dependencies: MaintenanceRecoveryDependencies,
) -> None:
    try:
        dependencies.verify_ingress_marker_absent(plan)
    except RuntimeDeploymentError:
        dependencies.deactivate_ingress_gate(plan)


def _evidence_digest(value: object) -> str:
    try:
        return require_sha256(
            getattr(value, "evidence_sha256", None),
            field="maintenance_closed_evidence",
        )
    except ValueError:
        raise RuntimeDeploymentError("维护安全态证据无效") from None


__all__ = [
    "MaintenanceClosedEvidence",
    "MaintenanceRecoveryDependencies",
    "default_dependencies",
    "restore_maintenance_closed",
]
