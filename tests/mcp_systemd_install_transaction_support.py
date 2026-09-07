"""systemd 安装事务测试的共享构造与可观测端口。"""

from __future__ import annotations

import hashlib
from contextlib import contextmanager
from pathlib import Path


_CONTENT = b"[Service]\nExecStart=/usr/bin/true\n"
_TEST_RUNTIME_REVISION = "1" * 40
_TEST_RELEASE_ID = "2" * 64
_TEST_INTERPRETER = "/release/venv/bin/python"
_REINDEX_CONTENT = (
    "[Service]\n"
    f"ExecStart={_TEST_INTERPRETER} -I -m codev_platform.cli reindex-queue worker "
    "--require-execution-mode isolated\n"
).encode()
_CODEGRAPH_CONTENT = (
    "[Unit]\n"
    "ConditionPathExists=!/var/lib/codev-platform/codegraph-maintenance.gate\n"
    "[Service]\n"
    f"ExecStart={_TEST_INTERPRETER} -I -m codev_platform.codegraph.server "
    "--http --port 18091\n"
).encode()


def _内容(name: str) -> bytes:
    if name == "codev-reindex.service":
        return _REINDEX_CONTENT
    if name == "codev-mcp-codegraph.service":
        return _CODEGRAPH_CONTENT
    return _CONTENT


def _规格(module, tmp_path: Path, name: str, *, enable: bool = True, restart: bool = True):
    activation_mode = (
        module.SystemdUnitActivationMode.REINDEX_STATE_MACHINE
        if name == "codev-reindex.service"
        else module.SystemdUnitActivationMode.CODEGRAPH_STATE_MACHINE
        if name == "codev-mcp-codegraph.service"
        else module.SystemdUnitActivationMode.INGRESS_STATE_MACHINE
        if name == "codev-webhook.service"
        else module.SystemdUnitActivationMode.STANDARD
    )
    return module.SystemdUnitInstallSpec(
        source=(tmp_path / name).resolve(),
        content_digest=hashlib.sha256(_内容(name)).hexdigest(),
        enable=enable,
        restart=restart,
        activation_mode=activation_mode,
    )


def _清单(module, tmp_path: Path):
    return module.SystemdInstallManifest(
        units=(
            _规格(module, tmp_path, "codev-web.service"),
            _规格(module, tmp_path, "codev-agent.service"),
            _规格(module, tmp_path, "codev-clock-resync.timer"),
        ),
        runtime_revision=_TEST_RUNTIME_REVISION,
    )


def _维护清单(module, tmp_path: Path):
    return module.SystemdInstallManifest(
        units=(
            _规格(module, tmp_path, "codev-mcp-codegraph.service"),
            _规格(module, tmp_path, "codev-reindex.service"),
            _规格(module, tmp_path, "codev-webhook.service"),
            _规格(module, tmp_path, "codev-clock-resync.timer"),
        ),
        runtime_revision=_TEST_RUNTIME_REVISION,
    )


def _原像(module, content: bytes = b"old\n", *, mode: int = 0o640, gid: int = 123):
    return module.SystemdUnitFileSnapshot(content=content, mode=mode, uid=0, gid=gid)


