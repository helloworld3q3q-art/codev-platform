"""CodeGraph systemd 主 unit 布局迁移的领域契约。"""
from __future__ import annotations

from collections.abc import Callable
from contextlib import AbstractContextManager
from dataclasses import dataclass
from pathlib import Path

from codev_platform.mcp_systemd_install_input import (
    VerifiedInstallInput,
    load_verified_install_input,
)
from codev_platform.ops.reindex_codegraph_resume_managed_path import (
    RootOwnedRegularFileSnapshot,
)


_CODEGRAPH_UNIT = "codev-mcp-codegraph.service"
_LEGACY_UNIT_DIRECTORY = Path("/etc/systemd/system")
_CANONICAL_UNIT_DIRECTORY = Path("/usr/local/lib/systemd/system")


class SystemdUnitLayoutMigrationError(RuntimeError):
    """固定 CodeGraph 主 unit 布局无法安全迁移。"""


@dataclass(frozen=True, slots=True)
class SystemdUnitRuntime:
    """迁移前后必须保持稳定的 systemd 解析事实。"""

    fragment_path: str
    unit_file_state: str
    active_state: str
    sub_state: str
    result: str
    exec_main_code: str
    exec_main_status: str
    invocation_id: str
    load_state: str = "loaded"

    def __post_init__(self) -> None:
        values = (
            self.fragment_path,
            self.unit_file_state,
            self.active_state,
            self.sub_state,
            self.result,
            self.exec_main_code,
            self.exec_main_status,
            self.invocation_id,
            self.load_state,
        )
        if not all(type(value) is str and "\x00" not in value for value in values):
            raise SystemdUnitLayoutMigrationError("systemd unit 运行状态无效")
        if not Path(self.fragment_path).is_absolute() or not self.load_state:
            raise SystemdUnitLayoutMigrationError("systemd unit 运行状态无效")

    def without_fragment(self) -> tuple[str, str, str, str, str, str, str, str]:
        """返回迁移不得改变的所有运行态字段。"""
        return (
            self.unit_file_state,
            self.active_state,
            self.sub_state,
            self.result,
            self.exec_main_code,
            self.exec_main_status,
            self.invocation_id,
            self.load_state,
        )


SnapshotReader = Callable[[Path], RootOwnedRegularFileSnapshot | None]
SnapshotCreator = Callable[[Path, RootOwnedRegularFileSnapshot], None]
SnapshotMover = Callable[[Path, Path, RootOwnedRegularFileSnapshot], None]
RuntimeReader = Callable[[str], SystemdUnitRuntime]
SystemctlRunner = Callable[[tuple[str, ...]], None]
EnableLinkReader = Callable[[str], str]
RuntimeMaskTargetReader = Callable[[], str | None]
TransitionLockFactory = Callable[[], AbstractContextManager[None]]
GateActiveReader = Callable[[], bool]
VerifiedInputLoader = Callable[[Path], VerifiedInstallInput]
ExpectedCodegraphPython = Callable[[], str | Path]


@dataclass(frozen=True, slots=True)
class UnitLayoutMigrationPorts:
    """迁移事务的外部边界，文件与 systemd 副作用均可替换验证。"""

    transition_lock: TransitionLockFactory
    gate_active: GateActiveReader
    read_snapshot: SnapshotReader
    create_if_absent: SnapshotCreator
    move_if_snapshot: SnapshotMover
    read_runtime: RuntimeReader
    systemctl: SystemctlRunner
    read_enable_link: EnableLinkReader
    read_runtime_mask_target: RuntimeMaskTargetReader
    expected_codegraph_python: ExpectedCodegraphPython
    load_verified_input: VerifiedInputLoader = load_verified_install_input


__all__ = [
    "SystemdUnitLayoutMigrationError",
    "SystemdUnitRuntime",
    "UnitLayoutMigrationPorts",
]
