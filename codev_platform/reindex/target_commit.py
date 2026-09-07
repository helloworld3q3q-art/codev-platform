"""生产者侧精确目标提交解析。

此模块只负责把 Git 仓或 webhook 事件收敛为完整小写非零 OID；队列、项目映射和
重建策略由调用方承担，避免生产者各自实现不同的回退规则。
"""
from __future__ import annotations

import os
import subprocess
from pathlib import Path

from .queue_ports import validate_target_commit

_GIT_TIMEOUT_SEC = 5.0


class TargetCommitError(ValueError):
    """目标提交无法被严格证明时抛出。"""


def require_target_commit(value: object) -> str:
    """只接受完整小写非零 Git OID，不接受别名、短 SHA 或空值。"""
    try:
        return validate_target_commit(value)
    except ValueError:
        raise TargetCommitError("目标提交必须是完整小写非零 Git OID") from None


def resolve_repo_head(repo: Path | str) -> str:
    """解析指定仓当前 HEAD 的完整提交 OID；失败时不做任何回退。"""
    try:
        root = Path(repo).expanduser().resolve(strict=True)
    except (OSError, RuntimeError):
        raise TargetCommitError("目标仓路径不可用") from None
    if not root.is_dir():
        raise TargetCommitError("目标仓路径不可用")
    try:
        completed = subprocess.run(
            ["git", "-C", str(root), "rev-parse", "--verify", "HEAD^{commit}"],
            stdin=subprocess.DEVNULL,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=_GIT_TIMEOUT_SEC,
            env={**os.environ, "GIT_TERMINAL_PROMPT": "0"},
        )
    except (OSError, subprocess.SubprocessError):
        raise TargetCommitError("无法解析目标仓当前提交") from None
    if completed.returncode != 0:
        raise TargetCommitError("无法解析目标仓当前提交")
    lines = completed.stdout.splitlines()
    if len(lines) != 1:
        raise TargetCommitError("目标仓当前提交输出无效")
    return require_target_commit(lines[0])


def resolve_project_head(project_id: str) -> str:
    """解析逻辑项目主仓当前 HEAD，主仓缺失或不唯一时拒绝入队。"""
    from codev_platform.core.repos import project_repo_specs

    try:
        main_repos = [spec.root for spec in project_repo_specs(project_id) if spec.is_main]
    except Exception:  # noqa: BLE001 - 配置读取失败统一收敛为生产者可处理错误
        raise TargetCommitError("无法解析项目主仓") from None
    if len(main_repos) != 1:
        raise TargetCommitError("项目主仓缺失或不唯一")
    return resolve_repo_head(main_repos[0])


__all__ = ["TargetCommitError", "require_target_commit", "resolve_project_head", "resolve_repo_head"]
