"""全量 systemd 安装事务的声明模型与外部端口契约。"""

from __future__ import annotations

import hashlib
import re
from collections.abc import Callable
from contextlib import AbstractContextManager
from dataclasses import dataclass
from enum import Enum
from pathlib import Path, PurePosixPath
from typing import TYPE_CHECKING

from codev_platform.core.runtime_models import (
    RuntimeModelError,
    require_runtime_revision as require_core_runtime_revision,
    require_sha256,
)
from codev_platform.core.systemd_environment_file import (
    require_systemd_environment_file_path,
)
from codev_platform.mcp_systemd_unit_registry import (
    CODEGRAPH_SYSTEMD_UNIT_NAME,
    REINDEX_SYSTEMD_UNIT_NAME,
    managed_systemd_unit,
)


if TYPE_CHECKING:
    from codev_platform.core.runtime_interpreter import ReleaseInterpreterIdentity
    from codev_platform.mcp_systemd_release_shadow import LegacyReleaseDropInSnapshot
    from codev_platform.runtime_release_binding import BoundRelease


_UNIT_NAME = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.@-]*\.(?:service|timer)\Z")
_SHA256 = re.compile(r"[0-9a-f]{64}\Z")
_REVERSIBLE_UNIT_FILE_STATES = frozenset({"enabled", "disabled", "not-found"})
_REVERSIBLE_ACTIVE_STATES = frozenset({"active", "inactive"})
_UNSAFE_WRITE_BITS = 0o022
_TARGET_USER = re.compile(r"[a-z_][a-z0-9_-]{0,31}\Z")


class SystemdInstallTransactionError(RuntimeError):
    """全量 systemd 安装无法在受控维护边界内完成。"""


def require_runtime_revision(value: object, *, git_only: bool = False) -> str:
    """验证唯一运行时版本；维护发布只接受真实 Git OID。"""
    try:
        return require_core_runtime_revision(value, git_only=git_only)
    except RuntimeModelError:
        suffix = "必须为 40 位 Git OID" if git_only else "必须为 40/64 位小写摘要"
        raise SystemdInstallTransactionError(f"systemd 安装运行时版本{suffix}") from None


class SystemdUnitActivationMode(str, Enum):
    """unit 的唯一激活所有者，避免安装事务与恢复状态机职责重叠。"""

    STANDARD = "standard"
    REINDEX_STATE_MACHINE = "reindex_state_machine"
    CODEGRAPH_STATE_MACHINE = "codegraph_state_machine"
    INGRESS_STATE_MACHINE = "ingress_state_machine"


@dataclass(frozen=True, slots=True)
class SystemdRuntimeBinding:
    """把安装包绑定到一个 current 发布、目标用户和环境文件。"""

    release_root: str
    expected_release_id: str
    target_user: str
    environment_file: str | None = None

    def __post_init__(self) -> None:
        root = _require_posix_absolute_path(self.release_root, "运行时版本根")
        try:
            release_id = require_sha256(self.expected_release_id, field="release_id")
        except RuntimeModelError:
            raise SystemdInstallTransactionError("systemd 运行时 release ID 无效") from None
        if (
            type(self.target_user) is not str
            or _TARGET_USER.fullmatch(self.target_user) is None
            or self.target_user == "root"
        ):
            raise SystemdInstallTransactionError("systemd 目标用户无效")
        environment_file = self.environment_file
        if environment_file is not None:
            try:
                environment_file = require_systemd_environment_file_path(environment_file)
            except ValueError:
                raise SystemdInstallTransactionError("systemd 环境文件路径无效") from None
        object.__setattr__(self, "release_root", root)
        object.__setattr__(self, "expected_release_id", release_id)
        object.__setattr__(self, "environment_file", environment_file)

    @property
    def immutable_python(self) -> PurePosixPath:
        return (
            PurePosixPath(self.release_root)
            / "releases"
            / self.expected_release_id
            / "venv/bin/python"
        )

    def to_mapping(self) -> dict[str, object]:
        return {
            "release_root": self.release_root,
            "expected_release_id": self.expected_release_id,
            "target_user": self.target_user,
            "environment_file": self.environment_file,
        }

    @classmethod
    def from_mapping(cls, value: object) -> SystemdRuntimeBinding:
        fields = {
            "release_root",
            "expected_release_id",
            "target_user",
            "environment_file",
        }
        if not isinstance(value, dict) or set(value) != fields:
            raise SystemdInstallTransactionError("systemd 运行时绑定格式无效")
        return cls(
            release_root=value["release_root"],  # type: ignore[arg-type]
            expected_release_id=value["expected_release_id"],  # type: ignore[arg-type]
            target_user=value["target_user"],  # type: ignore[arg-type]
            environment_file=value["environment_file"],  # type: ignore[arg-type]
        )


