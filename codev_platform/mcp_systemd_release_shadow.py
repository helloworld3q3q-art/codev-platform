"""旧 release systemd drop-in 的固定路径条件退役适配器。"""

from __future__ import annotations

import os
import re
from dataclasses import dataclass
from pathlib import Path

from codev_platform.mcp_systemd_install_contract import SystemdInstallTransactionError
from codev_platform.ops.reindex_codegraph_resume_managed_path import (
    RootOwnedRegularFileSnapshot,
    TrustedManagedPathError,
    _close_quietly,
    _open_trusted_parent,
    read_optional_root_owned_regular_file_snapshot,
)
from codev_platform.ops.systemd_unit_layout_filesystem import (
    move_root_owned_regular_file_if_snapshot,
)


_SYSTEMD_UNIT_DIRECTORY = Path("/etc/systemd/system")
_ACTIVE_FILE_NAME = "90-codev-release.conf"
_ARCHIVE_FILE_NAME = "90-codev-release.conf.codev-retired"
_MAX_SHADOW_BYTES = 128 * 1024
_MANAGED_UNIT_NAME = re.compile(r"codev-[A-Za-z0-9][A-Za-z0-9_.@-]*\.(?:service|timer)\Z")
_CODEV_DROP_IN_DIRECTORY = re.compile(
    r"(?P<unit>codev-[A-Za-z0-9][A-Za-z0-9_.@-]*\.(?:service|timer))\.d\Z"
)


@dataclass(frozen=True, slots=True)
class LegacyReleaseDropInSnapshot:
    """单个固定 drop-in 的位置状态与 root 文件完整原像。"""

    unit_name: str
    active_path: Path
    archive_path: Path
    active_snapshot: RootOwnedRegularFileSnapshot | None
    archive_snapshot: RootOwnedRegularFileSnapshot | None


def snapshot_legacy_release_dropins(
    unit_names: tuple[str, ...],
) -> tuple[LegacyReleaseDropInSnapshot, ...]:
    """冻结 manifest 内 shadow，并拒绝任何 manifest 外活动 shadow。"""
    names = _require_unit_names(unit_names)
    try:
        snapshots = tuple(_snapshot_one(name) for name in names)
        _require_no_unmanaged_active_shadow(names)
        return snapshots
    except (KeyboardInterrupt, SystemExit, MemoryError):
        raise
    except SystemdInstallTransactionError:
        raise
    except TrustedManagedPathError as error:
        raise SystemdInstallTransactionError("旧 release shadow 路径不受信任") from error
    except Exception as error:
        raise SystemdInstallTransactionError("旧 release shadow 原像无法读取") from error


def retire_legacy_release_dropins(
    snapshots: tuple[LegacyReleaseDropInSnapshot, ...],
) -> None:
    """将活动 shadow 按完整 inode 原像条件移动到固定非 drop-in 归档名。"""
    checked = _require_snapshots(snapshots)
    try:
        for snapshot in checked:
            if snapshot.active_snapshot is None:
                _verify_retired_one(snapshot)
                continue
            _move_shadow_file(
                snapshot.active_path,
                snapshot.archive_path,
                snapshot.active_snapshot,
            )
    except (KeyboardInterrupt, SystemExit, MemoryError):
        raise
    except SystemdInstallTransactionError:
        raise
    except TrustedManagedPathError as error:
        raise SystemdInstallTransactionError(f"旧 release shadow 条件退役失败：{error}") from error
    except Exception as error:
        raise SystemdInstallTransactionError("旧 release shadow 条件退役失败") from error


def restore_legacy_release_dropins(
    snapshots: tuple[LegacyReleaseDropInSnapshot, ...],
) -> None:
    """按冻结位置恢复 shadow；已恢复或原本归档时保持幂等。"""
    checked = _require_snapshots(snapshots)
    try:
        for snapshot in checked:
            _restore_one(snapshot)
    except (KeyboardInterrupt, SystemExit, MemoryError):
        raise
    except SystemdInstallTransactionError:
        raise
    except TrustedManagedPathError as error:
        raise SystemdInstallTransactionError(f"旧 release shadow 原像恢复失败：{error}") from error
    except Exception as error:
        raise SystemdInstallTransactionError("旧 release shadow 原像恢复失败") from error


