"""CodeGraph 唯一受控恢复状态机。"""

from __future__ import annotations

from collections.abc import Callable, Iterable
from contextlib import AbstractContextManager
from dataclasses import dataclass
from pathlib import Path
from typing import NoReturn, TypeVar

from codev_platform.ops.reindex_codegraph_resume_context import CodegraphResumeContext
from codev_platform.ops.reindex_codegraph_resume_adapters import (
    default_clear_codegraph_startup_bridge as _default_clear_codegraph_startup_bridge,
    default_codegraph_effective_payload_proof as _default_codegraph_effective_payload_proof,
    default_codegraph_health_proof as _default_codegraph_health_proof,
    default_codegraph_identity_reader as _default_codegraph_identity_reader,
    default_codegraph_maintenance_proof as _default_codegraph_maintenance_proof,
    default_codegraph_running_proof as _default_codegraph_running_proof,
    default_codegraph_stability_proof as _default_codegraph_stability_proof,
    default_configuration_proof as _default_configuration_proof,
    default_context_resolver as _default_context_resolver,
    default_enable_codegraph as _default_enable_codegraph,
    default_inspect_maintenance as _default_inspect_maintenance,
    default_manifest_proof as _default_manifest_proof,
    default_install_codegraph_startup_bridge as _default_install_codegraph_startup_bridge,
    default_operation_leases as _default_operation_leases,
    default_prepare_maintenance as _default_prepare_maintenance,
    default_prepare_reindex_handoff as _default_prepare_reindex_handoff,
    default_complete_reindex_handoff_locked as _default_complete_reindex_handoff_locked,
    default_remove_codegraph_hold as _default_remove_codegraph_hold,
    default_remove_codegraph_startup_bridge as _default_remove_codegraph_startup_bridge,
    default_remove_runtime_mask as _default_remove_runtime_mask,
    default_settle_maintenance_locked as _default_settle_maintenance_locked,
    default_staged_payload_proof as _default_staged_payload_proof,
    default_codegraph_normal_effective_payload_proof as _default_codegraph_normal_effective_payload_proof,
    default_start_codegraph as _default_start_codegraph,
    default_transition_session_lock as _default_transition_session_lock,
    default_transition_lock as _default_transition_lock,
)
from codev_platform.ops.reindex_codegraph_runtime_identity import CodegraphRuntimeIdentity
from codev_platform.ops.reindex_restore_handoff import ReindexRestoreHandoff


class CodegraphResumeError(RuntimeError):
    """CodeGraph 受控恢复未完成或无法证明安全状态。"""


ContextResolver = Callable[[str, str], CodegraphResumeContext]
ContextProof = Callable[[CodegraphResumeContext], None]
MaintenanceAction = Callable[[], None]
OperationLeases = Callable[[Iterable[Path]], AbstractContextManager[None]]
SystemdTransitionLock = Callable[[], AbstractContextManager[None]]
ReindexHandoffPreparer = Callable[
    [CodegraphResumeContext, CodegraphRuntimeIdentity], ReindexRestoreHandoff
]
HandoffAction = Callable[[ReindexRestoreHandoff], None]
LockedSettlement = Callable[[CodegraphResumeContext], bool]
RuntimeIdentityReader = Callable[[CodegraphResumeContext], CodegraphRuntimeIdentity]
RuntimeStabilityProof = Callable[[CodegraphResumeContext, CodegraphRuntimeIdentity], None]
_TransitionResult = TypeVar("_TransitionResult")


@dataclass(frozen=True, slots=True)
class CodegraphResumePorts:
    """恢复编排的窄依赖端口；状态机本身不直接耦合 systemd 或 HTTP。"""

    resolve_context: ContextResolver
    verify_configuration: ContextProof
    prepare_maintenance: MaintenanceAction
    inspect_maintenance: MaintenanceAction
    operation_leases: OperationLeases
    prove_manifest: ContextProof
    prepare_reindex_handoff: ReindexHandoffPreparer
    complete_reindex_handoff_locked: HandoffAction
    prove_codegraph_maintenance: MaintenanceAction
    clear_codegraph_startup_bridge: ContextProof
    prove_staged_payload: ContextProof
    install_codegraph_startup_bridge: ContextProof
    remove_runtime_mask: MaintenanceAction
    prove_codegraph_effective_payload: ContextProof
    enable_codegraph: MaintenanceAction
    remove_codegraph_hold: MaintenanceAction
    start_codegraph: MaintenanceAction
    prove_codegraph_running: ContextProof
    read_codegraph_identity: RuntimeIdentityReader
    prove_codegraph_health: ContextProof
    prove_codegraph_stable: RuntimeStabilityProof
    remove_codegraph_startup_bridge: ContextProof
    prove_codegraph_normal_effective_payload: ContextProof
    settle_maintenance_locked: LockedSettlement
    transition_session_lock: SystemdTransitionLock
    transition_lock: SystemdTransitionLock


