"""Webhook 在 reindex maintenance 内的关闭与最终开放生命周期。"""

from __future__ import annotations

import subprocess
import sys
from collections.abc import Callable
from dataclasses import dataclass

from codev_platform.ops.reindex_admin_systemd_guard import verify_systemd_unit_stopped
from codev_platform.ops.reindex_compensation import ExhaustiveCompensationRunner
from codev_platform.ops.reindex_webhook_maintenance_guard import (
    ensure_webhook_maintenance_guard,
    verify_webhook_maintenance_guard,
)
from codev_platform.ops.reindex_webhook_maintenance_hold import (
    activate_webhook_maintenance_hold,
    deactivate_webhook_maintenance_hold,
    verify_webhook_maintenance_hold,
)
from codev_platform.runtime_systemd_acceptance import verify_ingress_stability


_WEBHOOK_UNIT = "codev-webhook.service"
_SYSTEMCTL_TIMEOUT_SEC = 10.0

CommandRunner = Callable[..., object]
Proof = Callable[[], None]
Action = Callable[[], None]


class WebhookMaintenanceLifecycleError(RuntimeError):
    """Webhook 无法在维护窗口内关闭或在最终阶段安全开放。"""


@dataclass(frozen=True, slots=True)
class WebhookMaintenanceSettlement:
    """Webhook 关闭动作与最终证明分开记录，避免部分成功被误判。"""

    guard_ensured: bool
    hold_activated: bool
    stop_attempted: bool
    guard_proven: bool
    hold_proven: bool
    stopped_proven: bool

    @property
    def proven(self) -> bool:
        return all(
            (
                self.guard_ensured,
                self.hold_activated,
                self.stop_attempted,
                self.guard_proven,
                self.hold_proven,
                self.stopped_proven,
            )
        )


def prepare_webhook_maintenance(
    *,
    platform_name: str | None = None,
    command_runner: CommandRunner | None = None,
    guard_ensurer: Action | None = None,
    hold_activator: Action | None = None,
    guard_proof: Proof | None = None,
    hold_proof: Proof | None = None,
    stopped_proof: Proof | None = None,
) -> None:
    """先安装跨重启条件与 hold，再穷尽收敛 Webhook 进程和 cgroup。"""
    run = _require_linux_runner(platform_name, command_runner)
    ensure_guard, activate_hold, prove_guard, prove_hold, prove_stopped = _close_actions(
        run,
        guard_ensurer=guard_ensurer,
        hold_activator=hold_activator,
        guard_proof=guard_proof,
        hold_proof=hold_proof,
        stopped_proof=stopped_proof,
    )
    settlement, runner = _settle_closed(
        run,
        ensure_guard=ensure_guard,
        activate_hold=activate_hold,
        prove_guard=prove_guard,
        prove_hold=prove_hold,
        prove_stopped=prove_stopped,
    )
    runner.raise_deferred_interruption()
    if not settlement.proven:
        raise WebhookMaintenanceLifecycleError("Webhook 维护停机准备失败；安全状态未证明")


def verify_webhook_maintenance(
    *,
    platform_name: str | None = None,
    command_runner: CommandRunner | None = None,
    guard_proof: Proof | None = None,
    hold_proof: Proof | None = None,
    stopped_proof: Proof | None = None,
) -> None:
    """仅在 guard、hold 与 Webhook cgroup 均已关闭时返回。"""
    run = _require_linux_runner(platform_name, command_runner)
    _ensure_optional_callables(guard_proof, hold_proof, stopped_proof)
    prove_guard = _default_guard_proof(run) if guard_proof is None else guard_proof
    prove_hold = verify_webhook_maintenance_hold if hold_proof is None else hold_proof
    prove_stopped = _default_stopped_proof(run) if stopped_proof is None else stopped_proof
    try:
        prove_guard()
        prove_hold()
        prove_stopped()
    except (KeyboardInterrupt, SystemExit, MemoryError):
        raise
    except Exception as error:
        raise WebhookMaintenanceLifecycleError("Webhook 维护关闭状态无法证明") from error


def resume_webhook_maintenance(
    *,
    health_proof: Proof,
    platform_name: str | None = None,
    command_runner: CommandRunner | None = None,
    guard_proof: Proof | None = None,
    hold_proof: Proof | None = None,
    hold_deactivator: Action | None = None,
    enable_service: Action | None = None,
    start_service: Action | None = None,
    running_proof: Proof | None = None,
    settle_maintenance: Action | None = None,
) -> None:
    """只在最终恢复阶段解除 hold、启动 Webhook 并完成健康与稳定性验收。"""
    run = _require_linux_runner(platform_name, command_runner)
    if not callable(health_proof):
        raise WebhookMaintenanceLifecycleError("Webhook 恢复健康证明适配器不可用")
    _ensure_optional_callables(
        guard_proof,
        hold_proof,
        hold_deactivator,
        enable_service,
        start_service,
        running_proof,
        settle_maintenance,
    )
    prove_guard = _default_guard_proof(run) if guard_proof is None else guard_proof
    prove_hold = verify_webhook_maintenance_hold if hold_proof is None else hold_proof
    deactivate_hold = (
        deactivate_webhook_maintenance_hold if hold_deactivator is None else hold_deactivator
    )
    enable = (lambda: _enable_webhook(run)) if enable_service is None else enable_service
    start = (lambda: _start_webhook(run)) if start_service is None else start_service
    prove_running = verify_ingress_stability if running_proof is None else running_proof
    settle = _default_settle_maintenance(run) if settle_maintenance is None else settle_maintenance
    try:
        prove_guard()
        prove_hold()
        enable()
        deactivate_hold()
        start()
        prove_running()
        health_proof()
    except BaseException as error:
        recovered = _attempt_settlement(settle)
        if isinstance(error, (KeyboardInterrupt, SystemExit, MemoryError)):
            raise
        suffix = "已回到入口维护态" if recovered else "安全状态未证明"
        raise WebhookMaintenanceLifecycleError(f"Webhook 受控恢复失败；{suffix}") from error


