"""CodeGraph systemd 主 unit 布局迁移的无启停事务。"""
from __future__ import annotations

import os
import sys
from collections.abc import Callable
from contextlib import AbstractContextManager
from dataclasses import dataclass
from pathlib import Path
from typing import NoReturn

from codev_platform.mcp_systemd_install_contract import (
    SystemdInstallTransactionError,
    SystemdUnitPayload,
)
from codev_platform.mcp_systemd_install_input import VerifiedInstallInput
from codev_platform.ops.reindex_codegraph_resume_managed_path import (
    RootOwnedRegularFileSnapshot,
)
from codev_platform.ops.systemd_unit_layout_contract import (
    _CANONICAL_UNIT_DIRECTORY,
    _CODEGRAPH_UNIT,
    _LEGACY_UNIT_DIRECTORY,
    SystemdUnitLayoutMigrationError,
    SystemdUnitRuntime,
    UnitLayoutMigrationPorts,
)
from codev_platform.ops.systemd_unit_layout_payload import select_fixed_codegraph_payload


_MASKED_UNIT_FILE_STATES = frozenset({"masked", "masked-runtime"})


@dataclass(frozen=True, slots=True)
class _PreparedMigration:
    """单一固定 unit 的双路径原像、目标内容与运行态基线。"""

    payload: SystemdUnitPayload
    legacy_path: Path
    canonical_path: Path
    legacy_snapshot: RootOwnedRegularFileSnapshot | None
    canonical_snapshot: RootOwnedRegularFileSnapshot | None
    runtime: SystemdUnitRuntime

    @property
    def expected_canonical_snapshot(self) -> RootOwnedRegularFileSnapshot:
        """canonical 主 unit 必须精确绑定本次 manifest 的内容、权限和属主。"""
        return RootOwnedRegularFileSnapshot(
            content=self.payload.content,
            mode=0o644,
            uid=0,
            gid=0,
        )

    @property
    def can_restore_legacy_layout(self) -> bool:
        """只有本次仍持有 legacy 完整原像时，失败后才能承诺回滚到原优先级。"""
        return self.legacy_snapshot is not None

    @property
    def legacy_quarantine_path(self) -> Path:
        """成功迁移保留旧主 unit 的 root-only 审计副本，不再占用 systemd 主 unit 名。"""
        return self.legacy_path.with_name(f".{self.legacy_path.name}.codev-layout-backup")

    @property
    def canonical_rollback_path(self) -> Path:
        """补偿时隔离本次新建 canonical，避免条件删除覆盖外部替换。"""
        return self.canonical_path.with_name(
            f".{self.canonical_path.name}.codev-layout-rollback"
        )


@dataclass(slots=True)
class _MutationJournal:
    """仅记录本次已完成的可逆叶子移动，补偿不得猜测未发生的写入。"""

    canonical_create_attempted: bool = False
    canonical_created_snapshot: RootOwnedRegularFileSnapshot | None = None
    legacy_move_attempted: bool = False
    legacy_moved: bool = False


def migrate_from_manifest(
    manifest_path: Path,
    *,
    ports: UnitLayoutMigrationPorts | None = None,
    platform_name: str | None = None,
    effective_user_id: Callable[[], int] | None = None,
) -> None:
    """从受信 manifest 读取固定 CodeGraph payload，并执行无启停布局切换。"""
    _require_linux_root(platform_name, effective_user_id)
    selected_ports = _default_ports() if ports is None else ports
    _require_ports(selected_ports)
    with _migration_lock(selected_ports):
        try:
            install_input = selected_ports.load_verified_input(Path(manifest_path))
        except (KeyboardInterrupt, SystemExit, MemoryError):
            raise
        except (SystemdInstallTransactionError, SystemdUnitLayoutMigrationError):
            raise SystemdUnitLayoutMigrationError("systemd 布局迁移输入无法证明") from None
        except Exception as error:
            raise SystemdUnitLayoutMigrationError("systemd 布局迁移输入无法证明") from error
        _migrate_locked(install_input, selected_ports)


