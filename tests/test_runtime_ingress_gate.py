"""Webhook 跨重启入口门禁测试。"""

from __future__ import annotations

from dataclasses import dataclass

import pytest

from codev_platform.runtime_deployment_contract import DeploymentPlan, RuntimeDeploymentError
from codev_platform.runtime_ingress_gate import (
    INGRESS_GATE_DROP_IN_CONTENT,
    IngressGatePorts,
    activate_ingress_gate,
    deactivate_ingress_gate,
    ingress_gate_payload,
    verify_ingress_gate_active,
    verify_ingress_gate_inactive,
    verify_ingress_gate_marker_absent,
)


def _plan() -> DeploymentPlan:
    return DeploymentPlan(
        schema_version=2,
        target_revision="a" * 40,
        source_repo="/home/helloworld/work/codev-platform",
        runtime_root="/var/lib/codev-platform/runtime",
        requirements_lock="/srv/codev-artifacts/wsl-runtime.lock",
        approved_requirements="/srv/codev-artifacts/wsl-runtime.freeze",
        wheelhouse="/srv/codev-artifacts/wheelhouse",
        candidate_root="/var/tmp/codev-platform-candidates/helloworld",
        config_source="/home/helloworld/.codev-platform/config.json",
        environment_source="/etc/codev-platform/platform.source.env",
        service_user="helloworld",
        project_id="codev-platform",
        require_cuda=True,
    )


@dataclass
class _State:
    events: list[str]
    installed: bool = False
    guard: bytes | None = None

    def ports(self) -> IngressGatePorts:
        return IngressGatePorts(
            install_drop_in=self._install,
            verify_drop_in=lambda: self.events.append("verify-drop-in") or self.installed,
            reload_systemd=lambda: self.events.append("reload"),
            write_guard=self._write,
            read_guard=lambda: self.events.append("read") or self.guard,
            delete_guard=self._delete,
        )

    def _install(self) -> None:
        self.events.append("install")
        self.installed = True

    def _write(self, payload: bytes) -> None:
        self.events.append("write")
        self.guard = payload

    def _delete(self) -> None:
        self.events.append("delete")
        self.guard = None


def test_入口门禁先安装固定drop_in再创建计划绑定标记() -> None:
    state = _State([])

    evidence = activate_ingress_gate(_plan(), ports=state.ports(), platform_name="linux")

    assert state.events == ["install", "reload", "verify-drop-in", "write", "read"]
    assert state.guard == ingress_gate_payload(_plan())
    assert len(evidence.evidence_sha256) == 64
    assert INGRESS_GATE_DROP_IN_CONTENT == (
        b"[Unit]\n"
        b"ConditionPathExists=!/var/lib/codev-platform/runtime/ingress-closed.guard\n"
    )


def test_续跑必须同时证明固定drop_in和本次计划标记() -> None:
    state = _State([], installed=True, guard=ingress_gate_payload(_plan()))

    verify_ingress_gate_active(_plan(), ports=state.ports(), platform_name="linux")

    state.guard = ingress_gate_payload(
        DeploymentPlan(
            **{
                **_plan().to_mapping(),
                "target_revision": "c" * 40,
            }
        )
    )
    with pytest.raises(RuntimeDeploymentError, match="入口门禁身份不一致"):
        verify_ingress_gate_active(_plan(), ports=state.ports(), platform_name="linux")


def test_只有已证明活动的入口门禁才能删除() -> None:
    state = _State([], installed=True, guard=ingress_gate_payload(_plan()))

    deactivate_ingress_gate(_plan(), ports=state.ports(), platform_name="linux")

    assert state.guard is None
    assert state.events == ["verify-drop-in", "read", "delete", "read"]


def test_常态要求固定drop_in保留且入口标记不存在() -> None:
    state = _State([], installed=True)

    verify_ingress_gate_inactive(_plan(), ports=state.ports(), platform_name="linux")

    state.installed = False
    with pytest.raises(RuntimeDeploymentError, match="入口门禁 drop-in 无法证明"):
        verify_ingress_gate_inactive(_plan(), ports=state.ports(), platform_name="linux")


def test_切换前只证明入口marker不存在且不要求dropin已预装() -> None:
    state = _State([])

    evidence = verify_ingress_gate_marker_absent(
        _plan(),
        ports=state.ports(),
        platform_name="linux",
    )

    assert len(evidence.evidence_sha256) == 64
    assert state.events == ["read"]
    state.guard = b"unexpected\n"
    with pytest.raises(RuntimeDeploymentError, match="切换前"):
        verify_ingress_gate_marker_absent(
            _plan(),
            ports=state.ports(),
            platform_name="linux",
        )


def test_入口门禁证明显式允许总部署条件共存(monkeypatch) -> None:
    import codev_platform.runtime_ingress_gate as module

    captured: list[object] = []
    monkeypatch.setattr(
        module,
        "verify_systemd_condition_guard",
        lambda unit, **kwargs: captured.append((unit, kwargs)),
        raising=False,
    )

    assert module._verify_drop_in() is True

    unit, kwargs = captured[0]
    assert unit == "codev-webhook.service"
    assert kwargs["required"].content == INGRESS_GATE_DROP_IN_CONTENT
    assert len(kwargs["allowed"]) == 1
    assert b"deployment-maintenance.guard" in kwargs["allowed"][0].content
