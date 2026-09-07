"""全量 systemd 安装事务的 Linux 受信适配器。"""

from __future__ import annotations

from collections.abc import Callable, Iterator
from contextlib import contextmanager
from dataclasses import replace
from pathlib import Path

from codev_platform.core.runtime_interpreter import (
    ReleaseInterpreterIdentity,
    current_release_interpreter_identity,
)
from codev_platform.core.systemd_unit_resolution import (
    RuntimeMaskState,
    SystemdUnitResolutionError,
    classify_runtime_mask,
    read_runtime_mask_target,
    read_unit_resolution,
)
from codev_platform.mcp_systemd_install_contract import (
    SystemdInstallPorts,
    SystemdInstallTransactionError,
    SystemdUnitActivationMode,
    SystemdUnitInstallSpec,
    SystemdUnitFileSnapshot as _SystemdUnitFileSnapshot,
    SystemdUnitPayload,
    SystemdUnitState,
)
from codev_platform.mcp_systemd_install_policy import MAINTENANCE_DEFERRED_UNITS
from codev_platform.mcp_systemd_managed_contract import verify_managed_install_contract
from codev_platform.mcp_systemd_effective_payload import verify_effective_unit_payloads
from codev_platform.mcp_systemd_enablement import read_persistent_unit_enablement_state
from codev_platform.mcp_systemd_install_filesystem import (
    _installed_unit_path,
    default_drop_in_snapshot_reader,
    default_unit_restorer,
    default_unit_snapshot_reader,
    default_unit_writer,
)
from codev_platform.mcp_systemd_release_shadow import (
    restore_legacy_release_dropins,
    retire_legacy_release_dropins,
    snapshot_legacy_release_dropins,
    verify_legacy_release_dropins_retired,
)
from codev_platform.mcp_systemd_stage_receipt import (
    default_stage_receipt_restorer,
    default_stage_receipt_snapshot_reader,
    default_stage_receipt_verifier,
    default_stage_receipt_writer,
)
from codev_platform.mcp_systemd_staged_effective_payload import (
    verify_staged_effective_unit_payloads,
)
from codev_platform.mcp_systemd_target_preflight import (
    verify_target_user_systemd_preflight,
)
from codev_platform.mcp_systemd_runtime_binding import (
    bind_manifest_runtime as default_runtime_binding_lock,
    verify_bound_manifest_runtime as default_runtime_binding_verifier,
)
from codev_platform.mcp_systemd_systemctl import (
    read_systemctl_state as _read_systemctl_state,
    read_unit_process_state as default_unit_process_state_reader,
    restore_unit_activity_state as default_unit_activity_state_restorer,
    restore_unit_file_state as default_unit_file_state_restorer,
    run_systemctl_command as _run_systemctl_command,
    systemctl as default_systemctl,
    verify_running_units as default_running_unit_verifier,
)
from codev_platform.mcp_systemd_unit_registry import (
    WEBHOOK_SYSTEMD_UNIT_NAME,
)
from codev_platform.runtime_release_binding import BoundRelease

_WEBHOOK_UNIT = WEBHOOK_SYSTEMD_UNIT_NAME
_CODEGRAPH_UNIT = "codev-mcp-codegraph.service"

# 兼容既有安装适配器调用方；具体类型仍由安装契约定义。
SystemdUnitFileSnapshot = _SystemdUnitFileSnapshot


def default_ports() -> SystemdInstallPorts:
    """组装生产入口使用的 Linux 端口；源文件必须预先由受信输入层绑定。"""
    return SystemdInstallPorts(
        provision_maintenance_gate=default_gate_provisioner,
        installer_lock=default_installer_lock,
        runtime_binding_lock=default_runtime_binding_lock,
        verify_runtime_binding=default_runtime_binding_verifier,
        verify_target_user_preflight=default_target_user_preflight_verifier,
        verify_install_boundary=default_runtime_mask_proof,
        read_unit_source=_reject_unbound_unit_source,
        snapshot_legacy_release_dropins=snapshot_legacy_release_dropins,
        retire_legacy_release_dropins=retire_legacy_release_dropins,
        restore_legacy_release_dropins=restore_legacy_release_dropins,
        verify_legacy_release_dropins_retired=verify_legacy_release_dropins_retired,
        verify_managed_install_contract=verify_managed_install_contract,
        verify_effective_unit_payloads=default_effective_unit_payload_verifier,
        snapshot_installed_unit=default_unit_snapshot_reader,
        write_installed_unit=default_unit_writer,
        restore_installed_unit=default_unit_restorer,
        read_unit_state=default_unit_state_reader,
        read_unit_enablement_state=default_unit_enablement_state_reader,
        read_unit_process_state=default_unit_process_state_reader,
        restore_unit_file_state=default_unit_file_state_restorer,
        restore_unit_activity_state=default_unit_activity_state_restorer,
        systemctl=default_systemctl,
        verify_running_units=default_running_unit_verifier,
        read_stage_runtime_identity=default_stage_runtime_identity_reader,
        snapshot_stage_receipt=default_stage_receipt_snapshot_reader,
        write_stage_receipt=default_stage_receipt_writer,
        restore_stage_receipt=default_stage_receipt_restorer,
        verify_stage_receipt=default_stage_receipt_verifier,
    )