def migrate_verified_input(
    install_input: VerifiedInstallInput,
    *,
    ports: UnitLayoutMigrationPorts,
    platform_name: str | None = None,
    effective_user_id: Callable[[], int] | None = None,
) -> None:
    """测试与受控内存调用入口，仍强制 root/Linux 与维护转换锁。"""
    _require_linux_root(platform_name, effective_user_id)
    _require_ports(ports)
    with _migration_lock(ports):
        _migrate_locked(install_input, ports)


def select_fixed_payloads(
    install_input: VerifiedInstallInput,
    *,
    expected_python: str | Path | None = None,
) -> SystemdUnitPayload:
    """兼容旧调用方：选择唯一固定 CodeGraph payload 并校验运行时绑定。"""
    return select_fixed_codegraph_payload(install_input, expected_python=expected_python)


def _default_ports() -> UnitLayoutMigrationPorts:
    """延迟载入 Linux 适配器，避免事务核心反向依赖运行环境。"""
    from codev_platform.ops.systemd_unit_layout_systemd import default_ports

    return default_ports()


def _migration_lock(ports: UnitLayoutMigrationPorts) -> AbstractContextManager[None]:
    try:
        lock = ports.transition_lock()
    except (KeyboardInterrupt, SystemExit, MemoryError):
        raise
    except Exception as error:
        raise SystemdUnitLayoutMigrationError("无法取得 systemd 布局迁移独占锁") from error
    if not hasattr(lock, "__enter__") or not hasattr(lock, "__exit__"):
        raise SystemdUnitLayoutMigrationError("systemd 布局迁移独占锁无效")
    return _checked_lock(lock, ports.gate_active)


class _checked_lock(AbstractContextManager[None]):
    """在已创建的共享转换锁内确认维护 marker 未激活。"""

    def __init__(self, lock: AbstractContextManager[None], gate_active: Callable[[], bool]) -> None:
        self._lock = lock
        self._gate_active = gate_active

    def __enter__(self) -> None:
        self._lock.__enter__()
        try:
            if self._gate_active() is not False:
                raise SystemdUnitLayoutMigrationError("reindex 维护门禁正在生效")
        except BaseException:
            self._lock.__exit__(*sys.exc_info())
            raise
        return None

    def __exit__(self, *arguments: object) -> bool | None:
        return self._lock.__exit__(*arguments)


def _migrate_locked(install_input: VerifiedInstallInput, ports: UnitLayoutMigrationPorts) -> None:
    try:
        expected_python = ports.expected_codegraph_python()
    except (KeyboardInterrupt, SystemExit, MemoryError):
        raise
    except Exception as error:
        raise SystemdUnitLayoutMigrationError("CodeGraph 目标解释器无法证明") from error
    payload = select_fixed_payloads(install_input, expected_python=expected_python)
    _clear_inconsistent_runtime_mask(ports)
    prepared = _prepare_migration(payload, ports)
    if not _needs_finalization(prepared, ports):
        _verify_migrated(prepared, ports)
        return
    journal = _MutationJournal()
    try:
        _apply_file_switch(prepared, journal, ports)
        _verify_migrated(prepared, ports)
    except (KeyboardInterrupt, SystemExit, MemoryError):
        _raise_after_compensation(prepared, journal, ports, sys.exc_info()[1])
        raise
    except BaseException as error:
        _raise_after_compensation(prepared, journal, ports, error)
        raise SystemdUnitLayoutMigrationError("systemd 布局迁移失败") from error


def _clear_inconsistent_runtime_mask(ports: UnitLayoutMigrationPorts) -> None:
    """只清理由旧优先级缺陷留下、且已证明没有生效的 `/run -> /dev/null`。"""
    from codev_platform.core.systemd_unit_resolution import (
        RuntimeMaskState,
        SystemdUnitResolution,
        SystemdUnitResolutionError,
        classify_runtime_mask,
    )

    target = _read_runtime_mask_target(ports)
    if target is None:
        return
    runtime = _read_runtime(ports, _CODEGRAPH_UNIT)
    try:
        state = classify_runtime_mask(
            SystemdUnitResolution(
                unit_name=_CODEGRAPH_UNIT,
                load_state=runtime.load_state,
                unit_file_state=runtime.unit_file_state,
                fragment_path=runtime.fragment_path,
            ),
            runtime_mask_target=target,
        )
    except SystemdUnitResolutionError as error:
        raise SystemdUnitLayoutMigrationError("CodeGraph runtime mask 状态无法证明") from error
    if state is not RuntimeMaskState.INCONSISTENT_RUNTIME_ARTIFACT or target != "/dev/null":
        raise SystemdUnitLayoutMigrationError("CodeGraph runtime mask 状态不允许布局迁移")
    _run_systemctl(ports, ("systemctl", "unmask", "--runtime", _CODEGRAPH_UNIT))
    _run_systemctl(ports, ("systemctl", "daemon-reload"))
    if _read_runtime_mask_target(ports) is not None:
        raise SystemdUnitLayoutMigrationError("CodeGraph 无效 runtime mask 无法清除")
    runtime = _read_runtime(ports, _CODEGRAPH_UNIT)
    if runtime.load_state == "masked" or runtime.unit_file_state in _MASKED_UNIT_FILE_STATES:
        raise SystemdUnitLayoutMigrationError("CodeGraph runtime mask 清除后状态无法证明")