def _require_posix_absolute_path(value: object, label: str) -> str:
    if type(value) is not str or not value or value.startswith("//"):
        raise SystemdInstallTransactionError(f"{label}无效")
    try:
        path = PurePosixPath(value)
    except (TypeError, ValueError):
        raise SystemdInstallTransactionError(f"{label}无效") from None
    if (
        not path.is_absolute()
        or path.as_posix() != value
        or len(path.parts) < 2
        or any(part in {"", ".", ".."} for part in path.parts[1:])
        or any(char.isspace() or ord(char) < 32 or ord(char) == 127 for char in value)
        or any(char in value for char in ("'", '"', "\\", "%", ":"))
    ):
        raise SystemdInstallTransactionError(f"{label}无效")
    return value


def require_lexical_absolute_path(value: Path, label: str) -> Path:
    """拒绝路径回退，避免特权入口在规范化前接受歧义路径。"""
    try:
        path = Path(value)
        components = tuple(str(item) for item in path.parts[1:])
    except (TypeError, ValueError):
        raise SystemdInstallTransactionError(f"{label}无效") from None
    if not path.is_absolute():
        raise SystemdInstallTransactionError(f"{label}必须为绝对路径")
    if not components or any(item in {"", ".", ".."} for item in components):
        raise SystemdInstallTransactionError(f"{label}包含不安全路径回退")
    return path


@dataclass(frozen=True, slots=True)
class SystemdUnitInstallSpec:
    """单个受限源文件、内容摘要及可补偿生命周期动作。"""

    source: Path
    content_digest: str
    enable: bool
    restart: bool
    activation_mode: SystemdUnitActivationMode = SystemdUnitActivationMode.STANDARD

    def __post_init__(self) -> None:
        source = require_lexical_absolute_path(self.source, "systemd unit 源路径")
        if _UNIT_NAME.fullmatch(source.name) is None:
            raise SystemdInstallTransactionError("systemd unit 文件名无效")
        if type(self.content_digest) is not str or _SHA256.fullmatch(self.content_digest) is None:
            raise SystemdInstallTransactionError("systemd unit 内容摘要无效")
        if not all(type(value) is bool for value in (self.enable, self.restart)):
            raise SystemdInstallTransactionError("systemd unit 生命周期标记无效")
        _require_activation_mode(source.name, self.activation_mode)
        if not self.restart and self.activation_mode is not SystemdUnitActivationMode.STANDARD:
            raise SystemdInstallTransactionError("延迟激活 unit 必须声明最终 restart 目标")
        object.__setattr__(self, "source", source)

    @property
    def unit_name(self) -> str:
        """目标 unit 名只能由已验证的源文件名推导。"""
        return self.source.name

    def to_mapping(self) -> dict[str, object]:
        return {
            "source": str(self.source),
            "sha256": self.content_digest,
            "enable": self.enable,
            "restart": self.restart,
            "activation_mode": self.activation_mode.value,
        }


def _require_activation_mode(name: str, mode: object) -> None:
    try:
        expected = SystemdUnitActivationMode(managed_systemd_unit(name).activation_mode)
    except ValueError:
        expected = SystemdUnitActivationMode.STANDARD
    if type(mode) is not SystemdUnitActivationMode or mode is not expected:
        raise SystemdInstallTransactionError("systemd unit 激活所有者声明无效")


