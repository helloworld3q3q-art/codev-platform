"""systemd unit 实际解析结果与 runtime mask 状态的单一真值。"""
from __future__ import annotations

import os
import subprocess
from collections.abc import Callable
from dataclasses import dataclass
from enum import Enum
from pathlib import Path


_SYSTEMD_RUNTIME_UNIT_DIRECTORY = Path("/run/systemd/system")
_SYSTEMD_RUNTIME_UNIT_DIRECTORY_TEXT = "/run/systemd/system"
_SYSTEMCTL_TIMEOUT_SEC = 10.0
_MASKED_UNIT_FILE_STATES = frozenset({"masked", "masked-runtime"})
_EXPECTED_FIELDS = ("LoadState", "UnitFileState", "FragmentPath")


class SystemdUnitResolutionError(RuntimeError):
    """systemd unit 实际解析状态无法安全证明。"""


class RuntimeMaskState(str, Enum):
    """runtime mask 由 systemd 实际加载结果而非文件存在性分类。"""

    EFFECTIVE_RUNTIME_MASK = "effective_runtime_mask"
    UNMASKED = "unmasked"
    PERSISTENT_OR_UNKNOWN_MASK = "persistent_or_unknown_mask"
    INCONSISTENT_RUNTIME_ARTIFACT = "inconsistent_runtime_artifact"


@dataclass(frozen=True, slots=True)
class SystemdUnitResolution:
    """`systemctl show` 返回的三个精确、不可宽松解释的字段。"""

    unit_name: str
    load_state: str
    unit_file_state: str
    fragment_path: str

    def __post_init__(self) -> None:
        if not _valid_unit_name(self.unit_name):
            raise SystemdUnitResolutionError("systemd unit 名称无效")
        values = (self.load_state, self.unit_file_state, self.fragment_path)
        if not all(type(value) is str and "\x00" not in value for value in values):
            raise SystemdUnitResolutionError("systemd unit 解析状态无效")
        if not self.load_state or not self.unit_file_state:
            raise SystemdUnitResolutionError("systemd unit 解析状态无效")
        if self.load_state == "not-found" and self.fragment_path == "":
            return
        if not self.fragment_path or not (
            self.fragment_path.startswith("/") or Path(self.fragment_path).is_absolute()
        ):
            raise SystemdUnitResolutionError("systemd unit 解析路径无效")


CommandRunner = Callable[..., object]


def read_unit_resolution(
    unit_name: str,
    *,
    command_runner: CommandRunner | None = None,
) -> SystemdUnitResolution:
    """严格读取 systemd 已选择的主 unit 路径和 mask 相关状态。"""
    _require_unit_name(unit_name)
    run = _default_command_runner if command_runner is None else command_runner
    if not callable(run):
        raise SystemdUnitResolutionError("systemd unit 解析适配器不可用")
    result = _run_systemctl(
        run,
        (
            "systemctl",
            "show",
            unit_name,
            *(f"--property={field}" for field in _EXPECTED_FIELDS),
        ),
    )
    values = _parse_properties(getattr(result, "stdout", None), _EXPECTED_FIELDS)
    return SystemdUnitResolution(
        unit_name=unit_name,
        load_state=values["LoadState"],
        unit_file_state=values["UnitFileState"],
        fragment_path=values["FragmentPath"],
    )


def read_runtime_mask_target(unit_name: str) -> str | None:
    """读取 `/run` 层 mask 链接目标；普通文件、断链异常均拒绝猜测。"""
    _require_unit_name(unit_name)
    path = runtime_mask_path(unit_name)
    try:
        return os.readlink(path)
    except FileNotFoundError:
        return None
    except OSError as error:
        raise SystemdUnitResolutionError("systemd runtime mask 文件状态无法读取") from error


def runtime_mask_path(unit_name: str) -> Path:
    """返回固定 runtime mask 路径；调用方不得自行拼接另一份真值。"""
    _require_unit_name(unit_name)
    return _SYSTEMD_RUNTIME_UNIT_DIRECTORY / unit_name


def classify_runtime_mask(
    resolution: SystemdUnitResolution,
    *,
    runtime_mask_target: str | None,
) -> RuntimeMaskState:
    """将 systemd 实际解析与 `/run` 残留合并为失败关闭的唯一分类。"""
    if not isinstance(resolution, SystemdUnitResolution):
        raise SystemdUnitResolutionError("systemd unit 解析状态无效")
    if runtime_mask_target is not None and (
        type(runtime_mask_target) is not str or "\x00" in runtime_mask_target
    ):
        raise SystemdUnitResolutionError("systemd runtime mask 文件状态无效")
    expected_path = f"{_SYSTEMD_RUNTIME_UNIT_DIRECTORY_TEXT}/{resolution.unit_name}"
    if (
        resolution.load_state == "masked"
        and resolution.unit_file_state == "masked-runtime"
        and resolution.fragment_path == expected_path
        and runtime_mask_target == "/dev/null"
    ):
        return RuntimeMaskState.EFFECTIVE_RUNTIME_MASK
    if (
        resolution.load_state == "masked"
        or resolution.unit_file_state in _MASKED_UNIT_FILE_STATES
    ):
        return RuntimeMaskState.PERSISTENT_OR_UNKNOWN_MASK
    if runtime_mask_target is not None:
        return RuntimeMaskState.INCONSISTENT_RUNTIME_ARTIFACT
    return RuntimeMaskState.UNMASKED


def _require_unit_name(unit_name: str) -> None:
    if not _valid_unit_name(unit_name):
        raise SystemdUnitResolutionError("systemd unit 名称无效")


def _valid_unit_name(value: object) -> bool:
    return (
        type(value) is str
        and value.endswith((".service", ".timer"))
        and "/" not in value
        and "\\" not in value
        and "\x00" not in value
        and value not in {".service", ".timer"}
    )


def _run_systemctl(run: CommandRunner, command: tuple[str, ...]) -> object:
    try:
        result = run(command, timeout_sec=_SYSTEMCTL_TIMEOUT_SEC)
    except (KeyboardInterrupt, SystemExit, MemoryError):
        raise
    except Exception as error:
        raise SystemdUnitResolutionError("systemd unit 解析命令无法执行") from error
    if getattr(result, "returncode", None) != 0:
        raise SystemdUnitResolutionError("systemd unit 解析命令失败")
    return result


def _parse_properties(text: object, expected: tuple[str, ...]) -> dict[str, str]:
    if type(text) is not str:
        raise SystemdUnitResolutionError("systemd unit 解析状态格式无效")
    expected_names = set(expected)
    values: dict[str, str] = {}
    for line in text.splitlines():
        key, separator, value = line.partition("=")
        if (
            not separator
            or key not in expected_names
            or key in values
            or (key != "FragmentPath" and not value)
            or "\x00" in value
        ):
            raise SystemdUnitResolutionError("systemd unit 解析状态格式无效")
        values[key] = value
    if set(values) != expected_names:
        raise SystemdUnitResolutionError("systemd unit 解析状态不完整")
    return values


def _default_command_runner(command: tuple[str, ...], *, timeout_sec: float) -> object:
    return subprocess.run(
        command,
        stdin=subprocess.DEVNULL,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=timeout_sec,
        check=False,
    )


__all__ = [
    "RuntimeMaskState",
    "SystemdUnitResolution",
    "SystemdUnitResolutionError",
    "classify_runtime_mask",
    "read_runtime_mask_target",
    "read_unit_resolution",
    "runtime_mask_path",
]