def default_maintenance_stage_ports() -> SystemdInstallPorts:
    """组装维护态载荷安装端口，索引写入者与 ingress 只落盘、不激活。"""
    return replace(
        default_ports(),
        installer_lock=default_maintenance_stage_installer_lock,
        verify_install_boundary=default_maintenance_stage_runtime_proof,
        verify_effective_unit_payloads=(default_maintenance_stage_unit_payload_verifier),
    )


def default_install_only_ports() -> SystemdInstallPorts:
    """组装不改变进程活动态、但兼容常态或维护稳态的安装端口。"""
    return replace(
        default_ports(),
        installer_lock=default_install_only_installer_lock,
        verify_install_boundary=default_install_only_runtime_proof,
        verify_effective_unit_payloads=default_install_only_unit_payload_verifier,
    )


def default_guarded_stage_ports(
    verify_handoff_boundary: Callable[[], object],
) -> SystemdInstallPorts:
    """组装总门禁内的交接准备端口；持久化 stage 回执但保持全部进程不变。"""
    if not callable(verify_handoff_boundary):
        raise SystemdInstallTransactionError("门禁交接边界证明不可用")

    def verify_boundary() -> None:
        default_maintenance_stage_runtime_proof()
        verify_handoff_boundary()

    return replace(
        default_install_only_ports(),
        installer_lock=default_maintenance_stage_installer_lock,
        verify_install_boundary=verify_boundary,
        verify_effective_unit_payloads=default_maintenance_stage_unit_payload_verifier,
    )


def default_gate_provisioner() -> None:
    from codev_platform.reindex.maintenance_gate import provision_maintenance_gate

    provision_maintenance_gate()


def default_target_user_preflight_verifier(
    manifest,
    payloads: tuple[SystemdUnitPayload, ...],
    bound: BoundRelease,
) -> None:
    """以目标用户、冻结载荷和锁内 release 执行瞬时同源证明。"""
    verify_target_user_systemd_preflight(manifest, payloads, bound)


def default_stage_runtime_identity_reader() -> ReleaseInterpreterIdentity:
    """独立读取当前 root 安装进程的 release 与已证明解释器身份。"""
    try:
        return current_release_interpreter_identity()
    except (KeyboardInterrupt, SystemExit, MemoryError):
        raise
    except Exception as error:
        raise SystemdInstallTransactionError("stage 实际发布运行身份无法证明") from error


@contextmanager
def default_installer_lock() -> Iterator[None]:
    """持有转换独占锁后复核维护标记，拒绝与进行中的维护交错。"""
    body_raised = False
    try:
        from codev_platform.reindex.maintenance_gate import (
            maintenance_gate_active,
            maintenance_systemd_transition_lock,
        )

        with maintenance_systemd_transition_lock():
            if maintenance_gate_active() is not False:
                raise SystemdInstallTransactionError("reindex 维护门禁正在生效")
            try:
                yield
            except BaseException:
                body_raised = True
                raise
    except (KeyboardInterrupt, SystemExit, MemoryError):
        raise
    except SystemdInstallTransactionError:
        raise
    except Exception as error:
        if body_raised:
            raise
        raise SystemdInstallTransactionError("无法取得 systemd 安装独占锁") from error


@contextmanager
def default_maintenance_stage_installer_lock() -> Iterator[None]:
    """在同一转换排他锁内前后证明维护稳态，消除锁外 TOCTOU。"""
    body_raised = False
    try:
        from codev_platform.reindex.maintenance_gate import (
            maintenance_systemd_transition_lock,
        )

        with maintenance_systemd_transition_lock():
            default_maintenance_stage_runtime_proof()
            try:
                yield
            except BaseException:
                body_raised = True
                default_maintenance_stage_runtime_proof()
                raise
            default_maintenance_stage_runtime_proof()
    except (KeyboardInterrupt, SystemExit, MemoryError):
        raise
    except SystemdInstallTransactionError:
        raise
    except Exception as error:
        if body_raised:
            raise
        raise SystemdInstallTransactionError(
            "无法取得 maintenance-stage systemd 安装独占锁"
        ) from error


