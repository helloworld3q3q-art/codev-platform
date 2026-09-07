"""全量 systemd 安装的可补偿事务编排入口。"""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path

from codev_platform.mcp_systemd_install_compensation import (
    CompensationResult as _CompensationResult,
    CompensationRunner as _CompensationRunner,
)
from codev_platform.mcp_systemd_install_cli import run_install_cli as main
from codev_platform.mcp_systemd_install_contract import (
    SystemdInstallManifest,
    SystemdInstallPorts,
    SystemdInstallReport,
    SystemdInstallTransactionError,
    SystemdStageReceiptFileSnapshot,
    SystemdUnitFileSnapshot,
    SystemdUnitActivationMode,
    SystemdUnitInstallSpec,
    SystemdUnitPayload,
    SystemdUnitProcessState,
    SystemdUnitState,
)
from codev_platform.mcp_systemd_install_input import (
    VerifiedInstallInput,
    load_install_manifest,
    load_verified_install_input,
)
from codev_platform.mcp_systemd_install_signal import (
    TERMINATION_EXCEPTIONS as _TERMINATION_EXCEPTIONS,
    DeferredSigintGuard as _DeferredSigintGuard,
)
from codev_platform.mcp_systemd_install_prepare import (
    PreparedSystemdInstall as _PreparedInstall,
    prepare_systemd_install as _prepare_install,
    require_install_manifest as _require_manifest,
)
from codev_platform.mcp_systemd_install_policy import (
    GUARDED_STAGE_INSTALL_POLICY,
    INSTALL_ONLY_POLICY,
    MAINTENANCE_STAGE_INSTALL_POLICY,
    NORMAL_INSTALL_POLICY,
    SystemdInstallPolicy,
)
from codev_platform.mcp_systemd_install_systemd import (
    default_install_only_ports,
    default_maintenance_stage_ports,
    default_ports,
)
from codev_platform.mcp_systemd_install_validation import (
    provision_maintenance_gate as _provision_gate,
    require_install_ports as _require_ports,
    require_linux_root as _require_linux_root,
)
from codev_platform.mcp_systemd_install_verification import (
    verify_enabled_units as _verify_enabled_units,
    verify_install_boundary as _verify_install_boundary,
    verify_process_states as _verify_process_states,
    verify_running_units as _verify_running_units,
)


class _SafetyUnprovenInstallError(SystemdInstallTransactionError):
    """事务已发生可变动作但最终安全状态无法证明。"""


def install_systemd_transaction(
    manifest: SystemdInstallManifest,
    *,
    ports: SystemdInstallPorts | None = None,
    platform_name: str | None = None,
    effective_user_id: Callable[[], int] | None = None,
) -> SystemdInstallReport:
    """执行已在调用方受控绑定的内存 manifest 事务。"""
    return _install_manifest_transaction(
        manifest,
        ports,
        policy=NORMAL_INSTALL_POLICY,
        platform_name=platform_name,
        effective_user_id=effective_user_id,
        missing_ports="内存 systemd 安装事务必须显式提供受信端口",
    )


def install_systemd_maintenance_stage_transaction(
    manifest: SystemdInstallManifest,
    *,
    ports: SystemdInstallPorts | None = None,
    platform_name: str | None = None,
    effective_user_id: Callable[[], int] | None = None,
) -> SystemdInstallReport:
    """在已证明的维护窗口中只安装载荷，不激活两个写服务。"""
    return _install_manifest_transaction(
        manifest,
        ports,
        policy=MAINTENANCE_STAGE_INSTALL_POLICY,
        platform_name=platform_name,
        effective_user_id=effective_user_id,
        missing_ports="内存维护态安装事务必须显式提供受信端口",
    )


