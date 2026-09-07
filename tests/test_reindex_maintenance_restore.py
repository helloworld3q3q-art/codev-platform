"""reindex 专用 systemd 维护窗口的原子停机与恢复测试。"""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from tests.reindex_maintenance_test_support import (
    _dropin,
    _isolate_legacy_reindex_codegraph_boundary as _configure_legacy_reindex_codegraph_boundary,
    _ok,
    _standby_hooks,
)


@pytest.fixture(autouse=True)
def _isolate_legacy_reindex_codegraph_boundary(monkeypatch) -> None:
    _configure_legacy_reindex_codegraph_boundary(monkeypatch)


def test_restore必须先在维护策略下启动稳定worker再恢复基线重启策略(tmp_path) -> None:
    from codev_platform.ops.reindex_maintenance import restore_reindex_maintenance

    dropin = _dropin(tmp_path)
    dropin.parent.mkdir(parents=True)
    dropin.write_text("[Service]\nRestart=no\n", encoding="utf-8")
    calls: list[tuple[str, ...]] = []
    events: list[str] = []

    def _run(command: tuple[str, ...], **_kwargs) -> SimpleNamespace:
        calls.append(command)
        if command[1] == "show":
            return _ok(stdout="always\n")
        return _ok()

    restore_reindex_maintenance(
        platform_name="linux",
        dropin_path=dropin,
        command_runner=_run,
        stop_proof=lambda: events.append("proved"),
        stability_probe=lambda: events.append("stable"),
        external_worker_proof=lambda: None,
        gate_active_reader=lambda: True,
        **_standby_hooks(completer=lambda _generation, _invocation: events.append("marker-off")),
    )

    assert not dropin.exists()
    assert events == ["proved", "stable", "stable", "marker-off"]
    assert calls == [
        ("systemctl", "start", "codev-reindex.service"),
        ("systemctl", "daemon-reload"),
        (
            "systemctl",
            "show",
            "codev-reindex.service",
            "--property=Restart",
            "--value",
        ),
        ("systemctl", "enable", "codev-reindex.service"),
        ("systemctl", "is-enabled", "--quiet", "codev-reindex.service"),
    ]


def test_restore先完成待命身份绑定并在基线恢复后最后删除marker(tmp_path) -> None:
    """恢复期间 service 只能待命，marker 删除是把写权限交回 worker 的最后一步。"""
    from codev_platform.ops.reindex_maintenance import restore_reindex_maintenance

    dropin = _dropin(tmp_path)
    dropin.parent.mkdir(parents=True)
    dropin.write_text("[Service]\nRestart=no\n", encoding="utf-8")
    events: list[str] = []

    def _run(command, **_kwargs):
        if command == ("systemctl", "start", "codev-reindex.service"):
            events.append("start")
        elif command == ("systemctl", "daemon-reload"):
            events.append("reload")
        elif command[1:3] == ("show", "codev-reindex.service"):
            events.append("restart-baseline")
            return _ok(stdout="always\n")
        return _ok()

    restore_reindex_maintenance(
        platform_name="linux",
        dropin_path=dropin,
        command_runner=_run,
        stop_proof=lambda: events.append("stopped"),
        external_worker_proof=lambda: events.append("external"),
        stability_probe=lambda: events.append("stable"),
        gate_active_reader=lambda: True,
        standby_armer=lambda: events.append("armed") or SimpleNamespace(generation="a" * 32),
        unit_invocation_reader=lambda: events.append("identity") or "b" * 32,
        standby_claimer=lambda generation, invocation: events.append(
            f"claimed:{generation}:{invocation}"
        ),
        standby_renewer=lambda generation, invocation: events.append(
            f"renew:{generation}:{invocation}"
        ),
        standby_completer=lambda generation, invocation: events.append(
            f"complete:{generation}:{invocation}"
        ),
    )

    assert events == [
        "stopped",
        "external",
        "armed",
        "start",
        "identity",
        f"claimed:{'a' * 32}:{'b' * 32}",
        f"renew:{'a' * 32}:{'b' * 32}",
        "stable",
        f"renew:{'a' * 32}:{'b' * 32}",
        "reload",
        "restart-baseline",
        "stable",
        f"renew:{'a' * 32}:{'b' * 32}",
        "identity",
        f"complete:{'a' * 32}:{'b' * 32}",
    ]


