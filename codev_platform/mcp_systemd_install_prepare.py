"""全量 systemd 安装的只读准备阶段与冻结补偿计划。"""

from __future__ import annotations

from dataclasses import dataclass

from codev_platform.mcp_systemd_install_compensation import CompensationAction
from codev_platform.mcp_systemd_install_contract import (
    SystemdInstallManifest,
    SystemdInstallPorts,
    SystemdInstallTransactionError,
    SystemdStageReceiptFileSnapshot,
    SystemdUnitFileSnapshot,
    SystemdUnitPayload,
    SystemdUnitProcessState,
    SystemdUnitState,
    require_runtime_revision,
    require_unit_activation_mode,
)
from codev_platform.mcp_systemd_install_input import VerifiedInstallInput
from codev_platform.mcp_systemd_install_policy import (
    NORMAL_INSTALL_POLICY,
    SystemdInstallPolicy,
)
from codev_platform.mcp_systemd_install_signal import TERMINATION_EXCEPTIONS
from codev_platform.mcp_systemd_release_shadow import LegacyReleaseDropInSnapshot
from codev_platform.mcp_systemd_stage_receipt import (
    build_stage_receipt_content,
    verify_stage_payload_runtime_identity,
)


@dataclass(frozen=True, slots=True)
class PreparedSystemdInstall:
    """首次写入前冻结的载荷、原像、状态与完整补偿顺序。"""

    manifest: SystemdInstallManifest
    policy: SystemdInstallPolicy
    payloads: tuple[SystemdUnitPayload, ...]
    originals: tuple[tuple[SystemdUnitPayload, SystemdUnitFileSnapshot | None], ...]
    shadows: tuple[LegacyReleaseDropInSnapshot, ...]
    states: tuple[tuple[str, SystemdUnitState], ...]
    process_states: tuple[tuple[str, SystemdUnitProcessState], ...]
    stage_receipt_content: bytes | None
    stage_receipt_original: SystemdStageReceiptFileSnapshot | None
    compensation_actions: tuple[CompensationAction, ...]


def prepare_systemd_install(
    install_input: VerifiedInstallInput,
    ports: SystemdInstallPorts,
    *,
    policy: SystemdInstallPolicy = NORMAL_INSTALL_POLICY,
) -> PreparedSystemdInstall:
    """只读冻结全部事务输入，并在返回前构建不可变补偿计划。"""
    if not isinstance(install_input, VerifiedInstallInput):
        raise SystemdInstallTransactionError("systemd 安装输入无效")
    manifest = require_install_manifest(install_input.manifest)
    if not isinstance(policy, SystemdInstallPolicy):
        raise SystemdInstallTransactionError("systemd 安装策略无效")
    policy.require_manifest(manifest)
    payloads = install_input.payloads
    if tuple(payload.spec for payload in payloads) != manifest.units:
        raise SystemdInstallTransactionError("systemd 安装输入与清单不一致")
    ports.verify_managed_install_contract(manifest, payloads)
    receipt_content, receipt_original = _capture_stage_receipt(
        manifest,
        payloads,
        policy,
        ports,
    )
    originals = _capture_unit_originals(payloads, ports)
    shadows = _capture_shadow_originals(manifest, ports)
    states = _capture_unit_states(policy.state_names(manifest), ports)
    process_states = _capture_process_states(
        tuple(unit.unit_name for unit in manifest.units)
        if policy.proves_process_stability
        else (),
        ports,
    )
    actions = _build_compensation_actions(
        originals,
        shadows,
        states,
        receipt_content,
        receipt_original,
        process_states,
        restore_activity=policy.restores_activity,
        ports=ports,
    )
    return PreparedSystemdInstall(
        manifest,
        policy,
        payloads,
        originals,
        shadows,
        states,
        process_states,
        receipt_content,
        receipt_original,
        actions,
    )


def require_install_manifest(manifest: object) -> SystemdInstallManifest:
    """二次验证 manifest，拒绝绕过 dataclass 构造期的特殊启动语义。"""
    if not isinstance(manifest, SystemdInstallManifest):
        raise SystemdInstallTransactionError("systemd 安装 manifest 适配器不可用")
    require_runtime_revision(manifest.runtime_revision)
    for unit in manifest.units:
        require_unit_activation_mode(unit)
    return manifest


def _capture_stage_receipt(
    manifest: SystemdInstallManifest,
    payloads: tuple[SystemdUnitPayload, ...],
    policy: SystemdInstallPolicy,
    ports: SystemdInstallPorts,
) -> tuple[bytes | None, SystemdStageReceiptFileSnapshot | None]:
    """维护态在首次修改前冻结新回执与旧原像，普通安装不触碰回执。"""
    if not policy.writes_stage_receipt:
        return None, None
    try:
        runtime_release = ports.read_stage_runtime_identity()
        verify_stage_payload_runtime_identity(payloads, runtime_release)
        content = build_stage_receipt_content(manifest, runtime_release)
    except TERMINATION_EXCEPTIONS:
        raise
    except SystemdInstallTransactionError:
        raise
    except Exception as error:
        raise SystemdInstallTransactionError("stage 发布运行身份或受保护载荷无法证明") from error
    try:
        original = ports.snapshot_stage_receipt()
    except TERMINATION_EXCEPTIONS:
        raise
    except Exception as error:
        raise SystemdInstallTransactionError("stage 回执原像无法读取") from error
    if original is not None and not isinstance(original, SystemdStageReceiptFileSnapshot):
        raise SystemdInstallTransactionError("stage 回执原像无效")
    return content, original