def install_systemd_guarded_stage_transaction(
    manifest: SystemdInstallManifest,
    *,
    ports: SystemdInstallPorts | None = None,
    platform_name: str | None = None,
    effective_user_id: Callable[[], int] | None = None,
) -> SystemdInstallReport:
    """总门禁内安装并持久化交接回执，保持全部进程活动态不变。"""
    return _install_manifest_transaction(
        manifest,
        ports,
        policy=GUARDED_STAGE_INSTALL_POLICY,
        platform_name=platform_name,
        effective_user_id=effective_user_id,
        missing_ports="内存门禁交接事务必须显式提供受信端口",
    )


def install_systemd_install_only_transaction(
    manifest: SystemdInstallManifest,
    *,
    ports: SystemdInstallPorts | None = None,
    platform_name: str | None = None,
    effective_user_id: Callable[[], int] | None = None,
) -> SystemdInstallReport:
    """安装并 enable 固定载荷，但不启动、停止或重启任何 unit。"""
    return _install_manifest_transaction(
        manifest,
        ports,
        policy=INSTALL_ONLY_POLICY,
        platform_name=platform_name,
        effective_user_id=effective_user_id,
        missing_ports="内存 install-only 事务必须显式提供受信端口",
    )


def _install_manifest_transaction(
    manifest: SystemdInstallManifest,
    ports: SystemdInstallPorts | None,
    *,
    policy: SystemdInstallPolicy,
    platform_name: str | None,
    effective_user_id: Callable[[], int] | None,
    missing_ports: str,
) -> SystemdInstallReport:
    _require_linux_root(platform_name, effective_user_id)
    checked_manifest = _require_manifest(manifest)
    if ports is None:
        raise SystemdInstallTransactionError(missing_ports)
    _require_ports(ports)
    _provision_gate(ports.provision_maintenance_gate)
    return _install_while_locked(
        ports,
        lambda: VerifiedInstallInput(
            manifest=checked_manifest,
            payloads=_read_unit_payloads(checked_manifest, ports.read_unit_source),
        ),
        policy=policy,
    )


def install_systemd_from_manifest_path(
    path: Path,
    *,
    ports: SystemdInstallPorts | None = None,
    platform_name: str | None = None,
    effective_user_id: Callable[[], int] | None = None,
) -> SystemdInstallReport:
    """生产入口：独占安装锁内通过同一 dirfd 读取并摘要绑定全部输入。"""
    return _install_path_transaction(
        path,
        default_ports() if ports is None else ports,
        policy=NORMAL_INSTALL_POLICY,
        platform_name=platform_name,
        effective_user_id=effective_user_id,
    )


def install_systemd_maintenance_stage_from_manifest_path(
    path: Path,
    *,
    ports: SystemdInstallPorts | None = None,
    platform_name: str | None = None,
    effective_user_id: Callable[[], int] | None = None,
) -> SystemdInstallReport:
    """生产维护入口：同一排他锁内绑定输入并延迟激活写服务。"""
    return _install_path_transaction(
        path,
        default_maintenance_stage_ports() if ports is None else ports,
        policy=MAINTENANCE_STAGE_INSTALL_POLICY,
        platform_name=platform_name,
        effective_user_id=effective_user_id,
    )


def prepare_systemd_guarded_stage_from_manifest_path(
    path: Path,
    *,
    ports: SystemdInstallPorts | None = None,
    platform_name: str | None = None,
    effective_user_id: Callable[[], int] | None = None,
) -> SystemdInstallReport:
    """生产交接准备：总门禁内持久化 target stage 回执且不启动进程。"""
    if ports is None:
        raise SystemdInstallTransactionError("生产门禁交接必须显式注入计划绑定门禁端口")
    return _install_path_transaction(
        path,
        ports,
        policy=GUARDED_STAGE_INSTALL_POLICY,
        platform_name=platform_name,
        effective_user_id=effective_user_id,
    )