def resume_codegraph(
    *,
    project_id: str,
    target_commit: str,
    ports: CodegraphResumePorts | None = None,
) -> None:
    """在多仓租约中完成唯一顺序的 reindex 与 CodeGraph 受控恢复。"""
    active_ports = _default_ports() if ports is None else ports
    _require_ports(active_ports)
    context = _resolve_and_verify_context(active_ports, project_id, target_commit)
    _prepare_maintenance(active_ports, context)
    _resume_within_leases(active_ports, context)


def _resolve_and_verify_context(
    ports: CodegraphResumePorts,
    project_id: str,
    target_commit: str,
) -> CodegraphResumeContext:
    try:
        context = ports.resolve_context(project_id, target_commit)
        ports.verify_configuration(context)
        return context
    except (KeyboardInterrupt, SystemExit, MemoryError):
        raise
    except Exception as error:
        raise CodegraphResumeError("CodeGraph 恢复前置验证失败") from error


def _prepare_maintenance(
    ports: CodegraphResumePorts,
    context: CodegraphResumeContext,
) -> None:
    try:
        ports.prepare_maintenance()
        ports.clear_codegraph_startup_bridge(context)
        ports.inspect_maintenance()
    except (KeyboardInterrupt, SystemExit, MemoryError):
        raise
    except Exception as error:
        raise CodegraphResumeError("CodeGraph 维护准备失败；安全状态未证明") from error


def _resume_within_leases(
    ports: CodegraphResumePorts,
    context: CodegraphResumeContext,
) -> None:
    try:
        lease = ports.operation_leases(context.repositories)
        enter = lease.__enter__
        leave = lease.__exit__
    except BaseException as error:
        _raise_after_recovery(
            _operation_lease_error("CodeGraph 操作租约无法创建", error, recovered=True),
            True,
        )
    try:
        enter()
    except BaseException as error:
        _raise_after_recovery(
            _operation_lease_error("CodeGraph 操作租约无法取得", error, recovered=True),
            True,
        )
    body_error: BaseException | None = None
    body_recovered = False
    recovery_error: BaseException | None = None
    try:
        _verify_maintenance_preconditions(ports, context)
        _restore_and_start(ports, context)
    except BaseException as error:
        body_error = error
        try:
            body_recovered = _recover_after_error(ports, context, error)
        except BaseException as error_during_recovery:
            recovery_error = error_during_recovery
    try:
        if body_error is None:
            leave(None, None, None)
        else:
            leave(type(body_error), body_error, body_error.__traceback__)
    except BaseException as error:
        lease_error = _operation_lease_error(
            "CodeGraph 操作租约释放失败",
            error,
            recovered=body_recovered if body_error is not None else False,
            body_error=body_error,
        )
        _raise_after_recovery(lease_error, lease_error.recovered)
    if body_error is not None:
        if recovery_error is not None:
            if _is_interruption(body_error):
                raise body_error
            if _is_interruption(recovery_error):
                raise recovery_error
            recovery_error.__context__ = body_error
            _raise_after_recovery(recovery_error, False)
        _raise_after_recovery(body_error, body_recovered)


class _CodegraphOperationLeaseError(RuntimeError):
    """租约 enter/exit 异常；未知 ownership 下禁止重入维护转换锁。"""

    def __init__(
        self,
        message: str,
        *,
        recovered: bool,
        interruption: BaseException | None,
    ) -> None:
        super().__init__(message)
        self.recovered = recovered
        self.interruption = interruption


