"""systemd 主 unit 落盘内容与有效解析结果的闭环证明。"""

from __future__ import annotations

import shlex
import unicodedata
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Protocol

from codev_platform.core.systemd_maintenance_contract import (
    CODEGRAPH_MAINTENANCE_CONDITION_DIRECTIVE,
)
from codev_platform.runtime_systemd_gate_contract import (
    DEPLOYMENT_GUARD_DROP_IN_CONTENT,
    deployment_guard_drop_in_path,
)
from codev_platform.mcp_systemd_install_contract import (
    SystemdInstallTransactionError,
    SystemdUnitFileSnapshot,
    SystemdUnitPayload,
)
from codev_platform.mcp_systemd_unit_registry import (
    DEPLOYMENT_GUARDED_SYSTEMD_UNITS,
    managed_systemd_unit,
)


_LEGACY_RELEASE_DROP_IN_FILE = "90-codev-release.conf"
_CODEGRAPH_UNIT = "codev-mcp-codegraph.service"


class SystemctlShowResult(Protocol):
    """有效 unit 证明所需的最小命令结果。"""

    returncode: int
    stdout: str


SnapshotReader = Callable[[str], SystemdUnitFileSnapshot | None]
DropInSnapshotReader = Callable[[PurePosixPath], SystemdUnitFileSnapshot | None]
SystemctlShowRunner = Callable[[tuple[str, ...]], SystemctlShowResult]
InstalledUnitPathResolver = Callable[[str], Path]


@dataclass(frozen=True, slots=True)
class ExpectedSystemdDropIn:
    """有效 drop-in 的固定路径与完整 root 原像。"""

    path: PurePosixPath
    snapshot: SystemdUnitFileSnapshot

    def __post_init__(self) -> None:
        path = PurePosixPath(self.path)
        text = path.as_posix()
        if (
            not path.is_absolute()
            or text.startswith("//")
            or ".." in path.parts
            or any(char.isspace() or char == "\x00" for char in text)
        ):
            raise SystemdInstallTransactionError("systemd 期望 drop-in 路径无效")
        if not isinstance(self.snapshot, SystemdUnitFileSnapshot):
            raise SystemdInstallTransactionError("systemd 期望 drop-in 原像无效")
        object.__setattr__(self, "path", path)


def verify_effective_unit_payloads(
    payloads: tuple[SystemdUnitPayload, ...],
    *,
    snapshot_reader: SnapshotReader,
    systemctl_show: SystemctlShowRunner,
    installed_path: InstalledUnitPathResolver,
    expected_drop_ins: Mapping[str, tuple[ExpectedSystemdDropIn, ...]] | None = None,
    expected_effective_exec_starts: Mapping[str, tuple[str, ...]] | None = None,
    drop_in_snapshot_reader: DropInSnapshotReader | None = None,
) -> None:
    """reload 后证明目标文件与 systemd 有效解析仍精确绑定载荷。"""
    if type(payloads) is not tuple or not all(
        isinstance(payload, SystemdUnitPayload) for payload in payloads
    ):
        raise SystemdInstallTransactionError("systemd 有效 unit 载荷无效")
    expectations = _require_expected_drop_ins(expected_drop_ins)
    exec_start_overrides = _require_expected_effective_exec_starts(
        expected_effective_exec_starts,
        payloads,
    )
    if any(expectation for expectation in expectations.values()) and not callable(
        drop_in_snapshot_reader
    ):
        raise SystemdInstallTransactionError("systemd drop-in 原像读取器不可用")
    for payload in payloads:
        _verify_installed_unit_payload(
            payload,
            snapshot_reader=snapshot_reader,
            systemctl_show=systemctl_show,
            installed_path=installed_path,
            expected_drop_ins=expectations.get(payload.spec.unit_name),
            expected_effective_exec_start=exec_start_overrides.get(payload.spec.unit_name),
            drop_in_snapshot_reader=drop_in_snapshot_reader,
        )


