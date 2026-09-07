"""reindex 维护窗口的待命恢复编排。"""

from __future__ import annotations

import re
import sys
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

from codev_platform.ops.reindex_maintenance_final_proof import (
    FinalMaintenanceProof,
    collect_final_maintenance_proof,
)
from codev_platform.reindex.file_durability import durable_unlink

_RESTORE_STANDBY_TTL_SEC = 120.0
_REINDEX_UNIT = "codev-reindex.service"

CommandRunner = Callable[..., object]
StopProof = Callable[[], None]
StabilityProbe = Callable[[], None]
ExternalWorkerProof = Callable[[], None]
GateActivator = Callable[[], None]
GateDeactivator = Callable[[], None]
GateActiveReader = Callable[[], bool]
StandbyArmer = Callable[[], object]
UnitInvocationReader = Callable[[], str]
StandbyClaimer = Callable[[str, str], object]
StandbyRenewer = Callable[[str, str], object]
StandbyCompleter = Callable[[str, str], None]


@dataclass(frozen=True)
class _RestoreCompensationResult:
    """失败补偿的每一步证据；缺任一步均不得宣称已安全收敛。"""

    marker_reset: bool
    dropin_restored: bool
    reindex_stopped: bool
    codegraph_reprepared: bool
    webhook_reprepared: bool
    final_proof: FinalMaintenanceProof

    @property
    def safety_proven(self) -> bool:
        return (
            self.marker_reset
            and self.dropin_restored
            and self.reindex_stopped
            and self.codegraph_reprepared
            and self.webhook_reprepared
            and self.final_proof.proven
        )


def restore_reindex_maintenance(
    *,
    platform_name: str | None = None,
    dropin_path: Path | None = None,
    command_runner: CommandRunner | None = None,
    stop_proof: StopProof | None = None,
    stability_probe: StabilityProbe | None = None,
    external_worker_proof: ExternalWorkerProof | None = None,
    gate_deactivator: GateDeactivator | None = None,
    gate_activator: GateActivator | None = None,
    gate_active_reader: GateActiveReader | None = None,
    standby_armer: StandbyArmer | None = None,
    unit_invocation_reader: UnitInvocationReader | None = None,
    standby_claimer: StandbyClaimer | None = None,
    standby_renewer: StandbyRenewer | None = None,
    standby_completer: StandbyCompleter | None = None,
    codegraph_prepare: Callable[[], None] | None = None,
    codegraph_maintenance_proof: Callable[[], None] | None = None,
    webhook_prepare: Callable[[], None] | None = None,
    webhook_maintenance_proof: Callable[[], None] | None = None,
) -> None:
    """只启动待命实例，最后删除 marker 交接实际写权限。"""
    maintenance = _maintenance_module()
    maintenance._require_linux(platform_name)
    path = maintenance._validate_dropin_path(
        maintenance._DROPIN_PATH if dropin_path is None else dropin_path
    )
    run = maintenance._default_command_runner if command_runner is None else command_runner
    prove = maintenance._default_stop_proof if stop_proof is None else stop_proof
    prove_external = (
        maintenance._default_external_worker_proof
        if external_worker_proof is None
        else external_worker_proof
    )
    reset_marker = maintenance._default_gate_activator if gate_activator is None else gate_activator
    gate_active = (
        maintenance._default_gate_active_reader
        if gate_active_reader is None
        else gate_active_reader
    )
    arm = _default_standby_armer if standby_armer is None else standby_armer
    read_invocation = (
        _default_unit_invocation_reader(run)
        if unit_invocation_reader is None
        else unit_invocation_reader
    )
    claim = _default_standby_claimer if standby_claimer is None else standby_claimer
    renew = _default_standby_renewer if standby_renewer is None else standby_renewer
    complete = _default_standby_completer if standby_completer is None else standby_completer
    prepare_codegraph = codegraph_prepare
    prove_codegraph = codegraph_maintenance_proof
    prepare_webhook = webhook_prepare
    prove_webhook = webhook_maintenance_proof
    _ = gate_deactivator
    _require_callables(
        maintenance,
        run,
        prove,
        prove_external,
        reset_marker,
        gate_active,
        arm,
        read_invocation,
        claim,
        renew,
        complete,
        prepare_codegraph,
        prove_codegraph,
        prepare_webhook,
        prove_webhook,
    )
    maintenance._require_owned_dropin(path)
    maintenance._require_active_gate(gate_active)
    stable = stability_probe
    if stable is None:
        restart_baseline = maintenance._read_restart_count(run)
        stable = maintenance._default_stability_probe(run, expected_restarts=restart_baseline)
    if not callable(stable):
        raise maintenance.ReindexMaintenanceError("reindex 稳定性探针不可用")
    try:
        prove_codegraph()
        prove_webhook()
        prove()
        prove_external()
        record = arm()
        generation = _record_generation(record, maintenance)
        maintenance._run_systemctl(run, ("systemctl", "start", _REINDEX_UNIT))
        invocation_id = _unit_invocation_id(read_invocation(), maintenance)
        claim(generation, invocation_id)
        renew(generation, invocation_id)
        stable()
        renew(generation, invocation_id)
        _remove_owned_dropin(maintenance, path)
        maintenance._run_systemctl(run, ("systemctl", "daemon-reload"))
        maintenance._require_baseline_restart(run)
        stable()
        renew(generation, invocation_id)
        _require_claimed_invocation(read_invocation, invocation_id, maintenance)
        prove_codegraph()
        prove_webhook()
        maintenance._run_systemctl(run, ("systemctl", "enable", _REINDEX_UNIT))
        maintenance._run_systemctl(
            run,
            ("systemctl", "is-enabled", "--quiet", _REINDEX_UNIT),
        )
        complete(generation, invocation_id)
    except BaseException as error:
        compensation = _compensate_failed_restore(
            maintenance,
            path,
            run,
            prove,
            prove_external,
            reset_marker,
            prepare_codegraph,
            prove_codegraph,
            prepare_webhook,
            prove_webhook,
        )
        if isinstance(error, (KeyboardInterrupt, SystemExit, MemoryError)):
            raise
        message = (
            "reindex 维护窗口恢复失败；已证明安全停机状态"
            if compensation.safety_proven
            else "reindex 维护窗口恢复失败；安全状态未证明"
        )
        raise maintenance.ReindexMaintenanceError(message) from error