def _operation_lease_error(
    message: str,
    error: BaseException,
    *,
    recovered: bool,
    body_error: BaseException | None = None,
) -> _CodegraphOperationLeaseError:
    interruption = (
        body_error
        if body_error is not None and _is_interruption(body_error)
        else error
        if _is_interruption(error)
        else None
    )
    failure = _CodegraphOperationLeaseError(
        message,
        recovered=recovered,
        interruption=interruption,
    )
    if body_error is not None:
        failure.__context__ = body_error
    return failure


def _verify_maintenance_preconditions(
    ports: CodegraphResumePorts,
    context: CodegraphResumeContext,
) -> None:
    try:
        ports.inspect_maintenance()
        ports.prove_manifest(context)
    except (KeyboardInterrupt, SystemExit, MemoryError):
        raise
    except Exception as error:
        raise CodegraphResumeError("CodeGraph 恢复前置验证失败；维护状态保持") from error


def _restore_and_start(ports: CodegraphResumePorts, context: CodegraphResumeContext) -> None:
    ports.verify_configuration(context)
    _run_transition_session(ports, context)


def _run_transition_session(
    ports: CodegraphResumePorts,
    context: CodegraphResumeContext,
) -> None:
    """会话锁覆盖分相恢复，gate EX 只由内部短转换阶段按需取得。"""
    try:
        lock = ports.transition_session_lock()
        enter = lock.__enter__
        leave = lock.__exit__
    except BaseException as error:
        raise _transition_lock_error("CodeGraph 恢复会话锁无法取得", error) from error
    try:
        enter()
    except BaseException as error:
        raise _transition_lock_error("CodeGraph 恢复会话锁无法取得", error) from error
    body_error: BaseException | None = None
    try:
        identity = _run_systemd_transition(ports, context)
        handoff = ports.prepare_reindex_handoff(context, identity)
        _run_final_reindex_handoff_transition(ports, context, handoff)
    except BaseException as error:
        body_error = error
        raise
    finally:
        try:
            if body_error is None:
                leave(None, None, None)
            else:
                leave(type(body_error), body_error, body_error.__traceback__)
        except BaseException as error:
            raise _transition_lock_error("CodeGraph 恢复会话锁释放失败", error) from error


class _CodegraphTransitionLockError(RuntimeError):
    """转换锁无法证明；不得在未知锁状态下再次进入维护补偿。"""

    def __init__(self, message: str, *, interruption: BaseException | None = None) -> None:
        super().__init__(message)
        self.interruption = interruption


class _CodegraphTransitionBodyError(RuntimeError):
    """锁内激活失败；记录锁内补偿是否已完成最终证明。"""

    def __init__(
        self,
        original: BaseException,
        *,
        recovered: bool,
        recovery_error: BaseException | None,
    ) -> None:
        super().__init__("CodeGraph 组合激活失败")
        self.original = original
        self.recovered = recovered
        self.recovery_error = recovery_error
        self.interruption = (
            original
            if _is_interruption(original)
            else recovery_error
            if recovery_error is not None and _is_interruption(recovery_error)
            else None
        )


def _transition_lock_error(message: str, error: BaseException) -> _CodegraphTransitionLockError:
    interruption = error if _is_interruption(error) else None
    return _CodegraphTransitionLockError(message, interruption=interruption)


def _find_transition_lock_error(error: BaseException) -> _CodegraphTransitionLockError | None:
    """遍历异常链，防止租约退出异常覆盖转换锁的未知状态。"""
    pending = [error]
    visited: set[int] = set()
    while pending:
        current = pending.pop()
        if id(current) in visited:
            continue
        visited.add(id(current))
        if isinstance(current, _CodegraphTransitionLockError):
            return current
        if current.__cause__ is not None:
            pending.append(current.__cause__)
        if current.__context__ is not None:
            pending.append(current.__context__)
    return None