def require_unit_activation_mode(spec: object) -> SystemdUnitInstallSpec:
    """二次验证不可变对象未被绕过构造期篡改。"""
    if type(spec) is not SystemdUnitInstallSpec:
        raise SystemdInstallTransactionError("systemd unit 激活所有者声明无效")
    _require_activation_mode(spec.unit_name, spec.activation_mode)
    return spec


@dataclass(frozen=True, slots=True)
class SystemdInstallManifest:
    """版本化安装声明；只包含可逆的 enable/restart 动作。"""

    units: tuple[SystemdUnitInstallSpec, ...]
    runtime_revision: str
    runtime_binding: SystemdRuntimeBinding | None = None

    def __post_init__(self) -> None:
        try:
            units = tuple(self.units)
        except TypeError as error:
            raise SystemdInstallTransactionError("systemd 安装清单无效") from error
        if not units or not all(isinstance(unit, SystemdUnitInstallSpec) for unit in units):
            raise SystemdInstallTransactionError("systemd 安装清单必须包含受限 unit")
        names = tuple(unit.unit_name for unit in units)
        if len(names) != len(set(names)):
            raise SystemdInstallTransactionError("systemd 安装清单包含重复 unit")
        revision = require_runtime_revision(self.runtime_revision)
        binding = self.runtime_binding
        if binding is not None and type(binding) is not SystemdRuntimeBinding:
            raise SystemdInstallTransactionError("systemd 运行时绑定无效")
        object.__setattr__(self, "units", units)
        object.__setattr__(self, "runtime_revision", revision)

    @property
    def enable_units(self) -> tuple[str, ...]:
        return tuple(unit.unit_name for unit in self.units if unit.enable)

    @property
    def restart_units(self) -> tuple[str, ...]:
        return tuple(unit.unit_name for unit in self.units if unit.restart)

    @property
    def immediate_restart_units(self) -> tuple[str, ...]:
        """返回由安装事务直接重启并证明 active 的普通单元。"""
        return tuple(
            unit.unit_name
            for unit in self.units
            if unit.restart and unit.activation_mode is SystemdUnitActivationMode.STANDARD
        )

    @property
    def stateful_units(self) -> tuple[str, ...]:
        """仅记录事务可能改变的 unit，顺序与清单保持一致。"""
        names = {*self.enable_units, *self.restart_units}
        return tuple(unit.unit_name for unit in self.units if unit.unit_name in names)

    @classmethod
    def from_mapping(cls, payload: object) -> SystemdInstallManifest:
        if not isinstance(payload, dict):
            raise SystemdInstallTransactionError("systemd 安装 manifest 格式无效")
        version = payload.get("version")
        if type(version) is not int or version not in {4, 5}:
            if type(version) is int and version in {2, 3}:
                raise SystemdInstallTransactionError(
                    "旧 systemd 安装 manifest 含已废弃启动语义，请重新生成安装包"
                )
            raise SystemdInstallTransactionError("systemd 安装 manifest 版本无效")
        expected_fields = (
            {"version", "runtime_revision", "units", "runtime_binding"}
            if version == 5
            else {"version", "runtime_revision", "units"}
        )
        if set(payload) != expected_fields:
            raise SystemdInstallTransactionError("systemd 安装 manifest 格式无效")
        raw_units = payload["units"]
        if type(raw_units) is not list:
            raise SystemdInstallTransactionError("systemd 安装 manifest 的 units 必须为列表")
        return cls(
            tuple(_unit_from_mapping(raw) for raw in raw_units),
            runtime_revision=require_runtime_revision(payload["runtime_revision"]),
            runtime_binding=(
                SystemdRuntimeBinding.from_mapping(payload["runtime_binding"])
                if version == 5
                else None
            ),
        )

    def to_mapping(self) -> dict[str, object]:
        payload: dict[str, object] = {
            "version": 5 if self.runtime_binding is not None else 4,
            "runtime_revision": require_runtime_revision(self.runtime_revision),
            "units": [unit.to_mapping() for unit in self.units],
        }
        if self.runtime_binding is not None:
            payload["runtime_binding"] = self.runtime_binding.to_mapping()
        return payload


