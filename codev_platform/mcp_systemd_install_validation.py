"""systemd 安装入口的平台身份与端口完整性门禁。"""

from __future__ import annotations

import os
import sys
from collections.abc import Callable

from codev_platform.mcp_systemd_install_contract import (
    SystemdInstallPorts,
    SystemdInstallTransactionError,
)


_TERMINATION_EXCEPTIONS = (KeyboardInterrupt, SystemExit, MemoryError)


def require_linux_root(
    platform_name: str | None,
    effective_user_id: Callable[[], int] | None,
) -> None:
    platform = sys.platform if platform_name is None else platform_name
    if type(platform) is not str or not platform.startswith("linux"):
        raise SystemdInstallTransactionError("当前平台不支持全量 systemd 安装事务")
    reader = os.geteuid if effective_user_id is None else effective_user_id
    try:
        if not callable(reader) or reader() != 0:
            raise SystemdInstallTransactionError("全量 systemd 安装事务必须由 root 执行")
    except SystemdInstallTransactionError:
        raise
    except _TERMINATION_EXCEPTIONS:
        raise
    except Exception:
        raise SystemdInstallTransactionError("无法确认全量 systemd 安装事务身份") from None


def require_install_ports(ports: object) -> None:
    if not isinstance(ports, SystemdInstallPorts) or not all(
        callable(item)
        for item in (
            ports.provision_maintenance_gate,
            ports.installer_lock,
            ports.runtime_binding_lock,
            ports.verify_runtime_binding,
            ports.verify_target_user_preflight,
            ports.verify_install_boundary,
            ports.read_unit_source,
            ports.snapshot_legacy_release_dropins,
            ports.retire_legacy_release_dropins,
            ports.restore_legacy_release_dropins,
            ports.verify_legacy_release_dropins_retired,
            ports.verify_managed_install_contract,
            ports.verify_effective_unit_payloads,
            ports.snapshot_installed_unit,
            ports.write_installed_unit,
            ports.restore_installed_unit,
            ports.read_unit_state,
            ports.read_unit_process_state,
            ports.restore_unit_file_state,
            ports.restore_unit_activity_state,
            ports.systemctl,
            ports.verify_running_units,
            ports.read_stage_runtime_identity,
            ports.snapshot_stage_receipt,
            ports.write_stage_receipt,
            ports.restore_stage_receipt,
            ports.verify_stage_receipt,
        )
    ):
        raise SystemdInstallTransactionError("全量 systemd 安装事务适配器不可用")


def provision_maintenance_gate(provision: Callable[[], None]) -> None:
    try:
        provision()
    except _TERMINATION_EXCEPTIONS:
        raise
    except SystemdInstallTransactionError:
        raise
    except Exception as error:
        raise SystemdInstallTransactionError("无法安全预置 reindex 维护门禁锁") from error


__all__ = ["provision_maintenance_gate", "require_install_ports", "require_linux_root"]