def _prepare_migration(
    payload: SystemdUnitPayload,
    ports: UnitLayoutMigrationPorts,
) -> _PreparedMigration:
    legacy_path = _LEGACY_UNIT_DIRECTORY / payload.spec.unit_name
    canonical_path = _CANONICAL_UNIT_DIRECTORY / payload.spec.unit_name
    prepared = _PreparedMigration(
        payload=payload,
        legacy_path=legacy_path,
        canonical_path=canonical_path,
        legacy_snapshot=_read_snapshot(ports, legacy_path),
        canonical_snapshot=_read_snapshot(ports, canonical_path),
        runtime=_read_runtime(ports, payload.spec.unit_name),
    )
    _require_migratable_layout(prepared)
    _require_no_stale_isolation_artifacts(prepared, ports)
    return prepared


def _require_migratable_layout(prepared: _PreparedMigration) -> None:
    runtime = prepared.runtime
    if runtime.load_state != "loaded" or runtime.unit_file_state not in {"enabled", "disabled"}:
        raise SystemdUnitLayoutMigrationError("CodeGraph systemd 前态不可安全迁移")
    legacy = prepared.legacy_snapshot
    canonical = prepared.canonical_snapshot
    if legacy is None and canonical is None:
        raise SystemdUnitLayoutMigrationError("CodeGraph 主 unit 原像缺失")
    if legacy is not None:
        if runtime.fragment_path != str(prepared.legacy_path):
            raise SystemdUnitLayoutMigrationError("CodeGraph legacy unit 解析路径无法证明")
        if canonical is not None and not _snapshot_matches(
            canonical,
            prepared.expected_canonical_snapshot,
        ):
            raise SystemdUnitLayoutMigrationError("CodeGraph 双主文件 canonical 内容未知")
        return
    if not _snapshot_matches(canonical, prepared.expected_canonical_snapshot):
        raise SystemdUnitLayoutMigrationError("CodeGraph canonical unit 与当前 manifest 不一致")
    if runtime.fragment_path not in {str(prepared.canonical_path), str(prepared.legacy_path)}:
        raise SystemdUnitLayoutMigrationError("CodeGraph canonical unit 解析路径无法证明")


def _require_no_stale_isolation_artifacts(
    prepared: _PreparedMigration,
    ports: UnitLayoutMigrationPorts,
) -> None:
    """再次移动 legacy 前拒绝固定隔离名残留，避免覆盖上次失败留下的证据。"""
    if prepared.legacy_snapshot is None:
        return
    if (
        _read_snapshot(ports, prepared.legacy_quarantine_path) is not None
        or _read_snapshot(ports, prepared.canonical_rollback_path) is not None
    ):
        raise SystemdUnitLayoutMigrationError("CodeGraph 布局隔离叶子残留，拒绝再次迁移")


def _needs_finalization(
    prepared: _PreparedMigration,
    ports: UnitLayoutMigrationPorts,
) -> bool:
    """识别可证明的中断阶段；未知 enable 链接绝不以重接覆盖。"""
    if (
        prepared.legacy_snapshot is not None
        or prepared.runtime.fragment_path != str(prepared.canonical_path)
    ):
        return True
    if prepared.runtime.unit_file_state != "enabled":
        return False
    target = _read_enable_link(ports, _CODEGRAPH_UNIT)
    if target == str(prepared.canonical_path):
        return False
    if target == str(prepared.legacy_path):
        return True
    raise SystemdUnitLayoutMigrationError("CodeGraph enable 链接状态无法恢复")


