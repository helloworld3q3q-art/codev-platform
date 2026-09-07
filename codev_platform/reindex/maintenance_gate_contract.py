"""reindex 维护门禁的稳定错误合约。"""

from __future__ import annotations


class MaintenanceGateError(RuntimeError):
    """reindex 维护门禁无法安全变更。"""


class _MaintenanceLockUnavailable(MaintenanceGateError):
    """无法安全取得维护门禁的跨进程锁。"""


class MaintenanceGateLockBusyError(_MaintenanceLockUnavailable):
    """维护门禁锁在有界等待内仍被共享写任务占用。"""


__all__ = ["MaintenanceGateError", "MaintenanceGateLockBusyError"]
