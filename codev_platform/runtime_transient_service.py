"""部署叶子到通用 systemd 受管进程内核的兼容门面。"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
import math
import os
from pathlib import Path, PurePosixPath
import re
import sys

from codev_platform.runtime_deployment_contract import RuntimeDeploymentError
from codev_platform.runtime_managed_process import (
    ManagedProcessResult,
    ManagedProcessSpec,
    run_managed_process,
)
from codev_platform.runtime_managed_profiles import deployment_leaf_limits


_ACTION = re.compile(r"[a-z][a-z0-9-]{0,20}\Z")
_UNIT = re.compile(r"codev-runtime-deploy-[a-z][a-z0-9-]{0,20}\.service\Z")
_USER = re.compile(r"[a-z_][a-z0-9_-]{0,31}\Z")
_ENVIRONMENT_FILE = Path("/etc/codev-platform/platform.env")


@dataclass(frozen=True, slots=True)
class TransientServiceSpec:
    """部署层兼容规格；资源边界由固定 profile 提供。"""

    unit: str
    user: str | None
    executable: Path
    arguments: tuple[str, ...]
    environment_file: Path
    timeout_sec: float

    def validate(self) -> TransientServiceSpec:
        executable = Path(self.executable)
        environment = Path(self.environment_file)
        executable_posix = PurePosixPath(executable.as_posix())
        if type(self.unit) is not str or _UNIT.fullmatch(self.unit) is None:
            raise RuntimeDeploymentError("部署叶子 unit 名称无效")
        if self.user is not None and (
            type(self.user) is not str or self.user == "root" or _USER.fullmatch(self.user) is None
        ):
            raise RuntimeDeploymentError("部署叶子用户无效")
        if (
            not executable_posix.is_absolute()
            or executable_posix.as_posix().startswith("/mnt/")
            or environment.as_posix() != _ENVIRONMENT_FILE.as_posix()
        ):
            raise RuntimeDeploymentError("部署叶子受管路径无效")
        if (
            type(self.arguments) is not tuple
            or not self.arguments
            or any(
                type(item) is not str or not item or "\x00" in item or "\n" in item
                for item in self.arguments
            )
        ):
            raise RuntimeDeploymentError("部署叶子参数无效")
        if (
            type(self.timeout_sec) not in {int, float}
            or not math.isfinite(self.timeout_sec)
            or not 1 <= float(self.timeout_sec) <= 43_200
        ):
            raise RuntimeDeploymentError("部署叶子时限无效")
        return self


@dataclass(frozen=True, slots=True)
class TransientServicePorts:
    """兼容门面只依赖一个通用受管执行端口。"""

    run_managed: Callable[[ManagedProcessSpec], ManagedProcessResult]

    def __post_init__(self) -> None:
        if not callable(self.run_managed):
            raise RuntimeDeploymentError("部署叶子执行端口不可用")


def deployment_transient_unit(target_revision: str, action: str) -> str:
    """验证部署身份并返回跨修订稳定的恢复 slot。"""
    if (
        type(target_revision) is not str
        or re.fullmatch(r"[0-9a-f]{40}", target_revision) is None
        or type(action) is not str
        or _ACTION.fullmatch(action) is None
    ):
        raise RuntimeDeploymentError("部署叶子身份无效")
    return f"codev-runtime-deploy-{action}.service"


def run_transient_service(
    spec: TransientServiceSpec,
    *,
    ports: TransientServicePorts | None = None,
    platform_name: str | None = None,
) -> str:
    """把部署兼容规格映射到唯一受管内核，并固定部署错误边界。"""
    _require_linux_root(platform_name, require_root=ports is None)
    if type(spec) is not TransientServiceSpec:
        raise RuntimeDeploymentError("部署叶子规格无效")
    selected = spec.validate()
    active = TransientServicePorts(run_managed=run_managed_process) if ports is None else ports
    if type(active) is not TransientServicePorts:
        raise RuntimeDeploymentError("部署叶子执行端口不可用")
    managed_spec = ManagedProcessSpec(
        unit_slot=_slot_from_unit(selected.unit),
        argv=(selected.executable.as_posix(), *selected.arguments),
        working_directory="%h",
        environment=(),
        environment_file=selected.environment_file,
        user=selected.user,
        limits=deployment_leaf_limits(float(selected.timeout_sec)),
    )
    failed = False
    result: ManagedProcessResult | None = None
    try:
        result = active.run_managed(managed_spec)
    except (KeyboardInterrupt, SystemExit, MemoryError):
        raise
    except Exception:
        failed = True
    if failed or result is None:
        raise RuntimeDeploymentError("部署叶子无法执行") from None
    if result.returncode != 0 or result.stderr != b"":
        raise RuntimeDeploymentError("部署叶子执行失败")
    try:
        output = result.stdout.decode("utf-8", errors="strict")
    except UnicodeError:
        raise RuntimeDeploymentError("部署叶子输出无效") from None
    if "\x00" in output:
        raise RuntimeDeploymentError("部署叶子输出无效")
    return output


def _slot_from_unit(unit: str) -> str:
    if _UNIT.fullmatch(unit) is None:
        raise RuntimeDeploymentError("部署叶子 unit 名称无效")
    return unit.removeprefix("codev-runtime-").removesuffix(".service")


def _require_linux_root(platform_name: str | None, *, require_root: bool) -> None:
    selected = sys.platform if platform_name is None else platform_name
    if type(selected) is not str or not selected.startswith("linux"):
        raise RuntimeDeploymentError("部署叶子只允许在 Linux 执行")
    if require_root and (not hasattr(os, "geteuid") or os.geteuid() != 0):
        raise RuntimeDeploymentError("部署叶子要求 root")


__all__ = [
    "TransientServicePorts",
    "TransientServiceSpec",
    "deployment_transient_unit",
    "run_transient_service",
]