def test_restore默认读取器接受systemd乱序键值输出(tmp_path) -> None:
    """systemctl show 多字段输出不得依赖请求顺序，必须按字段名严格解析。"""
    from codev_platform.ops.reindex_maintenance import restore_reindex_maintenance

    dropin = _dropin(tmp_path)
    dropin.parent.mkdir(parents=True)
    dropin.write_text("[Service]\nRestart=no\n", encoding="utf-8")
    events: list[str] = []
    invocation_id = "b" * 32

    def _run(command: tuple[str, ...], **_kwargs) -> SimpleNamespace:
        if command[1:3] == ("show", "codev-reindex.service"):
            if "--property=InvocationID" in command:
                return _ok(
                    stdout=(f"InvocationID={invocation_id}\nActiveState=active\nSubState=running\n")
                )
            return _ok(stdout="always\n")
        return _ok()

    restore_reindex_maintenance(
        platform_name="linux",
        dropin_path=dropin,
        command_runner=_run,
        stop_proof=lambda: events.append("stopped"),
        stability_probe=lambda: events.append("stable"),
        external_worker_proof=lambda: events.append("external"),
        gate_active_reader=lambda: True,
        standby_armer=lambda: SimpleNamespace(generation="a" * 32),
        standby_claimer=lambda generation, invocation: events.append(
            f"claimed:{generation}:{invocation}"
        ),
        standby_renewer=lambda _generation, _invocation: None,
        standby_completer=lambda generation, invocation: events.append(
            f"complete:{generation}:{invocation}"
        ),
    )

    assert not dropin.exists()
    assert f"claimed:{'a' * 32}:{invocation_id}" in events
    assert f"complete:{'a' * 32}:{invocation_id}" in events


def test_restore基线恢复后待命实例变更时不得删除marker(tmp_path) -> None:
    """Restart=no 阶段的待命进程若已退出，不能把写权限交给未知新实例。"""
    from codev_platform.ops.reindex_maintenance import (
        ReindexMaintenanceError,
        restore_reindex_maintenance,
    )

    dropin = _dropin(tmp_path)
    dropin.parent.mkdir(parents=True)
    dropin.write_text("[Service]\nRestart=no\n", encoding="utf-8")
    events: list[str] = []
    invocations = iter(["b" * 32, "c" * 32])

    def _run(command, **_kwargs):
        if command[1:3] == ("show", "codev-reindex.service"):
            return _ok(stdout="always\n")
        return _ok()

    with pytest.raises(ReindexMaintenanceError) as captured:
        restore_reindex_maintenance(
            platform_name="linux",
            dropin_path=dropin,
            command_runner=_run,
            stop_proof=lambda: events.append("stopped"),
            stability_probe=lambda: events.append("stable"),
            external_worker_proof=lambda: events.append("external"),
            gate_active_reader=lambda: True,
            gate_activator=lambda: events.append("marker-reset"),
            standby_armer=lambda: SimpleNamespace(generation="a" * 32),
            unit_invocation_reader=lambda: next(invocations),
            standby_claimer=lambda _generation, _invocation: events.append("claimed"),
            standby_renewer=lambda _generation, _invocation: events.append("renew"),
            standby_completer=lambda *_args: pytest.fail("实例已变更不得删除 marker"),
        )

    assert dropin.read_text(encoding="utf-8") == "[Service]\nRestart=no\n"
    assert "已证明安全停机状态" in str(captured.value)
    assert "InvocationID" in str(captured.value.__cause__)
    assert events == [
        "stopped",
        "external",
        "claimed",
        "renew",
        "stable",
        "renew",
        "stable",
        "renew",
        "marker-reset",
        "external",
        "stopped",
    ]


def test_restore基线策略无法确认时回滚维护dropin(tmp_path) -> None:
    from codev_platform.ops.reindex_maintenance import (
        ReindexMaintenanceError,
        restore_reindex_maintenance,
    )

    dropin = _dropin(tmp_path)
    dropin.parent.mkdir(parents=True)
    dropin.write_text("[Service]\nRestart=no\n", encoding="utf-8")
    calls: list[tuple[str, ...]] = []

    def _run(command: tuple[str, ...], **_kwargs) -> SimpleNamespace:
        calls.append(command)
        if command[1] == "show":
            return _ok(stdout="no\n")
        return _ok()

    with pytest.raises(ReindexMaintenanceError):
        restore_reindex_maintenance(
            platform_name="linux",
            dropin_path=dropin,
            command_runner=_run,
            stop_proof=lambda: None,
            stability_probe=lambda: None,
            external_worker_proof=lambda: None,
            gate_deactivator=lambda: None,
            gate_activator=lambda: None,
            gate_active_reader=lambda: True,
            **_standby_hooks(),
        )

    assert dropin.read_text(encoding="utf-8") == "[Service]\nRestart=no\n"
    assert calls == [
        ("systemctl", "start", "codev-reindex.service"),
        ("systemctl", "daemon-reload"),
        (
            "systemctl",
            "show",
            "codev-reindex.service",
            "--property=Restart",
            "--value",
        ),
        ("systemctl", "daemon-reload"),
        ("systemctl", "stop", "codev-reindex.service"),
        ("systemctl", "reset-failed", "codev-reindex.service"),
    ]