def _close_actions(
    run: CommandRunner,
    *,
    guard_ensurer: Action | None,
    hold_activator: Action | None,
    guard_proof: Proof | None,
    hold_proof: Proof | None,
    stopped_proof: Proof | None,
) -> tuple[Action, Action, Proof, Proof, Proof]:
    _ensure_optional_callables(
        guard_ensurer,
        hold_activator,
        guard_proof,
        hold_proof,
        stopped_proof,
    )
    return (
        _default_guard_ensurer(run) if guard_ensurer is None else guard_ensurer,
        activate_webhook_maintenance_hold if hold_activator is None else hold_activator,
        _default_guard_proof(run) if guard_proof is None else guard_proof,
        verify_webhook_maintenance_hold if hold_proof is None else hold_proof,
        _default_stopped_proof(run) if stopped_proof is None else stopped_proof,
    )


def _settle_closed(
    run: CommandRunner,
    *,
    ensure_guard: Action,
    activate_hold: Action,
    prove_guard: Proof,
    prove_hold: Proof,
    prove_stopped: Proof,
) -> tuple[WebhookMaintenanceSettlement, ExhaustiveCompensationRunner]:
    runner = ExhaustiveCompensationRunner()
    attempt = runner.attempt
    settlement = WebhookMaintenanceSettlement(
        guard_ensured=attempt(ensure_guard),
        hold_activated=attempt(activate_hold),
        stop_attempted=attempt(lambda: _stop_webhook_safely(run)),
        guard_proven=attempt(prove_guard),
        hold_proven=attempt(prove_hold),
        stopped_proven=attempt(prove_stopped),
    )
    return settlement, runner


def _attempt_settlement(action: Action) -> bool:
    try:
        action()
        return True
    except (KeyboardInterrupt, SystemExit, MemoryError):
        raise
    except Exception:
        return False


def _stop_webhook_safely(run: CommandRunner) -> None:
    try:
        _run_systemctl(run, ("systemctl", "stop", _WEBHOOK_UNIT))
    except WebhookMaintenanceLifecycleError:
        _run_systemctl(
            run,
            ("systemctl", "kill", "--kill-who=all", "--signal=SIGKILL", _WEBHOOK_UNIT),
        )
    finally:
        _run_systemctl(run, ("systemctl", "reset-failed", _WEBHOOK_UNIT))


def _verify_webhook_stopped(run: CommandRunner) -> None:
    verify_systemd_unit_stopped(
        _WEBHOOK_UNIT,
        expected_restart="always",
        command_runner=run,
    )


def _enable_webhook(run: CommandRunner) -> None:
    _run_systemctl(run, ("systemctl", "enable", _WEBHOOK_UNIT))
    _run_systemctl(run, ("systemctl", "is-enabled", "--quiet", _WEBHOOK_UNIT))


def _start_webhook(run: CommandRunner) -> None:
    _run_systemctl(run, ("systemctl", "start", _WEBHOOK_UNIT))


def _default_guard_ensurer(run: CommandRunner) -> Action:
    return lambda: ensure_webhook_maintenance_guard(command_runner=run)


def _default_guard_proof(run: CommandRunner) -> Proof:
    return lambda: verify_webhook_maintenance_guard(command_runner=run)


def _default_stopped_proof(run: CommandRunner) -> Proof:
    return lambda: _verify_webhook_stopped(run)


def _default_settle_maintenance(run: CommandRunner) -> Action:
    return lambda: prepare_webhook_maintenance(command_runner=run)


def _require_linux_runner(
    platform_name: str | None,
    command_runner: CommandRunner | None,
) -> CommandRunner:
    platform = sys.platform if platform_name is None else platform_name
    if type(platform) is not str or not platform.startswith("linux"):
        raise WebhookMaintenanceLifecycleError("当前平台不支持 Webhook 维护生命周期")
    run = _default_command_runner if command_runner is None else command_runner
    if not callable(run):
        raise WebhookMaintenanceLifecycleError("Webhook 维护生命周期适配器不可用")
    return run


def _ensure_optional_callables(*items: object) -> None:
    if any(item is not None and not callable(item) for item in items):
        raise WebhookMaintenanceLifecycleError("Webhook 维护生命周期适配器不可用")


def _run_systemctl(run: CommandRunner, command: tuple[str, ...]) -> None:
    try:
        result = run(command, timeout_sec=_SYSTEMCTL_TIMEOUT_SEC)
    except (KeyboardInterrupt, SystemExit, MemoryError):
        raise
    except Exception as error:
        raise WebhookMaintenanceLifecycleError("Webhook systemd 维护命令无法执行") from error
    if getattr(result, "returncode", None) != 0:
        raise WebhookMaintenanceLifecycleError("Webhook systemd 维护命令失败")


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


__all__ = [
    "WebhookMaintenanceLifecycleError",
    "WebhookMaintenanceSettlement",
    "prepare_webhook_maintenance",
    "resume_webhook_maintenance",
    "verify_webhook_maintenance",
]
