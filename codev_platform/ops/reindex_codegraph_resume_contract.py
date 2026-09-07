"""CodeGraph 受控恢复跨模块共享的固定运维契约。"""

from __future__ import annotations

from pathlib import Path
from typing import Final


REINDEX_UNIT = "codev-reindex.service"
CODEGRAPH_UNIT = "codev-mcp-codegraph.service"
RESUME_ENVIRONMENT_FILE = Path("/etc/codev-platform/reindex-codegraph-resume.env")
RESUME_DROPIN_NAME = "20-codev-reindex-codegraph-resume.conf"
CODEGRAPH_STARTUP_BRIDGE_DROPIN_NAME = "15-codev-codegraph-startup-bridge.conf"
REINDEX_RESUME_DROPIN = Path("/etc/systemd/system") / f"{REINDEX_UNIT}.d" / RESUME_DROPIN_NAME
CODEGRAPH_RESUME_DROPIN = Path("/etc/systemd/system") / f"{CODEGRAPH_UNIT}.d" / RESUME_DROPIN_NAME
CODEGRAPH_STARTUP_BRIDGE_DROPIN = (
    Path("/etc/systemd/system")
    / f"{CODEGRAPH_UNIT}.d"
    / CODEGRAPH_STARTUP_BRIDGE_DROPIN_NAME
)
PROTECTED_ENVIRONMENT = frozenset(
    {
        "CODEV_PLATFORM_CONFIG",
        "PLATFORM_DATA_DIR",
        "CODEV_REINDEX_CONFIG_SHA256",
    }
)
FORBIDDEN_ENVIRONMENT = "CODEV_REINDEX_REPO_OVERRIDE"
_FAILURE_STAGE_LABELS: Final = {
    "write": "写入",
    "reload": "重载",
    "proof": "同源证明",
}
_PROOF_UNIT_LABELS: Final = {
    REINDEX_UNIT: "reindex",
    CODEGRAPH_UNIT: "CodeGraph",
}
_PROOF_PROPERTY_LABELS: Final = {
    "Environment": "静态环境",
    "EnvironmentFiles": "环境文件",
    "UnsetEnvironment": "取消环境",
    "PassEnvironment": "透传环境",
    "PAMName": "PAM 环境",
}
_ENVIRONMENT_FILES_REASON_LABELS: Final = {
    "format": "环境文件列表格式",
    "managed_reference": "恢复快照引用",
    "managed_snapshot": "恢复快照内容",
    "auxiliary": "辅助环境文件",
}


def codegraph_resume_failure_stage_label(value: object) -> str | None:
    """仅将内部白名单失败阶段转换为可公开显示的中文标签。"""
    if type(value) is not str:
        return None
    return _FAILURE_STAGE_LABELS.get(value)


def codegraph_resume_proof_location_label(unit: object, property_name: object) -> str | None:
    """仅将固定 unit 与固定 systemd 属性转换为可公开的证明位置。"""
    if type(unit) is not str or type(property_name) is not str:
        return None
    unit_label = _PROOF_UNIT_LABELS.get(unit)
    property_label = _PROOF_PROPERTY_LABELS.get(property_name)
    if unit_label is None or property_label is None:
        return None
    return f"{unit_label} {property_label}"


def codegraph_resume_proof_dropin_active_label(value: object) -> str | None:
    """仅将 proof 读取到的固定恢复 drop-in 状态转换为公开标签。"""
    if type(value) is not bool:
        return None
    return "恢复 drop-in 已生效" if value else "恢复 drop-in 未生效"


def codegraph_resume_environment_files_reason_label(value: object) -> str | None:
    """仅将环境文件证明的固定内部原因码转换为公开中文标签。"""
    if type(value) is not str:
        return None
    return _ENVIRONMENT_FILES_REASON_LABELS.get(value)


__all__ = [
    "CODEGRAPH_RESUME_DROPIN",
    "CODEGRAPH_STARTUP_BRIDGE_DROPIN",
    "CODEGRAPH_STARTUP_BRIDGE_DROPIN_NAME",
    "CODEGRAPH_UNIT",
    "codegraph_resume_environment_files_reason_label",
    "codegraph_resume_failure_stage_label",
    "codegraph_resume_proof_dropin_active_label",
    "codegraph_resume_proof_location_label",
    "FORBIDDEN_ENVIRONMENT",
    "PROTECTED_ENVIRONMENT",
    "REINDEX_RESUME_DROPIN",
    "REINDEX_UNIT",
    "RESUME_DROPIN_NAME",
    "RESUME_ENVIRONMENT_FILE",
]
