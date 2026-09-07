"""旧 release systemd drop-in 的固定路径与条件退役测试。"""

from __future__ import annotations

from dataclasses import replace
from pathlib import Path

import pytest


def _原像(content: bytes = b"[Service]\nExecStart=/old/python\n", *, inode: int = 11):
    from codev_platform.ops.reindex_codegraph_resume_managed_path import (
        RootOwnedRegularFileSnapshot,
    )

    return RootOwnedRegularFileSnapshot(
        content=content,
        mode=0o640,
        uid=0,
        gid=123,
        device=7,
        inode=inode,
    )


def _影子路径(root: Path, unit_name: str) -> tuple[Path, Path]:
    directory = root / f"{unit_name}.d"
    return (
        directory / "90-codev-release.conf",
        directory / "90-codev-release.conf.codev-retired",
    )


def _内存文件系统(monkeypatch: pytest.MonkeyPatch, module, root: Path, files: dict[Path, object]):
    moves: list[tuple[Path, Path, object]] = []
    monkeypatch.setattr(module, "_SYSTEMD_UNIT_DIRECTORY", root)
    monkeypatch.setattr(module, "_read_shadow_file", lambda path: files.get(path))
    monkeypatch.setattr(
        module,
        "_discover_shadow_unit_names",
        lambda: tuple(
            sorted(
                {
                    path.parent.name.removesuffix(".d")
                    for path in files
                    if path.name == "90-codev-release.conf"
                }
            )
        ),
    )

    def 条件移动(source: Path, destination: Path, expected: object) -> None:
        moves.append((source, destination, expected))
        if files.get(source) != expected or destination in files:
            raise module.TrustedManagedPathError("受管路径原像已变化")
        files[destination] = files.pop(source)

    monkeypatch.setattr(module, "_move_shadow_file", 条件移动)
    return moves