def verify_legacy_release_dropins_retired(
    snapshots: tuple[LegacyReleaseDropInSnapshot, ...],
) -> None:
    """证明活动 shadow 均已消失，归档与冻结原像保持同一 inode。"""
    checked = _require_snapshots(snapshots)
    try:
        for snapshot in checked:
            _verify_retired_one(snapshot)
        _require_no_unmanaged_active_shadow(tuple(item.unit_name for item in checked))
    except (KeyboardInterrupt, SystemExit, MemoryError):
        raise
    except SystemdInstallTransactionError:
        raise
    except TrustedManagedPathError as error:
        raise SystemdInstallTransactionError("旧 release shadow 退役状态不受信任") from error
    except Exception as error:
        raise SystemdInstallTransactionError("旧 release shadow 退役状态未证明") from error


def _snapshot_one(unit_name: str) -> LegacyReleaseDropInSnapshot:
    active_path, archive_path = _shadow_paths(unit_name)
    active = _read_shadow_file(active_path)
    archive = _read_shadow_file(archive_path)
    if active is not None and archive is not None:
        raise SystemdInstallTransactionError("旧 release 活动 shadow 与归档同时存在")
    snapshot = LegacyReleaseDropInSnapshot(
        unit_name=unit_name,
        active_path=active_path,
        archive_path=archive_path,
        active_snapshot=active,
        archive_snapshot=archive,
    )
    return _require_snapshot(snapshot)


def _require_no_unmanaged_active_shadow(manifest_names: tuple[str, ...]) -> None:
    allowed = set(manifest_names)
    for name in _discover_shadow_unit_names():
        active_path, _archive_path = _shadow_paths(name)
        if _read_shadow_file(active_path) is not None and name not in allowed:
            raise SystemdInstallTransactionError("发现 systemd manifest 外旧 release shadow")


def _restore_one(snapshot: LegacyReleaseDropInSnapshot) -> None:
    active, archive = _read_current_pair(snapshot)
    original = snapshot.active_snapshot
    if original is None:
        if _pair_matches_snapshot(active, archive, snapshot):
            return
        raise TrustedManagedPathError("受管路径原像已变化")
    if _matches(active, original) and archive is None:
        return
    if active is None and _matches(archive, original):
        _move_shadow_file(snapshot.archive_path, snapshot.active_path, original)
        return
    raise TrustedManagedPathError("受管路径原像已变化")


def _verify_retired_one(snapshot: LegacyReleaseDropInSnapshot) -> None:
    active, archive = _read_current_pair(snapshot)
    if active is not None:
        raise SystemdInstallTransactionError("旧 release 活动 shadow 仍然生效")
    expected = snapshot.active_snapshot or snapshot.archive_snapshot
    if expected is None:
        if archive is None:
            return
    elif _matches(archive, expected):
        return
    raise SystemdInstallTransactionError("旧 release shadow 退役状态未证明")


def _read_current_pair(
    snapshot: LegacyReleaseDropInSnapshot,
) -> tuple[RootOwnedRegularFileSnapshot | None, RootOwnedRegularFileSnapshot | None]:
    return _read_shadow_file(snapshot.active_path), _read_shadow_file(snapshot.archive_path)


def _pair_matches_snapshot(
    active: RootOwnedRegularFileSnapshot | None,
    archive: RootOwnedRegularFileSnapshot | None,
    snapshot: LegacyReleaseDropInSnapshot,
) -> bool:
    return _optional_matches(active, snapshot.active_snapshot) and _optional_matches(
        archive,
        snapshot.archive_snapshot,
    )


def _optional_matches(
    current: RootOwnedRegularFileSnapshot | None,
    expected: RootOwnedRegularFileSnapshot | None,
) -> bool:
    if expected is None:
        return current is None
    return _matches(current, expected)


def _matches(
    current: RootOwnedRegularFileSnapshot | None,
    expected: RootOwnedRegularFileSnapshot,
) -> bool:
    return current is not None and current.matches(expected, require_identity=True)


