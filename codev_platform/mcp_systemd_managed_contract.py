"""特权安装入口对固定受管 unit 集合的二次证明。"""

from __future__ import annotations

from pathlib import Path

from codev_platform.core.systemd_environment_file import (
    SystemdEnvironmentFileError,
    validate_systemd_environment_file,
)
from codev_platform.mcp_systemd_install_contract import (
    SystemdInstallManifest,
    SystemdInstallTransactionError,
    SystemdRuntimeBinding,
    SystemdUnitPayload,
)
from codev_platform.mcp_systemd_unit_registry import (
    MANAGED_SYSTEMD_UNIT_NAMES,
    MANAGED_SYSTEMD_UNITS,
)


def verify_managed_install_contract(
    manifest: SystemdInstallManifest,
    payloads: tuple[SystemdUnitPayload, ...],
) -> None:
    """拒绝缺项、多项或生命周期漂移，且不信任生成端已做过同类检查。"""
    binding = manifest.runtime_binding
    if type(binding) is not SystemdRuntimeBinding:
        raise SystemdInstallTransactionError("systemd 安装清单缺少 release 运行时绑定")
    names = tuple(unit.unit_name for unit in manifest.units)
    if frozenset(names) != MANAGED_SYSTEMD_UNIT_NAMES or len(names) != len(
        MANAGED_SYSTEMD_UNIT_NAMES
    ):
        raise SystemdInstallTransactionError("systemd 安装清单不等于固定受管 unit 集合")
    if tuple(payload.spec for payload in payloads) != manifest.units:
        raise SystemdInstallTransactionError("systemd 安装载荷与固定清单不一致")
    for unit in manifest.units:
        expected = MANAGED_SYSTEMD_UNITS[unit.unit_name]
        if (
            unit.enable is not expected.enable
            or unit.restart is not expected.restart
            or unit.activation_mode.value != expected.activation_mode
        ):
            raise SystemdInstallTransactionError(
                f"受管 systemd unit 生命周期声明漂移：{unit.unit_name}"
            )
    if binding.environment_file is not None:
        try:
            validate_systemd_environment_file(Path(binding.environment_file))
        except SystemdEnvironmentFileError:
            raise SystemdInstallTransactionError("systemd 环境文件不受信任") from None


__all__ = ["verify_managed_install_contract"]
