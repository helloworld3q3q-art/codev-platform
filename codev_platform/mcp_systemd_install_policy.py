"""systemd 安装模式的显式生命周期策略。"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum

from codev_platform.mcp_systemd_install_contract import (
    SystemdInstallManifest,
    SystemdInstallTransactionError,
    SystemdUnitActivationMode,
)
from codev_platform.mcp_systemd_unit_registry import (
    CODEGRAPH_SYSTEMD_UNIT_NAME as CODEGRAPH_UNIT_NAME,
    MAINTENANCE_DEFERRED_UNITS,
    REINDEX_SYSTEMD_UNIT_NAME as REINDEX_UNIT_NAME,
    managed_systemd_unit,
)


class SystemdInstallMode(str, Enum):
    """安装事务允许执行的生命周期模式。"""

    NORMAL = "normal"
    MAINTENANCE_STAGE = "maintenance-stage"
    GUARDED_STAGE = "guarded-stage"
    INSTALL_ONLY = "install-only"


@dataclass(frozen=True, slots=True)
class SystemdInstallPolicy:
    """把载荷写入与服务激活策略从事务编排中分离。"""

    mode: SystemdInstallMode
    deferred_units: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        if type(self) is not SystemdInstallPolicy or type(self.mode) is not SystemdInstallMode:
            raise SystemdInstallTransactionError("systemd 安装策略无效")
        try:
            deferred_units = tuple(self.deferred_units)
        except TypeError:
            raise SystemdInstallTransactionError("systemd 安装延迟激活策略无效") from None
        object.__setattr__(self, "deferred_units", deferred_units)
        self._require_invariants()

    def _require_invariants(self) -> None:
        """每次消费前重验精确类型与模式对应的完整延迟集合。"""
        mode = getattr(self, "mode", None)
        if type(self) is not SystemdInstallPolicy or type(mode) is not SystemdInstallMode:
            raise SystemdInstallTransactionError("systemd 安装策略无效")
        deferred_units = getattr(self, "deferred_units", None)
        expected = (
            MAINTENANCE_DEFERRED_UNITS
            if mode in {SystemdInstallMode.MAINTENANCE_STAGE, SystemdInstallMode.GUARDED_STAGE}
            else ()
        )
        if (
            type(deferred_units) is not tuple
            or not all(type(name) is str for name in deferred_units)
            or deferred_units != expected
        ):
            raise SystemdInstallTransactionError("systemd 安装延迟激活策略无效")

    def require_manifest(self, manifest: SystemdInstallManifest) -> None:
        """维护态必须覆盖完整延迟集合，禁止形成半维护发布。"""
        self._require_invariants()
        names = {unit.unit_name for unit in manifest.units}
        if self.mode is SystemdInstallMode.INSTALL_ONLY:
            return
        if self.mode is SystemdInstallMode.NORMAL:
            if names & set(MAINTENANCE_DEFERRED_UNITS):
                raise SystemdInstallTransactionError(
                    "包含延迟激活 unit 的 systemd 清单必须使用 maintenance-stage"
                )
            return
        if not set(self.deferred_units).issubset(names):
            raise SystemdInstallTransactionError("维护态 systemd 安装清单缺少受保护 unit")
        by_name = {unit.unit_name: unit for unit in manifest.units}
        for name in self.deferred_units:
            unit = by_name[name]
            expected_mode = SystemdUnitActivationMode(managed_systemd_unit(name).activation_mode)
            if not unit.enable or not unit.restart or unit.activation_mode is not expected_mode:
                raise SystemdInstallTransactionError("维护态 systemd 受保护 unit 生命周期声明无效")

    def enable_names(self, names: tuple[str, ...]) -> tuple[str, ...]:
        """返回本事务允许改变启用状态的 unit。"""
        self._require_invariants()
        if self.mode in {SystemdInstallMode.INSTALL_ONLY, SystemdInstallMode.GUARDED_STAGE}:
            return names
        deferred = frozenset(self.deferred_units)
        return tuple(name for name in names if name not in deferred)

    def restart_names(self, names: tuple[str, ...]) -> tuple[str, ...]:
        """返回本事务允许改变活动状态的 unit。"""
        self._require_invariants()
        if self.mode in {SystemdInstallMode.INSTALL_ONLY, SystemdInstallMode.GUARDED_STAGE}:
            return ()
        deferred = frozenset(self.deferred_units)
        return tuple(name for name in names if name not in deferred)

    def state_names(self, manifest: SystemdInstallManifest) -> tuple[str, ...]:
        """返回首次写入前必须冻结启用状态的 unit。"""
        self._require_invariants()
        names = {
            *self.enable_names(manifest.enable_units),
            *self.restart_names(manifest.restart_units),
        }
        return tuple(unit.unit_name for unit in manifest.units if unit.unit_name in names)

    def report_deferred_names(self, manifest: SystemdInstallManifest) -> tuple[str, ...]:
        """返回本事务明确未执行 restart 的目标。"""
        self._require_invariants()
        if self.mode is SystemdInstallMode.INSTALL_ONLY:
            return manifest.restart_units
        if self.mode is SystemdInstallMode.GUARDED_STAGE:
            return manifest.restart_units
        return self.deferred_units

    @property
    def restores_activity(self) -> bool:
        """安装-only 补偿严禁启动或停止原有服务。"""
        self._require_invariants()
        return self.mode not in {
            SystemdInstallMode.INSTALL_ONLY,
            SystemdInstallMode.GUARDED_STAGE,
        }

    @property
    def writes_stage_receipt(self) -> bool:
        self._require_invariants()
        return self.mode in {
            SystemdInstallMode.MAINTENANCE_STAGE,
            SystemdInstallMode.GUARDED_STAGE,
        }

    @property
    def proves_process_stability(self) -> bool:
        self._require_invariants()
        return self.mode in {
            SystemdInstallMode.INSTALL_ONLY,
            SystemdInstallMode.GUARDED_STAGE,
        }


NORMAL_INSTALL_POLICY = SystemdInstallPolicy(SystemdInstallMode.NORMAL)
MAINTENANCE_STAGE_INSTALL_POLICY = SystemdInstallPolicy(
    SystemdInstallMode.MAINTENANCE_STAGE,
    MAINTENANCE_DEFERRED_UNITS,
)
GUARDED_STAGE_INSTALL_POLICY = SystemdInstallPolicy(
    SystemdInstallMode.GUARDED_STAGE,
    MAINTENANCE_DEFERRED_UNITS,
)
INSTALL_ONLY_POLICY = SystemdInstallPolicy(SystemdInstallMode.INSTALL_ONLY)


__all__ = [
    "CODEGRAPH_UNIT_NAME",
    "GUARDED_STAGE_INSTALL_POLICY",
    "MAINTENANCE_DEFERRED_UNITS",
    "MAINTENANCE_STAGE_INSTALL_POLICY",
    "INSTALL_ONLY_POLICY",
    "NORMAL_INSTALL_POLICY",
    "REINDEX_UNIT_NAME",
    "SystemdInstallMode",
    "SystemdInstallPolicy",
]
