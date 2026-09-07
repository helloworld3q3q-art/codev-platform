"""CodeGraph 永久 guard、耐久 hold 与当前运行态停机的窄生命周期。"""

from __future__ import annotations

import subprocess
import sys
from collections.abc import Callable
from dataclasses import dataclass
from typing import NoReturn

from codev_platform.ops.reindex_compensation import ExhaustiveCompensationRunner


_CODEGRAPH_UNIT = "codev-mcp-codegraph.service"
_SYSTEMCTL_TIMEOUT_SEC = 10.0


class CodegraphMaintenanceLifecycleError(RuntimeError):
    """CodeGraph 维护停机生命周期无法安全完成。"""


CommandRunner = Callable[..., object]
Proof = Callable[[], None]
MaskApplier = Callable[[], None]


@dataclass(frozen=True, slots=True)
class _MaintenanceSettlement:
    """动作与最终证明分开记录，避免单一布尔值掩盖部分失败。"""

    guard_ensured: bool
    hold_activated: bool
    mask_applied: bool
    stop_attempted: bool
    guard_proven: bool
    hold_proven: bool
    mask_proven: bool
    stop_proven: bool

    @property
    def proven(self) -> bool:
        return all(
            (
                self.guard_ensured,
                self.hold_activated,
                self.mask_applied,
                self.stop_attempted,
                self.guard_proven,
                self.hold_proven,
                self.mask_proven,
                self.stop_proven,
            )
        )


def prepare_codegraph_maintenance(
    *,
    platform_name: str | None = None,
    command_runner: CommandRunner | None = None,
    guard_ensurer: MaskApplier | None = None,
    hold_activator: MaskApplier | None = None,
    mask_applier: MaskApplier | None = None,
    guard_proof: Proof | None = None,
    hold_proof: Proof | None = None,
    mask_proof: Proof | None = None,
    stop_proof: Proof | None = None,
) -> None:
    """先建立跨重启条件与 hold，再穷尽当前运行态的停机收敛。"""
    run = _require_linux_runner(platform_name, command_runner)
    ensure_guard = _default_guard_ensurer if guard_ensurer is None else guard_ensurer
    activate_hold = _default_hold_activator if hold_activator is None else hold_activator
    apply_mask = _default_mask_applier if mask_applier is None else mask_applier
    prove_guard = _default_guard_proof if guard_proof is None else guard_proof
    prove_hold = _default_hold_proof if hold_proof is None else hold_proof
    prove_mask = _default_mask_proof if mask_proof is None else mask_proof
    prove_stopped = _default_stop_proof if stop_proof is None else stop_proof
    _require_callables(
        ensure_guard,
        activate_hold,
        apply_mask,
        prove_guard,
        prove_hold,
        prove_mask,
        prove_stopped,
    )
    try:
        ensure_guard()
        prove_guard()
    except BaseException as error:
        _settle_runtime_without_new_hold(
            run=run,
            apply_mask=apply_mask,
            prove_guard=prove_guard,
            prove_hold=prove_hold,
            prove_mask=prove_mask,
            prove_stopped=prove_stopped,
            original=error,
        )
    runner = ExhaustiveCompensationRunner()
    attempt = runner.attempt
    settlement = _MaintenanceSettlement(
        guard_ensured=True,
        hold_activated=attempt(activate_hold),
        mask_applied=attempt(apply_mask),
        stop_attempted=attempt(lambda: _stop_codegraph_safely(run)),
        guard_proven=attempt(prove_guard),
        hold_proven=attempt(prove_hold),
        mask_proven=attempt(prove_mask),
        stop_proven=attempt(prove_stopped),
    )
    runner.raise_deferred_interruption()
    if not settlement.proven:
        raise CodegraphMaintenanceLifecycleError("CodeGraph 维护停机准备失败；安全状态未证明")


def _settle_runtime_without_new_hold(
    *,
    run: CommandRunner,
    apply_mask: MaskApplier,
    prove_guard: Proof,
    prove_hold: Proof,
    prove_mask: Proof,
    prove_stopped: Proof,
    original: BaseException,
) -> NoReturn:
    """guard 硬屏障失败后不创建 hold，仅穷尽当前启动周期的停机收敛。"""
    runner = ExhaustiveCompensationRunner()
    attempt = runner.attempt
    attempt(apply_mask)
    attempt(lambda: _stop_codegraph_safely(run))
    for proof in (prove_guard, prove_hold, prove_mask, prove_stopped):
        attempt(proof)
    if not isinstance(original, Exception) or isinstance(original, MemoryError):
        raise original
    runner.raise_deferred_interruption()
    raise CodegraphMaintenanceLifecycleError(
        "CodeGraph 永久维护条件未证明；未创建耐久 hold；安全状态未证明"
    ) from original


