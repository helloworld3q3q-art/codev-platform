"""systemd 版本运行时的纯路径、argv、环境与预装脚本策略。"""

from __future__ import annotations

from collections.abc import Sequence
from pathlib import Path, PurePosixPath

from codev_platform.core.runtime_models import SystemdRuntime


def resolve_systemd_runtime(
    cfg: dict,
    runtime: SystemdRuntime | None,
) -> SystemdRuntime:
    """显式值优先，否则从统一版本根解析器生成运行时。"""
    from codev_platform.runtime_release import release_root

    selected = SystemdRuntime(release_root(cfg)) if runtime is None else runtime
    if not isinstance(selected, SystemdRuntime) or not selected.release_root.is_absolute():
        raise ValueError("受管 systemd 运行时必须使用绝对版本根")
    return selected


def rewrite_python_argv(
    original: Sequence[str | Path],
    runtime: SystemdRuntime,
) -> list[str]:
    """替换解释器并确保单个隔离标志，完整保留原模块与参数。"""
    if not isinstance(runtime, SystemdRuntime) or not runtime.release_root.is_absolute():
        raise ValueError("受管 systemd 运行时必须使用绝对版本根")
    values = [str(item) for item in original]
    if not values or any(not item or "\x00" in item or "\n" in item for item in values):
        raise ValueError("Python 单元 argv 无效")
    tail = values[1:]
    try:
        module_marker = tail.index("-m")
    except ValueError:
        while tail[:1] == ["-I"]:
            tail = tail[1:]
    else:
        tail = [item for item in tail[:module_marker] if item != "-I"] + tail[module_marker:]
    return [str(runtime.python), "-I", *tail]


def runtime_environment(runtime: SystemdRuntime) -> tuple[str, ...]:
    """返回单元与 transient preflight 共用的有序环境覆盖。"""
    release = runtime.release_root / "current"
    return _runtime_environment_values(release, runtime.python)


def posix_runtime_environment(release_root: str) -> tuple[str, ...]:
    """按 manifest 的 POSIX 版本根生成跨主机稳定的瞬时服务环境。"""
    root = PurePosixPath(release_root)
    if not root.is_absolute() or root.as_posix() != release_root:
        raise ValueError("受管 systemd 运行时必须使用 POSIX 绝对版本根")
    release = root / "current"
    return _runtime_environment_values(release, release / "venv/bin/python")


def _runtime_environment_values(release, python) -> tuple[str, ...]:
    return (
        "PYTHONPATH=",
        "PYTHONHOME=",
        "PYTHONNOUSERSITE=1",
        "PYTHONDONTWRITEBYTECODE=1",
        f"CODEV_PLATFORM_RELEASE_FILE={release}/release.json",
        f"PATH={python.parent}:{release}/base/venv/bin:/usr/local/bin:/usr/bin:/bin",
    )


def render_runtime_environment_lines(
    environment_file: str,
    runtime: SystemdRuntime,
    *,
    working_directory: str = "%h",
) -> str:
    """渲染 EnvironmentFile 后的固定覆盖和经过校验的 WorkingDirectory。"""
    directory = _require_working_directory(working_directory)
    return (
        environment_file
        + "".join(f"Environment={item}\n" for item in runtime_environment(runtime))
        + f"WorkingDirectory={directory}\n"
    )


def _require_working_directory(value: object) -> str:
    """兼容渲染可显式使用 %h；生产入口必须传入服务账号的绝对 home。"""
    if type(value) is str and value == "%h":
        return value
    if type(value) is not str or not value or "\x00" in value or "\n" in value:
        raise ValueError("systemd 工作目录无效")
    path = PurePosixPath(value)
    if (
        not path.is_absolute()
        or value.startswith("//")
        or path.as_posix() != value
        or ".." in path.parts
    ):
        raise ValueError("systemd 工作目录无效")
    return value


__all__ = [
    "posix_runtime_environment",
    "render_runtime_environment_lines",
    "resolve_systemd_runtime",
    "rewrite_python_argv",
    "runtime_environment",
]