def _capture_shadow_originals(
    manifest: SystemdInstallManifest,
    ports: SystemdInstallPorts,
) -> tuple[LegacyReleaseDropInSnapshot, ...]:
    names = tuple(unit.unit_name for unit in manifest.units)
    shadows = ports.snapshot_legacy_release_dropins(names)
    valid = type(shadows) is tuple and all(
        isinstance(item, LegacyReleaseDropInSnapshot) for item in shadows
    )
    if not valid or tuple(item.unit_name for item in shadows) != names:
        raise SystemdInstallTransactionError("旧 release shadow 原像无效")
    return shadows


def _capture_unit_originals(
    payloads: tuple[SystemdUnitPayload, ...],
    ports: SystemdInstallPorts,
) -> tuple[tuple[SystemdUnitPayload, SystemdUnitFileSnapshot | None], ...]:
    try:
        originals = tuple(
            (payload, ports.snapshot_installed_unit(payload.spec.unit_name)) for payload in payloads
        )
    except TERMINATION_EXCEPTIONS:
        raise
    except Exception as error:
        raise SystemdInstallTransactionError("systemd unit 原像无法读取") from error
    if any(
        snapshot is not None and not isinstance(snapshot, SystemdUnitFileSnapshot)
        for _payload, snapshot in originals
    ):
        raise SystemdInstallTransactionError("systemd unit 原像无效")
    return originals


def _capture_unit_states(
    names: tuple[str, ...],
    ports: SystemdInstallPorts,
) -> tuple[tuple[str, SystemdUnitState], ...]:
    try:
        states = tuple((name, ports.read_unit_state(name)) for name in names)
    except TERMINATION_EXCEPTIONS:
        raise
    except Exception as error:
        raise SystemdInstallTransactionError("systemd unit 运行状态无法读取") from error
    if not all(isinstance(state, SystemdUnitState) for _name, state in states):
        raise SystemdInstallTransactionError("systemd unit 运行状态无效")
    return states


def _capture_process_states(
    names: tuple[str, ...],
    ports: SystemdInstallPorts,
) -> tuple[tuple[str, SystemdUnitProcessState], ...]:
    try:
        states = tuple((name, ports.read_unit_process_state(name)) for name in names)
    except TERMINATION_EXCEPTIONS:
        raise
    except Exception as error:
        raise SystemdInstallTransactionError("systemd unit 进程状态无法读取") from error
    if not all(type(state) is SystemdUnitProcessState for _name, state in states):
        raise SystemdInstallTransactionError("systemd unit 进程状态无效")
    return states


def _build_compensation_actions(
    originals: tuple[tuple[SystemdUnitPayload, SystemdUnitFileSnapshot | None], ...],
    shadows: tuple[LegacyReleaseDropInSnapshot, ...],
    states: tuple[tuple[str, SystemdUnitState], ...],
    receipt_content: bytes | None,
    receipt_original: SystemdStageReceiptFileSnapshot | None,
    process_states: tuple[tuple[str, SystemdUnitProcessState], ...],
    restore_activity: bool,
    ports: SystemdInstallPorts,
) -> tuple[CompensationAction, ...]:
    """按 systemd 可见性顺序冻结补偿，单项失败由执行器继续耗尽。"""
    before_removal = tuple(
        (name, state) for name, state in states if state.unit_file_state == "not-found"
    )
    after_reload = tuple(
        (name, state) for name, state in states if state.unit_file_state != "not-found"
    )
    return (
        *(
            (CompensationAction(lambda: ports.restore_stage_receipt(receipt_original)),)
            if receipt_content is not None
            else ()
        ),
        *(
            CompensationAction(
                lambda name=name, state=state: ports.restore_unit_file_state(name, state)
            )
            for name, state in before_removal
        ),
        *(
            (
                CompensationAction(
                    lambda name=name, state=state: ports.restore_unit_activity_state(name, state)
                )
                for name, state in before_removal
            )
            if restore_activity
            else ()
        ),
        *(
            CompensationAction(
                lambda payload=payload, snapshot=snapshot: ports.restore_installed_unit(
                    payload.spec.unit_name,
                    snapshot,
                )
            )
            for payload, snapshot in reversed(originals)
        ),
        *(
            CompensationAction(
                lambda snapshot=snapshot: ports.restore_legacy_release_dropins((snapshot,))
            )
            for snapshot in reversed(shadows)
        ),
        CompensationAction(lambda: ports.systemctl(("systemctl", "daemon-reload"))),
        *(
            CompensationAction(
                lambda name=name, state=state: ports.restore_unit_file_state(name, state)
            )
            for name, state in after_reload
        ),
        *(
            (
                CompensationAction(
                    lambda name=name, state=state: ports.restore_unit_activity_state(name, state)
                )
                for name, state in after_reload
            )
            if restore_activity
            else ()
        ),
        *(
            CompensationAction(
                lambda payload=payload, snapshot=snapshot: (
                    ports.snapshot_installed_unit(payload.spec.unit_name) == snapshot
                ),
                proof=True,
            )
            for payload, snapshot in originals
        ),
        CompensationAction(
            lambda: (
                ports.snapshot_legacy_release_dropins(
                    tuple(snapshot.unit_name for snapshot in shadows)
                )
                == shadows
            ),
            proof=True,
        ),
        *(
            CompensationAction(
                lambda name=name, state=state: ports.read_unit_state(name) == state,
                proof=True,
            )
            for name, state in states
        ),
        *(
            (
                CompensationAction(
                    lambda: ports.snapshot_stage_receipt() == receipt_original,
                    proof=True,
                ),
            )
            if receipt_content is not None
            else ()
        ),
        *(
            CompensationAction(
                lambda name=name, state=state: ports.read_unit_process_state(name) == state,
                proof=True,
            )
            for name, state in process_states
        ),
    )


__all__ = [
    "PreparedSystemdInstall",
    "prepare_systemd_install",
    "require_install_manifest",
]