def test_restore启动后的稳定性失败时保留dropin并停止reindex(tmp_path) -> None:
    from codev_platform.ops.reindex_maintenance import (
        ReindexMaintenanceError,
        restore_reindex_maintenance,
    )

    dropin = _dropin(tmp_path)
    dropin.parent.mkdir(parents=True)
    dropin.write_text("[Service]\nRestart=no\n", encoding="utf-8")
    calls: list[tuple[str, ...]] = []
    proofs: list[str] = []

    with pytest.raises(ReindexMaintenanceError) as captured:
        restore_reindex_maintenance(
            platform_name="linux",
            dropin_path=dropin,
            command_runner=lambda command, **_kwargs: calls.append(command) or _ok(),
            stop_proof=lambda: proofs.append("stopped"),
            stability_probe=lambda: (_ for _ in ()).throw(RuntimeError("worker unstable")),
            external_worker_proof=lambda: proofs.append("external"),
            gate_deactivator=lambda: None,
            gate_activator=lambda: None,
            gate_active_reader=lambda: True,
            **_standby_hooks(),
        )

    assert dropin.read_text(encoding="utf-8") == "[Service]\nRestart=no\n"
    assert "已证明安全停机状态" in str(captured.value)
    assert proofs == ["stopped", "external", "external", "stopped"]
    assert calls == [
        ("systemctl", "start", "codev-reindex.service"),
        ("systemctl", "daemon-reload"),
        ("systemctl", "stop", "codev-reindex.service"),
        ("systemctl", "reset-failed", "codev-reindex.service"),
    ]


def test_restore补偿重载失败时必须报告安全状态未证明(tmp_path) -> None:
    from codev_platform.ops.reindex_maintenance import (
        ReindexMaintenanceError,
        restore_reindex_maintenance,
    )

    dropin = _dropin(tmp_path)
    dropin.parent.mkdir(parents=True)
    dropin.write_text("[Service]\nRestart=no\n", encoding="utf-8")
    calls: list[tuple[str, ...]] = []
    proofs: list[str] = []

    def _run(command: tuple[str, ...], **_kwargs) -> SimpleNamespace:
        calls.append(command)
        if command == ("systemctl", "daemon-reload"):
            return SimpleNamespace(returncode=1, stdout="", stderr="敏感错误")
        return _ok()

    with pytest.raises(ReindexMaintenanceError, match="安全状态未证明") as captured:
        restore_reindex_maintenance(
            platform_name="linux",
            dropin_path=dropin,
            command_runner=_run,
            stop_proof=lambda: proofs.append("stopped"),
            stability_probe=lambda: (_ for _ in ()).throw(RuntimeError("worker unstable")),
            external_worker_proof=lambda: proofs.append("external"),
            gate_deactivator=lambda: None,
            gate_activator=lambda: None,
            gate_active_reader=lambda: True,
            **_standby_hooks(),
        )

    assert "已回滚为安全停机状态" not in str(captured.value)
    assert proofs == ["stopped", "external", "external", "stopped"]
    assert calls == [
        ("systemctl", "start", "codev-reindex.service"),
        ("systemctl", "daemon-reload"),
        ("systemctl", "stop", "codev-reindex.service"),
        ("systemctl", "reset-failed", "codev-reindex.service"),
    ]


def test_restore补偿最终停机证明失败时必须报告安全状态未证明(tmp_path) -> None:
    from codev_platform.ops.reindex_maintenance import (
        ReindexMaintenanceError,
        restore_reindex_maintenance,
    )

    dropin = _dropin(tmp_path)
    dropin.parent.mkdir(parents=True)
    dropin.write_text("[Service]\nRestart=no\n", encoding="utf-8")
    proofs: list[str] = []

    def _stop_proof() -> None:
        proofs.append("stopped")
        if proofs.count("stopped") == 2:
            raise RuntimeError("unit still active")

    with pytest.raises(ReindexMaintenanceError, match="安全状态未证明") as captured:
        restore_reindex_maintenance(
            platform_name="linux",
            dropin_path=dropin,
            command_runner=lambda _command, **_kwargs: _ok(),
            stop_proof=_stop_proof,
            stability_probe=lambda: (_ for _ in ()).throw(RuntimeError("worker unstable")),
            external_worker_proof=lambda: proofs.append("external"),
            gate_deactivator=lambda: None,
            gate_activator=lambda: None,
            gate_active_reader=lambda: True,
            **_standby_hooks(),
        )

    assert "已回滚为安全停机状态" not in str(captured.value)
    assert proofs == ["stopped", "external", "external", "stopped"]


