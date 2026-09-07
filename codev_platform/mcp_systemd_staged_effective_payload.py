"""恢复阶段受保护 systemd unit 的有效载荷组合验证。"""

from __future__ import annotations

from pathlib import PurePosixPath

from codev_platform.core.systemd_maintenance_contract import (
    CODEGRAPH_MAINTENANCE_GUARD_DROPIN_CONTENT,
    CODEGRAPH_MAINTENANCE_GUARD_DROPIN_PATH,
)
from codev_platform.mcp_systemd_effective_payload import (
    DropInSnapshotReader,
    ExpectedSystemdDropIn,
    InstalledUnitPathResolver,
    SnapshotReader,
    SystemctlShowRunner,
    verify_effective_unit_payloads,
)
from codev_platform.mcp_systemd_install_contract import (
    REINDEX_SYSTEMD_UNIT_NAME,
    SystemdInstallTransactionError,
    SystemdUnitFileSnapshot,
    SystemdUnitPayload,
)
from codev_platform.ops.reindex_codegraph_resume_contract import (
    CODEGRAPH_RESUME_DROPIN,
    CODEGRAPH_STARTUP_BRIDGE_DROPIN,
    CODEGRAPH_UNIT,
    REINDEX_RESUME_DROPIN,
)
from codev_platform.runtime_systemd_gate_contract import (
    DEPLOYMENT_GUARD_DROP_IN_CONTENT,
    deployment_guard_drop_in_path,
)


def verify_staged_effective_unit_payloads(
    payloads: tuple[SystemdUnitPayload, ...],
    *,
    resume_dropin_content: bytes,
    allow_reindex_local_dropins: bool,
    codegraph_startup_bridge_dropin_content: bytes | None = None,
    codegraph_effective_exec_start: tuple[str, ...] | None = None,
    snapshot_reader: SnapshotReader,
    systemctl_show: SystemctlShowRunner,
    installed_path: InstalledUnitPathResolver,
    drop_in_snapshot_reader: DropInSnapshotReader,
) -> None:
    """证明恢复期有效载荷；M1 仅保留 reindex 的既有本地运行覆盖。"""
    if type(allow_reindex_local_dropins) is not bool:
        raise SystemdInstallTransactionError("reindex 本地 drop-in 策略无效")
    bridge_content, bridge_exec_start = _require_startup_bridge_contract(
        codegraph_startup_bridge_dropin_content,
        codegraph_effective_exec_start,
    )
    expected = _expected_drop_ins(
        resume_dropin_content,
        allow_reindex_local_dropins=allow_reindex_local_dropins,
        codegraph_startup_bridge_dropin_content=bridge_content,
    )
    verify_effective_unit_payloads(
        payloads,
        snapshot_reader=snapshot_reader,
        systemctl_show=systemctl_show,
        installed_path=installed_path,
        expected_drop_ins=expected,
        expected_effective_exec_starts=(
            {} if bridge_exec_start is None else {CODEGRAPH_UNIT: bridge_exec_start}
        ),
        drop_in_snapshot_reader=drop_in_snapshot_reader,
    )


def _expected_drop_ins(
    resume_dropin_content: bytes,
    *,
    allow_reindex_local_dropins: bool,
    codegraph_startup_bridge_dropin_content: bytes | None,
) -> dict[str, tuple[ExpectedSystemdDropIn, ...]]:
    resume_snapshot = SystemdUnitFileSnapshot(
        content=resume_dropin_content,
        mode=0o644,
        uid=0,
        gid=0,
    )
    deployment_guard_snapshot = SystemdUnitFileSnapshot(
        content=DEPLOYMENT_GUARD_DROP_IN_CONTENT,
        mode=0o644,
        uid=0,
        gid=0,
    )
    codegraph_guard_snapshot = SystemdUnitFileSnapshot(
        content=CODEGRAPH_MAINTENANCE_GUARD_DROPIN_CONTENT,
        mode=0o644,
        uid=0,
        gid=0,
    )
    codegraph_dropins = [
        ExpectedSystemdDropIn(
            deployment_guard_drop_in_path(CODEGRAPH_UNIT),
            deployment_guard_snapshot,
        ),
    ]
    if codegraph_startup_bridge_dropin_content is not None:
        codegraph_dropins.append(
            ExpectedSystemdDropIn(
                PurePosixPath(CODEGRAPH_STARTUP_BRIDGE_DROPIN.as_posix()),
                SystemdUnitFileSnapshot(
                    content=codegraph_startup_bridge_dropin_content,
                    mode=0o644,
                    uid=0,
                    gid=0,
                ),
            )
        )
    codegraph_dropins.extend(
        (
            ExpectedSystemdDropIn(
                PurePosixPath(CODEGRAPH_RESUME_DROPIN.as_posix()),
                resume_snapshot,
            ),
            ExpectedSystemdDropIn(
                PurePosixPath(CODEGRAPH_MAINTENANCE_GUARD_DROPIN_PATH.as_posix()),
                codegraph_guard_snapshot,
            ),
        )
    )
    expected = {
        CODEGRAPH_UNIT: tuple(codegraph_dropins),
    }
    if allow_reindex_local_dropins:
        return expected
    expected[REINDEX_SYSTEMD_UNIT_NAME] = (
        ExpectedSystemdDropIn(
            deployment_guard_drop_in_path(REINDEX_SYSTEMD_UNIT_NAME),
            deployment_guard_snapshot,
        ),
        ExpectedSystemdDropIn(
            PurePosixPath(REINDEX_RESUME_DROPIN.as_posix()),
            resume_snapshot,
        ),
    )
    return expected


def _require_startup_bridge_contract(
    content: bytes | None,
    exec_start: tuple[str, ...] | None,
) -> tuple[bytes | None, tuple[str, ...] | None]:
    if content is None and exec_start is None:
        return None, None
    if (
        type(content) is not bytes
        or not content
        or len(content) > 128 * 1024
        or type(exec_start) is not tuple
        or not exec_start
    ):
        raise SystemdInstallTransactionError("CodeGraph 启动 bridge 契约无效")
    return content, exec_start


__all__ = ["verify_staged_effective_unit_payloads"]