def _require_callables(maintenance: object, *items: object) -> None:
    if not all(callable(item) for item in items):
        raise maintenance.ReindexMaintenanceError("维护窗口适配器不可用")


def _remove_owned_dropin(maintenance: object, path: Path) -> None:
    """生产固定路径使用可信 dirfd 删除；自定义测试路径保留耐久删除。"""
    maintenance._require_owned_dropin(path)
    if path == maintenance._DROPIN_PATH and sys.platform.startswith("linux"):
        from codev_platform.ops.reindex_codegraph_resume_managed_path import (
            read_optional_root_owned_regular_file_snapshot,
            remove_root_owned_regular_file,
        )

        if remove_root_owned_regular_file(path) is not True:
            raise maintenance.ReindexMaintenanceError("reindex 维护 drop-in 无法删除")
        if (
            read_optional_root_owned_regular_file_snapshot(
                path,
                max_bytes=maintenance._MAX_DROPIN_BYTES,
            )
            is not None
        ):
            raise maintenance.ReindexMaintenanceError("reindex 维护 drop-in 删除状态无法证明")
        return
    durable_unlink(path)
    if path.exists() or path.is_symlink():
        raise maintenance.ReindexMaintenanceError("reindex 维护 drop-in 删除状态无法证明")


def _record_generation(record: object, maintenance: object) -> str:
    generation = getattr(record, "generation", None)
    if type(generation) is not str or re.fullmatch(r"[0-9a-f]{32}", generation) is None:
        raise maintenance.ReindexMaintenanceError("reindex 恢复待命 generation 无效")
    return generation


def _unit_invocation_id(value: object, maintenance: object) -> str:
    if type(value) is not str or re.fullmatch(r"[0-9a-f]{32}", value) is None:
        raise maintenance.ReindexMaintenanceError("codev-reindex InvocationID 无效")
    return value


def _require_claimed_invocation(
    reader: UnitInvocationReader,
    claimed_invocation_id: str,
    maintenance: object,
) -> None:
    """基线恢复后再次确认仍是同一待命实例，才允许删除 marker。"""
    current = _unit_invocation_id(reader(), maintenance)
    if current != claimed_invocation_id:
        raise maintenance.ReindexMaintenanceError("codev-reindex 待命实例 InvocationID 已变化")