def _verify_installed_unit_payload(
    payload: SystemdUnitPayload,
    *,
    snapshot_reader: SnapshotReader,
    systemctl_show: SystemctlShowRunner,
    installed_path: InstalledUnitPathResolver,
    expected_drop_ins: tuple[ExpectedSystemdDropIn, ...] | None,
    expected_effective_exec_start: tuple[str, ...] | None,
    drop_in_snapshot_reader: DropInSnapshotReader | None,
) -> None:
    name = payload.spec.unit_name
    runtime_bound = _runtime_bound_unit(name)
    expected_snapshot = SystemdUnitFileSnapshot(
        content=payload.content,
        mode=0o644,
        uid=0,
        gid=0,
    )
    if snapshot_reader(name) != expected_snapshot:
        raise SystemdInstallTransactionError("systemd 主 unit 目标载荷未证明")
    expected_exec_start = (
        expected_effective_exec_start
        if expected_effective_exec_start is not None
        else parse_unit_exec_start(payload)
        if runtime_bound
        else None
    )
    properties = _read_effective_unit_properties(
        name,
        require_exec_start=expected_exec_start is not None,
        systemctl_show=systemctl_show,
    )
    if properties["FragmentPath"] != installed_path(name).as_posix():
        raise SystemdInstallTransactionError("systemd 有效 FragmentPath 未证明")
    drop_in_paths = _verify_effective_drop_in_paths(properties["DropInPaths"])
    required_drop_ins = expected_drop_ins
    if required_drop_ins is None and not runtime_bound:
        required_drop_ins = _non_runtime_expected_drop_ins(name)
    if required_drop_ins is not None:
        _verify_expected_drop_ins(
            drop_in_paths,
            required_drop_ins,
            drop_in_snapshot_reader,
        )
    if name == _CODEGRAPH_UNIT:
        verify_codegraph_maintenance_condition(payload)
    if expected_exec_start is not None:
        actual_exec_start = _parse_effective_exec_start(properties["ExecStart"])
        if actual_exec_start != expected_exec_start:
            raise SystemdInstallTransactionError("systemd 有效 ExecStart 未证明")


def _runtime_bound_unit(name: str) -> bool:
    try:
        return managed_systemd_unit(name).runtime_bound
    except Exception:
        raise SystemdInstallTransactionError("systemd unit 注册信息无效") from None


def _non_runtime_expected_drop_ins(name: str) -> tuple[ExpectedSystemdDropIn, ...]:
    """按注册表的部署守卫成员关系证明非运行时 unit 的唯一 drop-in。"""
    if name not in DEPLOYMENT_GUARDED_SYSTEMD_UNITS:
        return ()
    return (
        ExpectedSystemdDropIn(
            deployment_guard_drop_in_path(name),
            SystemdUnitFileSnapshot(
                content=DEPLOYMENT_GUARD_DROP_IN_CONTENT,
                mode=0o644,
                uid=0,
                gid=0,
            ),
        ),
    )


def parse_unit_exec_start(payload: SystemdUnitPayload) -> tuple[str, ...] | None:
    """解析单个 unit 的唯一 ExecStart，供安装与发布身份证明共用。"""
    directives = _parse_unit_directives(payload.content)
    commands = [
        value for section, key, value in directives if section == "Service" and key == "ExecStart"
    ]
    if not commands:
        if payload.spec.unit_name.endswith(".timer"):
            return None
        raise SystemdInstallTransactionError("systemd unit ExecStart 载荷无效")
    if len(commands) != 1 or not commands[0]:
        raise SystemdInstallTransactionError("systemd unit ExecStart 载荷无效")
    return _parse_exec_argv(commands[0], "systemd unit ExecStart 载荷无效")


def verify_codegraph_maintenance_condition(payload: SystemdUnitPayload) -> None:
    """要求 CodeGraph canonical unit 只声明唯一精确的耐久启动条件。"""
    if not isinstance(payload, SystemdUnitPayload) or payload.spec.unit_name != _CODEGRAPH_UNIT:
        raise SystemdInstallTransactionError("CodeGraph 耐久维护条件载荷无效")
    directives = _parse_unit_directives(payload.content)
    conditions = tuple(
        (section, key, value)
        for section, key, value in directives
        if key.startswith(("Condition", "Assert"))
    )
    expected_key, _, expected_value = CODEGRAPH_MAINTENANCE_CONDITION_DIRECTIVE.partition("=")
    if conditions != (("Unit", expected_key, expected_value),):
        raise SystemdInstallTransactionError("CodeGraph 耐久维护条件未精确绑定")


