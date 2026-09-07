"""reindex 维护窗口的 prepare 编排叶子。"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

from codev_platform.ops.reindex_compensation import ExhaustiveCompensationRunner
from codev_platform.ops.reindex_maintenance_final_proof import (
    FinalMaintenanceProof,
    collect_final_maintenance_proof,
)


CommandRunner = Callable[..., object]
Proof = Callable[[], None]
GateActivator = Callable[[], None]


@dataclass(frozen=True)
class _PrepareCompensationResult:
    """prepare 失败后各个安全边界的最终证明结果。"""

    gate_activated: bool
    dropin_restored: bool
    reindex_stopped: bool
    codegraph_reprepared: bool
    webhook_reprepared: bool
    final_proof: FinalMaintenanceProof
    deferred_interruption: BaseException | None

    @property
    def safety_proven(self) -> bool:
        return (
            self.gate_activated
            and self.dropin_restored
            and self.reindex_stopped
            and self.codegraph_reprepared
            and self.webhook_reprepared
            and self.final_proof.proven
        )


class _DefaultMarkerActivationError(RuntimeError):
    """默认 marker 在转换临界区内发布失败，必须转入安全补偿。"""


class _TransitionIntentEntryError(RuntimeError):
    """尚未进入 intent 临界区即失败；调用方不得越权执行补偿。"""


def prepare_reindex_maintenance(
    *,
    maintenance: object,
    platform_name: str | None,
    dropin_path: Path,
    command_runner: CommandRunner | None,
    stop_proof: Proof | None,
    external_worker_proof: Proof | None,
    gate_activator: GateActivator | None,
    codegraph_prepare: Proof | None,
    codegraph_maintenance_proof: Proof | None,
    webhook_prepare: Proof | None,
    webhook_maintenance_proof: Proof | None,
) -> None:
    """先发布维护 marker，再在同一转换域收敛 CodeGraph、Webhook 与 reindex。"""
    maintenance._require_linux(platform_name)
    path = maintenance._validate_dropin_path(dropin_path)
    run = maintenance._default_command_runner if command_runner is None else command_runner
    prove_reindex = maintenance._default_stop_proof if stop_proof is None else stop_proof
    prove_external = (
        maintenance._default_external_worker_proof
        if external_worker_proof is None
        else external_worker_proof
    )
    activate_gate = gate_activator
    prepare_codegraph = (
        _default_codegraph_prepare if codegraph_prepare is None else codegraph_prepare
    )
    prove_codegraph = (
        _default_codegraph_maintenance_proof
        if codegraph_maintenance_proof is None
        else codegraph_maintenance_proof
    )
    prepare_webhook = webhook_prepare
    prove_webhook = webhook_maintenance_proof
    if not all(
        callable(item)
        for item in (
            run,
            prove_reindex,
            prove_external,
            prepare_codegraph,
            prove_codegraph,
            prepare_webhook,
            prove_webhook,
        )
    ):
        raise maintenance.ReindexMaintenanceError("维护窗口适配器不可用")

    try:
        if activate_gate is not None:
            _activate_gate_with_bounded_preemption(
                maintenance=maintenance,
                activate_gate=activate_gate,
                path=path,
                run=run,
                prove_reindex=prove_reindex,
                prove_external=prove_external,
            )
        _prepare_codegraph_with_bounded_preemption(
            maintenance=maintenance,
            path=path,
            run=run,
            prove_reindex=prove_reindex,
            prove_external=prove_external,
            prepare_codegraph=prepare_codegraph,
            prepare_webhook=prepare_webhook,
            activate_gate_while_intent_locked=(
                _default_activate_gate_while_transition_intent_locked
                if activate_gate is None
                else None
            ),
        )
    except _TransitionIntentEntryError as error:
        original = error.__cause__ if error.__cause__ is not None else error
        if not isinstance(original, Exception) or isinstance(original, MemoryError):
            raise original from None
        raise maintenance.ReindexMaintenanceError(
            "无法取得 systemd 转换意图；运行态未更改"
        ) from original
    except BaseException as error:
        compensation = _compensate_failed_prepare(
            maintenance=maintenance,
            path=path,
            run=run,
            prove_reindex=prove_reindex,
            prove_external=prove_external,
            activate_gate=activate_gate,
            prepare_codegraph=prepare_codegraph,
            prove_codegraph=prove_codegraph,
            prepare_webhook=prepare_webhook,
            prove_webhook=prove_webhook,
        )
        _raise_prepare_interruption(error, compensation)
        if isinstance(error, maintenance.ReindexMaintenanceError):
            message = str(error)
        elif isinstance(error, _DefaultMarkerActivationError):
            message = "默认维护 marker 启用失败"
        else:
            message = "维护边界准备失败"
        suffix = "；已证明安全停机状态" if compensation.safety_proven else "；安全状态未证明"
        raise maintenance.ReindexMaintenanceError(f"{message}{suffix}") from error
    try:
        maintenance._restore_safety_dropin(path, run)
        maintenance._run_systemctl(run, ("systemctl", "stop", maintenance._REINDEX_UNIT))
        maintenance._run_systemctl(
            run,
            ("systemctl", "reset-failed", maintenance._REINDEX_UNIT),
        )
        prove_external()
        prove_reindex()
        prove_codegraph()
        prove_webhook()
    except BaseException as error:
        compensation = _compensate_failed_prepare(
            maintenance=maintenance,
            path=path,
            run=run,
            prove_reindex=prove_reindex,
            prove_external=prove_external,
            activate_gate=activate_gate,
            prepare_codegraph=prepare_codegraph,
            prove_codegraph=prove_codegraph,
            prepare_webhook=prepare_webhook,
            prove_webhook=prove_webhook,
        )
        _raise_prepare_interruption(error, compensation)
        message = (
            "reindex 维护窗口准备失败；已证明安全停机状态"
            if compensation.safety_proven
            else "reindex 维护窗口准备失败；安全状态未证明"
        )
        raise maintenance.ReindexMaintenanceError(message) from error


def _activate_gate_with_bounded_preemption(
    *,
    maintenance: object,
    activate_gate: GateActivator,
    path: Path,
    run: CommandRunner,
    prove_reindex: Proof,
    prove_external: Proof,
) -> None:
    """仅在有界 gate 锁忙时预停 reindex 后重试一次。"""
    try:
        activate_gate()
        return
    except MemoryError:
        raise
    except Exception as error:
        if not _is_gate_lock_busy(error):
            raise maintenance.ReindexMaintenanceError("无法启用 reindex 维护门禁") from error
    _preempt_reindex_for_gate_retry(
        maintenance=maintenance,
        path=path,
        run=run,
        prove_reindex=prove_reindex,
        prove_external=prove_external,
    )
    try:
        activate_gate()
    except BaseException as error:
        if isinstance(error, (KeyboardInterrupt, SystemExit, MemoryError)):
            raise
        raise maintenance.ReindexMaintenanceError("维护 marker 未启用") from error


def _prepare_codegraph_with_bounded_preemption(
    *,
    maintenance: object,
    path: Path,
    run: CommandRunner,
    prove_reindex: Proof,
    prove_external: Proof,
    prepare_codegraph: Proof,
    prepare_webhook: Proof | None = None,
    activate_gate_while_intent_locked: Proof | None,
) -> None:
    """取得 intent 后先发布 marker，首次 gate 忙时再预停并仅重试一次。"""
    entered = False
    try:
        intent = _codegraph_transition_intent()
        with intent as gate_lock:
            entered = True
            if activate_gate_while_intent_locked is not None:
                try:
                    activate_gate_while_intent_locked()
                except (KeyboardInterrupt, SystemExit, MemoryError):
                    raise
                except Exception as error:
                    raise _DefaultMarkerActivationError from error
            try:
                _prepare_codegraph_with_transition_lock(
                    prepare_codegraph,
                    gate_lock=gate_lock,
                    prepare_webhook=prepare_webhook,
                )
                return
            except MemoryError:
                raise
            except Exception as error:
                if not _is_gate_lock_busy(error):
                    raise
            _preempt_reindex_for_gate_retry(
                maintenance=maintenance,
                path=path,
                run=run,
                prove_reindex=prove_reindex,
                prove_external=prove_external,
            )
            _prepare_codegraph_with_transition_lock(
                prepare_codegraph,
                gate_lock=gate_lock,
                prepare_webhook=prepare_webhook,
            )
    except BaseException as error:
        if not entered:
            raise _TransitionIntentEntryError from error
        raise


def _is_gate_lock_busy(error: Exception) -> bool:
    from codev_platform.reindex.maintenance_gate import MaintenanceGateLockBusyError

    return isinstance(error, MaintenanceGateLockBusyError)


def _preempt_reindex_for_gate_retry(
    *,
    maintenance: object,
    path: Path,
    run: CommandRunner,
    prove_reindex: Proof,
    prove_external: Proof,
) -> None:
    maintenance._restore_safety_dropin(path, run)
    maintenance._stop_reindex_safely(run)
    prove_external()
    prove_reindex()


def _compensate_failed_prepare(
    *,
    maintenance: object,
    path: Path,
    run: CommandRunner,
    prove_reindex: Proof,
    prove_external: Proof,
    activate_gate: GateActivator | None,
    prepare_codegraph: Proof,
    prove_codegraph: Proof,
    prepare_webhook: Proof,
    prove_webhook: Proof,
) -> _PrepareCompensationResult:
    """默认路径持有 intent，按 marker、reindex、gate/CodeGraph 顺序收敛。"""
    runner = ExhaustiveCompensationRunner()
    attempt = runner.attempt
    if activate_gate is None:
        outcomes = _compensate_default_prepare_boundaries(
            maintenance=maintenance,
            path=path,
            run=run,
            prepare_codegraph=prepare_codegraph,
            prepare_webhook=prepare_webhook,
            attempt=attempt,
        )
        gate_activated, dropin_restored, reindex_stopped, codegraph_reprepared = outcomes
        webhook_reprepared = codegraph_reprepared
    else:
        gate_activated = attempt(activate_gate)
        dropin_restored, reindex_stopped = _settle_reindex_prepare(
            maintenance=maintenance,
            path=path,
            run=run,
            attempt=attempt,
        )
        codegraph_reprepared = attempt(
            lambda: _prepare_codegraph_with_new_transition(
                prepare_codegraph,
                prepare_webhook=prepare_webhook,
            )
        )
        webhook_reprepared = codegraph_reprepared
    final_proof = collect_final_maintenance_proof(
        attempt=attempt,
        prove_external_workers=prove_external,
        prove_reindex_stopped=prove_reindex,
        prove_codegraph_held=prove_codegraph,
        prove_webhook_closed=prove_webhook,
    )
    return _PrepareCompensationResult(
        gate_activated=gate_activated,
        dropin_restored=dropin_restored,
        reindex_stopped=reindex_stopped,
        codegraph_reprepared=codegraph_reprepared,
        webhook_reprepared=webhook_reprepared,
        final_proof=final_proof,
        deferred_interruption=runner.deferred_interruption,
    )


def _raise_prepare_interruption(
    original: BaseException,
    compensation: _PrepareCompensationResult,
) -> None:
    """补偿已耗尽后，优先保留原始终止/取消语义，再交还补偿期中断。"""
    if not isinstance(original, Exception) or isinstance(original, MemoryError):
        raise original
    if compensation.deferred_interruption is not None:
        raise compensation.deferred_interruption


def _compensate_default_prepare_boundaries(
    *,
    maintenance: object,
    path: Path,
    run: CommandRunner,
    prepare_codegraph: Proof,
    prepare_webhook: Proof,
    attempt: Callable[[Proof], bool],
) -> tuple[bool, bool, bool, bool]:
    """marker 已耐久后才允许预停旧读者，再取得 gate EX。"""
    outcomes = {"gate": False, "dropin": False, "reindex": False, "codegraph": False}

    def settle_default_boundaries() -> None:
        with _codegraph_transition_intent() as gate_lock:
            outcomes["gate"] = attempt(_default_activate_gate_while_transition_intent_locked)
            outcomes["dropin"], outcomes["reindex"] = _settle_reindex_prepare(
                maintenance=maintenance,
                path=path,
                run=run,
                attempt=attempt,
            )
            outcomes["codegraph"] = attempt(
                lambda: _prepare_codegraph_with_transition_lock(
                    prepare_codegraph,
                    gate_lock=gate_lock,
                    prepare_webhook=prepare_webhook,
                )
            )

    attempt(settle_default_boundaries)
    return (
        outcomes["gate"],
        outcomes["dropin"],
        outcomes["reindex"],
        outcomes["codegraph"],
    )


def _settle_reindex_prepare(
    *,
    maintenance: object,
    path: Path,
    run: CommandRunner,
    attempt: Callable[[Proof], bool],
) -> tuple[bool, bool]:
    """只收敛 reindex 配置与进程；证明必须留到全部动作完成后。"""
    dropin_restored = attempt(lambda: maintenance._restore_safety_dropin(path, run))
    reindex_stopped = attempt(lambda: maintenance._stop_reindex_safely(run))
    return dropin_restored, reindex_stopped


def _default_codegraph_prepare() -> None:
    from codev_platform.ops.reindex_codegraph_lifecycle import (
        prepare_codegraph_maintenance,
    )

    prepare_codegraph_maintenance()


def _prepare_codegraph_with_transition_lock(
    prepare_codegraph: Proof,
    *,
    gate_lock: Callable,
    prepare_webhook: Proof | None = None,
) -> None:
    """在 intent 所属 gate EX 临界区收敛 CodeGraph 与可选入口关闭动作。"""
    from codev_platform.ops.reindex_codegraph_transition import codegraph_systemd_transition

    with codegraph_systemd_transition(lock_factory=gate_lock):
        prepare_codegraph()
        if prepare_webhook is not None:
            prepare_webhook()


def _prepare_codegraph_with_new_transition(
    prepare_codegraph: Proof,
    *,
    prepare_webhook: Proof | None = None,
) -> None:
    with _codegraph_transition_intent() as gate_lock:
        _prepare_codegraph_with_transition_lock(
            prepare_codegraph,
            gate_lock=gate_lock,
            prepare_webhook=prepare_webhook,
        )


def _codegraph_transition_intent():
    from codev_platform.reindex.maintenance_gate import (
        maintenance_systemd_transition_intent,
    )

    return maintenance_systemd_transition_intent()


def _default_activate_gate_while_transition_intent_locked() -> None:
    from codev_platform.reindex.maintenance_gate import (
        activate_maintenance_gate_while_systemd_transition_intent_locked,
    )

    activate_maintenance_gate_while_systemd_transition_intent_locked()


def _default_codegraph_maintenance_proof() -> None:
    from codev_platform.ops.reindex_codegraph_lifecycle import (
        verify_codegraph_maintenance,
    )

    verify_codegraph_maintenance()


__all__ = ["prepare_reindex_maintenance"]
