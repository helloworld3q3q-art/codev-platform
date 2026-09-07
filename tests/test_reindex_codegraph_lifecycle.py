"""CodeGraph runtime mask 停机生命周期的回归测试。"""

from __future__ import annotations

from types import SimpleNamespace

import pytest


def _ok() -> SimpleNamespace:
    return SimpleNamespace(returncode=0, stdout="", stderr="不得泄露原始错误")


def test_准备先证明永久guard再施加耐久门禁和runtime_mask并完成四重证明() -> None:
    from codev_platform.ops.reindex_codegraph_lifecycle import (
        prepare_codegraph_maintenance,
    )

    events: list[object] = []

    prepare_codegraph_maintenance(
        platform_name="linux",
        command_runner=lambda command, **_kwargs: events.append(command) or _ok(),
        guard_ensurer=lambda: events.append("guard"),
        hold_activator=lambda: events.append("hold"),
        mask_applier=lambda: events.append("mask"),
        guard_proof=lambda: events.append("guard-proof"),
        hold_proof=lambda: events.append("hold-proof"),
        mask_proof=lambda: events.append("mask-proof"),
        stop_proof=lambda: events.append("stop-proof"),
    )

    assert events == [
        "guard",
        "guard-proof",
        "hold",
        "mask",
        ("systemctl", "stop", "codev-mcp-codegraph.service"),
        ("systemctl", "reset-failed", "codev-mcp-codegraph.service"),
        "guard-proof",
        "hold-proof",
        "mask-proof",
        "stop-proof",
    ]


def test_施加mask失败时仍穷尽停止与全部证明但拒绝声称成功() -> None:
    from codev_platform.ops.reindex_codegraph_lifecycle import (
        CodegraphMaintenanceLifecycleError,
        prepare_codegraph_maintenance,
    )

    events: list[object] = []

    with pytest.raises(CodegraphMaintenanceLifecycleError, match="安全状态未证明"):
        prepare_codegraph_maintenance(
            platform_name="linux",
            command_runner=lambda command, **_kwargs: events.append(command) or _ok(),
            guard_ensurer=lambda: events.append("guard"),
            hold_activator=lambda: events.append("hold"),
            mask_applier=lambda: (
                events.append("mask") or (_ for _ in ()).throw(RuntimeError("mask failed"))
            ),
            guard_proof=lambda: events.append("guard-proof"),
            hold_proof=lambda: events.append("hold-proof"),
            mask_proof=lambda: events.append("mask-proof"),
            stop_proof=lambda: events.append("stop-proof"),
        )

    assert events == [
        "guard",
        "guard-proof",
        "hold",
        "mask",
        ("systemctl", "stop", "codev-mcp-codegraph.service"),
        ("systemctl", "reset-failed", "codev-mcp-codegraph.service"),
        "guard-proof",
        "hold-proof",
        "mask-proof",
        "stop-proof",
    ]


def test_stop失败时仅强制收敛固定codegraph_unit后再证明() -> None:
    from codev_platform.ops.reindex_codegraph_lifecycle import (
        prepare_codegraph_maintenance,
    )

    commands: list[tuple[str, ...]] = []

    def run(command: tuple[str, ...], **_kwargs) -> SimpleNamespace:
        commands.append(command)
        if command == ("systemctl", "stop", "codev-mcp-codegraph.service"):
            return SimpleNamespace(returncode=1, stdout="", stderr="敏感错误")
        return _ok()

    prepare_codegraph_maintenance(
        platform_name="linux",
        command_runner=run,
        guard_ensurer=lambda: None,
        hold_activator=lambda: None,
        mask_applier=lambda: None,
        guard_proof=lambda: None,
        hold_proof=lambda: None,
        mask_proof=lambda: None,
        stop_proof=lambda: None,
    )

    assert commands == [
        ("systemctl", "stop", "codev-mcp-codegraph.service"),
        (
            "systemctl",
            "kill",
            "--kill-who=all",
            "--signal=SIGKILL",
            "codev-mcp-codegraph.service",
        ),
        ("systemctl", "reset-failed", "codev-mcp-codegraph.service"),
    ]


def test_默认停机证明接受runtime_mask后的restart_no(monkeypatch: pytest.MonkeyPatch) -> None:
    from codev_platform.ops import reindex_admin_systemd_guard as guard
    from codev_platform.ops.reindex_codegraph_lifecycle import (
        prepare_codegraph_maintenance,
    )

    checks: list[tuple[str, str]] = []

    def 记录停机证明(unit: str, *, expected_restart: str, **_kwargs) -> None:
        checks.append((unit, expected_restart))

    monkeypatch.setattr(guard, "verify_systemd_unit_stopped", 记录停机证明)

    prepare_codegraph_maintenance(
        platform_name="linux",
        command_runner=lambda command, **_kwargs: _ok(),
        guard_ensurer=lambda: None,
        hold_activator=lambda: None,
        mask_applier=lambda: None,
        guard_proof=lambda: None,
        hold_proof=lambda: None,
        mask_proof=lambda: None,
    )

    assert checks == [("codev-mcp-codegraph.service", "no")]