def test_restore补偿最终外部worker证明失败时必须报告安全状态未证明(tmp_path) -> None:
    from codev_platform.ops.reindex_maintenance import (
        ReindexMaintenanceError,
        restore_reindex_maintenance,
    )

    dropin = _dropin(tmp_path)
    dropin.parent.mkdir(parents=True)
    dropin.write_text("[Service]\nRestart=no\n", encoding="utf-8")
    proofs: list[str] = []

    def _external_worker_proof() -> None:
        proofs.append("external")
        if proofs.count("external") == 2:
            raise RuntimeError("detached worker remains")

    with pytest.raises(ReindexMaintenanceError, match="安全状态未证明") as captured:
        restore_reindex_maintenance(
            platform_name="linux",
            dropin_path=dropin,
            command_runner=lambda _command, **_kwargs: _ok(),
            stop_proof=lambda: proofs.append("stopped"),
            stability_probe=lambda: (_ for _ in ()).throw(RuntimeError("worker unstable")),
            external_worker_proof=_external_worker_proof,
            gate_deactivator=lambda: None,
            gate_activator=lambda: None,
            gate_active_reader=lambda: True,
            **_standby_hooks(),
        )

    assert "已回滚为安全停机状态" not in str(captured.value)
    assert proofs == ["stopped", "external", "external", "stopped"]


def test_restore最终marker交接失败时补偿必须回到维护稳态并证明安全(tmp_path) -> None:
    from codev_platform.ops.reindex_maintenance import (
        ReindexMaintenanceError,
        restore_reindex_maintenance,
    )

    dropin = _dropin(tmp_path)
    dropin.parent.mkdir(parents=True)
    dropin.write_text("[Service]\nRestart=no\n", encoding="utf-8")
    calls: list[tuple[str, ...]] = []
    events: list[str] = []
    gate = {"phase": "restore_claimed"}

    def _run(command: tuple[str, ...], **_kwargs) -> SimpleNamespace:
        calls.append(command)
        if command[1] == "show":
            return _ok(stdout="always\n")
        return _ok()

    def _activate_gate() -> None:
        gate["phase"] = "maintenance"
        events.append("marker-reset")

    def _complete(_generation: str, _invocation: str) -> None:
        events.append("complete")
        raise RuntimeError("marker final handoff failed")

    with pytest.raises(ReindexMaintenanceError) as captured:
        restore_reindex_maintenance(
            platform_name="linux",
            dropin_path=dropin,
            command_runner=_run,
            stop_proof=lambda: events.append("stopped"),
            stability_probe=lambda: events.append("stable"),
            external_worker_proof=lambda: events.append("external"),
            gate_activator=_activate_gate,
            gate_active_reader=lambda: True,
            **_standby_hooks(completer=_complete),
        )

    assert "已证明安全停机状态" in str(captured.value)
    assert gate["phase"] == "maintenance"
    assert dropin.read_text(encoding="utf-8") == "[Service]\nRestart=no\n"
    assert events == [
        "stopped",
        "external",
        "stable",
        "stable",
        "complete",
        "marker-reset",
        "external",
        "stopped",
    ]
    assert calls == [
        ("systemctl", "start", "codev-reindex.service"),
        ("systemctl", "daemon-reload"),
        (
            "systemctl",
            "show",
            "codev-reindex.service",
            "--property=Restart",
            "--value",
        ),
        ("systemctl", "enable", "codev-reindex.service"),
        ("systemctl", "is-enabled", "--quiet", "codev-reindex.service"),
        ("systemctl", "daemon-reload"),
        ("systemctl", "stop", "codev-reindex.service"),
        ("systemctl", "reset-failed", "codev-reindex.service"),
    ]


