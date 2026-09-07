"""reindex 维护状态机与 CodeGraph 跨重启停机边界的接线回归。"""

from __future__ import annotations

from contextlib import contextmanager
from pathlib import Path
from types import SimpleNamespace

import pytest

from tests.reindex_maintenance_test_support import (
    _isolate_legacy_webhook_boundary as _configure_legacy_webhook_boundary,
)


@pytest.fixture(autouse=True)
def _isolate_legacy_webhook_boundary(monkeypatch: pytest.MonkeyPatch) -> None:
    _configure_legacy_webhook_boundary(monkeypatch)


def _dropin(tmp_path: Path) -> Path:
    return tmp_path / "codev-reindex.service.d" / "10-codev-reindex-maintenance.conf"


def _ok(*, stdout: str = "") -> SimpleNamespace:
    return SimpleNamespace(returncode=0, stdout=stdout, stderr="不得输出原始错误")


def _standby_hooks(events: list[str]) -> dict[str, object]:
    return {
        "standby_armer": lambda: SimpleNamespace(generation="a" * 32),
        "unit_invocation_reader": lambda: "b" * 32,
        "standby_claimer": lambda *_args: events.append("claim"),
        "standby_renewer": lambda *_args: events.append("renew"),
        "standby_completer": lambda *_args: events.append("complete"),
    }


def test_prepare先发布reindex_marker再完成codegraph维护停机(
    tmp_path: Path,
) -> None:
    from codev_platform.ops.reindex_maintenance import prepare_reindex_maintenance

    events: list[str] = []
    commands: list[tuple[str, ...]] = []

    prepare_reindex_maintenance(
        platform_name="linux",
        dropin_path=_dropin(tmp_path),
        command_runner=lambda command, **_kwargs: commands.append(command) or _ok(),
        codegraph_prepare=lambda: events.append("codegraph-prepare"),
        codegraph_maintenance_proof=lambda: events.append("codegraph-proof"),
        gate_activator=lambda: events.append("gate"),
        external_worker_proof=lambda: events.append("external"),
        stop_proof=lambda: events.append("reindex-proof"),
    )

    assert events == [
        "gate",
        "codegraph-prepare",
        "external",
        "reindex-proof",
        "codegraph-proof",
    ]
    assert commands == [
        ("systemctl", "daemon-reload"),
        ("systemctl", "stop", "codev-reindex.service"),
        ("systemctl", "reset-failed", "codev-reindex.service"),
    ]


def test_prepare的codegraph准备失败后保持marker并单调补偿双服务(
    tmp_path: Path,
) -> None:
    from codev_platform.ops.reindex_maintenance import (
        ReindexMaintenanceError,
        prepare_reindex_maintenance,
    )

    events: list[str] = []
    commands: list[tuple[str, ...]] = []
    prepare_attempts = 0

    def prepare_codegraph() -> None:
        nonlocal prepare_attempts
        prepare_attempts += 1
        events.append(f"codegraph-prepare-{prepare_attempts}")
        if prepare_attempts == 1:
            raise RuntimeError("mask failed")

    with pytest.raises(ReindexMaintenanceError, match="已证明安全停机状态"):
        prepare_reindex_maintenance(
            platform_name="linux",
            dropin_path=_dropin(tmp_path),
            command_runner=lambda command, **_kwargs: commands.append(command) or _ok(),
            codegraph_prepare=prepare_codegraph,
            codegraph_maintenance_proof=lambda: events.append("codegraph-proof"),
            gate_activator=lambda: events.append("marker"),
            external_worker_proof=lambda: events.append("external"),
            stop_proof=lambda: events.append("reindex-proof"),
        )

    assert events == [
        "marker",
        "codegraph-prepare-1",
        "marker",
        "codegraph-prepare-2",
        "external",
        "reindex-proof",
        "codegraph-proof",
    ]
    assert commands == [
        ("systemctl", "daemon-reload"),
        ("systemctl", "stop", "codev-reindex.service"),
        ("systemctl", "reset-failed", "codev-reindex.service"),
    ]
    assert _dropin(tmp_path).is_file()