def test_停机证明接受inactive_dead且空control_group() -> None:
    from codev_platform.ops.reindex_admin_systemd_guard import (
        verify_systemd_unit_stopped,
    )

    def run(_command: tuple[str, ...], **_kwargs) -> SimpleNamespace:
        return SimpleNamespace(
            returncode=0,
            stdout=("ActiveState=inactive\nSubState=dead\nControlGroup=\nRestart=no\n"),
        )

    verify_systemd_unit_stopped(
        "codev-mcp-codegraph.service",
        expected_restart="no",
        platform_name="linux",
        command_runner=run,
        cgroup_events_reader=lambda _path: pytest.fail("空 ControlGroup 不应读取 cgroup.events"),
    )


def test_验证必须同时检查耐久门禁runtime_mask和停机证明() -> None:
    from codev_platform.ops.reindex_codegraph_lifecycle import (
        verify_codegraph_maintenance,
    )

    events: list[str] = []

    verify_codegraph_maintenance(
        platform_name="linux",
        guard_proof=lambda: events.append("guard"),
        hold_proof=lambda: events.append("hold"),
        mask_proof=lambda: events.append("mask"),
        stop_proof=lambda: events.append("stop"),
    )

    assert events == ["guard", "hold", "mask", "stop"]


def test_guard中断仍穷尽停机后原样重抛且不会删除永久文件() -> None:
    from codev_platform.ops.reindex_codegraph_lifecycle import (
        prepare_codegraph_maintenance,
    )

    events: list[object] = []

    with pytest.raises(KeyboardInterrupt, match="guard interrupted"):
        prepare_codegraph_maintenance(
            platform_name="linux",
            command_runner=lambda command, **_kwargs: events.append(command) or _ok(),
            guard_ensurer=lambda: (
                events.append("guard")
                or (_ for _ in ()).throw(KeyboardInterrupt("guard interrupted"))
            ),
            hold_activator=lambda: events.append("hold"),
            mask_applier=lambda: events.append("mask"),
            guard_proof=lambda: events.append("guard-proof"),
            hold_proof=lambda: events.append("hold-proof"),
            mask_proof=lambda: events.append("mask-proof"),
            stop_proof=lambda: events.append("stop-proof"),
        )

    assert events == [
        "guard",
        "mask",
        ("systemctl", "stop", "codev-mcp-codegraph.service"),
        ("systemctl", "reset-failed", "codev-mcp-codegraph.service"),
        "guard-proof",
        "hold-proof",
        "mask-proof",
        "stop-proof",
    ]


def test_guard首次证明失败时不得创建hold但仍穷尽运行态收敛与最终证明() -> None:
    from codev_platform.ops.reindex_codegraph_lifecycle import (
        CodegraphMaintenanceLifecycleError,
        prepare_codegraph_maintenance,
    )

    events: list[object] = []
    proof_count = 0

    def prove_guard() -> None:
        nonlocal proof_count
        proof_count += 1
        events.append(f"guard-proof-{proof_count}")
        if proof_count == 1:
            raise RuntimeError("guard proof failed")

    with pytest.raises(CodegraphMaintenanceLifecycleError, match="安全状态未证明"):
        prepare_codegraph_maintenance(
            platform_name="linux",
            command_runner=lambda command, **_kwargs: events.append(command) or _ok(),
            guard_ensurer=lambda: events.append("guard"),
            hold_activator=lambda: events.append("hold"),
            mask_applier=lambda: events.append("mask"),
            guard_proof=prove_guard,
            hold_proof=lambda: events.append("hold-proof"),
            mask_proof=lambda: events.append("mask-proof"),
            stop_proof=lambda: events.append("stop-proof"),
        )

    assert events == [
        "guard",
        "guard-proof-1",
        "mask",
        ("systemctl", "stop", "codev-mcp-codegraph.service"),
        ("systemctl", "reset-failed", "codev-mcp-codegraph.service"),
        "guard-proof-2",
        "hold-proof",
        "mask-proof",
        "stop-proof",
    ]


