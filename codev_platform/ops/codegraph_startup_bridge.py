"""M1 中以冻结运行时启动 CodeGraph 的一次性兼容桥。"""

from __future__ import annotations

import runpy
from pathlib import Path


_CODEGRAPH_UNIT = "codev-mcp-codegraph.service"
_CODEGRAPH_HOLD_PATH = Path("/var/lib/codev-platform/codegraph-maintenance.gate")
_SERVICE_START_PERMITTER = "require_codegraph_service_start_permitted"


def main() -> None:
    """只在 M1 首次服务启动时临时放行，随后执行冻结 server 模块。"""
    from codev_platform.codegraph import maintenance_gate

    _install_m1_initial_start_permit(maintenance_gate)
    runpy.run_module(
        "codev_platform.codegraph.server",
        run_name="__main__",
        alter_sys=True,
    )


def _install_m1_initial_start_permit(gate: object) -> None:
    """marker 活跃时仅替换下一次服务启动校验，避免放宽请求或后端门禁。"""
    marker_active = getattr(gate, "_maintenance_marker_active", None)
    if not callable(marker_active) or marker_active() is not True:
        _raise_gate_error(gate)
    original = getattr(gate, _SERVICE_START_PERMITTER, None)
    if not callable(original):
        _raise_gate_error(gate)

    def allow_once() -> None:
        setattr(gate, _SERVICE_START_PERMITTER, original)
        _require_m1_start_boundary(gate)

    setattr(gate, _SERVICE_START_PERMITTER, allow_once)


def _require_m1_start_boundary(gate: object) -> None:
    """桥仅认可仍受 M1 marker 保护的固定 CodeGraph 服务进程。"""
    marker_active = getattr(gate, "_maintenance_marker_active", None)
    runtime_mask_active = getattr(gate, "_runtime_mask_active", None)
    if (
        not callable(marker_active)
        or marker_active() is not True
        or _codegraph_hold_active()
        or not callable(runtime_mask_active)
        or runtime_mask_active() is not False
        or not _current_process_in_codegraph_cgroup()
    ):
        _raise_gate_error(gate)


def _codegraph_hold_active() -> bool:
    try:
        _CODEGRAPH_HOLD_PATH.lstat()
    except FileNotFoundError:
        return False
    except Exception:
        return True
    return True


def _current_process_in_codegraph_cgroup() -> bool:
    try:
        from codev_platform.reindex.external_worker_guard import (
            current_process_in_systemd_unit_cgroup,
        )

        return current_process_in_systemd_unit_cgroup(_CODEGRAPH_UNIT) is True
    except Exception:
        return False


def _raise_gate_error(gate: object) -> None:
    error_type = getattr(gate, "CodegraphMaintenanceGateError", RuntimeError)
    raise error_type("CodeGraph M1 启动桥边界无法证明")


if __name__ == "__main__":
    main()
