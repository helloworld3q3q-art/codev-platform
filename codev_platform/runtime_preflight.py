"""版本化 systemd 运行时预检的轻量编排入口。"""

from __future__ import annotations

import argparse
import importlib
import json
import os
import time
from collections.abc import Callable, Sequence
from pathlib import Path

from codev_platform.runtime_preflight_contract import (
    DEFAULT_PROBE_TIMEOUT_SEC,
    PathRequirement,
    ProbeDeadline,
    RuntimePreflightError,
)
from codev_platform.runtime_preflight_queue import (
    probe_file_queue_operational as _probe_file_queue_operational,
    probe_queue_readonly as _probe_queue_readonly,
)
from codev_platform.runtime_preflight_filesystem import (
    probe_atomic_directory,
    probe_directory_rx,
    probe_directory_traverse,
    probe_directory_write,
    probe_file_rx,
    probe_flock_directory,
    probe_import_sources,
    probe_runtime_identity,
)
from codev_platform.runtime_preflight_paths import permission_requirements
from codev_platform.runtime_release_environment import MANAGED_IMPORTS


def _import_managed_module(module_name: str) -> object:
    """受管入口导入适配点，便于隔离测试且不隐藏生产行为。"""
    return importlib.import_module(module_name)


def probe_current_user(
    requirements: tuple[PathRequirement, ...],
    *,
    timeout_sec: float = DEFAULT_PROBE_TIMEOUT_SEC,
    monotonic: Callable[[], float] = time.monotonic,
) -> None:
    """在共享整体时限内按固定顺序执行探针。"""
    probe_current_user_until(
        requirements,
        ProbeDeadline.after(timeout_sec, monotonic),
    )


def probe_current_user_until(
    requirements: tuple[PathRequirement, ...],
    deadline: ProbeDeadline,
) -> None:
    """复用调用方的绝对截止点执行探针，不把剩余时间重算为新时限。"""
    if type(requirements) is not tuple or not requirements:
        raise TypeError("权限探针要求必须是非空元组")
    if type(deadline) is not ProbeDeadline:
        raise TypeError("权限探针截止点类型无效")
    names: set[str] = set()
    for requirement in requirements:
        if type(requirement) is not PathRequirement or requirement.name in names:
            raise TypeError("权限探针要求类型无效或重复")
        names.add(requirement.name)
        try:
            deadline.ensure()
            _probe_requirement(requirement, deadline)
            deadline.ensure()
        except (KeyboardInterrupt, SystemExit, MemoryError):
            raise
        except Exception:
            raise RuntimePreflightError(requirement.name) from None


def _probe_requirement(requirement: PathRequirement, deadline: ProbeDeadline) -> None:
    path = requirement.path
    if requirement.kind == "runtime_identity":
        probe_runtime_identity(_required_path(path))
    elif requirement.kind == "directory_traverse":
        probe_directory_traverse(_required_path(path))
    elif requirement.kind == "directory_rx":
        probe_directory_rx(_required_path(path))
    elif requirement.kind == "file_rx":
        probe_file_rx(_required_path(path))
    elif requirement.kind == "import_source":
        probe_import_sources(
            _required_path(path),
            requirement.forbidden_root,
            MANAGED_IMPORTS,
            _import_managed_module,
            deadline,
        )
    elif requirement.kind == "directory_rw":
        probe_directory_write(_required_path(path))
    elif requirement.kind == "atomic_directory":
        probe_atomic_directory(_required_path(path))
    elif requirement.kind == "flock_directory":
        probe_flock_directory(_required_path(path))
    elif requirement.kind == "file_queue_operational":
        _probe_file_queue_operational(_required_path(path), deadline)
    elif requirement.kind == "queue_readonly":
        _probe_queue_readonly(requirement.database_dsn, deadline)
    else:
        raise ValueError("权限探针类型未接线")


def _required_path(path: Path | None) -> Path:
    if path is None:
        raise ValueError("权限探针缺少路径")
    return path


def _load_probe_requirements() -> tuple[PathRequirement, ...]:
    from codev_platform.core.config import load_config
    from codev_platform.core.runtime_models import SystemdRuntime
    from codev_platform.runtime_release import release_root

    cfg = load_config()
    declared = os.environ.get("CODEV_PLATFORM_RELEASE_FILE")
    if declared:
        release_file = Path(declared).expanduser()
        if (
            not release_file.is_absolute()
            or release_file.name != "release.json"
            or release_file.parent.name != "current"
        ):
            raise ValueError("版本元数据环境声明无效")
        root = release_file.parent.parent
    else:
        root = release_root(cfg)
    return permission_requirements(cfg, SystemdRuntime(Path(root)))


def _emit(ok: bool, checks: list[dict[str, str]]) -> None:
    payload = {"checks": checks, "ok": ok, "schema_version": 1}
    print(
        json.dumps(
            payload,
            ensure_ascii=True,
            allow_nan=False,
            sort_keys=True,
            separators=(",", ":"),
        ),
        flush=True,
    )


def main(argv: Sequence[str] | None = None) -> int:
    """运行固定 probe 子命令并只输出脱敏结构化结果。"""
    parser = argparse.ArgumentParser(prog="python -m codev_platform.runtime_preflight")
    parser.add_argument("action", choices=("probe",))
    parser.parse_args(argv)
    try:
        requirements = _load_probe_requirements()
    except (KeyboardInterrupt, SystemExit, MemoryError):
        raise
    except Exception:
        _emit(False, [{"name": "preflight_setup", "status": "failed"}])
        return 1
    try:
        probe_current_user(requirements)
    except RuntimePreflightError as error:
        _emit(False, [{"name": error.check_name, "status": "failed"}])
        return 1
    checks = [{"name": item.name, "status": "passed"} for item in requirements]
    _emit(True, checks)
    return 0


if __name__ == "__main__":  # pragma: no cover - 由 python -m 入口执行
    raise SystemExit(main())


__all__ = [
    "PathRequirement",
    "ProbeDeadline",
    "RuntimePreflightError",
    "main",
    "permission_requirements",
    "probe_current_user",
    "probe_current_user_until",
]
