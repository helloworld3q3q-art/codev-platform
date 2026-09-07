"""reindex 待命准备、锁内基线结算与最终 marker 交接。"""

from __future__ import annotations

import re
import sys
from collections.abc import Callable
from dataclasses import dataclass


Action = Callable[[], None]
StandbyAction = Callable[[str, str], object]
StandbyArmer = Callable[[], object]
InvocationReader = Callable[[], str]


@dataclass(frozen=True, slots=True)
class ReindexRestoreHandoff:
    """绑定到唯一 systemd invocation 的不可变交接令牌。"""

    generation: str
    invocation_id: str

    def __post_init__(self) -> None:
        _require_identity(self.generation, "reindex 恢复 generation 无效")
        _require_identity(self.invocation_id, "reindex 恢复 InvocationID 无效")


@dataclass(frozen=True, slots=True)
class ReindexRestoreHandoffPorts:
    """把待命、基线恢复和最终提交依赖隔离为窄端口。"""

    prove_dropin: Action
    prove_gate_active: Action
    prove_reindex_stopped: Action
    prove_external_workers: Action
    arm_standby: StandbyArmer
    start_reindex: Action
    read_invocation: InvocationReader
    claim_standby: StandbyAction
    renew_standby: StandbyAction
    prove_stable: Action
    enable_reindex: Action
    restore_baseline_restart: Action
    renew_standby_locked: StandbyAction
    complete_standby_locked: StandbyAction


def prepare_reindex_restore_handoff(
    *,
    ports: ReindexRestoreHandoffPorts | None = None,
) -> ReindexRestoreHandoff:
    """在会话锁内启动并绑定待命实例；marker 与 Restart=no 在返回时仍保持。"""
    _require_session_guard()
    active = _default_ports() if ports is None else ports
    _require_ports(active)
    try:
        active.prove_dropin()
        active.prove_gate_active()
        active.prove_reindex_stopped()
        active.prove_external_workers()
        generation = _record_generation(active.arm_standby())
        active.start_reindex()
        invocation_id = _require_identity(
            active.read_invocation(),
            "reindex 恢复 InvocationID 无效",
        )
        active.claim_standby(generation, invocation_id)
        active.renew_standby(generation, invocation_id)
        active.prove_stable()
        active.renew_standby(generation, invocation_id)
        active.prove_dropin()
        _require_same_invocation(active, invocation_id)
        return ReindexRestoreHandoff(generation, invocation_id)
    except (KeyboardInterrupt, SystemExit, MemoryError):
        raise
    except Exception as error:
        raise _maintenance_error("reindex 恢复待命准备失败") from error


def settle_reindex_restore_handoff_while_session_locked(
    handoff: ReindexRestoreHandoff,
    *,
    ports: ReindexRestoreHandoffPorts | None = None,
) -> None:
    """只持管理员会话锁时恢复基线并稳定复证，仍不删除 marker。"""
    _require_session_guard()
    active = _default_ports() if ports is None else ports
    token = _require_handoff(handoff)
    _require_ports(active)
    try:
        active.prove_dropin()
        _require_same_invocation(active, token.invocation_id)
        active.renew_standby(token.generation, token.invocation_id)
        active.enable_reindex()
        active.restore_baseline_restart()
        active.prove_stable()
        active.renew_standby(token.generation, token.invocation_id)
        _require_same_invocation(active, token.invocation_id)
    except (KeyboardInterrupt, SystemExit, MemoryError):
        raise
    except Exception as error:
        raise _maintenance_error("reindex 恢复待命结算失败") from error


def complete_reindex_restore_handoff_while_transition_locked(
    handoff: ReindexRestoreHandoff,
    *,
    ports: ReindexRestoreHandoffPorts | None = None,
) -> None:
    """短转换 EX 内仅复核同一实例并删除 marker，禁止在此等待稳定窗口。"""
    _require_transition_guard()
    active = _default_ports() if ports is None else ports
    token = _require_handoff(handoff)
    _require_ports(active)
    try:
        active.renew_standby_locked(token.generation, token.invocation_id)
        _require_same_invocation(active, token.invocation_id)
        active.complete_standby_locked(token.generation, token.invocation_id)
    except (KeyboardInterrupt, SystemExit, MemoryError):
        raise
    except Exception as error:
        raise _maintenance_error("reindex 恢复最终交接失败") from error


