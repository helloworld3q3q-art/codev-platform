"""root 受管部署回执存储：安全读取、排他锁与原子提交。"""

from __future__ import annotations

from contextlib import AbstractContextManager
from pathlib import Path

from codev_platform.runtime_managed_file import (
    RootOwnedRegularFileSnapshot,
    TrustedManagedPathError,
    read_optional_root_owned_regular_file_snapshot,
)
from codev_platform.runtime_deployment_contract import (
    DeploymentPlan,
    DeploymentReceipt,
    LegacyDeploymentPlanAudit,
    LegacyDeploymentReceiptAudit,
    RuntimeDeploymentError,
    decode_legacy_deployment_receipt_audit,
    reject_legacy_deployment_pipeline,
)
from codev_platform.runtime_storage import deployment_lock


_MAX_RECEIPT_BYTES = 32 * 1024
_RECEIPT_MODE = 0o600


class RuntimeDeploymentReceiptStore:
    """一个目标提交一个回执；同一运行时根共用一把顶层部署锁。"""

    def __init__(self, root: Path) -> None:
        self._root = Path(root)

    def lock(self, plan: DeploymentPlan) -> AbstractContextManager[None]:
        self._require_plan_root(plan)
        return deployment_lock(self._root)

    def load(self, plan: DeploymentPlan) -> DeploymentReceipt | None:
        reject_legacy_deployment_pipeline(plan)

    def load_legacy_audit(
        self,
        plan: LegacyDeploymentPlanAudit,
    ) -> LegacyDeploymentReceiptAudit | None:
        """只读加载受信任的 schema 1 回执，并核对其完整计划身份。"""
        self._require_legacy_plan_root(plan)
        path = self._legacy_audit_path(plan)
        snapshot = _read_receipt_snapshot(path)
        if snapshot is None:
            return None
        payload = _require_receipt_snapshot(snapshot).content
        receipt = decode_legacy_deployment_receipt_audit(payload)
        if (
            receipt.plan_sha256 != plan.digest
            or receipt.target_revision != plan.target_revision
            or receipt.baseline_revision != plan.baseline_revision
        ):
            raise RuntimeDeploymentError("审计回执与计划身份不一致")
        return receipt

    def save(self, plan: DeploymentPlan, receipt: DeploymentReceipt) -> None:
        del receipt
        reject_legacy_deployment_pipeline(plan)

    def _require_plan_root(self, plan: DeploymentPlan) -> None:
        reject_legacy_deployment_pipeline(plan)

    def _legacy_audit_path(self, plan: LegacyDeploymentPlanAudit) -> Path:
        self._require_legacy_plan_root(plan)
        return self._root / "deployments" / f"{plan.target_revision}.json"

    def _require_legacy_plan_root(self, plan: LegacyDeploymentPlanAudit) -> None:
        if type(plan) is not LegacyDeploymentPlanAudit:
            raise RuntimeDeploymentError("旧部署计划审计类型无效")
        if Path(plan.runtime_root) != self._root:
            raise RuntimeDeploymentError("旧部署计划审计运行时根不匹配")


def _require_receipt_snapshot(snapshot: object) -> RootOwnedRegularFileSnapshot:
    if (
        type(snapshot) is not RootOwnedRegularFileSnapshot
        or snapshot.mode != _RECEIPT_MODE
        or snapshot.uid != 0
        or snapshot.gid != 0
    ):
        raise RuntimeDeploymentError("部署回执元数据不受信任")
    return snapshot


def _read_receipt_snapshot(path: Path) -> RootOwnedRegularFileSnapshot | None:
    try:
        return read_optional_root_owned_regular_file_snapshot(
            path,
            max_bytes=_MAX_RECEIPT_BYTES,
        )
    except TrustedManagedPathError:
        raise RuntimeDeploymentError("部署回执无法安全读取") from None


__all__ = ["RuntimeDeploymentReceiptStore"]
