"""运行时 fd tree 的声明式策略与结果契约。"""

from __future__ import annotations

import stat
from dataclasses import dataclass, field

from codev_platform._runtime_fd_tree_snapshot import (
    RuntimeFdTreeAccessRepairSnapshot,
    RuntimeFdTreeSnapshot,
    require_access_repair_snapshot as _require_access_repair_snapshot,
    require_identity_snapshot as _require_identity_snapshot,
)


_MAX_POSIX_ID = (1 << 32) - 2


class RuntimeFdTreeError(RuntimeError):
    """运行时对象树无法按声明式操作安全遍历。"""


@dataclass(frozen=True, slots=True)
class RuntimeFdModePolicy:
    """不携带回调的对象 mode 映射策略。"""

    directory_mode: int
    regular_mode: int
    executable_mode: int
    executable_mask: int

    def __post_init__(self) -> None:
        values = (
            self.directory_mode,
            self.regular_mode,
            self.executable_mode,
            self.executable_mask,
        )
        if any(type(value) is not int for value in values):
            raise TypeError("运行时对象 mode 策略无效")
        if (
            self.directory_mode != 0o750
            or self.regular_mode != 0o640
            or self.executable_mode != 0o750
            or self.executable_mask != stat.S_IXUSR
        ):
            raise ValueError("运行时对象 mode 策略不受支持")

    def canonical_mode(self, mode: int) -> int:
        """仅根据文件类型和 owner 执行位返回规范 mode。"""
        if type(mode) is not int:
            raise TypeError("运行时对象 mode 无效")
        if stat.S_ISDIR(mode):
            return self.directory_mode
        if stat.S_ISREG(mode):
            return self.executable_mode if mode & self.executable_mask else self.regular_mode
        raise RuntimeFdTreeError("运行时对象树包含特殊文件")


@dataclass(frozen=True, slots=True)
class RuntimeFdGroupPolicy:
    """内容对象服务主组发布策略。"""

    target_gid: int

    def __post_init__(self) -> None:
        if type(self.target_gid) is not int:
            raise TypeError("运行时对象目标 GID 无效")
        if not 0 < self.target_gid <= _MAX_POSIX_ID:
            raise ValueError("运行时对象目标 GID 超出严格正整数范围")


@dataclass(frozen=True, slots=True)
class RuntimeRootMarkerPolicy:
    """对象根未完成标记的声明式读取契约。"""

    name: str
    trusted_contents: tuple[bytes, ...]
    max_bytes: int

    def __post_init__(self) -> None:
        if (
            type(self.name) is not str
            or not self.name
            or self.name in {".", ".."}
            or "/" in self.name
            or "\x00" in self.name
        ):
            raise ValueError("运行时对象标记名称无效")
        if type(self.max_bytes) is not int or not 0 < self.max_bytes <= 4096:
            raise ValueError("运行时对象标记预算无效")
        if (
            type(self.trusted_contents) is not tuple
            or not self.trusted_contents
            or len(set(self.trusted_contents)) != len(self.trusted_contents)
            or any(
                type(content) is not bytes or not 0 < len(content) <= self.max_bytes
                for content in self.trusted_contents
            )
        ):
            raise ValueError("运行时对象标记内容契约无效")


@dataclass(frozen=True, slots=True)
class VerifyOnly:
    """只读复验；任何对象身份字段都不得被遍历器改变。"""

    mode_policy: RuntimeFdModePolicy
    marker_policy: RuntimeRootMarkerPolicy
    require_marker: bool = field(default=False, init=False)
    forbid_marker: bool = field(default=False, init=False)

    def __post_init__(self) -> None:
        _require_operation_policies(self.mode_policy, self.marker_policy)


@dataclass(frozen=True, slots=True)
class CanonicalizeMode:
    """只定型 mode；除权限位与 ctime 外的对象身份必须稳定。"""

    mode_policy: RuntimeFdModePolicy
    marker_policy: RuntimeRootMarkerPolicy
    require_marker: bool = field(default=True, init=False)
    forbid_marker: bool = field(default=False, init=False)

    def __post_init__(self) -> None:
        _require_operation_policies(self.mode_policy, self.marker_policy)


@dataclass(frozen=True, slots=True)
class PreflightCompletedAccessRepair:
    """只读预检已完成对象；仅允许历史目录 ``0755`` 漂移。"""

    mode_policy: RuntimeFdModePolicy
    marker_policy: RuntimeRootMarkerPolicy
    require_marker: bool = field(default=False, init=False)
    forbid_marker: bool = field(default=True, init=False)

    def __post_init__(self) -> None:
        _require_operation_policies(self.mode_policy, self.marker_policy)


@dataclass(frozen=True, slots=True)
class ConvergeCompletedAccessRepair:
    """叶子优先收敛已预检完成对象的目录访问 mode。"""

    mode_policy: RuntimeFdModePolicy
    marker_policy: RuntimeRootMarkerPolicy
    expected_snapshot: RuntimeFdTreeAccessRepairSnapshot
    require_marker: bool = field(default=False, init=False)
    forbid_marker: bool = field(default=True, init=False)

    def __post_init__(self) -> None:
        _require_operation_policies(self.mode_policy, self.marker_policy)
        _require_access_repair_snapshot(self.expected_snapshot)


