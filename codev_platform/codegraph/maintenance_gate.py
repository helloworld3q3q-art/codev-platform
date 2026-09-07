"""CodeGraph 对 reindex 维护窗口的启动门禁。"""
from __future__ import annotations

import sys
from pathlib import Path

from codev_platform.core.systemd_unit_resolution import (
    RuntimeMaskState,
    SystemdUnitResolution,
    classify_runtime_mask,
    read_runtime_mask_target,
    read_unit_resolution,
)


_SYSTEMD_RUNTIME_DIRECTORY = Path("/run/systemd/system")
_CODEGRAPH_SYSTEMD_UNIT = "codev-mcp-codegraph.service"


class CodegraphMaintenanceGateError(RuntimeError):
    """维护标记或运行态无法证明安全时拒绝启动 CodeGraph。"""


def _is_linux() -> bool:
    return sys.platform.startswith("linux")


def _runtime_mask_active() -> bool:
    """以 systemd 实际解析结果识别 hold；残留或读取异常均失败关闭。"""
    if not _systemd_detected():
        return False
    try:
        resolution = read_unit_resolution(_CODEGRAPH_SYSTEMD_UNIT)
        return (
            classify_runtime_mask(
                _require_resolution(resolution),
                runtime_mask_target=read_runtime_mask_target(_CODEGRAPH_SYSTEMD_UNIT),
            )
            is not RuntimeMaskState.UNMASKED
        )
    except MemoryError:
        raise
    except Exception:
        return True


def _maintenance_marker_active() -> bool:
    """服务启动边界只读取 fail-closed marker，不取得会与转换锁冲突的共享许可。"""
    try:
        from codev_platform.reindex.maintenance_gate import maintenance_gate_active

        return maintenance_gate_active()
    except Exception:
        return True


def _require_resolution(value: object) -> SystemdUnitResolution:
    """隔离运行时类型校验，避免门禁把宽松 mock/异常结果误判为未维护。"""
    if not isinstance(value, SystemdUnitResolution):
        raise CodegraphMaintenanceGateError("CodeGraph systemd 解析状态无效")
    return value


def _systemd_detected() -> bool:
    """只在可证明无 systemd 时允许兼容的脱离式启动。"""
    if not _is_linux():
        return False
    try:
        _SYSTEMD_RUNTIME_DIRECTORY.lstat()
    except FileNotFoundError:
        return False
    except MemoryError:
        raise
    except Exception:
        return True
    return True


def codegraph_requires_managed_service() -> bool:
    """Linux systemd 环境下只允许固定 unit，禁止 detached Python 代理。"""
    return _systemd_detected()


def codegraph_backend_start_permitted() -> bool:
    """后端创建前的无锁 marker 快检；异常一律拒绝，不跨 await 持有 flock。"""
    try:
        return not _maintenance_marker_active()
    except Exception:
        return False


def codegraph_start_permitted() -> bool:
    """检查当前是否可启动 CodeGraph；任何异常均按维护中处理。"""
    try:
        return codegraph_backend_start_permitted() and not _runtime_mask_active()
    except Exception:
        return False


def codegraph_request_permitted() -> bool:
    """MCP/HTTP 请求热路径只复核维护 marker，不执行 systemctl 或 flock。"""
    try:
        return not _maintenance_marker_active()
    except Exception:
        return False


def require_codegraph_request_permitted() -> None:
    """请求入口的轻量 fail-closed 门禁；服务启动的 systemd 强校验在启动边界完成。"""
    if not codegraph_request_permitted():
        raise CodegraphMaintenanceGateError("reindex 维护门禁已激活或状态不可证明，拒绝访问 CodeGraph")


def require_codegraph_start_permitted() -> None:
    """无可证明许可时抛出受控错误，供 HTTP 与 MCP 请求入口复用。"""
    if not codegraph_start_permitted():
        raise CodegraphMaintenanceGateError(
            "reindex 维护门禁已激活或状态不可证明，拒绝启动或访问 CodeGraph"
        )


def codegraph_service_start_permitted() -> bool:
    """验证 CodeGraph 服务启动许可及 Linux systemd 下的固定 unit 身份。"""
    if not _systemd_detected():
        return codegraph_start_permitted()
    if _maintenance_marker_active() or _runtime_mask_active():
        return False
    try:
        from codev_platform.reindex.external_worker_guard import (
            current_process_in_systemd_unit_cgroup,
        )

        return current_process_in_systemd_unit_cgroup(_CODEGRAPH_SYSTEMD_UNIT)
    except Exception:
        return False


def require_codegraph_service_start_permitted() -> None:
    """拒绝 systemd 外手工代理进入 CodeGraph HTTP、MCP 或后端启动路径。"""
    if not codegraph_service_start_permitted():
        raise CodegraphMaintenanceGateError(
            "reindex 维护门禁、runtime mask 或 CodeGraph 服务身份无法证明，"
            "拒绝启动或访问 CodeGraph"
        )


__all__ = [
    "CodegraphMaintenanceGateError",
    "codegraph_backend_start_permitted",
    "codegraph_requires_managed_service",
    "codegraph_request_permitted",
    "codegraph_service_start_permitted",
    "codegraph_start_permitted",
    "require_codegraph_request_permitted",
    "require_codegraph_service_start_permitted",
    "require_codegraph_start_permitted",
]