def _端口(
    module,
    manifest,
    events: list[object],
    *,
    fail_command: str | None = None,
    installer_lock_factory=None,
    shadow_states: dict[str, str] | None = None,
    shadow_retire_fail_at: int | None = None,
    shadow_verify_failure: bool = False,
    shadow_restore_failure: str | None = None,
    effective_payload_failure: bool = False,
):
    files = {
        unit.unit_name: _原像(module, gid=index + 100) for index, unit in enumerate(manifest.units)
    }
    states = {
        unit.unit_name: module.SystemdUnitState(
            unit_file_state="disabled",
            active_state="inactive",
        )
        for unit in manifest.units
    }
    process_states = {
        unit.unit_name: module.SystemdUnitProcessState("inactive", 0, "") for unit in manifest.units
    }
    initial_files = dict(files)
    initial_states = dict(states)
    receipt = None
    current_shadow_states = (
        {unit.unit_name: "active" for unit in manifest.units}
        if shadow_states is None
        else shadow_states
    )

    def shadow原像(name: str):
        from codev_platform.mcp_systemd_release_shadow import LegacyReleaseDropInSnapshot
        from codev_platform.ops.reindex_codegraph_resume_managed_path import (
            RootOwnedRegularFileSnapshot,
        )

        directory = Path("/etc/systemd/system") / f"{name}.d"
        original = RootOwnedRegularFileSnapshot(
            content=f"shadow:{name}\n".encode(),
            mode=0o640,
            uid=0,
            gid=123,
            device=7,
            inode=1000 + tuple(unit.unit_name for unit in manifest.units).index(name),
        )
        state = current_shadow_states[name]
        return LegacyReleaseDropInSnapshot(
            unit_name=name,
            active_path=directory / "90-codev-release.conf",
            archive_path=directory / "90-codev-release.conf.codev-retired",
            active_snapshot=original if state == "active" else None,
            archive_snapshot=original if state == "retired" else None,
        )

    def 读取shadow原像(names: tuple[str, ...]):
        events.append(("读取shadow原像", names))
        return tuple(shadow原像(name) for name in names)

    def 退役shadow(snapshots) -> None:
        events.append(("退役shadow", tuple(item.unit_name for item in snapshots)))
        for index, snapshot in enumerate(snapshots, start=1):
            if shadow_retire_fail_at == index:
                raise RuntimeError(f"第 {index} 个 shadow 退役失败")
            if snapshot.active_snapshot is not None:
                current_shadow_states[snapshot.unit_name] = "retired"

    def 恢复shadow(snapshots) -> None:
        events.append(("恢复shadow", tuple(item.unit_name for item in snapshots)))
        for snapshot in snapshots:
            if shadow_restore_failure == snapshot.unit_name:
                raise RuntimeError("shadow 恢复失败")
            current_shadow_states[snapshot.unit_name] = (
                "active"
                if snapshot.active_snapshot is not None
                else "retired"
                if snapshot.archive_snapshot is not None
                else "absent"
            )

    def 验证shadow已退役(snapshots) -> None:
        events.append(("验证shadow退役", tuple(item.unit_name for item in snapshots)))
        if shadow_verify_failure:
            raise RuntimeError("shadow 最终证明失败")
        for snapshot in snapshots:
            expected = (
                "retired"
                if (snapshot.active_snapshot is not None or snapshot.archive_snapshot is not None)
                else "absent"
            )
            if current_shadow_states[snapshot.unit_name] != expected:
                raise RuntimeError("shadow 尚未退役")

    def 验证有效载荷(payloads) -> None:
        events.append(("验证有效载荷", tuple(item.spec.unit_name for item in payloads)))
        if effective_payload_failure:
            raise RuntimeError("systemd 有效载荷证明失败")

    @contextmanager
    def 默认安装锁():
        events.append("进入安装锁")
        try:
            yield
        finally:
            events.append("退出安装锁")

    def 读取源(source: Path) -> bytes:
        events.append(("读取源", source))
        return _内容(source.name)

    def 写入(name: str, content: bytes) -> None:
        events.append(("写入", name, content))
        files[name] = module.SystemdUnitFileSnapshot(content=content, mode=0o644, uid=0, gid=0)

    def 恢复文件(name: str, snapshot) -> None:
        events.append(("恢复文件", name, snapshot))
        files[name] = snapshot

    def 读取状态(name: str):
        events.append(("读取状态", name))
        return states[name]

    def 恢复启用态(name: str, state) -> None:
        events.append(("恢复启用态", name, state))
        states[name] = module.SystemdUnitState(
            unit_file_state=state.unit_file_state,
            active_state=states[name].active_state,
        )

    def 恢复活动态(name: str, state) -> None:
        events.append(("恢复活动态", name, state))
        states[name] = module.SystemdUnitState(
            unit_file_state=states[name].unit_file_state,
            active_state=state.active_state,
        )

    def 执行命令(command: tuple[str, ...]) -> None:
        events.append(("systemctl", command))
        action = command[1]
        if fail_command == action:
            raise RuntimeError(f"{action} 失败")
        for name in command[2:]:
            if action == "enable":
                states[name] = module.SystemdUnitState("enabled", states[name].active_state)
            elif action == "restart":
                states[name] = module.SystemdUnitState(states[name].unit_file_state, "active")

    def 读取回执原像():
        return receipt

    def 写入回执(content: bytes) -> None:
        nonlocal receipt
        receipt = module.SystemdStageReceiptFileSnapshot(content, 0o600, 0, 0)

    def 恢复回执(snapshot) -> None:
        nonlocal receipt
        receipt = snapshot

    def 复证回执(content: bytes) -> None:
        if receipt != module.SystemdStageReceiptFileSnapshot(content, 0o600, 0, 0):
            raise RuntimeError("stage 回执复证失败")

    @contextmanager
    def 运行时绑定锁(_manifest):
        events.append("进入运行时绑定锁")
        try:
            yield object()
        finally:
            events.append("退出运行时绑定锁")

    ports = module.SystemdInstallPorts(
        provision_maintenance_gate=lambda: events.append("预置门禁"),
        installer_lock=(默认安装锁 if installer_lock_factory is None else installer_lock_factory),
        runtime_binding_lock=运行时绑定锁,
        verify_runtime_binding=lambda _manifest, _bound: events.append("验证运行时绑定"),
        verify_target_user_preflight=lambda _manifest, _payloads, _bound: events.append(
            "目标用户预检"
        ),
        verify_install_boundary=lambda: events.append("复核mask"),
        read_unit_source=读取源,
        snapshot_legacy_release_dropins=读取shadow原像,
        retire_legacy_release_dropins=退役shadow,
        restore_legacy_release_dropins=恢复shadow,
        verify_legacy_release_dropins_retired=验证shadow已退役,
        verify_managed_install_contract=lambda _manifest, _payloads: None,
        verify_effective_unit_payloads=验证有效载荷,
        snapshot_installed_unit=lambda name: events.append(("读取原像", name)) or files[name],
        write_installed_unit=写入,
        restore_installed_unit=恢复文件,
        read_unit_state=读取状态,
        read_unit_enablement_state=lambda name: states[name].unit_file_state,
        read_unit_process_state=lambda name: process_states[name],
        restore_unit_file_state=恢复启用态,
        restore_unit_activity_state=恢复活动态,
        systemctl=执行命令,
        verify_running_units=lambda names: events.append(("验证运行", names)),
        read_stage_runtime_identity=lambda: _测试发布身份(module, manifest),
        snapshot_stage_receipt=读取回执原像,
        write_stage_receipt=写入回执,
        restore_stage_receipt=恢复回执,
        verify_stage_receipt=复证回执,
    )
    return ports, files, states, initial_files, initial_states


def _测试发布身份(module, manifest):
    from codev_platform.core.runtime_interpreter import ReleaseInterpreterIdentity

    revision = manifest.runtime_revision
    if type(revision) is not str or len(revision) != 40:
        revision = _TEST_RUNTIME_REVISION
    return ReleaseInterpreterIdentity(
        runtime_revision=revision,
        release_id=_TEST_RELEASE_ID,
        interpreter_path=_TEST_INTERPRETER,
    )