def _apply_file_switch(
    prepared: _PreparedMigration,
    journal: _MutationJournal,
    ports: UnitLayoutMigrationPorts,
) -> None:
    """先条件首建 canonical，再把 legacy 移至隔离名，绝不覆盖或按名删除。"""
    if prepared.legacy_snapshot is not None and prepared.canonical_snapshot is None:
        journal.canonical_create_attempted = True
        _create_if_absent(ports, prepared.canonical_path, prepared.expected_canonical_snapshot)
        created = _read_snapshot(ports, prepared.canonical_path)
        if not _snapshot_matches(created, prepared.expected_canonical_snapshot):
            raise SystemdUnitLayoutMigrationError("新建 CodeGraph canonical 主 unit 原像无法证明")
        journal.canonical_created_snapshot = created
    if prepared.legacy_snapshot is not None:
        journal.legacy_move_attempted = True
        _move_if_snapshot(
            ports,
            prepared.legacy_path,
            prepared.legacy_quarantine_path,
            prepared.legacy_snapshot,
        )
        journal.legacy_moved = True
    _run_systemctl(ports, ("systemctl", "daemon-reload"))
    if prepared.runtime.unit_file_state == "enabled":
        _run_systemctl(ports, ("systemctl", "reenable", _CODEGRAPH_UNIT))


def _verify_migrated(prepared: _PreparedMigration, ports: UnitLayoutMigrationPorts) -> None:
    if _read_snapshot(ports, prepared.legacy_path) is not None:
        raise SystemdUnitLayoutMigrationError("CodeGraph legacy 主 unit 仍存在")
    if not _snapshot_matches(
        _read_snapshot(ports, prepared.canonical_path),
        prepared.expected_canonical_snapshot,
    ):
        raise SystemdUnitLayoutMigrationError("CodeGraph canonical 主 unit 内容无法证明")
    runtime = _read_runtime(ports, _CODEGRAPH_UNIT)
    if (
        runtime.fragment_path != str(prepared.canonical_path)
        or runtime.without_fragment() != prepared.runtime.without_fragment()
    ):
        raise SystemdUnitLayoutMigrationError("CodeGraph 主 unit 迁移后运行状态漂移")
    if prepared.runtime.unit_file_state == "enabled":
        if _read_enable_link(ports, _CODEGRAPH_UNIT) != str(prepared.canonical_path):
            raise SystemdUnitLayoutMigrationError("CodeGraph enable 链接未指向 canonical unit")


def _raise_after_compensation(
    prepared: _PreparedMigration,
    journal: _MutationJournal,
    ports: UnitLayoutMigrationPorts,
    error: BaseException | None,
) -> NoReturn:
    if not prepared.can_restore_legacy_layout:
        raise SystemdUnitLayoutMigrationError("systemd 布局迁移恢复阶段失败；安全状态未证明") from error
    try:
        _compensate(prepared, journal, ports)
    except (KeyboardInterrupt, SystemExit, MemoryError):
        raise
    except Exception as compensation_error:
        raise SystemdUnitLayoutMigrationError("systemd 布局迁移失败；安全状态未证明") from compensation_error
    raise SystemdUnitLayoutMigrationError("systemd 布局迁移失败；已回滚双路径原像") from error


def _compensate(
    prepared: _PreparedMigration,
    journal: _MutationJournal,
    ports: UnitLayoutMigrationPorts,
) -> None:
    """只回移本次已证明移动的叶子；未知替换一律留下证据并失败。"""
    if _legacy_was_moved_for_compensation(prepared, journal, ports):
        assert prepared.legacy_snapshot is not None
        _move_if_snapshot(
            ports,
            prepared.legacy_quarantine_path,
            prepared.legacy_path,
            prepared.legacy_snapshot,
        )
    created = _created_canonical_snapshot_for_compensation(prepared, journal, ports)
    if created is not None:
        _move_if_snapshot(
            ports,
            prepared.canonical_path,
            prepared.canonical_rollback_path,
            created,
        )
    _run_systemctl(ports, ("systemctl", "daemon-reload"))
    if prepared.runtime.unit_file_state == "enabled":
        _run_systemctl(ports, ("systemctl", "reenable", _CODEGRAPH_UNIT))
    _verify_rollback(prepared, ports)