def _parse_unit_directives(content: bytes) -> tuple[tuple[str, str, str], ...]:
    try:
        text = content.decode("utf-8")
    except UnicodeDecodeError as error:
        raise SystemdInstallTransactionError("systemd unit 指令载荷无效") from error
    if "\\" in text or any(_is_unsafe_unit_character(char) for char in text):
        raise SystemdInstallTransactionError("systemd unit 指令载荷无效")
    lines = text.split("\n")
    section = ""
    directives: list[tuple[str, str, str]] = []
    for line in lines:
        stripped = line.strip(" \t")
        if not stripped or stripped.startswith(("#", ";")):
            continue
        if stripped.startswith("[") and stripped.endswith("]"):
            section = stripped[1:-1].strip(" \t")
            continue
        key, separator, value = stripped.partition("=")
        if separator:
            directives.append((section, key.strip(" \t"), value.strip(" \t")))
    return tuple(directives)


def _is_unsafe_unit_character(char: str) -> bool:
    """空白只允许 ASCII 空格、LF、TAB，并拒绝隐藏格式字符。"""
    if char in {" ", "\n", "\t"}:
        return False
    if char.isspace():
        return True
    return unicodedata.category(char) in {
        "Cc",
        "Cf",
        "Cs",
        "Co",
        "Cn",
        "Zl",
        "Zp",
    }


def _read_effective_unit_properties(
    name: str,
    *,
    require_exec_start: bool,
    systemctl_show: SystemctlShowRunner,
) -> dict[str, str]:
    properties = (
        ("FragmentPath", "DropInPaths", "ExecStart")
        if require_exec_start
        else ("FragmentPath", "DropInPaths")
    )
    result = systemctl_show(
        (
            "systemctl",
            "show",
            name,
            *(f"--property={property_name}" for property_name in properties),
        )
    )
    if result.returncode != 0:
        raise SystemdInstallTransactionError("systemd 有效 unit 属性无法读取")
    return _parse_effective_unit_properties(result.stdout, properties)


def _parse_effective_unit_properties(
    text: object,
    expected: tuple[str, ...],
) -> dict[str, str]:
    if type(text) is not str:
        raise SystemdInstallTransactionError("systemd 有效 unit 属性输出无效")
    values: dict[str, str] = {}
    expected_names = set(expected)
    for line in text.splitlines():
        key, separator, value = line.partition("=")
        if not separator or key not in expected_names or key in values:
            raise SystemdInstallTransactionError("systemd 有效 unit 属性输出无效")
        values[key] = value
    if set(values) != expected_names:
        raise SystemdInstallTransactionError("systemd 有效 unit 属性不完整")
    return values


def _verify_effective_drop_in_paths(value: str) -> tuple[PurePosixPath, ...]:
    paths: list[PurePosixPath] = []
    for item in value.split():
        if "\x00" in item:
            raise SystemdInstallTransactionError("systemd 有效 drop-in 路径无效")
        path = PurePosixPath(item)
        if not path.is_absolute() or str(path) != item or ".." in path.parts:
            raise SystemdInstallTransactionError("systemd 有效 drop-in 路径无效")
        if path.name == _LEGACY_RELEASE_DROP_IN_FILE:
            raise SystemdInstallTransactionError("旧 release shadow 仍存在有效 drop-in")
        paths.append(path)
    return tuple(paths)


def _require_expected_drop_ins(
    value: Mapping[str, tuple[ExpectedSystemdDropIn, ...]] | None,
) -> dict[str, tuple[ExpectedSystemdDropIn, ...]]:
    if value is None:
        return {}
    if not isinstance(value, Mapping):
        raise SystemdInstallTransactionError("systemd 期望 drop-in 契约无效")
    normalized: dict[str, tuple[ExpectedSystemdDropIn, ...]] = {}
    for name, items in value.items():
        if (
            type(name) is not str
            or type(items) is not tuple
            or not all(isinstance(item, ExpectedSystemdDropIn) for item in items)
        ):
            raise SystemdInstallTransactionError("systemd 期望 drop-in 契约无效")
        paths = tuple(item.path for item in items)
        if len(paths) != len(set(paths)):
            raise SystemdInstallTransactionError("systemd 期望 drop-in 路径重复")
        normalized[name] = items
    return normalized