def install_systemd_install_only_from_manifest_path(
    path: Path,
    *,
    ports: SystemdInstallPorts | None = None,
    platform_name: str | None = None,
    effective_user_id: Callable[[], int] | None = None,
) -> SystemdInstallReport:
    """生产 install-only 入口；与普通安装共用同一受补偿事务。"""
    return _install_path_transaction(
        path,
        default_install_only_ports() if ports is None else ports,
        policy=INSTALL_ONLY_POLICY,
        platform_name=platform_name,
        effective_user_id=effective_user_id,
    )


def _install_path_transaction(
    path: Path,
    ports: SystemdInstallPorts,
    *,
    policy: SystemdInstallPolicy,
    platform_name: str | None,
    effective_user_id: Callable[[], int] | None,
) -> SystemdInstallReport:
    _require_linux_root(platform_name, effective_user_id)
    _require_ports(ports)
    _provision_gate(ports.provision_maintenance_gate)
    return _install_while_locked(
        ports,
        lambda: load_verified_install_input(path),
        policy=policy,
    )


def _install_while_locked(
    ports: SystemdInstallPorts,
    input_loader: Callable[[], VerifiedInstallInput],
    *,
    policy: SystemdInstallPolicy,
) -> SystemdInstallReport:
    """在 SIGINT 延后守卫内完成事务结算，再恢复调用方原有中断语义。"""
    guard = _DeferredSigintGuard()
    outcome: BaseException | None = None
    report: SystemdInstallReport | None = None
    close_error: BaseException | None = None
    try:
        try:
            report = _install_while_locked_guarded(ports, input_loader, policy, guard)
        except BaseException as error:
            outcome = error
        finally:
            close_error = guard.close()
        _raise_after_transaction_settlement(outcome, close_error, guard)
    except _TERMINATION_EXCEPTIONS as interrupted:
        if isinstance(outcome, _SafetyUnprovenInstallError):
            raise outcome from interrupted
        raise
    if report is None:
        raise SystemdInstallTransactionError("全量 systemd 安装事务未生成结果")
    return report


def _raise_after_transaction_settlement(
    outcome: BaseException | None,
    close_error: BaseException | None,
    guard: _DeferredSigintGuard,
) -> None:
    """按安全优先级结算守卫关闭、延后中断和事务结果。"""
    if close_error is not None:
        if isinstance(outcome, _SafetyUnprovenInstallError):
            raise outcome from close_error
        if outcome is not None:
            raise close_error from outcome
        raise close_error
    if guard.pending:
        if isinstance(outcome, _SafetyUnprovenInstallError):
            raise outcome from guard.make_interrupt()
        try:
            guard.replay_once()
        except BaseException as interrupted:
            if outcome is None:
                raise
            raise interrupted from outcome
    if outcome is not None:
        raise outcome