def _created_canonical_snapshot_for_compensation(
    prepared: _PreparedMigration,
    journal: _MutationJournal,
    ports: UnitLayoutMigrationPorts,
) -> RootOwnedRegularFileSnapshot | None:
    """创建返回异常时不猜测叶子身份；已落盘 canonical 必须保留为失败证据。"""
    if journal.canonical_created_snapshot is not None:
        return journal.canonical_created_snapshot
    if not journal.canonical_create_attempted:
        return None
    created = _read_snapshot(ports, prepared.canonical_path)
    if created is None:
        return None
    raise SystemdUnitLayoutMigrationError("新建 CodeGraph canonical 主 unit 身份无法证明")


def _legacy_was_moved_for_compensation(
    prepared: _PreparedMigration,
    journal: _MutationJournal,
    ports: UnitLayoutMigrationPorts,
) -> bool:
    """移动报告异常时以原始 inode 探测 backup，避免遗漏已改名的 legacy 叶子。"""
    if journal.legacy_moved:
        return True
    if not journal.legacy_move_attempted:
        return False
    assert prepared.legacy_snapshot is not None
    quarantine = _read_snapshot(ports, prepared.legacy_quarantine_path)
    if quarantine is not None:
        if quarantine.matches(prepared.legacy_snapshot, require_identity=True):
            return True
        raise SystemdUnitLayoutMigrationError("CodeGraph legacy 隔离原像无法证明")
    legacy = _read_snapshot(ports, prepared.legacy_path)
    if legacy is not None and legacy.matches(prepared.legacy_snapshot, require_identity=True):
        return False
    raise SystemdUnitLayoutMigrationError("CodeGraph legacy 移动结果无法证明")


def _verify_rollback(prepared: _PreparedMigration, ports: UnitLayoutMigrationPorts) -> None:
    if not _snapshot_matches(
        _read_snapshot(ports, prepared.legacy_path),
        prepared.legacy_snapshot,
    ):
        raise SystemdUnitLayoutMigrationError("CodeGraph legacy 原像回滚无法证明")
    if not _snapshot_matches(
        _read_snapshot(ports, prepared.canonical_path),
        prepared.canonical_snapshot,
    ):
        raise SystemdUnitLayoutMigrationError("CodeGraph canonical 原像回滚无法证明")
    runtime = _read_runtime(ports, _CODEGRAPH_UNIT)
    if (
        runtime.fragment_path != prepared.runtime.fragment_path
        or runtime.without_fragment() != prepared.runtime.without_fragment()
    ):
        raise SystemdUnitLayoutMigrationError("CodeGraph 运行状态回滚无法证明")
    if prepared.runtime.unit_file_state == "enabled":
        if _read_enable_link(ports, _CODEGRAPH_UNIT) != str(prepared.legacy_path):
            raise SystemdUnitLayoutMigrationError("CodeGraph enable 链接回滚无法证明")


def _require_linux_root(
    platform_name: str | None,
    effective_user_id: Callable[[], int] | None,
) -> None:
    platform = sys.platform if platform_name is None else platform_name
    if type(platform) is not str or not platform.startswith("linux"):
        raise SystemdUnitLayoutMigrationError("当前平台不支持 systemd 主 unit 布局迁移")
    reader = os.geteuid if effective_user_id is None else effective_user_id
    try:
        if not callable(reader) or reader() != 0:
            raise SystemdUnitLayoutMigrationError("systemd 主 unit 布局迁移必须由 root 执行")
    except SystemdUnitLayoutMigrationError:
        raise
    except (KeyboardInterrupt, SystemExit, MemoryError):
        raise
    except Exception as error:
        raise SystemdUnitLayoutMigrationError("systemd 主 unit 布局迁移身份无法证明") from error


