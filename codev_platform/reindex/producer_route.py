"""Guard local hook producers from crossing an unsupported queue-owner boundary."""

from __future__ import annotations

import os
from pathlib import Path

from codev_platform.core.config import get
from codev_platform.core.wsl_data_owner import is_wsl_unc_path


class LocalHookRelayRequired(RuntimeError):
    """The local process must not open a file queue owned by another runtime."""


def effective_queue_backend(cfg: dict) -> str:
    """Mirror queue-factory fallback semantics with whitespace normalization."""
    configured = str(get(cfg, "reindex.queue_backend", "file") or "file")
    return "pg" if configured.strip().lower() == "pg" else "file"


def _configured_queue_root(cfg: dict) -> Path:
    configured = os.environ.get("PLATFORM_DATA_DIR") or get(
        cfg,
        "data.platform_data_dir",
    )
    if isinstance(configured, str) and configured:
        return Path(configured) / "reindex_queue"
    from codev_platform.core.paths import data_root

    return data_root(cfg=cfg) / "reindex_queue"


def require_local_hook_queue_route(
    cfg: dict,
    *,
    platform_name: str | None = None,
    queue_root: Path | None = None,
    selected_backend: str | None = None,
) -> None:
    """Reject Windows writes to a WSL-owned file spool.

    The generated Git hook already owns the branch/remote validation and WSL
    relay. Reimplementing that policy in the Python CLI would create a second,
    drifting producer path, so direct Windows hook commands fail closed here.
    """
    backend = selected_backend or effective_queue_backend(cfg)
    runtime = os.name if platform_name is None else platform_name
    root = _configured_queue_root(cfg) if queue_root is None else queue_root
    if runtime != "nt" or backend != "file" or not is_wsl_unc_path(root):
        return
    raise LocalHookRelayRequired(
        "Windows 进程不能直接打开 WSL 所有的 FileSpoolQueue；"
        "hook 生产者请通过仓库已安装的 Git hook/WSL relay，提交索引重试运行 "
        "`git hook run post-commit`。状态和等待请使用 owner-aware HTTP 命令，"
        "不要直接运行 `codev-platform post-commit` 或 `reindex-queue status`。"
    )


__all__ = [
    "LocalHookRelayRequired",
    "effective_queue_backend",
    "require_local_hook_queue_route",
]