def _install_while_locked_guarded(
    ports: SystemdInstallPorts,
    input_loader: Callable[[], VerifiedInstallInput],
    policy: SystemdInstallPolicy,
    guard: _DeferredSigintGuard,
) -> SystemdInstallReport:
    """守卫已建立后的事务主体；独占锁持续持有至体内补偿和复证结束。"""
    prepared: _PreparedInstall | None = None
    body_error: BaseException | None = None
    binding_scope_error: BaseException | None = None
    body_compensation: _CompensationResult | None = None
    mutations_started = False
    report: SystemdInstallReport | None = None
    try:
        with ports.installer_lock():
            try:
                _verify_install_boundary(ports.verify_install_boundary)
                install_input = input_loader()
                try:
                    with ports.runtime_binding_lock(install_input.manifest) as bound:
                        try:
                            ports.verify_runtime_binding(install_input.manifest, bound)
                            ports.verify_managed_install_contract(
                                install_input.manifest,
                                install_input.payloads,
                            )
                            ports.verify_target_user_preflight(
                                install_input.manifest,
                                install_input.payloads,
                                bound,
                            )
                            guard.arm()
                            prepared = _prepare_install(install_input, ports, policy=policy)
                            guard.raise_if_pending()
                            mutations_started = True
                            report = _apply_install(prepared, ports)
                            _verify_install_boundary(ports.verify_install_boundary)
                            guard.raise_if_pending()
                        except BaseException as error:
                            body_error = error
                            if mutations_started and prepared is not None:
                                body_compensation = _compensate_failed_install(prepared, ports)
                            raise
                except BaseException as error:
                    if body_error is None or error is not body_error:
                        binding_scope_error = error
                        if body_error is None:
                            body_error = error
                        if mutations_started and prepared is not None and body_compensation is None:
                            body_compensation = _compensate_failed_install(prepared, ports)
                    raise
            except BaseException:
                raise
        guard.raise_if_pending()
    except BaseException as error:
        if binding_scope_error is not None and mutations_started:
            _raise_after_runtime_binding_scope_failure(error, body_compensation)
        if error is body_error:
            _raise_after_body_failure(
                error,
                prepared if mutations_started else None,
                body_compensation,
            )
        if body_error is not None:
            if mutations_started:
                _raise_after_installer_lock_exit_failure(error)
            _raise_unmutated_failure(error)
        if mutations_started:
            _raise_after_installer_lock_exit_failure(error)
        _raise_unmutated_failure(error)
    if body_error is not None:
        _raise_after_body_failure(
            body_error,
            prepared if mutations_started else None,
            body_compensation,
        )
    if report is None:
        raise SystemdInstallTransactionError("全量 systemd 安装事务未生成结果")
    return report


def _raise_after_runtime_binding_scope_failure(
    error: BaseException,
    compensation: _CompensationResult | None,
) -> None:
    """绑定作用域退出失败后，即使补偿成功也不能宣称锁安全已证明。"""
    _raise_safety_unproven(
        "全量 systemd 安装事务失败；运行时绑定锁状态未证明",
        error,
        compensation,
    )


def _read_unit_payloads(
    manifest: SystemdInstallManifest,
    reader: Callable[[Path], bytes],
) -> tuple[SystemdUnitPayload, ...]:
    try:
        return tuple(
            SystemdUnitPayload(spec=unit, content=reader(unit.source)) for unit in manifest.units
        )
    except _TERMINATION_EXCEPTIONS:
        raise
    except SystemdInstallTransactionError:
        raise
    except Exception as error:
        raise SystemdInstallTransactionError("systemd unit 源文件无法安全读取") from error


def _apply_install(
    prepared: _PreparedInstall,
    ports: SystemdInstallPorts,
) -> SystemdInstallReport:
    for payload in prepared.payloads:
        ports.write_installed_unit(payload.spec.unit_name, payload.content)
    ports.retire_legacy_release_dropins(prepared.shadows)
    _run_systemctl(ports.systemctl, ("systemctl", "daemon-reload"))
    ports.verify_effective_unit_payloads(prepared.payloads)
    _run_install_lifecycle_commands(
        prepared.manifest,
        prepared.policy,
        ports.systemctl,
    )
    _verify_enabled_units(
        prepared.policy.enable_names(prepared.manifest.enable_units),
        ports.read_unit_enablement_state,
    )
    _verify_running_units(
        prepared.policy.restart_names(prepared.manifest.immediate_restart_units),
        ports.verify_running_units,
    )
    ports.verify_legacy_release_dropins_retired(prepared.shadows)
    _verify_process_states(prepared.process_states, ports.read_unit_process_state)
    if prepared.stage_receipt_content is not None:
        ports.write_stage_receipt(prepared.stage_receipt_content)
        ports.verify_stage_receipt(prepared.stage_receipt_content)
    return SystemdInstallReport(
        deferred_activation_units=prepared.policy.report_deferred_names(prepared.manifest),
    )