def _unit_from_mapping(raw: object) -> SystemdUnitInstallSpec:
    if not isinstance(raw, dict) or set(raw) != {
        "source",
        "sha256",
        "enable",
        "restart",
        "activation_mode",
    }:
        raise SystemdInstallTransactionError("systemd unit manifest 条目无效")
    source = raw["source"]
    digest = raw["sha256"]
    if type(source) is not str or type(digest) is not str:
        raise SystemdInstallTransactionError("systemd unit manifest 条目无效")
    try:
        activation_mode = SystemdUnitActivationMode(raw["activation_mode"])
    except (TypeError, ValueError):
        raise SystemdInstallTransactionError("systemd unit 激活所有者声明无效") from None
    return SystemdUnitInstallSpec(
        source=Path(source),
        content_digest=digest,
        enable=raw["enable"],  # type: ignore[arg-type]
        restart=raw["restart"],  # type: ignore[arg-type]
        activation_mode=activation_mode,
    )


@dataclass(frozen=True, slots=True)
class SystemdInstallReport:
    """安装事务的无秘密结果，显式标出由后续状态机激活的 unit。"""

    deferred_activation_units: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        try:
            deferred_names = tuple(self.deferred_activation_units)
        except TypeError:
            raise SystemdInstallTransactionError("systemd 安装报告无效") from None
        if not all(
            type(name) is str and _UNIT_NAME.fullmatch(name) is not None for name in deferred_names
        ) or len(deferred_names) != len(set(deferred_names)):
            raise SystemdInstallTransactionError("systemd 安装报告包含无效 unit")
        object.__setattr__(self, "deferred_activation_units", deferred_names)


@dataclass(frozen=True, slots=True)
class SystemdUnitPayload:
    """已绑定到清单摘要的 unit 内容，后续安装不再重新按路径读取。"""

    spec: SystemdUnitInstallSpec
    content: bytes

    def __post_init__(self) -> None:
        if not isinstance(self.spec, SystemdUnitInstallSpec):
            raise SystemdInstallTransactionError("systemd unit 载荷无效")
        if type(self.content) is not bytes or not self.content:
            raise SystemdInstallTransactionError("systemd unit 源文件无效")
        digest = hashlib.sha256(self.content).hexdigest()
        if digest != self.spec.content_digest:
            raise SystemdInstallTransactionError("systemd unit 源文件摘要不匹配")


@dataclass(frozen=True, slots=True)
class SystemdUnitFileSnapshot:
    """受管目标 unit 的完整可恢复原像。"""

    content: bytes
    mode: int
    uid: int
    gid: int

    def __post_init__(self) -> None:
        if type(self.content) is not bytes:
            raise SystemdInstallTransactionError("systemd unit 原像内容无效")
        if type(self.mode) is not int or not 0 <= self.mode <= 0o777:
            raise SystemdInstallTransactionError("systemd unit 原像权限无效")
        if self.mode & _UNSAFE_WRITE_BITS:
            raise SystemdInstallTransactionError("systemd unit 原像权限不受信任")
        if type(self.uid) is not int or self.uid != 0 or type(self.gid) is not int or self.gid < 0:
            raise SystemdInstallTransactionError("systemd unit 原像所有者不受信任")


@dataclass(frozen=True, slots=True)
class SystemdStageReceiptFileSnapshot:
    """maintenance-stage 回执的完整可恢复原像。"""

    content: bytes
    mode: int
    uid: int
    gid: int

    def __post_init__(self) -> None:
        if type(self.content) is not bytes:
            raise SystemdInstallTransactionError("stage 回执原像内容无效")
        if type(self.mode) is not int or not 0 <= self.mode <= 0o777:
            raise SystemdInstallTransactionError("stage 回执原像权限无效")
        if self.mode & _UNSAFE_WRITE_BITS:
            raise SystemdInstallTransactionError("stage 回执原像权限不受信任")
        if type(self.uid) is not int or self.uid != 0 or type(self.gid) is not int or self.gid < 0:
            raise SystemdInstallTransactionError("stage 回执原像所有者不受信任")