def prove_reindex_restore_handoff_while_session_locked(
    handoff: ReindexRestoreHandoff,
    *,
    ports: ReindexRestoreHandoffPorts | None = None,
) -> None:
    """在 marker 仍存在且无 gate EX 时完成最终稳定与实例证明。"""
    _require_session_guard()
    active = _default_ports() if ports is None else ports
    token = _require_handoff(handoff)
    _require_ports(active)
    try:
        active.prove_stable()
        _require_same_invocation(active, token.invocation_id)
    except (KeyboardInterrupt, SystemExit, MemoryError):
        raise
    except Exception as error:
        raise _maintenance_error("reindex 恢复最终实例证明失败") from error


def _default_ports() -> ReindexRestoreHandoffPorts:
    maintenance = _maintenance_module()
    run = maintenance._default_command_runner
    restart_baseline = maintenance._read_restart_count(run)
    stable = maintenance._default_stability_probe(
        run,
        expected_restarts=restart_baseline,
    )
    return ReindexRestoreHandoffPorts(
        prove_dropin=lambda: maintenance._require_owned_dropin(maintenance._DROPIN_PATH),
        prove_gate_active=_default_gate_proof,
        prove_reindex_stopped=maintenance._default_stop_proof,
        prove_external_workers=maintenance._default_external_worker_proof,
        arm_standby=_default_standby_armer,
        start_reindex=lambda: maintenance._run_systemctl(
            run,
            ("systemctl", "start", maintenance._REINDEX_UNIT),
        ),
        read_invocation=_default_invocation_reader(run),
        claim_standby=_default_standby_claimer,
        renew_standby=_default_standby_renewer,
        prove_stable=stable,
        enable_reindex=lambda: _enable_reindex(maintenance, run),
        restore_baseline_restart=lambda: _restore_baseline_restart(
            maintenance,
            run,
        ),
        renew_standby_locked=_default_standby_locked_renewer,
        complete_standby_locked=_default_standby_locked_completer,
    )


def _default_gate_proof() -> None:
    maintenance = _maintenance_module()
    maintenance._require_active_gate(maintenance._default_gate_active_reader)
    maintenance._require_maintenance_phase(maintenance._default_gate_record_reader)


def _default_standby_armer() -> object:
    from codev_platform.reindex.maintenance_gate import arm_restore_standby

    return arm_restore_standby(ttl_sec=120.0)


def _default_standby_claimer(generation: str, invocation_id: str) -> object:
    from codev_platform.reindex.maintenance_gate import claim_restore_standby

    return claim_restore_standby(
        generation=generation,
        invocation_id=invocation_id,
    )


def _default_standby_renewer(generation: str, invocation_id: str) -> object:
    from codev_platform.reindex.maintenance_gate import renew_claimed_restore_standby

    return renew_claimed_restore_standby(
        generation=generation,
        invocation_id=invocation_id,
        ttl_sec=120.0,
    )


def _default_standby_locked_renewer(generation: str, invocation_id: str) -> object:
    from codev_platform.reindex.maintenance_gate import (
        renew_claimed_restore_standby_while_systemd_transition_locked,
    )

    return renew_claimed_restore_standby_while_systemd_transition_locked(
        generation=generation,
        invocation_id=invocation_id,
        ttl_sec=120.0,
    )


def _default_standby_locked_completer(generation: str, invocation_id: str) -> None:
    from codev_platform.reindex.maintenance_gate import (
        complete_restore_standby_while_systemd_transition_locked,
    )

    complete_restore_standby_while_systemd_transition_locked(
        generation=generation,
        invocation_id=invocation_id,
    )