def _run_systemd_transition(
    ports: CodegraphResumePorts,
    context: CodegraphResumeContext,
) -> CodegraphRuntimeIdentity:
    """在 worker 尚未启动时完成 CodeGraph 的长时受控恢复。"""

    def activate_codegraph() -> CodegraphRuntimeIdentity:
        ports.prove_codegraph_maintenance()
        ports.verify_configuration(context)
        ports.prove_staged_payload(context)
        ports.install_codegraph_startup_bridge(context)
        ports.remove_runtime_mask()
        ports.prove_codegraph_effective_payload(context)
        ports.remove_codegraph_hold()
        ports.start_codegraph()
        ports.prove_codegraph_running(context)
        identity = ports.read_codegraph_identity(context)
        if type(identity) is not CodegraphRuntimeIdentity:
            raise CodegraphResumeError("CodeGraph 运行实例身份无效")
        ports.prove_codegraph_health(context)
        ports.prove_codegraph_stable(context, identity)
        ports.remove_codegraph_startup_bridge(context)
        ports.prove_codegraph_normal_effective_payload(context)
        ports.prove_codegraph_running(context)
        if ports.read_codegraph_identity(context) != identity:
            raise CodegraphResumeError("CodeGraph bridge 回切后运行实例身份已变化")
        ports.prove_codegraph_health(context)
        ports.prove_codegraph_stable(context, identity)
        ports.enable_codegraph()
        return identity

    return _run_locked_transition(ports, context, activate_codegraph)


def _run_final_reindex_handoff_transition(
    ports: CodegraphResumePorts,
    context: CodegraphResumeContext,
    handoff: ReindexRestoreHandoff,
) -> None:
    """只在短 gate EX 中提交已完成的 handoff，锁异常仍按未知 ownership 处理。"""
    _run_locked_transition(
        ports,
        context,
        lambda: ports.complete_reindex_handoff_locked(handoff),
    )


def _run_locked_transition(
    ports: CodegraphResumePorts,
    context: CodegraphResumeContext,
    action: Callable[[], _TransitionResult],
) -> _TransitionResult:
    """统一管理 gate EX、锁内补偿和锁 ownership 语义。"""
    try:
        lock = ports.transition_lock()
        enter = lock.__enter__
        leave = lock.__exit__
    except BaseException as error:
        raise _transition_lock_error("CodeGraph systemd 转换锁无法取得", error) from error
    try:
        enter()
    except BaseException as error:
        raise _transition_lock_error("CodeGraph systemd 转换锁无法取得", error) from error
    body_error: BaseException | None = None
    recovery_error: BaseException | None = None
    recovered = False
    result: _TransitionResult | None = None
    try:
        result = action()
    except BaseException as error:
        body_error = error
        try:
            recovered = ports.settle_maintenance_locked(context) is True
        except BaseException as settle_error:
            recovery_error = settle_error
    finally:
        try:
            if body_error is None:
                leave(None, None, None)
            else:
                leave(type(body_error), body_error, body_error.__traceback__)
        except BaseException as error:
            raise _transition_lock_error("CodeGraph systemd 转换锁释放失败", error) from error
    if body_error is not None:
        raise _CodegraphTransitionBodyError(
            body_error,
            recovered=recovered,
            recovery_error=recovery_error,
        ) from body_error
    return result


def _raise_after_recovery(error: BaseException, recovered: bool) -> NoReturn:
    lease_error = _find_operation_lease_error(error)
    if lease_error is not None and lease_error.interruption is not None:
        raise lease_error.interruption
    body_failure = _find_transition_body_error(error)
    if body_failure is not None and body_failure.interruption is not None:
        raise body_failure.interruption
    transition_error = _find_transition_lock_error(error)
    if transition_error is not None and transition_error.interruption is not None:
        raise transition_error.interruption
    if _is_interruption(error):
        raise error
    prefix = (
        "CodeGraph 恢复前置验证失败"
        if isinstance(error, CodegraphResumeError)
        else "CodeGraph 恢复失败"
    )
    message = f"{prefix}；{'已回到维护状态' if recovered else '安全状态未证明'}"
    raise CodegraphResumeError(message) from error


def _is_interruption(error: BaseException) -> bool:
    return not isinstance(error, Exception) or isinstance(error, MemoryError)


def _return_to_maintenance(
    ports: CodegraphResumePorts,
    context: CodegraphResumeContext,
) -> bool:
    try:
        ports.prepare_maintenance()
        ports.clear_codegraph_startup_bridge(context)
        ports.inspect_maintenance()
        return True
    except (KeyboardInterrupt, SystemExit, MemoryError):
        raise
    except Exception:
        return False


def _recover_after_error(
    ports: CodegraphResumePorts,
    context: CodegraphResumeContext,
    error: BaseException,
) -> bool:
    """锁内已补偿时直接复用结果；锁 ownership 未知时禁止重入。"""
    lease_error = _find_operation_lease_error(error)
    if lease_error is not None:
        return lease_error.recovered
    body_failure = _find_transition_body_error(error)
    if body_failure is not None:
        return body_failure.recovered
    if _find_transition_lock_error(error) is not None:
        return False
    return _return_to_maintenance(ports, context)