@dataclass(frozen=True, slots=True)
class SystemdUnitState:
    """仅接受可由 enable/disable 与 restart/stop 精确复原的状态。"""

    unit_file_state: str
    active_state: str

    def __post_init__(self) -> None:
        if self.unit_file_state not in _REVERSIBLE_UNIT_FILE_STATES:
            raise SystemdInstallTransactionError("systemd unit 启用状态不可逆")
        if self.active_state not in _REVERSIBLE_ACTIVE_STATES:
            raise SystemdInstallTransactionError("systemd unit 活动状态不可逆")
        if self.unit_file_state == "not-found" and self.active_state != "inactive":
            raise SystemdInstallTransactionError("不存在的 systemd unit 运行状态无效")


@dataclass(frozen=True, slots=True)
class SystemdUnitProcessState:
    """install-only 前后必须完全一致的活动进程快照。"""

    active_state: str
    main_pid: int
    invocation_id: str

    def __post_init__(self) -> None:
        if (
            type(self.active_state) is not str
            or re.fullmatch(r"[a-z][a-z-]*", self.active_state) is None
            or type(self.main_pid) is not int
            or self.main_pid < 0
            or type(self.invocation_id) is not str
            or (
                self.invocation_id != ""
                and re.fullmatch(r"[0-9a-f]{32}", self.invocation_id) is None
            )
        ):
            raise SystemdInstallTransactionError("systemd unit 进程状态无效")


GateProvisioner = Callable[[], None]
InstallerLockFactory = Callable[[], AbstractContextManager[None]]
InstallBoundaryProof = Callable[[], None]
UnitSourceReader = Callable[[Path], bytes]
LegacyReleaseDropInSnapshotReader = Callable[
    [tuple[str, ...]], tuple["LegacyReleaseDropInSnapshot", ...]
]
LegacyReleaseDropInRetirer = Callable[[tuple["LegacyReleaseDropInSnapshot", ...]], None]
LegacyReleaseDropInRestorer = Callable[[tuple["LegacyReleaseDropInSnapshot", ...]], None]
LegacyReleaseDropInVerifier = Callable[[tuple["LegacyReleaseDropInSnapshot", ...]], None]
EffectiveUnitPayloadVerifier = Callable[[tuple[SystemdUnitPayload, ...]], None]
ManagedInstallContractVerifier = Callable[
    [SystemdInstallManifest, tuple[SystemdUnitPayload, ...]], None
]
RuntimeBindingLockFactory = Callable[
    [SystemdInstallManifest], AbstractContextManager["BoundRelease"]
]
RuntimeBindingVerifier = Callable[[SystemdInstallManifest, "BoundRelease"], None]
TargetUserPreflightVerifier = Callable[
    [SystemdInstallManifest, tuple[SystemdUnitPayload, ...], "BoundRelease"], None
]
InstalledUnitSnapshotReader = Callable[[str], SystemdUnitFileSnapshot | None]
InstalledUnitWriter = Callable[[str, bytes], None]
InstalledUnitRestorer = Callable[[str, SystemdUnitFileSnapshot | None], None]
UnitStateReader = Callable[[str], SystemdUnitState]
UnitEnablementStateReader = Callable[[str], str]
UnitProcessStateReader = Callable[[str], SystemdUnitProcessState]
UnitFileStateRestorer = Callable[[str, SystemdUnitState], None]
UnitActivityStateRestorer = Callable[[str, SystemdUnitState], None]
SystemctlRunner = Callable[[tuple[str, ...]], None]
RunningUnitVerifier = Callable[[tuple[str, ...]], None]
StageReceiptSnapshotReader = Callable[[], SystemdStageReceiptFileSnapshot | None]
StageRuntimeIdentityReader = Callable[[], "ReleaseInterpreterIdentity"]
StageReceiptWriter = Callable[[bytes], None]
StageReceiptRestorer = Callable[[SystemdStageReceiptFileSnapshot | None], None]
StageReceiptVerifier = Callable[[bytes], None]