def test_restore最终marker交接失败且无法回到维护稳态时必须报告安全状态未证明(tmp_path) -> None:
    from codev_platform.ops.reindex_maintenance import (
        ReindexMaintenanceError,
        restore_reindex_maintenance,
    )

    dropin = _dropin(tmp_path)
    dropin.parent.mkdir(parents=True)
    dropin.write_text("[Service]\nRestart=no\n", encoding="utf-8")
    proofs: list[str] = []
    gate = {"phase": "restore_claimed"}

    def _activate_gate() -> None:
        raise RuntimeError("cannot recreate marker")

    def _complete(_generation: str, _invocation: str) -> None:
        raise RuntimeError("marker final handoff failed")

    def _run(command: tuple[str, ...], **_kwargs) -> SimpleNamespace:
        return _ok(stdout="always\n") if command[1] == "show" else _ok()

    with pytest.raises(ReindexMaintenanceError, match="安全状态未证明") as captured:
        restore_reindex_maintenance(
            platform_name="linux",
            dropin_path=dropin,
            command_runner=_run,
            stop_proof=lambda: proofs.append("stopped"),
            stability_probe=lambda: None,
            external_worker_proof=lambda: proofs.append("external"),
            gate_activator=_activate_gate,
            gate_active_reader=lambda: True,
            **_standby_hooks(completer=_complete),
        )

    assert "已证明安全停机状态" not in str(captured.value)
    assert gate["phase"] == "restore_claimed"
    assert proofs == ["stopped", "external", "external", "stopped"]


def test_restore拒绝删除被替换的非受管dropin(tmp_path) -> None:
    from codev_platform.ops.reindex_maintenance import (
        ReindexMaintenanceError,
        restore_reindex_maintenance,
    )

    dropin = _dropin(tmp_path)
    dropin.parent.mkdir(parents=True)
    dropin.write_text("[Service]\nRestart=always\n", encoding="utf-8")
    calls: list[tuple[str, ...]] = []

    def _run(command: tuple[str, ...], **_kwargs) -> SimpleNamespace:
        calls.append(command)
        return _ok(stdout="always\n") if command[1] == "show" else _ok()

    with pytest.raises(ReindexMaintenanceError):
        restore_reindex_maintenance(
            platform_name="linux",
            dropin_path=dropin,
            command_runner=_run,
            stop_proof=lambda: None,
            external_worker_proof=lambda: None,
            stability_probe=lambda: None,
            gate_deactivator=lambda: pytest.fail("非受管 drop-in 不得移除门禁"),
        )

    assert dropin.read_text(encoding="utf-8") == "[Service]\nRestart=always\n"
    assert calls == []


def test_restore维护门禁丢失时拒绝启动worker(tmp_path) -> None:
    from codev_platform.ops.reindex_maintenance import (
        ReindexMaintenanceError,
        restore_reindex_maintenance,
    )

    dropin = _dropin(tmp_path)
    dropin.parent.mkdir(parents=True)
    dropin.write_text("[Service]\nRestart=no\n", encoding="utf-8")
    calls: list[tuple[str, ...]] = []

    with pytest.raises(ReindexMaintenanceError):
        restore_reindex_maintenance(
            platform_name="linux",
            dropin_path=dropin,
            command_runner=lambda command, **_kwargs: calls.append(command) or _ok(),
            stop_proof=lambda: None,
            external_worker_proof=lambda: None,
            gate_active_reader=lambda: False,
            gate_deactivator=lambda: pytest.fail("门禁丢失不得恢复"),
        )

    assert dropin.read_text(encoding="utf-8") == "[Service]\nRestart=no\n"
    assert calls == []


def test_默认稳定性探针拒绝启动后自动重启计数增长(monkeypatch) -> None:
    from codev_platform.ops import reindex_maintenance as maintenance

    clock = iter([0.0, 0.0])
    monkeypatch.setattr(maintenance.time, "monotonic", lambda: next(clock))
    monkeypatch.setattr(maintenance.time, "sleep", lambda _seconds: None)

    with pytest.raises(maintenance.ReindexMaintenanceError):
        maintenance._wait_for_stable_worker(
            lambda _command, **_kwargs: _ok(stdout="active\nrunning\n8\n"),
            expected_restarts=7,
            startup_timeout_sec=1.0,
            stable_window_sec=0.0,
        )


def test_默认稳定性探针接受systemd乱序键值输出(monkeypatch) -> None:
    """systemd 可能按内部顺序输出 show 字段，稳定性判断只按字段名取值。"""
    from codev_platform.ops import reindex_maintenance as maintenance

    monkeypatch.setattr(maintenance.time, "monotonic", lambda: 0.0)
    monkeypatch.setattr(maintenance.time, "sleep", lambda _seconds: None)

    maintenance._wait_for_stable_worker(
        lambda _command, **_kwargs: _ok(
            stdout="NRestarts=7\nActiveState=active\nSubState=running\n"
        ),
        expected_restarts=7,
        startup_timeout_sec=1.0,
        stable_window_sec=0.0,
    )
