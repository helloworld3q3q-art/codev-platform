"""把计划绑定门禁、stage 准备端口与总门禁撤销收敛为单一交接边界。"""

from __future__ import annotations

from collections.abc import Callable
from contextlib import AbstractContextManager
from dataclasses import dataclass
from pathlib import Path

from codev_platform.core.runtime_interpreter import ReleaseInterpreterIdentity
from codev_platform.mcp_systemd_install_contract import SystemdInstallPorts
from codev_platform.runtime_deployment_contract import DeploymentPlan, RuntimeDeploymentError


PlanProbe = Callable[[DeploymentPlan], object]
TransitionLock = Callable[[], AbstractContextManager[None]]
StagePortsBuilder = Callable[[Callable[[], None]], SystemdInstallPorts]
StagePayloadProof = Callable[[DeploymentPlan, ReleaseInterpreterIdentity], None]


@dataclass(frozen=True, slots=True)
class SystemdHandoffPorts:
    """交接层只依赖共享转换锁、两类门禁证明和 stage 端口工厂。"""

    transition_lock: TransitionLock
    build_stage_ports: StagePortsBuilder
    verify_deployment_guard: PlanProbe
    verify_ingress_gate: PlanProbe
    deactivate_deployment_guard: PlanProbe
    verify_deployment_guard_inactive: PlanProbe
    verify_staged_payload: StagePayloadProof

    def validate(self) -> SystemdHandoffPorts:
        if type(self) is not SystemdHandoffPorts or not all(
            callable(item)
            for item in (
                self.transition_lock,
                self.build_stage_ports,
                self.verify_deployment_guard,
                self.verify_ingress_gate,
                self.deactivate_deployment_guard,
                self.verify_deployment_guard_inactive,
                self.verify_staged_payload,
            )
        ):
            raise RuntimeDeploymentError("systemd 门禁交接端口不可用")
        return self


def build_guarded_stage_install_ports(
    plan: DeploymentPlan,
    *,
    ports: SystemdHandoffPorts | None = None,
) -> SystemdInstallPorts:
    """构建只能在本计划总门禁和入口门禁同时成立时提交的安装端口。"""
    _require_plan(plan)
    active = default_ports() if ports is None else ports.validate()

    def verify_boundary() -> None:
        _verify_active_gates(plan, active)

    try:
        return active.build_stage_ports(verify_boundary)
    except (KeyboardInterrupt, SystemExit, MemoryError):
        raise
    except RuntimeDeploymentError:
        raise
    except Exception:
        raise RuntimeDeploymentError("systemd 门禁交接安装端口无法构建") from None


def deactivate_systemd_handoff_guard(
    plan: DeploymentPlan,
    runtime_release: ReleaseInterpreterIdentity,
    *,
    ports: SystemdHandoffPorts | None = None,
) -> None:
    """同一 systemd 转换锁内证明接替门禁、撤销总门禁并复验最终形态。"""
    _require_plan(plan)
    release = _require_release(plan, runtime_release)
    active = default_ports() if ports is None else ports.validate()
    try:
        with active.transition_lock():
            _verify_active_gates(plan, active)
            active.verify_staged_payload(plan, release)
            active.deactivate_deployment_guard(plan)
            active.verify_deployment_guard_inactive(plan)
            active.verify_ingress_gate(plan)
    except (KeyboardInterrupt, SystemExit, MemoryError):
        raise
    except RuntimeDeploymentError:
        raise
    except Exception:
        raise RuntimeDeploymentError("systemd 门禁交接失败") from None


def default_ports() -> SystemdHandoffPorts:
    from codev_platform.mcp_systemd_install_systemd import default_guarded_stage_ports
    from codev_platform.reindex.maintenance_gate import maintenance_systemd_transition_lock
    from codev_platform.runtime_deployment_guard import (
        deactivate_deployment_guard,
        verify_deployment_guard_active,
        verify_deployment_guard_inactive,
    )
    from codev_platform.runtime_ingress_gate import verify_ingress_gate_active

    return SystemdHandoffPorts(
        transition_lock=maintenance_systemd_transition_lock,
        build_stage_ports=default_guarded_stage_ports,
        verify_deployment_guard=verify_deployment_guard_active,
        verify_ingress_gate=verify_ingress_gate_active,
        deactivate_deployment_guard=deactivate_deployment_guard,
        verify_deployment_guard_inactive=verify_deployment_guard_inactive,
        verify_staged_payload=default_staged_payload_proof,
    )


def default_staged_payload_proof(
    plan: DeploymentPlan,
    expected: ReleaseInterpreterIdentity,
) -> None:
    """复证当前发布、stage 回执、canonical 载荷及持久启用状态。"""
    from codev_platform.mcp_systemd_stage_receipt import prove_staged_systemd_payload
    from codev_platform.runtime_release_binding import snapshot_current_release

    try:
        bound = snapshot_current_release(Path(plan.runtime_root))
        actual = ReleaseInterpreterIdentity(
            runtime_revision=bound.runtime_revision,
            release_id=bound.release_id,
            interpreter_path=bound.interpreter_path.as_posix(),
        )
        if actual != expected:
            raise RuntimeDeploymentError("systemd stage 当前发布身份发生漂移")
        prove_staged_systemd_payload(expected, require_effective=False)
    except (KeyboardInterrupt, SystemExit, MemoryError):
        raise
    except RuntimeDeploymentError:
        raise
    except Exception:
        raise RuntimeDeploymentError("systemd stage 完整载荷无法复证") from None


def _verify_active_gates(plan: DeploymentPlan, ports: SystemdHandoffPorts) -> None:
    ports.verify_deployment_guard(plan)
    ports.verify_ingress_gate(plan)


def _require_plan(plan: object) -> None:
    if type(plan) is not DeploymentPlan:
        raise RuntimeDeploymentError("systemd 门禁交接计划无效")


def _require_release(
    plan: DeploymentPlan,
    release: object,
) -> ReleaseInterpreterIdentity:
    if (
        type(release) is not ReleaseInterpreterIdentity
        or release.runtime_revision != plan.target_revision
    ):
        raise RuntimeDeploymentError("systemd 门禁交接发布身份无效")
    expected_root = Path(plan.runtime_root) / "releases" / release.release_id
    interpreter = Path(release.interpreter_path)
    if interpreter == expected_root or not interpreter.is_relative_to(expected_root):
        raise RuntimeDeploymentError("systemd 门禁交接发布身份无效")
    return release


__all__ = [
    "SystemdHandoffPorts",
    "build_guarded_stage_install_ports",
    "default_staged_payload_proof",
    "deactivate_systemd_handoff_guard",
]