def _compensate_failed_restore(
    maintenance: object,
    path: Path,
    run: CommandRunner,
    prove: StopProof,
    prove_external: ExternalWorkerProof,
    reset_marker: GateActivator,
    prepare_codegraph: Callable[[], None],
    prove_codegraph: Callable[[], None],
    prepare_webhook: Callable[[], None],
    prove_webhook: Callable[[], None],
) -> _RestoreCompensationResult:
    """无论异常类型都尝试回到 maintenance + Restart=no + 已停机的失败关闭状态。"""
    marker_reset = _attempt_compensation_action(reset_marker)
    codegraph_reprepared = _attempt_compensation_action(
        lambda: _prepare_codegraph_with_transition_lock(prepare_codegraph)
    )
    webhook_reprepared = _attempt_compensation_action(prepare_webhook)
    dropin_restored = _attempt_compensation_action(
        lambda: maintenance._restore_safety_dropin(path, run)
    )
    reindex_stopped = _attempt_compensation_action(lambda: maintenance._stop_reindex_safely(run))
    final_proof = collect_final_maintenance_proof(
        attempt=_attempt_compensation_action,
        prove_external_workers=prove_external,
        prove_reindex_stopped=prove,
        prove_codegraph_held=prove_codegraph,
        prove_webhook_closed=prove_webhook,
    )
    return _RestoreCompensationResult(
        marker_reset=marker_reset,
        dropin_restored=dropin_restored,
        reindex_stopped=reindex_stopped,
        codegraph_reprepared=codegraph_reprepared,
        webhook_reprepared=webhook_reprepared,
        final_proof=final_proof,
    )


def _attempt_compensation_action(action: Callable[[], None]) -> bool:
    """复用维护 facade 的中断语义，避免 restore 叶子漂移。"""
    return _maintenance_module()._attempt_compensation_action(action)


def _prepare_codegraph_with_transition_lock(prepare_codegraph: Callable[[], None]) -> None:
    """restore 补偿复用唯一转换锁，避免与 installer、迁移或 prepare 并发变更 unit。"""
    from codev_platform.ops.reindex_codegraph_transition import codegraph_systemd_transition

    with codegraph_systemd_transition(lock_factory=_codegraph_transition_lock):
        prepare_codegraph()


def _codegraph_transition_lock():
    from codev_platform.reindex.maintenance_gate import maintenance_systemd_transition_lock

    return maintenance_systemd_transition_lock()


def _default_standby_armer() -> object:
    from codev_platform.reindex.maintenance_gate import arm_restore_standby

    return arm_restore_standby(ttl_sec=_RESTORE_STANDBY_TTL_SEC)


def _default_standby_claimer(generation: str, invocation_id: str) -> object:
    from codev_platform.reindex.maintenance_gate import claim_restore_standby

    return claim_restore_standby(generation=generation, invocation_id=invocation_id)


def _default_standby_renewer(generation: str, invocation_id: str) -> object:
    from codev_platform.reindex.maintenance_gate import renew_claimed_restore_standby

    return renew_claimed_restore_standby(
        generation=generation,
        invocation_id=invocation_id,
        ttl_sec=_RESTORE_STANDBY_TTL_SEC,
    )


def _default_standby_completer(generation: str, invocation_id: str) -> None:
    from codev_platform.reindex.maintenance_gate import complete_restore_standby

    complete_restore_standby(generation=generation, invocation_id=invocation_id)


def _default_unit_invocation_reader(run: CommandRunner) -> UnitInvocationReader:
    def read() -> str:
        maintenance = _maintenance_module()
        values = maintenance._read_systemctl_properties(
            run,
            unit=_REINDEX_UNIT,
            properties=("ActiveState", "SubState", "InvocationID"),
            error_message="codev-reindex 待命实例状态格式无效",
        )
        if values["ActiveState"] != "active" or values["SubState"] != "running":
            raise maintenance.ReindexMaintenanceError("codev-reindex 待命实例未处于运行态")
        return values["InvocationID"]

    return read


def _maintenance_module():
    """延迟取得通用 systemd 文件与证明基元，避免 facade 导入环。"""
    from . import reindex_maintenance

    return reindex_maintenance


__all__ = ["restore_reindex_maintenance"]
