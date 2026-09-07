"""组合激活失败时在既有转换锁内收敛双服务边界。"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass

from codev_platform.ops.reindex_compensation import ExhaustiveCompensationRunner
from codev_platform.ops.reindex_maintenance_final_proof import (
    FinalMaintenanceProof,
    collect_final_maintenance_proof,
)


Action = Callable[[], None]


@dataclass(frozen=True, slots=True)
class CombinedMaintenanceSettlement:
    """锁内补偿动作和最终证明的完整结果。"""

    reindex_gate_activated: bool
    codegraph_prepared: bool
    reindex_safety_restored: bool
    reindex_stopped: bool
    webhook_prepared: bool
    final_proof: FinalMaintenanceProof

    @property
    def proven(self) -> bool:
        return (
            self.reindex_gate_activated
            and self.codegraph_prepared
            and self.reindex_safety_restored
            and self.reindex_stopped
            and self.webhook_prepared
            and self.final_proof.proven
        )


def settle_combined_maintenance_while_transition_locked(
    *,
    activate_reindex_gate_locked: Action | None = None,
    prepare_codegraph: Action | None = None,
    restore_reindex_safety: Action | None = None,
    stop_reindex: Action | None = None,
    prove_external_workers: Action | None = None,
    prove_reindex_stopped: Action | None = None,
    prove_codegraph_held: Action | None = None,
    prepare_webhook: Action | None = None,
    prove_webhook_closed: Action | None = None,
) -> CombinedMaintenanceSettlement:
    """不取得新锁，先恢复 marker，再由 CodeGraph 生命周期收敛耐久边界。"""
    _require_transition_guard()
    maintenance = _maintenance_module()
    actions = (
        _default_activate_reindex_gate_locked
        if activate_reindex_gate_locked is None
        else activate_reindex_gate_locked,
        _default_prepare_codegraph if prepare_codegraph is None else prepare_codegraph,
        _default_restore_reindex_safety
        if restore_reindex_safety is None
        else restore_reindex_safety,
        _default_stop_reindex if stop_reindex is None else stop_reindex,
        maintenance._default_external_worker_proof
        if prove_external_workers is None
        else prove_external_workers,
        maintenance._default_stop_proof if prove_reindex_stopped is None else prove_reindex_stopped,
        maintenance._default_codegraph_maintenance_proof
        if prove_codegraph_held is None
        else prove_codegraph_held,
        maintenance._default_webhook_prepare if prepare_webhook is None else prepare_webhook,
        (
            maintenance._default_webhook_maintenance_proof
            if prove_webhook_closed is None
            else prove_webhook_closed
        ),
    )
    if not all(callable(action) for action in actions):
        raise maintenance.ReindexMaintenanceError("组合维护收敛适配器不可用")
    (
        marker,
        codegraph,
        dropin,
        stop,
        prove_external,
        prove_reindex,
        prove_codegraph,
        webhook,
        prove_webhook,
    ) = actions
    runner = ExhaustiveCompensationRunner()
    attempt = runner.attempt
    marker_ok = attempt(marker)
    codegraph_ok = attempt(codegraph)
    webhook_ok = attempt(webhook)
    dropin_ok = attempt(dropin)
    stop_ok = attempt(stop)
    final_proof = collect_final_maintenance_proof(
        attempt=attempt,
        prove_external_workers=prove_external,
        prove_reindex_stopped=prove_reindex,
        prove_codegraph_held=prove_codegraph,
        prove_webhook_closed=prove_webhook,
    )
    result = CombinedMaintenanceSettlement(
        reindex_gate_activated=marker_ok,
        codegraph_prepared=codegraph_ok,
        reindex_safety_restored=dropin_ok,
        reindex_stopped=stop_ok,
        webhook_prepared=webhook_ok,
        final_proof=final_proof,
    )
    runner.raise_deferred_interruption()
    return result


def _default_activate_reindex_gate_locked() -> None:
    from codev_platform.reindex.maintenance_gate import (
        activate_maintenance_gate_while_systemd_transition_locked,
    )

    activate_maintenance_gate_while_systemd_transition_locked()


def _default_prepare_codegraph() -> None:
    from codev_platform.ops.reindex_codegraph_lifecycle import (
        prepare_codegraph_maintenance,
    )

    prepare_codegraph_maintenance()


def _default_restore_reindex_safety() -> None:
    maintenance = _maintenance_module()
    maintenance._restore_safety_dropin(
        maintenance._DROPIN_PATH,
        maintenance._default_command_runner,
    )


def _default_stop_reindex() -> None:
    maintenance = _maintenance_module()
    maintenance._stop_reindex_safely(maintenance._default_command_runner)


def _maintenance_module():
    from codev_platform.ops import reindex_maintenance

    return reindex_maintenance


def _require_transition_guard() -> None:
    from codev_platform.reindex.maintenance_gate import (
        require_systemd_transition_guard,
    )

    require_systemd_transition_guard()


__all__ = [
    "CombinedMaintenanceSettlement",
    "settle_combined_maintenance_while_transition_locked",
]