def test_shadow缺失时退役恢复与证明均为空操作(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    from codev_platform import mcp_systemd_release_shadow as module

    root = tmp_path.resolve()
    files: dict[Path, object] = {}
    moves = _内存文件系统(monkeypatch, module, root, files)

    snapshots = module.snapshot_legacy_release_dropins(("codev-a.service",))

    assert len(snapshots) == 1
    assert snapshots[0].active_snapshot is None
    assert snapshots[0].archive_snapshot is None
    module.retire_legacy_release_dropins(snapshots)
    module.verify_legacy_release_dropins_retired(snapshots)
    module.restore_legacy_release_dropins(snapshots)
    assert moves == []


def test_活动shadow按固定归档名和完整inode条件退役(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    from codev_platform import mcp_systemd_release_shadow as module

    root = tmp_path.resolve()
    active, archive = _影子路径(root, "codev-a.service")
    original = _原像()
    files: dict[Path, object] = {active: original}
    moves = _内存文件系统(monkeypatch, module, root, files)

    snapshots = module.snapshot_legacy_release_dropins(("codev-a.service",))
    module.retire_legacy_release_dropins(snapshots)
    module.verify_legacy_release_dropins_retired(snapshots)

    assert snapshots == (
        module.LegacyReleaseDropInSnapshot(
            unit_name="codev-a.service",
            active_path=active,
            archive_path=archive,
            active_snapshot=original,
            archive_snapshot=None,
        ),
    )
    assert moves == [(active, archive, original)]
    assert files == {archive: original}


def test_已归档shadow再次安装保持幂等(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    from codev_platform import mcp_systemd_release_shadow as module

    root = tmp_path.resolve()
    active, archive = _影子路径(root, "codev-a.service")
    original = _原像()
    files: dict[Path, object] = {archive: original}
    moves = _内存文件系统(monkeypatch, module, root, files)

    snapshots = module.snapshot_legacy_release_dropins(("codev-a.service",))
    module.retire_legacy_release_dropins(snapshots)
    module.verify_legacy_release_dropins_retired(snapshots)
    module.restore_legacy_release_dropins(snapshots)

    assert snapshots[0].active_snapshot is None
    assert snapshots[0].archive_snapshot == original
    assert files == {archive: original}
    assert moves == []


def test_活动与归档shadow同时存在时失败关闭(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    from codev_platform import mcp_systemd_release_shadow as module

    root = tmp_path.resolve()
    active, archive = _影子路径(root, "codev-a.service")
    files = {active: _原像(), archive: _原像(inode=12)}
    _内存文件系统(monkeypatch, module, root, files)

    with pytest.raises(module.SystemdInstallTransactionError, match="同时存在"):
        module.snapshot_legacy_release_dropins(("codev-a.service",))


@pytest.mark.parametrize("reason", ("符号链接", "非 root 文件", "父目录不安全"))
def test_不受信路径无法冻结原像(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    reason: str,
) -> None:
    from codev_platform import mcp_systemd_release_shadow as module

    monkeypatch.setattr(module, "_SYSTEMD_UNIT_DIRECTORY", tmp_path.resolve())
    monkeypatch.setattr(module, "_discover_shadow_unit_names", lambda: ())

    def 拒绝读取(_path: Path):
        raise module.TrustedManagedPathError(reason)

    monkeypatch.setattr(module, "_read_shadow_file", 拒绝读取)

    with pytest.raises(module.SystemdInstallTransactionError, match="不受信任"):
        module.snapshot_legacy_release_dropins(("codev-a.service",))


def test_manifest外活动shadow在任何移动前被拒绝(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    from codev_platform import mcp_systemd_release_shadow as module

    root = tmp_path.resolve()
    managed_active, _managed_archive = _影子路径(root, "codev-a.service")
    foreign_active, _foreign_archive = _影子路径(root, "codev-foreign.service")
    files = {managed_active: _原像(), foreign_active: _原像(inode=22)}
    moves = _内存文件系统(monkeypatch, module, root, files)

    with pytest.raises(module.SystemdInstallTransactionError, match="manifest 外"):
        module.snapshot_legacy_release_dropins(("codev-a.service",))

    assert moves == []
    assert foreign_active in files


def test_仅有非受管socket_dropin目录时不阻断安装(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """枚举范围必须与 manifest 允许的 service/timer 类型严格一致。"""
    from codev_platform import mcp_systemd_release_shadow as module

    root = tmp_path.resolve()
    monkeypatch.setattr(module, "_SYSTEMD_UNIT_DIRECTORY", root)
    monkeypatch.setattr(module, "_read_shadow_file", lambda _path: None)
    monkeypatch.setattr(
        module,
        "_open_trusted_parent",
        lambda *_args, **_kwargs: (17, ".codev-release-shadow-scan"),
    )
    monkeypatch.setattr(module.os, "listdir", lambda _descriptor: ["codev-x.socket.d"])
    monkeypatch.setattr(module, "_close_quietly", lambda _descriptor: None)

    snapshots = module.snapshot_legacy_release_dropins(("codev-a.service",))

    assert len(snapshots) == 1
    assert snapshots[0].unit_name == "codev-a.service"


def test_最终证明再次拒绝事务期间出现的manifest外shadow(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """准备后新增的外部覆盖不能凭 manifest 内文件已退役而漏过最终证明。"""
    from codev_platform import mcp_systemd_release_shadow as module

    root = tmp_path.resolve()
    active, _archive = _影子路径(root, "codev-a.service")
    foreign_active, _foreign_archive = _影子路径(root, "codev-foreign.service")
    files = {active: _原像()}
    _内存文件系统(monkeypatch, module, root, files)
    snapshots = module.snapshot_legacy_release_dropins(("codev-a.service",))
    module.retire_legacy_release_dropins(snapshots)
    files[foreign_active] = _原像(inode=22)

    with pytest.raises(module.SystemdInstallTransactionError, match="manifest 外"):
        module.verify_legacy_release_dropins_retired(snapshots)


def test_退役前inode变化时条件移动失败且不覆盖归档(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    from codev_platform import mcp_systemd_release_shadow as module

    root = tmp_path.resolve()
    active, archive = _影子路径(root, "codev-a.service")
    files = {active: _原像(inode=11)}
    _内存文件系统(monkeypatch, module, root, files)
    snapshots = module.snapshot_legacy_release_dropins(("codev-a.service",))
    files[active] = _原像(inode=12)

    with pytest.raises(module.SystemdInstallTransactionError, match="原像已变化"):
        module.retire_legacy_release_dropins(snapshots)

    assert archive not in files


def test_补偿把本事务归档的shadow按原inode条件移回(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    from codev_platform import mcp_systemd_release_shadow as module

    root = tmp_path.resolve()
    active, archive = _影子路径(root, "codev-a.service")
    original = _原像()
    files = {active: original}
    moves = _内存文件系统(monkeypatch, module, root, files)
    snapshots = module.snapshot_legacy_release_dropins(("codev-a.service",))
    module.retire_legacy_release_dropins(snapshots)

    module.restore_legacy_release_dropins(snapshots)

    assert moves == [(active, archive, original), (archive, active, original)]
    assert files == {active: original}


def test_伪造非固定路径的shadow原像被拒绝(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    from codev_platform import mcp_systemd_release_shadow as module

    root = tmp_path.resolve()
    active, _archive = _影子路径(root, "codev-a.service")
    files = {active: _原像()}
    _内存文件系统(monkeypatch, module, root, files)
    snapshot = module.snapshot_legacy_release_dropins(("codev-a.service",))[0]
    forged = replace(snapshot, archive_path=root / "任意归档名")

    with pytest.raises(module.SystemdInstallTransactionError, match="固定路径"):
        module.retire_legacy_release_dropins((forged,))