def _default_invocation_reader(run):
    maintenance = _maintenance_module()

    def read() -> str:
        values = maintenance._read_systemctl_properties(
            run,
            unit=maintenance._REINDEX_UNIT,
            properties=("ActiveState", "SubState", "InvocationID"),
            error_message="codev-reindex 待命实例状态格式无效",
            timeout_sec=1.0,
        )
        if values["ActiveState"] != "active" or values["SubState"] != "running":
            raise _maintenance_error("codev-reindex 待命实例未处于运行态")
        return values["InvocationID"]

    return read


def _enable_reindex(maintenance: object, run) -> None:
    maintenance._run_systemctl(run, ("systemctl", "enable", maintenance._REINDEX_UNIT))
    maintenance._run_systemctl(
        run,
        ("systemctl", "is-enabled", "--quiet", maintenance._REINDEX_UNIT),
    )


def _restore_baseline_restart(maintenance: object, run) -> None:
    path = maintenance._DROPIN_PATH
    maintenance._require_owned_dropin(path)
    if sys.platform.startswith("linux"):
        from codev_platform.ops.reindex_codegraph_resume_managed_path import (
            read_optional_root_owned_regular_file_snapshot,
            remove_root_owned_regular_file,
        )

        if remove_root_owned_regular_file(path) is not True:
            raise _maintenance_error("reindex 维护 drop-in 无法删除")
        if (
            read_optional_root_owned_regular_file_snapshot(
                path,
                max_bytes=maintenance._MAX_DROPIN_BYTES,
            )
            is not None
        ):
            raise _maintenance_error("reindex 维护 drop-in 删除状态无法证明")
    else:
        from codev_platform.reindex.file_durability import durable_unlink

        durable_unlink(path)
        if path.exists() or path.is_symlink():
            raise _maintenance_error("reindex 维护 drop-in 删除状态无法证明")
    maintenance._run_systemctl(run, ("systemctl", "daemon-reload"))
    maintenance._require_baseline_restart(run)


def _require_same_invocation(
    ports: ReindexRestoreHandoffPorts,
    expected: str,
) -> None:
    if (
        _require_identity(
            ports.read_invocation(),
            "reindex 恢复 InvocationID 无效",
        )
        != expected
    ):
        raise _maintenance_error("codev-reindex 待命实例 InvocationID 已变化")


def _record_generation(record: object) -> str:
    return _require_identity(
        getattr(record, "generation", None),
        "reindex 恢复 generation 无效",
    )


def _require_identity(value: object, message: str) -> str:
    if type(value) is not str or re.fullmatch(r"[0-9a-f]{32}", value) is None:
        raise _maintenance_error(message)
    return value


def _require_handoff(value: object) -> ReindexRestoreHandoff:
    if type(value) is not ReindexRestoreHandoff:
        raise _maintenance_error("reindex 恢复交接令牌无效")
    _require_identity(value.generation, "reindex 恢复 generation 无效")
    _require_identity(value.invocation_id, "reindex 恢复 InvocationID 无效")
    return value


def _require_ports(value: object) -> None:
    if type(value) is not ReindexRestoreHandoffPorts or not all(
        callable(getattr(value, name)) for name in ReindexRestoreHandoffPorts.__dataclass_fields__
    ):
        raise _maintenance_error("reindex 恢复交接适配器不可用")


def _maintenance_error(message: str) -> RuntimeError:
    return _maintenance_module().ReindexMaintenanceError(message)


def _require_transition_guard() -> None:
    from codev_platform.reindex.maintenance_gate import (
        require_systemd_transition_guard,
    )

    require_systemd_transition_guard()


def _require_session_guard() -> None:
    from codev_platform.reindex.maintenance_gate import (
        require_systemd_transition_session_guard,
    )

    require_systemd_transition_session_guard()


def _maintenance_module():
    from codev_platform.ops import reindex_maintenance

    return reindex_maintenance


__all__ = [
    "ReindexRestoreHandoff",
    "ReindexRestoreHandoffPorts",
    "complete_reindex_restore_handoff_while_transition_locked",
    "prepare_reindex_restore_handoff",
    "prove_reindex_restore_handoff_while_session_locked",
    "settle_reindex_restore_handoff_while_session_locked",
]