def _compensate_failed_install(
    prepared: _PreparedInstall,
    ports: SystemdInstallPorts,
    *,
    runner: _CompensationRunner | None = None,
) -> _CompensationResult:
    """每个补偿分量独立尝试；任意失败也不得阻断 stop/restart 或其余 unit。"""
    current_runner = _CompensationRunner() if runner is None else runner
    current_runner.run_all(prepared.compensation_actions)
    current_runner.run(lambda: _prove_install_state(ports.verify_install_boundary))
    return current_runner.result()


def _prove_install_state(proof: Callable[[], None]) -> bool:
    """补偿完成后复证安装模式要求的 CodeGraph/维护状态。"""
    _verify_install_boundary(proof)
    return True


def _run_install_lifecycle_commands(
    manifest: SystemdInstallManifest,
    policy: SystemdInstallPolicy,
    run: Callable[[tuple[str, ...]], None],
) -> None:
    enable_units = policy.enable_names(manifest.enable_units)
    restart_units = policy.restart_names(manifest.immediate_restart_units)
    if enable_units:
        _run_systemctl(run, ("systemctl", "enable", *enable_units))
    if restart_units:
        _run_systemctl(run, ("systemctl", "restart", *restart_units))


def _run_systemctl(run: Callable[[tuple[str, ...]], None], command: tuple[str, ...]) -> None:
    try:
        run(command)
    except _TERMINATION_EXCEPTIONS:
        raise
    except SystemdInstallTransactionError:
        raise
    except Exception as error:
        raise SystemdInstallTransactionError("systemd 安装命令失败") from error


def _raise_after_body_failure(
    error: BaseException,
    prepared: _PreparedInstall | None,
    compensation: _CompensationResult | None,
) -> None:
    if prepared is None:
        _raise_unmutated_failure(error)
    if compensation is None or not compensation.proven:
        _raise_safety_unproven("全量 systemd 安装事务失败；安全状态未证明", error, compensation)
    if isinstance(error, _TERMINATION_EXCEPTIONS):
        raise error
    raise SystemdInstallTransactionError(
        "全量 systemd 安装事务失败；已回滚受管 unit 与服务状态"
    ) from error


def _raise_after_installer_lock_exit_failure(error: BaseException) -> None:
    """锁退出异常后无法证明所有权，旧原像不得在重新取锁后继续使用。"""
    _raise_safety_unproven(
        "全量 systemd 安装事务安装锁退出异常；安全状态未证明",
        error,
        None,
    )


def _raise_safety_unproven(
    message: str,
    error: BaseException,
    compensation: _CompensationResult | None,
) -> None:
    cause = compensation.deferred_interrupt if compensation is not None else None
    raise _SafetyUnprovenInstallError(message) from (cause or error)


def _raise_unmutated_failure(error: BaseException) -> None:
    if isinstance(error, _TERMINATION_EXCEPTIONS):
        raise error
    if isinstance(error, SystemdInstallTransactionError):
        raise error
    raise SystemdInstallTransactionError("全量 systemd 安装事务失败") from error


__all__ = [
    "SystemdInstallManifest",
    "SystemdInstallPorts",
    "SystemdInstallReport",
    "SystemdInstallTransactionError",
    "SystemdStageReceiptFileSnapshot",
    "SystemdUnitFileSnapshot",
    "SystemdUnitActivationMode",
    "SystemdUnitInstallSpec",
    "SystemdUnitPayload",
    "SystemdUnitProcessState",
    "SystemdUnitState",
    "install_systemd_from_manifest_path",
    "install_systemd_guarded_stage_transaction",
    "install_systemd_install_only_from_manifest_path",
    "install_systemd_install_only_transaction",
    "install_systemd_maintenance_stage_from_manifest_path",
    "install_systemd_maintenance_stage_transaction",
    "install_systemd_transaction",
    "load_install_manifest",
    "main",
    "prepare_systemd_guarded_stage_from_manifest_path",
]


if __name__ == "__main__":
    raise SystemExit(main())