def test_prepare默认路径实际委托codegraph_lifecycle(
    tmp_path: Path,
    monkeypatch,
) -> None:
    from codev_platform.ops import reindex_codegraph_lifecycle as lifecycle
    from codev_platform.ops.reindex_maintenance import prepare_reindex_maintenance

    events: list[str] = []
    monkeypatch.setattr(
        lifecycle,
        "prepare_codegraph_maintenance",
        lambda: events.append("lifecycle-prepare"),
    )
    monkeypatch.setattr(
        lifecycle,
        "verify_codegraph_maintenance",
        lambda: events.append("lifecycle-proof"),
    )

    prepare_reindex_maintenance(
        platform_name="linux",
        dropin_path=_dropin(tmp_path),
        command_runner=lambda _command, **_kwargs: _ok(),
        gate_activator=lambda: events.append("gate"),
        external_worker_proof=lambda: events.append("external"),
        stop_proof=lambda: events.append("reindex-proof"),
    )

    assert events == [
        "gate",
        "lifecycle-prepare",
        "external",
        "reindex-proof",
        "lifecycle-proof",
    ]


def test_prepare默认跨模块顺序固定为intent_marker_gate_guard_hold_runtime_stop(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from codev_platform.ops import reindex_codegraph_lifecycle as lifecycle
    from codev_platform.ops import reindex_maintenance_prepare as prepare_module
    from codev_platform.ops.reindex_maintenance import prepare_reindex_maintenance

    events: list[str] = []

    @contextmanager
    def gate_ex():
        events.append("gate-ex-enter")
        try:
            yield
        finally:
            events.append("gate-ex-exit")

    @contextmanager
    def transition_intent():
        events.append("intent-enter")
        try:
            yield gate_ex
        finally:
            events.append("intent-exit")

    monkeypatch.setattr(prepare_module, "_codegraph_transition_intent", transition_intent)
    monkeypatch.setattr(
        prepare_module,
        "_default_activate_gate_while_transition_intent_locked",
        lambda: events.append("marker"),
    )
    monkeypatch.setattr(
        lifecycle,
        "prepare_codegraph_maintenance",
        lambda: events.extend(["guard", "guard-proof", "hold", "runtime-mask", "stop"]),
    )
    monkeypatch.setattr(
        lifecycle,
        "verify_codegraph_maintenance",
        lambda: events.append("codegraph-final-proof"),
    )

    prepare_reindex_maintenance(
        platform_name="linux",
        dropin_path=_dropin(tmp_path),
        command_runner=lambda command, **_kwargs: events.append(f"systemctl-{command[1]}") or _ok(),
        external_worker_proof=lambda: events.append("external"),
        stop_proof=lambda: events.append("reindex-proof"),
    )

    assert events == [
        "intent-enter",
        "marker",
        "gate-ex-enter",
        "guard",
        "guard-proof",
        "hold",
        "runtime-mask",
        "stop",
        "gate-ex-exit",
        "intent-exit",
        "systemctl-daemon-reload",
        "systemctl-stop",
        "systemctl-reset-failed",
        "external",
        "reindex-proof",
        "codegraph-final-proof",
    ]

    空状态 = frozenset()
    只有M = frozenset({"M"})
    只有MG = frozenset({"M", "G"})
    完整MGH = frozenset({"M", "G", "H"})
    耐久映射 = {"marker": "M", "guard": "G", "hold": "H"}
    actual_projection: list[frozenset[str]] = [空状态]
    for event in events:
        actual_projection.append(
            actual_projection[-1] | {耐久映射[event]}
            if event in 耐久映射
            else actual_projection[-1]
        )
    assert actual_projection == [
        空状态,
        空状态,
        只有M,
        只有M,
        只有MG,
        只有MG,
        完整MGH,
        完整MGH,
        完整MGH,
        完整MGH,
        完整MGH,
        完整MGH,
        完整MGH,
        完整MGH,
        完整MGH,
        完整MGH,
        完整MGH,
    ]


def test_status必须同时证明codegraph_runtime_mask和停机(
    tmp_path: Path,
) -> None:
    from codev_platform.ops.reindex_maintenance import inspect_reindex_maintenance

    path = _dropin(tmp_path)
    path.parent.mkdir(parents=True)
    path.write_text("[Service]\nRestart=no\n", encoding="utf-8")
    events: list[str] = []

    inspect_reindex_maintenance(
        platform_name="linux",
        dropin_path=path,
        gate_active_reader=lambda: True,
        gate_record_reader=lambda: SimpleNamespace(phase="maintenance"),
        external_worker_proof=lambda: events.append("external"),
        stop_proof=lambda: events.append("reindex-proof"),
        codegraph_maintenance_proof=lambda: events.append("codegraph-proof"),
    )

    assert events == ["external", "reindex-proof", "codegraph-proof"]


def test_restore在启动和交接marker前均复证codegraph仍被hold(
    tmp_path: Path,
) -> None:
    from codev_platform.ops.reindex_maintenance import restore_reindex_maintenance

    path = _dropin(tmp_path)
    path.parent.mkdir(parents=True)
    path.write_text("[Service]\nRestart=no\n", encoding="utf-8")
    events: list[str] = []

    def run(command: tuple[str, ...], **_kwargs) -> SimpleNamespace:
        if command[1] == "show":
            return _ok(stdout="always\n")
        if command == ("systemctl", "start", "codev-reindex.service"):
            events.append("start")
        return _ok()

    restore_reindex_maintenance(
        platform_name="linux",
        dropin_path=path,
        command_runner=run,
        stop_proof=lambda: events.append("reindex-proof"),
        external_worker_proof=lambda: events.append("external"),
        stability_probe=lambda: events.append("stable"),
        gate_active_reader=lambda: True,
        codegraph_maintenance_proof=lambda: events.append("codegraph-proof"),
        **_standby_hooks(events),
    )

    assert events.count("codegraph-proof") == 2
    assert events.index("codegraph-proof") < events.index("start")
    assert events.index("codegraph-proof", events.index("start")) < events.index("complete")


def test_restore默认路径两次委托codegraph_lifecycle证明(
    tmp_path: Path,
    monkeypatch,
) -> None:
    from codev_platform.ops import reindex_codegraph_lifecycle as lifecycle
    from codev_platform.ops.reindex_maintenance import restore_reindex_maintenance

    path = _dropin(tmp_path)
    path.parent.mkdir(parents=True)
    path.write_text("[Service]\nRestart=no\n", encoding="utf-8")
    events: list[str] = []
    monkeypatch.setattr(
        lifecycle,
        "verify_codegraph_maintenance",
        lambda: events.append("lifecycle-proof"),
    )

    restore_reindex_maintenance(
        platform_name="linux",
        dropin_path=path,
        command_runner=lambda command, **_kwargs: (
            _ok(stdout="always\n") if command[1] == "show" else _ok()
        ),
        stop_proof=lambda: None,
        external_worker_proof=lambda: None,
        stability_probe=lambda: None,
        gate_active_reader=lambda: True,
        **_standby_hooks(events),
    )

    assert events.count("lifecycle-proof") == 2


def test_prepare补偿先重施codegraph再做最终统一复证(
    tmp_path: Path,
) -> None:
    from codev_platform.ops.reindex_maintenance import (
        ReindexMaintenanceError,
        prepare_reindex_maintenance,
    )

    events: list[str] = []
    proof_calls = 0

    def prove_codegraph() -> None:
        nonlocal proof_calls
        proof_calls += 1
        events.append("codegraph-proof")
        if proof_calls == 1:
            raise RuntimeError("首次 CodeGraph 证明失败")

    with pytest.raises(ReindexMaintenanceError, match="已证明安全停机状态"):
        prepare_reindex_maintenance(
            platform_name="linux",
            dropin_path=_dropin(tmp_path),
            command_runner=lambda _command, **_kwargs: _ok(),
            codegraph_prepare=lambda: events.append("codegraph-prepare"),
            codegraph_maintenance_proof=prove_codegraph,
            gate_activator=lambda: events.append("gate"),
            external_worker_proof=lambda: events.append("external"),
            stop_proof=lambda: events.append("reindex-proof"),
        )

    assert events[-4:] == [
        "codegraph-prepare",
        "external",
        "reindex-proof",
        "codegraph-proof",
    ]


def test_restore首轮codegraph证明失败后按最终统一复证结论报告(
    tmp_path: Path,
) -> None:
    from codev_platform.ops.reindex_maintenance import (
        ReindexMaintenanceError,
        restore_reindex_maintenance,
    )

    path = _dropin(tmp_path)
    path.parent.mkdir(parents=True)
    path.write_text("[Service]\nRestart=no\n", encoding="utf-8")
    events: list[str] = []
    proof_calls = 0

    def prove_codegraph() -> None:
        nonlocal proof_calls
        proof_calls += 1
        events.append("codegraph-proof")
        if proof_calls == 1:
            raise RuntimeError("首轮 CodeGraph 证明失败")

    with pytest.raises(ReindexMaintenanceError, match="已证明安全停机状态"):
        restore_reindex_maintenance(
            platform_name="linux",
            dropin_path=path,
            command_runner=lambda command, **_kwargs: _ok(
                stdout="always\n" if command[:2] == ("systemctl", "show") else ""
            ),
            stop_proof=lambda: events.append("reindex-proof"),
            external_worker_proof=lambda: events.append("external"),
            stability_probe=lambda: None,
            gate_activator=lambda: events.append("reset-marker"),
            gate_active_reader=lambda: True,
            codegraph_prepare=lambda: events.append("codegraph-prepare"),
            codegraph_maintenance_proof=prove_codegraph,
            **_standby_hooks(events),
        )

    assert events[-4:] == [
        "codegraph-prepare",
        "external",
        "reindex-proof",
        "codegraph-proof",
    ]


def test_restore末轮codegraph证明失败后按最终统一复证结论报告(
    tmp_path: Path,
) -> None:
    from codev_platform.ops.reindex_maintenance import (
        ReindexMaintenanceError,
        restore_reindex_maintenance,
    )

    path = _dropin(tmp_path)
    path.parent.mkdir(parents=True)
    path.write_text("[Service]\nRestart=no\n", encoding="utf-8")
    events: list[str] = []
    proof_calls = 0

    def prove_codegraph() -> None:
        nonlocal proof_calls
        proof_calls += 1
        events.append("codegraph-proof")
        if proof_calls == 2:
            raise RuntimeError("末轮 CodeGraph 证明失败")

    with pytest.raises(ReindexMaintenanceError, match="已证明安全停机状态"):
        restore_reindex_maintenance(
            platform_name="linux",
            dropin_path=path,
            command_runner=lambda command, **_kwargs: _ok(
                stdout="always\n" if command[:2] == ("systemctl", "show") else ""
            ),
            stop_proof=lambda: events.append("reindex-proof"),
            external_worker_proof=lambda: events.append("external"),
            stability_probe=lambda: events.append("stable"),
            gate_activator=lambda: events.append("reset-marker"),
            gate_active_reader=lambda: True,
            codegraph_prepare=lambda: events.append("codegraph-prepare"),
            codegraph_maintenance_proof=prove_codegraph,
            **_standby_hooks(events),
        )

    compensation_prepare = events.index("codegraph-prepare")
    assert events[compensation_prepare : compensation_prepare + 4] == [
        "codegraph-prepare",
        "external",
        "reindex-proof",
        "codegraph-proof",
    ]


def test_restore补偿codegraph证明失败时不得报告安全已证明(
    tmp_path: Path,
) -> None:
    from codev_platform.ops.reindex_maintenance import (
        ReindexMaintenanceError,
        restore_reindex_maintenance,
    )

    path = _dropin(tmp_path)
    path.parent.mkdir(parents=True)
    path.write_text("[Service]\nRestart=no\n", encoding="utf-8")
    proof_calls = 0

    def prove_codegraph() -> None:
        nonlocal proof_calls
        proof_calls += 1
        if proof_calls == 2:
            raise RuntimeError("补偿 CodeGraph 证明失败")

    with pytest.raises(ReindexMaintenanceError, match="安全状态未证明"):
        restore_reindex_maintenance(
            platform_name="linux",
            dropin_path=path,
            command_runner=lambda _command, **_kwargs: _ok(),
            stop_proof=lambda: None,
            external_worker_proof=lambda: None,
            stability_probe=lambda: (_ for _ in ()).throw(RuntimeError("恢复失败")),
            gate_activator=lambda: None,
            gate_active_reader=lambda: True,
            codegraph_prepare=lambda: None,
            codegraph_maintenance_proof=prove_codegraph,
            **_standby_hooks([]),
        )