@contextmanager
def default_install_only_installer_lock() -> Iterator[None]:
    """串行管理员转换并复验运行模式，不阻断存量 worker 的写许可。"""
    body_raised = False
    try:
        from codev_platform.reindex.maintenance_gate import (
            maintenance_systemd_transition_session,
        )

        with maintenance_systemd_transition_session():
            before = _install_only_runtime_mode()
            try:
                yield
            except BaseException as body_error:
                body_raised = True
                after = _install_only_runtime_mode()
                if after is not before:
                    raise SystemdInstallTransactionError(
                        "install-only 失败结算期间维护模式发生漂移"
                    ) from body_error
                raise
            after = _install_only_runtime_mode()
            if after is not before:
                raise SystemdInstallTransactionError("install-only 期间维护模式发生漂移")
    except (KeyboardInterrupt, SystemExit, MemoryError):
        raise
    except SystemdInstallTransactionError:
        raise
    except Exception as error:
        if body_raised:
            raise
        raise SystemdInstallTransactionError(
            "无法取得 install-only systemd 管理员会话锁"
        ) from error


def default_runtime_mask_proof() -> None:
    try:
        resolution = read_unit_resolution(_CODEGRAPH_UNIT)
        state = classify_runtime_mask(
            resolution,
            runtime_mask_target=read_runtime_mask_target(_CODEGRAPH_UNIT),
        )
    except SystemdUnitResolutionError:
        raise SystemdInstallTransactionError("CodeGraph runtime mask 状态无法证明") from None
    if state is RuntimeMaskState.UNMASKED:
        return
    if state is RuntimeMaskState.EFFECTIVE_RUNTIME_MASK:
        raise SystemdInstallTransactionError("CodeGraph 正处于 runtime mask 维护窗口")
    if state is RuntimeMaskState.INCONSISTENT_RUNTIME_ARTIFACT:
        raise SystemdInstallTransactionError("CodeGraph 存在无效 runtime mask 残留，请先迁移布局")
    raise SystemdInstallTransactionError("CodeGraph systemd mask 状态不可安全安装")


def default_maintenance_stage_runtime_proof() -> None:
    """证明索引维护稳态和 ingress 停止，不接受单一 marker 或 mask。"""
    from codev_platform.ops.reindex_maintenance import (
        ReindexMaintenanceError,
        inspect_reindex_maintenance,
    )

    try:
        inspect_reindex_maintenance()
    except ReindexMaintenanceError as error:
        raise SystemdInstallTransactionError(
            "maintenance-stage 要求 reindex 与 CodeGraph 均处于维护稳态"
        ) from error
    try:
        ingress = default_unit_process_state_reader(_WEBHOOK_UNIT)
    except SystemdInstallTransactionError as error:
        raise SystemdInstallTransactionError("maintenance-stage 无法证明 Webhook 已停止") from error
    if ingress.active_state != "inactive" or ingress.main_pid != 0:
        raise SystemdInstallTransactionError("maintenance-stage 要求 Webhook 已停止")


def _install_only_runtime_mode() -> RuntimeMaskState:
    try:
        resolution = read_unit_resolution(_CODEGRAPH_UNIT)
        state = classify_runtime_mask(
            resolution,
            runtime_mask_target=read_runtime_mask_target(_CODEGRAPH_UNIT),
        )
    except SystemdUnitResolutionError:
        raise SystemdInstallTransactionError("install-only 运行模式无法证明") from None
    if state is RuntimeMaskState.UNMASKED:
        from codev_platform.reindex.maintenance_gate import maintenance_gate_active

        if maintenance_gate_active() is not False:
            raise SystemdInstallTransactionError("install-only 常态维护门禁不一致")
        default_runtime_mask_proof()
        return state
    if state is RuntimeMaskState.EFFECTIVE_RUNTIME_MASK:
        default_maintenance_stage_runtime_proof()
        return state
    raise SystemdInstallTransactionError("install-only 运行模式不安全")


def default_install_only_runtime_proof() -> None:
    _install_only_runtime_mode()


def default_effective_unit_payload_verifier(
    payloads: tuple[SystemdUnitPayload, ...],
) -> None:
    verify_effective_unit_payloads(
        payloads,
        snapshot_reader=default_unit_snapshot_reader,
        systemctl_show=_run_systemctl_command,
        installed_path=_installed_unit_path,
        drop_in_snapshot_reader=default_drop_in_snapshot_reader,
    )