def _require_ports(ports: UnitLayoutMigrationPorts) -> None:
    if not isinstance(ports, UnitLayoutMigrationPorts):
        raise SystemdUnitLayoutMigrationError("systemd 布局迁移适配器无效")
    values = (
        ports.transition_lock,
        ports.gate_active,
        ports.read_snapshot,
        ports.create_if_absent,
        ports.move_if_snapshot,
        ports.read_runtime,
        ports.systemctl,
        ports.read_enable_link,
        ports.read_runtime_mask_target,
        ports.expected_codegraph_python,
        ports.load_verified_input,
    )
    if not all(callable(value) for value in values):
        raise SystemdUnitLayoutMigrationError("systemd 布局迁移适配器不可用")


def _read_snapshot(
    ports: UnitLayoutMigrationPorts,
    path: Path,
) -> RootOwnedRegularFileSnapshot | None:
    try:
        snapshot = ports.read_snapshot(path)
    except (KeyboardInterrupt, SystemExit, MemoryError):
        raise
    except Exception as error:
        raise SystemdUnitLayoutMigrationError("systemd 主 unit 原像无法读取") from error
    if snapshot is not None and not isinstance(snapshot, RootOwnedRegularFileSnapshot):
        raise SystemdUnitLayoutMigrationError("systemd 主 unit 原像无效")
    return snapshot


def _create_if_absent(
    ports: UnitLayoutMigrationPorts,
    path: Path,
    snapshot: RootOwnedRegularFileSnapshot,
) -> None:
    try:
        ports.create_if_absent(path, snapshot)
    except (KeyboardInterrupt, SystemExit, MemoryError):
        raise
    except Exception as error:
        raise SystemdUnitLayoutMigrationError("systemd canonical unit 无法安全写入") from error


def _move_if_snapshot(
    ports: UnitLayoutMigrationPorts,
    source: Path,
    destination: Path,
    snapshot: RootOwnedRegularFileSnapshot,
) -> None:
    try:
        ports.move_if_snapshot(source, destination, snapshot)
    except (KeyboardInterrupt, SystemExit, MemoryError):
        raise
    except Exception as error:
        raise SystemdUnitLayoutMigrationError("systemd legacy unit 原像已变化") from error


def _snapshot_matches(
    current: RootOwnedRegularFileSnapshot | None,
    expected: RootOwnedRegularFileSnapshot | None,
) -> bool:
    if current is None or expected is None:
        return current is expected
    return current.matches(expected, require_identity=False)


def _read_runtime(ports: UnitLayoutMigrationPorts, name: str) -> SystemdUnitRuntime:
    try:
        runtime = ports.read_runtime(name)
    except (KeyboardInterrupt, SystemExit, MemoryError):
        raise
    except Exception as error:
        raise SystemdUnitLayoutMigrationError("systemd unit 运行状态无法读取") from error
    if not isinstance(runtime, SystemdUnitRuntime):
        raise SystemdUnitLayoutMigrationError("systemd unit 运行状态无效")
    return runtime


def _run_systemctl(ports: UnitLayoutMigrationPorts, command: tuple[str, ...]) -> None:
    try:
        ports.systemctl(command)
    except (KeyboardInterrupt, SystemExit, MemoryError):
        raise
    except Exception as error:
        raise SystemdUnitLayoutMigrationError("systemd 布局迁移命令失败") from error


def _read_enable_link(ports: UnitLayoutMigrationPorts, name: str) -> str:
    try:
        target = ports.read_enable_link(name)
    except (KeyboardInterrupt, SystemExit, MemoryError):
        raise
    except Exception as error:
        raise SystemdUnitLayoutMigrationError("systemd enable 链接无法证明") from error
    if type(target) is not str or not Path(target).is_absolute() or "\x00" in target:
        raise SystemdUnitLayoutMigrationError("systemd enable 链接无效")
    return target


def _read_runtime_mask_target(ports: UnitLayoutMigrationPorts) -> str | None:
    try:
        target = ports.read_runtime_mask_target()
    except (KeyboardInterrupt, SystemExit, MemoryError):
        raise
    except Exception as error:
        raise SystemdUnitLayoutMigrationError("CodeGraph runtime mask 状态无法读取") from error
    if target is not None and (type(target) is not str or "\x00" in target):
        raise SystemdUnitLayoutMigrationError("CodeGraph runtime mask 状态无效")
    return target


__all__ = ["migrate_from_manifest", "migrate_verified_input", "select_fixed_payloads"]