@dataclass(frozen=True, slots=True)
class PreflightGroup:
    """只读预检；目录和普通文件只允许处于 root 组或目标组。"""

    mode_policy: RuntimeFdModePolicy
    marker_policy: RuntimeRootMarkerPolicy
    group_policy: RuntimeFdGroupPolicy
    require_marker: bool = field(default=False, init=False)
    forbid_marker: bool = field(default=True, init=False)

    def __post_init__(self) -> None:
        _require_group_operation_policies(
            self.mode_policy,
            self.marker_policy,
            self.group_policy,
        )


@dataclass(frozen=True, slots=True)
class PublishGroup:
    """叶子优先发布目标服务组；只允许 GID 与 ctime 改变。"""

    mode_policy: RuntimeFdModePolicy
    marker_policy: RuntimeRootMarkerPolicy
    group_policy: RuntimeFdGroupPolicy
    expected_snapshot: RuntimeFdTreeSnapshot
    require_marker: bool = field(default=False, init=False)
    forbid_marker: bool = field(default=True, init=False)

    def __post_init__(self) -> None:
        _require_group_operation_policies(
            self.mode_policy,
            self.marker_policy,
            self.group_policy,
        )
        _require_identity_snapshot(self.expected_snapshot)


@dataclass(frozen=True, slots=True)
class VerifyGroup:
    """只读复验目录和普通文件已精确发布到目标服务组。"""

    mode_policy: RuntimeFdModePolicy
    marker_policy: RuntimeRootMarkerPolicy
    group_policy: RuntimeFdGroupPolicy
    expected_snapshot: RuntimeFdTreeSnapshot
    require_marker: bool = field(default=False, init=False)
    forbid_marker: bool = field(default=True, init=False)

    def __post_init__(self) -> None:
        _require_group_operation_policies(
            self.mode_policy,
            self.marker_policy,
            self.group_policy,
        )
        _require_identity_snapshot(self.expected_snapshot)


RuntimeFdGroupOperation = PreflightGroup | PublishGroup | VerifyGroup
RuntimeFdCompletedAccessRepairOperation = (
    PreflightCompletedAccessRepair | ConvergeCompletedAccessRepair
)
RuntimeFdTreeOperation = (
    VerifyOnly
    | CanonicalizeMode
    | RuntimeFdCompletedAccessRepairOperation
    | RuntimeFdGroupOperation
)


@dataclass(frozen=True, slots=True)
class RuntimeFdTreeReport:
    """不含路径或内容的遍历证明摘要。"""

    entries: int
    total_bytes: int
    root_device: int
    root_inode: int
    identity_snapshot: RuntimeFdTreeSnapshot
    access_repair_snapshot: RuntimeFdTreeAccessRepairSnapshot | None = None


def _require_operation_policies(
    mode_policy: RuntimeFdModePolicy,
    marker_policy: RuntimeRootMarkerPolicy,
) -> None:
    if (
        type(mode_policy) is not RuntimeFdModePolicy
        or type(
            marker_policy,
        )
        is not RuntimeRootMarkerPolicy
    ):
        raise TypeError("运行时对象树操作策略无效")


def _require_group_operation_policies(
    mode_policy: RuntimeFdModePolicy,
    marker_policy: RuntimeRootMarkerPolicy,
    group_policy: RuntimeFdGroupPolicy,
) -> None:
    _require_operation_policies(mode_policy, marker_policy)
    if type(group_policy) is not RuntimeFdGroupPolicy:
        raise TypeError("运行时对象树 GID 操作策略无效")


# 类型迁移到私有模块后仍声明原公开模块，兼容反射、序列化和错误展示。
for _public_contract in (
    RuntimeFdTreeError,
    RuntimeFdModePolicy,
    RuntimeFdGroupPolicy,
    RuntimeRootMarkerPolicy,
    VerifyOnly,
    CanonicalizeMode,
    PreflightCompletedAccessRepair,
    ConvergeCompletedAccessRepair,
    PreflightGroup,
    PublishGroup,
    VerifyGroup,
    RuntimeFdTreeReport,
):
    _public_contract.__module__ = "codev_platform.runtime_fd_tree"
del _public_contract


__all__ = [
    "CanonicalizeMode",
    "ConvergeCompletedAccessRepair",
    "PreflightGroup",
    "PreflightCompletedAccessRepair",
    "PublishGroup",
    "RuntimeFdCompletedAccessRepairOperation",
    "RuntimeFdGroupOperation",
    "RuntimeFdGroupPolicy",
    "RuntimeFdModePolicy",
    "RuntimeFdTreeError",
    "RuntimeFdTreeOperation",
    "RuntimeFdTreeReport",
    "RuntimeRootMarkerPolicy",
    "VerifyGroup",
    "VerifyOnly",
]