def verify_codegraph_maintenance(
    *,
    platform_name: str | None = None,
    guard_proof: Proof | None = None,
    hold_proof: Proof | None = None,
    mask_proof: Proof | None = None,
    stop_proof: Proof | None = None,
) -> None:
    """仅在永久 guard、hold、runtime mask 与固定 unit 停机均可证明时返回。"""
    _require_linux_runner(platform_name, command_runner=None)
    prove_guard = _default_guard_proof if guard_proof is None else guard_proof
    prove_hold = _default_hold_proof if hold_proof is None else hold_proof
    prove_mask = _default_mask_proof if mask_proof is None else mask_proof
    prove_stopped = _default_stop_proof if stop_proof is None else stop_proof
    _require_callables(prove_guard, prove_hold, prove_mask, prove_stopped)
    runner = ExhaustiveCompensationRunner()
    proven = tuple(
        runner.attempt(proof) for proof in (prove_guard, prove_hold, prove_mask, prove_stopped)
    )
    runner.raise_deferred_interruption()
    if not all(proven):
        raise CodegraphMaintenanceLifecycleError("CodeGraph 维护状态无法证明")


def start_codegraph_service(
    *,
    platform_name: str | None = None,
    command_runner: CommandRunner | None = None,
) -> None:
    """仅启动固定 CodeGraph unit；运行态与健康证明由恢复编排后续完成。"""
    run = _require_linux_runner(platform_name, command_runner)
    _run_systemctl(run, ("systemctl", "start", _CODEGRAPH_UNIT))


def enable_codegraph_service(
    *,
    platform_name: str | None = None,
    command_runner: CommandRunner | None = None,
) -> None:
    """恢复全部健康证明后才写入开机启用关系。"""
    run = _require_linux_runner(platform_name, command_runner)
    _run_systemctl(run, ("systemctl", "enable", _CODEGRAPH_UNIT))
    _run_systemctl(
        run,
        ("systemctl", "is-enabled", "--quiet", _CODEGRAPH_UNIT),
    )


def _stop_codegraph_safely(run: CommandRunner) -> None:
    """只对固定 CodeGraph unit 做受控停止；stop 失败时收敛其整个 cgroup。"""
    try:
        _run_systemctl(run, ("systemctl", "stop", _CODEGRAPH_UNIT))
    except CodegraphMaintenanceLifecycleError:
        _run_systemctl(
            run,
            (
                "systemctl",
                "kill",
                "--kill-who=all",
                "--signal=SIGKILL",
                _CODEGRAPH_UNIT,
            ),
        )
    finally:
        _run_systemctl(run, ("systemctl", "reset-failed", _CODEGRAPH_UNIT))


def _require_linux_runner(
    platform_name: str | None,
    command_runner: CommandRunner | None,
) -> CommandRunner:
    platform = sys.platform if platform_name is None else platform_name
    if type(platform) is not str or not platform.startswith("linux"):
        raise CodegraphMaintenanceLifecycleError("当前平台不支持 CodeGraph systemd 维护")
    run = _default_command_runner if command_runner is None else command_runner
    if not callable(run):
        raise CodegraphMaintenanceLifecycleError("CodeGraph systemd 维护适配器不可用")
    return run


def _require_callables(*items: object) -> None:
    if not all(callable(item) for item in items):
        raise CodegraphMaintenanceLifecycleError("CodeGraph 维护证明适配器不可用")


def _run_systemctl(run: CommandRunner, command: tuple[str, ...]) -> object:
    try:
        result = run(command, timeout_sec=_SYSTEMCTL_TIMEOUT_SEC)
    except MemoryError:
        raise
    except Exception as error:
        raise CodegraphMaintenanceLifecycleError("CodeGraph systemd 维护命令无法执行") from error
    if getattr(result, "returncode", None) != 0:
        raise CodegraphMaintenanceLifecycleError("CodeGraph systemd 维护命令失败")
    return result


def _default_command_runner(command: tuple[str, ...], *, timeout_sec: float) -> object:
    return subprocess.run(
        command,
        stdin=subprocess.DEVNULL,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=timeout_sec,
        check=False,
    )


def _default_mask_applier() -> None:
    from codev_platform.ops.reindex_codegraph_maintenance import (
        ensure_codegraph_runtime_mask,
    )

    ensure_codegraph_runtime_mask()


def _default_guard_ensurer() -> None:
    from codev_platform.ops.reindex_codegraph_maintenance_guard import (
        ensure_codegraph_maintenance_guard,
    )

    ensure_codegraph_maintenance_guard()


def _default_hold_activator() -> None:
    from codev_platform.ops.reindex_codegraph_maintenance_hold import (
        activate_codegraph_maintenance_hold,
    )

    activate_codegraph_maintenance_hold()


def _default_guard_proof() -> None:
    from codev_platform.ops.reindex_codegraph_maintenance_guard import (
        verify_codegraph_maintenance_guard,
    )

    verify_codegraph_maintenance_guard()


def _default_hold_proof() -> None:
    from codev_platform.ops.reindex_codegraph_maintenance_hold import (
        verify_codegraph_maintenance_hold,
    )

    verify_codegraph_maintenance_hold()


def _default_mask_proof() -> None:
    from codev_platform.ops.reindex_codegraph_maintenance import (
        verify_codegraph_runtime_mask,
    )

    verify_codegraph_runtime_mask()


def _default_stop_proof() -> None:
    from codev_platform.ops.reindex_admin_systemd_guard import (
        verify_codev_codegraph_runtime_masked_stopped,
    )

    verify_codev_codegraph_runtime_masked_stopped()


__all__ = [
    "CodegraphMaintenanceLifecycleError",
    "enable_codegraph_service",
    "prepare_codegraph_maintenance",
    "start_codegraph_service",
    "verify_codegraph_maintenance",
]
