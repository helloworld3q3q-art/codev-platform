"""跨重启部署停写门禁的顺序、身份和幂等测试。"""

from __future__ import annotations

from dataclasses import dataclass

import pytest

from codev_platform.runtime_deployment_contract import DeploymentPlan, RuntimeDeploymentError
from codev_platform.runtime_deployment_guard import (
    GUARDED_SYSTEMD_UNITS,
    DeploymentGuardPorts,
    activate_deployment_guard,
    deactivate_deployment_guard,
    deployment_guard_payload,
    verify_deployment_guard_active,
    verify_deployment_guard_inactive,
    verify_deployment_guard_marker_absent,
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
    drop_ins: set[str]
    guard: bytes | None = None

    def ports(self) -> DeploymentGuardPorts:
        return DeploymentGuardPorts(
            install_drop_in=lambda unit: (
                self.events.append(f"install:{unit}") or self.drop_ins.add(unit)
            ),
            verify_drop_in=lambda unit: (
                self.events.append(f"verify:{unit}") or (unit in self.drop_ins)
            ),
            reload_systemd=lambda: self.events.append("reload"),
            write_guard=lambda payload: (
                self.events.append("write_guard") or setattr(self, "guard", payload)
            ),
            read_guard=lambda: self.events.append("read_guard") or self.guard,
            delete_guard=lambda: self.events.append("delete_guard") or setattr(self, "guard", None),
        )


def test_先完整安装reload并复验有效dropin再创建耐久门禁() -> None:
    state = _State([], set())

    evidence = activate_deployment_guard(_plan(), ports=state.ports(), platform_name="linux")

    assert len(evidence.evidence_sha256) == 64
    write_index = state.events.index("write_guard")
    assert state.events[: len(GUARDED_SYSTEMD_UNITS)] == [
        f"install:{unit}" for unit in GUARDED_SYSTEMD_UNITS
    ]
    reload_index = state.events.index("reload")
    assert all(
        reload_index < state.events.index(f"verify:{unit}") for unit in GUARDED_SYSTEMD_UNITS
    )
    assert all(state.events.index(f"verify:{unit}") < write_index for unit in GUARDED_SYSTEMD_UNITS)
    assert state.events.count("reload") == 1
    assert state.guard == deployment_guard_payload(_plan())


def test_platform_docs_观测写入者受跨重启门禁保护() -> None:
    assert "codev-mcp-platform-docs.service" in GUARDED_SYSTEMD_UNITS


def test_部署守卫成员清单唯一复用systemd注册表() -> None:
    from codev_platform.mcp_systemd_unit_registry import DEPLOYMENT_GUARDED_SYSTEMD_UNITS

    assert GUARDED_SYSTEMD_UNITS is DEPLOYMENT_GUARDED_SYSTEMD_UNITS
    assert "codev-memory-maintenance.timer" in GUARDED_SYSTEMD_UNITS
    assert "codev-clock-resync.service" not in GUARDED_SYSTEMD_UNITS
    assert "codev-clock-resync.timer" not in GUARDED_SYSTEMD_UNITS


def test_门禁复验要求全部dropin和计划身份精确一致() -> None:
    state = _State([], set(GUARDED_SYSTEMD_UNITS), deployment_guard_payload(_plan()))

    verify_deployment_guard_active(_plan(), ports=state.ports(), platform_name="linux")

    state.guard = deployment_guard_payload(
        DeploymentPlan(**{**_plan().to_mapping(), "project_id": "other"})
    )
    with pytest.raises(RuntimeDeploymentError, match="门禁"):
        verify_deployment_guard_active(_plan(), ports=state.ports(), platform_name="linux")


def test_只有已复验门禁才能删除且删除后立即复证() -> None:
    state = _State([], set(GUARDED_SYSTEMD_UNITS), deployment_guard_payload(_plan()))

    evidence = deactivate_deployment_guard(_plan(), ports=state.ports(), platform_name="linux")

    assert len(evidence.evidence_sha256) == 64
    assert state.guard is None
    assert state.events[-2:] == ["delete_guard", "read_guard"]


def test_dropin或门禁写入失败时绝不伪称已激活() -> None:
    state = _State([], set())
    ports = state.ports()
    object.__setattr__(ports, "verify_drop_in", lambda _unit: False)

    with pytest.raises(RuntimeDeploymentError, match="drop-in"):
        activate_deployment_guard(_plan(), ports=ports, platform_name="linux")

    assert state.guard is None


def test_运行态要求dropin仍在但门禁文件不存在() -> None:
    state = _State([], set(GUARDED_SYSTEMD_UNITS), None)

    evidence = verify_deployment_guard_inactive(
        _plan(),
        ports=state.ports(),
        platform_name="linux",
    )

    assert len(evidence.evidence_sha256) == 64
    state.guard = deployment_guard_payload(_plan())
    with pytest.raises(RuntimeDeploymentError, match="仍处于激活"):
        verify_deployment_guard_inactive(
            _plan(),
            ports=state.ports(),
            platform_name="linux",
        )


def test_维护前只证明marker不存在且不要求dropin已预装() -> None:
    state = _State([], set(), None)

    evidence = verify_deployment_guard_marker_absent(
        _plan(),
        ports=state.ports(),
        platform_name="linux",
    )

    assert len(evidence.evidence_sha256) == 64
    assert state.events == ["read_guard"]
    state.guard = b"unexpected\n"
    with pytest.raises(RuntimeDeploymentError, match="维护前"):
        verify_deployment_guard_marker_absent(
            _plan(),
            ports=state.ports(),
            platform_name="linux",
        )


def test_默认门禁文件操作复用root可信dirfd原语(monkeypatch) -> None:
    import codev_platform.runtime_deployment_guard as module
    from codev_platform.ops.reindex_codegraph_resume_managed_path import (
        RootOwnedRegularFileSnapshot,
    )

    events: list[object] = []
    payload = deployment_guard_payload(_plan())
    snapshot = RootOwnedRegularFileSnapshot(payload, 0o600, 0, 0)
    monkeypatch.setattr(
        module,
        "write_root_owned_regular_file_atomic",
        lambda path, content, **kwargs: events.append(("write", path, content, kwargs)),
        raising=False,
    )
    monkeypatch.setattr(
        module,
        "read_optional_root_owned_regular_file_snapshot",
        lambda path, **kwargs: events.append(("read", path, kwargs)) or snapshot,
        raising=False,
    )
    monkeypatch.setattr(
        module,
        "remove_root_owned_regular_file",
        lambda path: events.append(("delete", path)) or True,
        raising=False,
    )

    module._write_guard(payload)
    assert module._read_guard() == payload
    module._delete_guard()

    assert events[0][0] == "write"
    assert events[0][3] == {"mode": 0o600, "uid": 0, "gid": 0}
    assert events[1][0] == "read"
    assert events[2][0] == "read"
    assert events[3][0] == "delete"


def test_CodeGraph部署门禁证明显式允许自身维护条件(monkeypatch) -> None:
    import codev_platform.runtime_deployment_guard as module

    captured: list[object] = []
    monkeypatch.setattr(
        module,
        "verify_systemd_condition_guard",
        lambda unit, **kwargs: captured.append((unit, kwargs)),
        raising=False,
    )

    assert module._verify_drop_in("codev-mcp-codegraph.service") is True

    unit, kwargs = captured[0]
    assert unit == "codev-mcp-codegraph.service"
    assert kwargs["required"].content == module.DEPLOYMENT_GUARD_DROP_IN_CONTENT
    assert len(kwargs["allowed"]) == 1
    assert b"codegraph-maintenance.gate" in kwargs["allowed"][0].content