def test_guard普通失败时不得创建hold但仍穷尽当前运行态停机() -> None:
    from codev_platform.ops.reindex_codegraph_lifecycle import (
        CodegraphMaintenanceLifecycleError,
        prepare_codegraph_maintenance,
    )

    events: list[object] = []
    with pytest.raises(CodegraphMaintenanceLifecycleError, match="安全状态未证明"):
        prepare_codegraph_maintenance(
            platform_name="linux",
            command_runner=lambda command, **_kwargs: events.append(command) or _ok(),
            guard_ensurer=lambda: (
                events.append("guard") or (_ for _ in ()).throw(RuntimeError("guard failed"))
            ),
            hold_activator=lambda: events.append("hold"),
            mask_applier=lambda: events.append("mask"),
            guard_proof=lambda: events.append("guard-proof"),
            hold_proof=lambda: events.append("hold-proof"),
            mask_proof=lambda: events.append("mask-proof"),
            stop_proof=lambda: events.append("stop-proof"),
        )

    assert events == [
        "guard",
        "mask",
        ("systemctl", "stop", "codev-mcp-codegraph.service"),
        ("systemctl", "reset-failed", "codev-mcp-codegraph.service"),
        "guard-proof",
        "hold-proof",
        "mask-proof",
        "stop-proof",
    ]


def test_逐耐久前缀始终满足hold只能出现在guard之后() -> None:
    """按真实掉电投影排除 runtime 动作，禁止形成 hold-only 耐久状态。"""
    from codev_platform.ops.reindex_codegraph_lifecycle import (
        prepare_codegraph_maintenance,
    )

    events: list[str] = []
    prepare_codegraph_maintenance(
        platform_name="linux",
        command_runner=lambda command, **_kwargs: events.append(command[1]) or _ok(),
        guard_ensurer=lambda: events.append("guard"),
        hold_activator=lambda: events.append("hold"),
        mask_applier=lambda: events.append("mask"),
        guard_proof=lambda: events.append("guard-proof"),
        hold_proof=lambda: events.append("hold-proof"),
        mask_proof=lambda: events.append("mask-proof"),
        stop_proof=lambda: events.append("stop-proof"),
    )

    assert events == [
        "guard",
        "guard-proof",
        "hold",
        "mask",
        "stop",
        "reset-failed",
        "guard-proof",
        "hold-proof",
        "mask-proof",
        "stop-proof",
    ]
    durable_names = frozenset({"guard", "hold"})
    expected_projection = [
        frozenset(),
        frozenset({"guard"}),
        frozenset({"guard"}),
        frozenset({"guard", "hold"}),
        frozenset({"guard", "hold"}),
        frozenset({"guard", "hold"}),
        frozenset({"guard", "hold"}),
        frozenset({"guard", "hold"}),
        frozenset({"guard", "hold"}),
        frozenset({"guard", "hold"}),
        frozenset({"guard", "hold"}),
    ]
    actual_projection = [
        frozenset(events[:length]) & durable_names for length in range(len(events) + 1)
    ]
    assert actual_projection == expected_projection
    assert all("hold" not in state or "guard" in state for state in actual_projection)


def test_恢复时只允许启动固定codegraph_unit() -> None:
    from codev_platform.ops.reindex_codegraph_lifecycle import start_codegraph_service

    commands: list[tuple[str, ...]] = []

    start_codegraph_service(
        platform_name="linux",
        command_runner=lambda command, **_kwargs: commands.append(command) or _ok(),
    )

    assert commands == [("systemctl", "start", "codev-mcp-codegraph.service")]


def test_固定codegraph_unit启动失败时不泄露systemctl原始输出() -> None:
    from codev_platform.ops.reindex_codegraph_lifecycle import (
        CodegraphMaintenanceLifecycleError,
        start_codegraph_service,
    )

    with pytest.raises(CodegraphMaintenanceLifecycleError) as raised:
        start_codegraph_service(
            platform_name="linux",
            command_runner=lambda *_args, **_kwargs: SimpleNamespace(
                returncode=1,
                stdout="",
                stderr="敏感 systemctl 输出",
            ),
        )

    assert "敏感 systemctl 输出" not in str(raised.value)


def test_CodeGraph恢复成功后启用并复证开机启动关系() -> None:
    from codev_platform.ops.reindex_codegraph_lifecycle import (
        enable_codegraph_service,
    )

    commands: list[tuple[str, ...]] = []
    enable_codegraph_service(
        platform_name="linux",
        command_runner=lambda command, **_kwargs: commands.append(command) or _ok(),
    )

    assert commands == [
        ("systemctl", "enable", "codev-mcp-codegraph.service"),
        (
            "systemctl",
            "is-enabled",
            "--quiet",
            "codev-mcp-codegraph.service",
        ),
    ]