@dataclass(frozen=True, slots=True)
class SystemdInstallPorts:
    """事务外部边界显式注入，编排层不依赖具体 Linux 实现。"""

    provision_maintenance_gate: GateProvisioner
    installer_lock: InstallerLockFactory
    runtime_binding_lock: RuntimeBindingLockFactory
    verify_runtime_binding: RuntimeBindingVerifier
    verify_target_user_preflight: TargetUserPreflightVerifier
    verify_install_boundary: InstallBoundaryProof
    read_unit_source: UnitSourceReader
    snapshot_legacy_release_dropins: LegacyReleaseDropInSnapshotReader
    retire_legacy_release_dropins: LegacyReleaseDropInRetirer
    restore_legacy_release_dropins: LegacyReleaseDropInRestorer
    verify_legacy_release_dropins_retired: LegacyReleaseDropInVerifier
    verify_managed_install_contract: ManagedInstallContractVerifier
    verify_effective_unit_payloads: EffectiveUnitPayloadVerifier
    snapshot_installed_unit: InstalledUnitSnapshotReader
    write_installed_unit: InstalledUnitWriter
    restore_installed_unit: InstalledUnitRestorer
    read_unit_state: UnitStateReader
    read_unit_enablement_state: UnitEnablementStateReader
    read_unit_process_state: UnitProcessStateReader
    restore_unit_file_state: UnitFileStateRestorer
    restore_unit_activity_state: UnitActivityStateRestorer
    systemctl: SystemctlRunner
    verify_running_units: RunningUnitVerifier
    read_stage_runtime_identity: StageRuntimeIdentityReader
    snapshot_stage_receipt: StageReceiptSnapshotReader
    write_stage_receipt: StageReceiptWriter
    restore_stage_receipt: StageReceiptRestorer
    verify_stage_receipt: StageReceiptVerifier


__all__ = [
    "InstalledUnitRestorer",
    "InstalledUnitSnapshotReader",
    "InstalledUnitWriter",
    "CODEGRAPH_SYSTEMD_UNIT_NAME",
    "EffectiveUnitPayloadVerifier",
    "InstallBoundaryProof",
    "InstallerLockFactory",
    "LegacyReleaseDropInRestorer",
    "LegacyReleaseDropInRetirer",
    "LegacyReleaseDropInSnapshotReader",
    "LegacyReleaseDropInVerifier",
    "ManagedInstallContractVerifier",
    "REINDEX_SYSTEMD_UNIT_NAME",
    "RunningUnitVerifier",
    "RuntimeBindingLockFactory",
    "RuntimeBindingVerifier",
    "StageReceiptRestorer",
    "StageRuntimeIdentityReader",
    "StageReceiptSnapshotReader",
    "StageReceiptVerifier",
    "StageReceiptWriter",
    "SystemctlRunner",
    "SystemdInstallManifest",
    "SystemdInstallPorts",
    "SystemdInstallReport",
    "SystemdRuntimeBinding",
    "SystemdInstallTransactionError",
    "SystemdStageReceiptFileSnapshot",
    "SystemdUnitFileSnapshot",
    "SystemdUnitActivationMode",
    "SystemdUnitInstallSpec",
    "SystemdUnitPayload",
    "SystemdUnitProcessState",
    "SystemdUnitState",
    "TargetUserPreflightVerifier",
    "UnitActivityStateRestorer",
    "UnitEnablementStateReader",
    "UnitFileStateRestorer",
    "UnitSourceReader",
    "UnitStateReader",
    "UnitProcessStateReader",
    "require_lexical_absolute_path",
    "require_runtime_revision",
    "require_unit_activation_mode",
]
