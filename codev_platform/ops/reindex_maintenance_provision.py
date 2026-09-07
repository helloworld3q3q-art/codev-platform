"""reindex 维护门禁锁的特权预置职责。"""

from __future__ import annotations

import sys
from collections.abc import Callable

GateProvisioner = Callable[[], None]


def provision_reindex_maintenance(
    *,
    platform_name: str | None = None,
    gate_provisioner: GateProvisioner | None = None,
) -> None:
    """只预置默认维护门禁目录和锁，不写 systemd、不停止服务。"""
    platform = sys.platform if platform_name is None else platform_name
    if type(platform) is not str or not platform.startswith("linux"):
        raise _maintenance_error("当前平台不支持 systemd reindex 维护门禁预置")
    provision = _default_gate_provisioner if gate_provisioner is None else gate_provisioner
    if not callable(provision):
        raise _maintenance_error("维护门禁预置适配器不可用")
    try:
        provision()
    except MemoryError:
        raise
    except Exception as error:
        raise _maintenance_error("无法安全预置 reindex 维护门禁锁") from error


def _default_gate_provisioner() -> None:
    from codev_platform.reindex.maintenance_gate import provision_maintenance_gate

    provision_maintenance_gate()


def _maintenance_error(message: str) -> RuntimeError:
    """运行时延迟取得主维护模块的受控错误类型，避免导入环。"""
    from codev_platform.ops.reindex_maintenance import ReindexMaintenanceError

    return ReindexMaintenanceError(message)


__all__ = ["provision_reindex_maintenance"]