def default_staged_effective_unit_payload_verifier(
    payloads: tuple[SystemdUnitPayload, ...],
    *,
    resume_dropin_content: bytes,
    allow_reindex_local_dropins: bool = False,
    codegraph_startup_bridge_dropin_content: bytes | None = None,
    codegraph_effective_exec_start: tuple[str, ...] | None = None,
) -> None:
    """恢复期证明受保护 unit；M1 可保留 reindex 的本地运行覆盖。"""
    verify_staged_effective_unit_payloads(
        payloads,
        resume_dropin_content=resume_dropin_content,
        allow_reindex_local_dropins=allow_reindex_local_dropins,
        codegraph_startup_bridge_dropin_content=codegraph_startup_bridge_dropin_content,
        codegraph_effective_exec_start=codegraph_effective_exec_start,
        snapshot_reader=default_unit_snapshot_reader,
        systemctl_show=_run_systemctl_command,
        installed_path=_installed_unit_path,
        drop_in_snapshot_reader=default_drop_in_snapshot_reader,
    )


def default_maintenance_stage_unit_payload_verifier(
    payloads: tuple[SystemdUnitPayload, ...],
) -> None:
    """普通 unit 证明有效解析；被 mask 的写服务证明 canonical 落盘原像。"""
    deferred = frozenset(MAINTENANCE_DEFERRED_UNITS)
    names = {payload.spec.unit_name for payload in payloads}
    if not deferred.issubset(names):
        raise SystemdInstallTransactionError("维护态 systemd 安装载荷缺少受保护 unit")
    immediate = tuple(payload for payload in payloads if payload.spec.unit_name != _CODEGRAPH_UNIT)
    if immediate:
        default_effective_unit_payload_verifier(immediate)
    for payload in payloads:
        if payload.spec.unit_name != _CODEGRAPH_UNIT:
            continue
        snapshot = default_unit_snapshot_reader(payload.spec.unit_name)
        if (
            snapshot is None
            or snapshot.content != payload.content
            or snapshot.mode != 0o644
            or snapshot.uid != 0
            or snapshot.gid != 0
        ):
            raise SystemdInstallTransactionError("维护态 systemd 受保护 unit 落盘载荷未证明")


def default_install_only_unit_payload_verifier(
    payloads: tuple[SystemdUnitPayload, ...],
) -> None:
    """按锁内实际模式选择正常或维护态有效载荷证明。"""
    state = _install_only_runtime_mode()
    if state is RuntimeMaskState.UNMASKED:
        default_effective_unit_payload_verifier(payloads)
    else:
        default_maintenance_stage_unit_payload_verifier(payloads)


def _reject_unbound_unit_source(_source: Path) -> bytes:
    raise SystemdInstallTransactionError("systemd unit 源文件必须经受信 manifest 输入层绑定")


def default_unit_state_reader(name: str) -> SystemdUnitState:
    """只接受 enable/disable、restart/stop 能对称恢复的明确状态。"""
    reported_state = _read_systemctl_state(("systemctl", "is-enabled", name))
    active_state = _read_systemctl_state(("systemctl", "is-active", name))
    if reported_state not in {"enabled", "disabled", "masked-runtime", "not-found"}:
        return SystemdUnitState(
            unit_file_state=reported_state,
            active_state=active_state,
        )
    persistent_state = read_persistent_unit_enablement_state(name)
    if reported_state == "not-found":
        if persistent_state != "disabled":
            raise SystemdInstallTransactionError("不存在的 systemd unit 存在持久启用链接")
        if active_state == "unknown":
            active_state = "inactive"
        return SystemdUnitState(
            unit_file_state="not-found",
            active_state=active_state,
        )
    if reported_state != "masked-runtime" and reported_state != persistent_state:
        raise SystemdInstallTransactionError("systemd unit 持久启用状态与运行报告不一致")
    return SystemdUnitState(
        unit_file_state=persistent_state,
        active_state=active_state,
    )


def default_unit_enablement_state_reader(name: str) -> str:
    """只读取 root-owned 持久启用链接，不耦合 daemon-reload 后的瞬时活动态。"""
    return read_persistent_unit_enablement_state(name)


__all__ = [
    "default_gate_provisioner",
    "default_installer_lock",
    "default_install_only_installer_lock",
    "default_install_only_ports",
    "default_guarded_stage_ports",
    "default_install_only_runtime_proof",
    "default_install_only_unit_payload_verifier",
    "default_maintenance_stage_installer_lock",
    "default_maintenance_stage_ports",
    "default_maintenance_stage_runtime_proof",
    "default_maintenance_stage_unit_payload_verifier",
    "default_effective_unit_payload_verifier",
    "default_ports",
    "default_runtime_mask_proof",
    "default_running_unit_verifier",
    "default_stage_runtime_identity_reader",
    "default_systemctl",
    "default_unit_activity_state_restorer",
    "default_unit_enablement_state_reader",
    "default_unit_file_state_restorer",
    "default_unit_restorer",
    "default_unit_snapshot_reader",
    "default_unit_state_reader",
    "default_unit_writer",
    "SystemdUnitActivationMode",
    "SystemdUnitInstallSpec",
]
