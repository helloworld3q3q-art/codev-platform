"""从单一配置快照规划运行时所需的文件与数据库探针。"""

from __future__ import annotations

import os
from collections.abc import Mapping
from pathlib import Path

from codev_platform.core.runtime_models import SystemdRuntime
from codev_platform.runtime_preflight_contract import PathRequirement
from codev_platform.runtime_preflight_queue import queue_requirement


def permission_requirements(
    cfg: dict[str, object],
    runtime: SystemdRuntime,
    *,
    environment: Mapping[str, str] | None = None,
) -> tuple[PathRequirement, ...]:
    """纯函数式规划探针；不创建目录，也不再次加载配置。"""
    if type(cfg) is not dict:
        raise TypeError("权限探针配置必须是字典")
    if type(runtime) is not SystemdRuntime:
        raise TypeError("runtime 必须是 SystemdRuntime")
    root = runtime.release_root.expanduser()
    if not root.is_absolute():
        raise ValueError("运行时根必须是绝对路径")

    current = root / "current"
    base = current / "base"
    data = _configured_data_root(cfg, os.environ if environment is None else environment)
    queue_probe = queue_requirement(cfg, data, os.environ if environment is None else environment)
    run_dir = data / "run"
    chroma = data / "chroma"
    return (
        PathRequirement("runtime_identity_release", "runtime_identity", current),
        PathRequirement("runtime_root_traverse", "directory_traverse", root),
        PathRequirement("runtime_release_read_execute", "directory_rx", current),
        PathRequirement("runtime_python_read_execute", "file_rx", runtime.python),
        PathRequirement("runtime_base_read_execute", "directory_rx", base),
        PathRequirement(
            "runtime_base_python_read_execute",
            "file_rx",
            base / "venv" / "bin" / "python",
        ),
        PathRequirement("application_import_source", "import_source", current, base),
        PathRequirement("data_root_read_execute", "directory_rx", data),
        PathRequirement("logs_directory_rw", "directory_rw", data / "logs"),
        PathRequirement("chroma_directory_rw", "directory_rw", chroma),
        PathRequirement("codegraph_directory_rw", "directory_rw", data / "codegraph_ext"),
        PathRequirement("graph_store_directory_rw", "directory_rw", data / "graph_store"),
        # agent-memory 的向量后端与 Chroma daemon 共用真实 persist 根。
        PathRequirement("agent_memory_vector_directory_rw", "directory_rw", chroma),
        queue_probe,
        PathRequirement("journal_atomic_replace", "atomic_directory", run_dir),
        PathRequirement("run_lock_create_flock", "flock_directory", run_dir),
    )


def _configured_data_root(
    cfg: dict[str, object],
    environment: Mapping[str, str],
) -> Path:
    """复用 core.paths 的唯一数据根优先级，并避免再次加载配置。"""
    from codev_platform.core.paths import data_root

    return data_root(cfg=cfg, environment=environment)


__all__ = ["permission_requirements"]