def _find_operation_lease_error(
    error: BaseException,
) -> _CodegraphOperationLeaseError | None:
    pending = [error]
    visited: set[int] = set()
    while pending:
        current = pending.pop()
        if id(current) in visited:
            continue
        visited.add(id(current))
        if isinstance(current, _CodegraphOperationLeaseError):
            return current
        if current.__cause__ is not None:
            pending.append(current.__cause__)
        if current.__context__ is not None:
            pending.append(current.__context__)
    return None


def _find_transition_body_error(
    error: BaseException,
) -> _CodegraphTransitionBodyError | None:
    pending = [error]
    visited: set[int] = set()
    while pending:
        current = pending.pop()
        if id(current) in visited:
            continue
        visited.add(id(current))
        if isinstance(current, _CodegraphTransitionBodyError):
            return current
        if current.__cause__ is not None:
            pending.append(current.__cause__)
        if current.__context__ is not None:
            pending.append(current.__context__)
    return None


def _require_ports(ports: CodegraphResumePorts) -> None:
    if not isinstance(ports, CodegraphResumePorts) or not all(
        callable(item)
        for item in (
            ports.resolve_context,
            ports.verify_configuration,
            ports.prepare_maintenance,
            ports.inspect_maintenance,
            ports.operation_leases,
            ports.prove_manifest,
            ports.prepare_reindex_handoff,
            ports.complete_reindex_handoff_locked,
            ports.prove_codegraph_maintenance,
            ports.clear_codegraph_startup_bridge,
            ports.prove_staged_payload,
            ports.install_codegraph_startup_bridge,
            ports.remove_runtime_mask,
            ports.prove_codegraph_effective_payload,
            ports.enable_codegraph,
            ports.remove_codegraph_hold,
            ports.start_codegraph,
            ports.prove_codegraph_running,
            ports.read_codegraph_identity,
            ports.prove_codegraph_health,
            ports.prove_codegraph_stable,
            ports.remove_codegraph_startup_bridge,
            ports.prove_codegraph_normal_effective_payload,
            ports.settle_maintenance_locked,
            ports.transition_session_lock,
            ports.transition_lock,
        )
    ):
        raise CodegraphResumeError("CodeGraph 恢复适配器不可用")


def _default_ports() -> CodegraphResumePorts:
    return CodegraphResumePorts(
        resolve_context=_default_context_resolver,
        verify_configuration=_default_configuration_proof,
        prepare_maintenance=_default_prepare_maintenance,
        inspect_maintenance=_default_inspect_maintenance,
        operation_leases=_default_operation_leases,
        prove_manifest=_default_manifest_proof,
        prepare_reindex_handoff=_default_prepare_reindex_handoff,
        complete_reindex_handoff_locked=_default_complete_reindex_handoff_locked,
        prove_codegraph_maintenance=_default_codegraph_maintenance_proof,
        clear_codegraph_startup_bridge=_default_clear_codegraph_startup_bridge,
        prove_staged_payload=_default_staged_payload_proof,
        install_codegraph_startup_bridge=_default_install_codegraph_startup_bridge,
        remove_runtime_mask=_default_remove_runtime_mask,
        prove_codegraph_effective_payload=_default_codegraph_effective_payload_proof,
        enable_codegraph=_default_enable_codegraph,
        remove_codegraph_hold=_default_remove_codegraph_hold,
        start_codegraph=_default_start_codegraph,
        prove_codegraph_running=_default_codegraph_running_proof,
        read_codegraph_identity=_default_codegraph_identity_reader,
        prove_codegraph_health=_default_codegraph_health_proof,
        prove_codegraph_stable=_default_codegraph_stability_proof,
        remove_codegraph_startup_bridge=_default_remove_codegraph_startup_bridge,
        prove_codegraph_normal_effective_payload=(
            _default_codegraph_normal_effective_payload_proof
        ),
        settle_maintenance_locked=_default_settle_maintenance_locked,
        transition_session_lock=_default_transition_session_lock,
        transition_lock=_default_transition_lock,
    )


__all__ = ["CodegraphResumeError", "CodegraphResumePorts", "resume_codegraph"]