def _require_expected_effective_exec_starts(
    value: Mapping[str, tuple[str, ...]] | None,
    payloads: tuple[SystemdUnitPayload, ...],
) -> dict[str, tuple[str, ...]]:
    if value is None:
        return {}
    if not isinstance(value, Mapping):
        raise SystemdInstallTransactionError("systemd 有效 ExecStart 覆盖契约无效")
    payload_names = {payload.spec.unit_name for payload in payloads}
    normalized: dict[str, tuple[str, ...]] = {}
    for name, argv in value.items():
        if (
            type(name) is not str
            or name not in payload_names
            or not _runtime_bound_unit(name)
            or type(argv) is not tuple
            or not argv
            or not all(
                type(item) is str and item and "\x00" not in item and "\n" not in item
                for item in argv
            )
        ):
            raise SystemdInstallTransactionError("systemd 有效 ExecStart 覆盖契约无效")
        normalized[name] = argv
    return normalized


def _verify_expected_drop_ins(
    actual_paths: tuple[PurePosixPath, ...],
    expected: tuple[ExpectedSystemdDropIn, ...],
    reader: DropInSnapshotReader | None,
) -> None:
    if actual_paths != tuple(item.path for item in expected):
        raise SystemdInstallTransactionError("systemd 有效 drop-in 路径集合未证明")
    if not expected:
        return
    if not callable(reader):
        raise SystemdInstallTransactionError("systemd drop-in 原像读取器不可用")
    for item in expected:
        try:
            snapshot = reader(item.path)
        except (KeyboardInterrupt, SystemExit, MemoryError):
            raise
        except Exception as error:
            raise SystemdInstallTransactionError("systemd drop-in 原像无法读取") from error
        if snapshot != item.snapshot:
            raise SystemdInstallTransactionError("systemd 有效 drop-in 原像未证明")


def _parse_effective_exec_start(value: str) -> tuple[str, ...]:
    prefix = "{ path="
    argv_marker = " ; argv[]="
    ignore_marker = " ; ignore_errors="
    if (
        value.count(prefix) != 1
        or value.count(argv_marker) != 1
        or value.count(ignore_marker) != 1
        or not value.startswith(prefix)
        or not value.endswith("}")
    ):
        raise SystemdInstallTransactionError("systemd 有效 ExecStart 输出无效")
    path_text, separator, remainder = value[len(prefix) :].partition(argv_marker)
    if not separator:
        raise SystemdInstallTransactionError("systemd 有效 ExecStart 输出无效")
    argv_text, separator, suffix = remainder.partition(ignore_marker)
    if not separator:
        raise SystemdInstallTransactionError("systemd 有效 ExecStart 输出无效")
    ignore_errors, separator, _runtime_fields = suffix.partition(" ; ")
    if ignore_errors != "no" or not separator:
        raise SystemdInstallTransactionError("systemd 有效 ExecStart 输出无效")
    path_argv = _parse_exec_argv(path_text, "systemd 有效 ExecStart 输出无效")
    actual_argv = _parse_exec_argv(argv_text, "systemd 有效 ExecStart 输出无效")
    if len(path_argv) != 1 or path_argv[0] != actual_argv[0]:
        raise SystemdInstallTransactionError("systemd 有效 ExecStart 输出无效")
    return actual_argv


def _parse_exec_argv(value: str, message: str) -> tuple[str, ...]:
    try:
        argv = tuple(shlex.split(value, posix=True))
    except ValueError as error:
        raise SystemdInstallTransactionError(message) from error
    if not argv or any("\x00" in item for item in argv):
        raise SystemdInstallTransactionError(message)
    executable = PurePosixPath(argv[0])
    if not executable.is_absolute() or str(executable) != argv[0] or ".." in executable.parts:
        raise SystemdInstallTransactionError(message)
    return argv


__all__ = [
    "ExpectedSystemdDropIn",
    "parse_unit_exec_start",
    "verify_codegraph_maintenance_condition",
    "verify_effective_unit_payloads",
]