def _require_unit_names(unit_names: object) -> tuple[str, ...]:
    try:
        names = tuple(unit_names)  # type: ignore[arg-type]
    except TypeError:
        raise SystemdInstallTransactionError("旧 release shadow unit 清单无效") from None
    if (
        not all(type(name) is str and _MANAGED_UNIT_NAME.fullmatch(name) for name in names)
        or len(names) != len(set(names))
    ):
        raise SystemdInstallTransactionError("旧 release shadow unit 清单无效")
    return names


def _require_snapshots(snapshots: object) -> tuple[LegacyReleaseDropInSnapshot, ...]:
    try:
        checked = tuple(snapshots)  # type: ignore[arg-type]
    except TypeError:
        raise SystemdInstallTransactionError("旧 release shadow 原像清单无效") from None
    validated = tuple(_require_snapshot(snapshot) for snapshot in checked)
    names = tuple(snapshot.unit_name for snapshot in validated)
    if len(names) != len(set(names)):
        raise SystemdInstallTransactionError("旧 release shadow 原像清单重复")
    return validated


def _require_snapshot(snapshot: object) -> LegacyReleaseDropInSnapshot:
    if not isinstance(snapshot, LegacyReleaseDropInSnapshot):
        raise SystemdInstallTransactionError("旧 release shadow 原像无效")
    expected_active, expected_archive = _shadow_paths(snapshot.unit_name)
    if snapshot.active_path != expected_active or snapshot.archive_path != expected_archive:
        raise SystemdInstallTransactionError("旧 release shadow 原像不是固定路径")
    originals = (snapshot.active_snapshot, snapshot.archive_snapshot)
    if any(item is not None and not _has_complete_identity(item) for item in originals):
        raise SystemdInstallTransactionError("旧 release shadow 原像身份无效")
    if all(item is not None for item in originals):
        raise SystemdInstallTransactionError("旧 release 活动 shadow 与归档同时存在")
    return snapshot


def _has_complete_identity(snapshot: object) -> bool:
    return (
        isinstance(snapshot, RootOwnedRegularFileSnapshot)
        and snapshot.device > 0
        and snapshot.inode > 0
    )


def _shadow_paths(unit_name: str) -> tuple[Path, Path]:
    if type(unit_name) is not str or _MANAGED_UNIT_NAME.fullmatch(unit_name) is None:
        raise SystemdInstallTransactionError("旧 release shadow unit 名无效")
    directory = _SYSTEMD_UNIT_DIRECTORY / f"{unit_name}.d"
    return directory / _ACTIVE_FILE_NAME, directory / _ARCHIVE_FILE_NAME


def _read_shadow_file(path: Path) -> RootOwnedRegularFileSnapshot | None:
    return read_optional_root_owned_regular_file_snapshot(path, max_bytes=_MAX_SHADOW_BYTES)


def _move_shadow_file(
    source: Path,
    destination: Path,
    expected: RootOwnedRegularFileSnapshot,
) -> None:
    move_root_owned_regular_file_if_snapshot(source, destination, expected)


def _discover_shadow_unit_names() -> tuple[str, ...]:
    """通过 root 可信目录描述符枚举固定 codev drop-in 目录。"""
    parent = _open_trusted_parent(
        _SYSTEMD_UNIT_DIRECTORY / ".codev-release-shadow-scan",
        create_missing=False,
    )
    if parent is None:
        raise TrustedManagedPathError("systemd unit 目录不存在")
    descriptor, _leaf = parent
    try:
        names: set[str] = set()
        for entry in os.listdir(descriptor):
            match = _CODEV_DROP_IN_DIRECTORY.fullmatch(entry)
            if match is not None:
                names.add(match.group("unit"))
        return tuple(sorted(names))
    except (KeyboardInterrupt, SystemExit, MemoryError):
        raise
    except OSError as error:
        raise TrustedManagedPathError("systemd unit 目录无法安全枚举") from error
    finally:
        _close_quietly(descriptor)


__all__ = [
    "LegacyReleaseDropInSnapshot",
    "SystemdInstallTransactionError",
    "TrustedManagedPathError",
    "restore_legacy_release_dropins",
    "retire_legacy_release_dropins",
    "snapshot_legacy_release_dropins",
    "verify_legacy_release_dropins_retired",
]
