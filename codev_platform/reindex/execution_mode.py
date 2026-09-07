"""reindex worker 执行模式的单一配置边界。"""
from __future__ import annotations


class ExecutionModeError(ValueError):
    """reindex execution_mode 缺失以外的配置不符合严格枚举。"""


def execution_mode(cfg: dict) -> str:
    """isolated 是唯一默认值；legacy 必须由配置显式选择。"""
    if type(cfg) is not dict:
        raise ExecutionModeError("worker 配置根必须是对象")
    section = cfg.get("reindex", {})
    if type(section) is not dict:
        raise ExecutionModeError("reindex 配置必须是对象")
    if "execution_mode" not in section:
        return "isolated"
    mode = section["execution_mode"]
    if type(mode) is not str or mode not in {"isolated", "legacy"}:
        raise ExecutionModeError("reindex.execution_mode 只允许 isolated 或 legacy")
    return mode


__all__ = ["ExecutionModeError", "execution_mode"]
