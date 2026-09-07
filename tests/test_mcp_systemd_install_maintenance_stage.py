"""maintenance-stage systemd 安装的延迟激活与失败关闭测试。"""

from __future__ import annotations

from dataclasses import replace
from pathlib import Path

import pytest

from tests.mcp_systemd_install_transaction_support import _维护清单, _端口


def test_门禁交接准备先持久化stage回执且不改变任何进程(tmp_path: Path) -> None:
    """总门禁撤销前只允许安装、enable 和持久化回执，不允许启动服务。"""
    from codev_platform import mcp_systemd_install_transaction as module

    events: list[object] = []
    manifest = _维护清单(module, tmp_path)
    ports, _files, states, _initial_files, initial_states = _端口(
        module,
        manifest,
        events,
    )
    write_receipt = ports.write_stage_receipt
    verify_receipt = ports.verify_stage_receipt
    read_process_state = ports.read_unit_process_state
    ports = replace(
        ports,
        read_unit_process_state=lambda name: (
            events.append(("读取进程态", name)),
            read_process_state(name),
        )[-1],
        write_stage_receipt=lambda content: (
            events.append("写入stage回执"),
            write_receipt(content),
        )[-1],
        verify_stage_receipt=lambda content: (
            events.append("复证stage回执"),
            verify_receipt(content),
        )[-1],
    )

    report = module.install_systemd_guarded_stage_transaction(
        manifest,
        ports=ports,
        platform_name="linux",
        effective_user_id=lambda: 0,
    )

    lifecycle_commands = [
        event[1] for event in events if isinstance(event, tuple) and event[0] == "systemctl"
    ]
    assert not any(command[1] == "restart" for command in lifecycle_commands)
    assert set(next(command[2:] for command in lifecycle_commands if command[1] == "enable")) == {
        unit.unit_name for unit in manifest.units if unit.enable
    }
    assert {name: state.active_state for name, state in states.items()} == {
        name: state.active_state for name, state in initial_states.items()
    }
    assert all(("读取进程态", unit.unit_name) in events for unit in manifest.units)
    assert events.index("写入stage回执") < events.index("复证stage回执")
    assert report.deferred_activation_units == manifest.restart_units


def test_门禁交接最终复验失败会恢复回执载荷和enable原像(tmp_path: Path) -> None:
    from codev_platform import mcp_systemd_install_transaction as module

    manifest = _维护清单(module, tmp_path)
    events: list[object] = []
    ports, files, states, initial_files, initial_states = _端口(
        module,
        manifest,
        events,
    )
    calls = 0

    def 证明门禁仍生效() -> None:
        nonlocal calls
        calls += 1
        if calls == 2:
            raise module.SystemdInstallTransactionError("总门禁发生漂移")

    ports = replace(ports, verify_install_boundary=证明门禁仍生效)

    with pytest.raises(module.SystemdInstallTransactionError, match="已回滚"):
        module.install_systemd_guarded_stage_transaction(
            manifest,
            ports=ports,
            platform_name="linux",
            effective_user_id=lambda: 0,
        )

    assert calls == 3
    assert files == initial_files
    assert states == initial_states
    assert ports.snapshot_stage_receipt() is None


def test_门禁交接拒绝systemctl成功但enable状态未生效(tmp_path: Path) -> None:
    from codev_platform import mcp_systemd_install_transaction as module

    manifest = _维护清单(module, tmp_path)
    events: list[object] = []
    ports, files, states, initial_files, initial_states = _端口(
        module,
        manifest,
        events,
    )
    ports = replace(
        ports,
        systemctl=lambda command: events.append(("systemctl-noop", command)),
    )

    with pytest.raises(module.SystemdInstallTransactionError, match="已回滚") as captured:
        module.install_systemd_guarded_stage_transaction(
            manifest,
            ports=ports,
            platform_name="linux",
            effective_user_id=lambda: 0,
        )

    assert captured.value.__cause__ is not None
    assert "启用状态" in str(captured.value.__cause__)
    assert files == initial_files
    assert states == initial_states
    assert ports.snapshot_stage_receipt() is None


def test_生产门禁交接入口必须显式注入计划绑定端口(tmp_path: Path) -> None:
    from codev_platform import mcp_systemd_install_transaction as module

    with pytest.raises(module.SystemdInstallTransactionError, match="显式.*门禁"):
        module.prepare_systemd_guarded_stage_from_manifest_path(
            tmp_path / "install-manifest.json",
            platform_name="linux",
            effective_user_id=lambda: 0,
        )


def test_维护态安装冻结全部文件但不读取或改变延迟单元状态(
    tmp_path: Path,
) -> None:
    from codev_platform import mcp_systemd_install_transaction as module

    events: list[object] = []
    manifest = _维护清单(module, tmp_path)
    ports, _files, states, _initial_files, _initial_states = _端口(
        module,
        manifest,
        events,
    )

    report = module.install_systemd_maintenance_stage_transaction(
        manifest,
        ports=ports,
        platform_name="linux",
        effective_user_id=lambda: 0,
    )

    protected = (
        "codev-reindex.service",
        "codev-mcp-codegraph.service",
        "codev-webhook.service",
    )
    assert report.deferred_activation_units == protected
    assert all(("读取原像", name) in events for name in protected)
    assert all(("读取状态", name) not in events for name in protected)
    assert all(states[name].active_state == "inactive" for name in protected)
    lifecycle_commands = [
        event[1] for event in events if isinstance(event, tuple) and event[0] == "systemctl"
    ]
    assert all(
        not set(command[2:]) & set(protected)
        for command in lifecycle_commands
        if command[1] in {"enable", "restart"}
    )
    assert ("systemctl", "enable", "codev-clock-resync.timer") in lifecycle_commands
    assert ("systemctl", "restart", "codev-clock-resync.timer") in lifecycle_commands


def test_维护态安装清单缺任一写服务时在锁内零修改拒绝(tmp_path: Path) -> None:
    from codev_platform import mcp_systemd_install_transaction as module

    full_manifest = _维护清单(module, tmp_path)
    manifest = module.SystemdInstallManifest(
        tuple(
            unit for unit in full_manifest.units if unit.unit_name != "codev-mcp-codegraph.service"
        ),
        runtime_revision=full_manifest.runtime_revision,
    )
    events: list[object] = []
    ports, _files, _states, _initial_files, _initial_states = _端口(
        module,
        manifest,
        events,
    )

    with pytest.raises(module.SystemdInstallTransactionError, match="缺少"):
        module.install_systemd_maintenance_stage_transaction(
            manifest,
            ports=ports,
            platform_name="linux",
            effective_user_id=lambda: 0,
        )

    assert "进入安装锁" in events
    assert not any(
        isinstance(event, tuple) and event[0] in {"写入", "读取原像", "读取状态"}
        for event in events
    )


def test_延迟激活写服务必须在合同层声明最终重启目标(tmp_path: Path) -> None:
    from codev_platform import mcp_systemd_install_transaction as module

    full_manifest = _维护清单(module, tmp_path)
    codegraph = next(
        unit for unit in full_manifest.units if unit.unit_name == "codev-mcp-codegraph.service"
    )

    with pytest.raises(module.SystemdInstallTransactionError, match="最终 restart"):
        replace(codegraph, restart=False)


def test_维护态写服务必须在策略层保留最终启用声明(tmp_path: Path) -> None:
    from codev_platform import mcp_systemd_install_transaction as module

    full_manifest = _维护清单(module, tmp_path)
    units = tuple(
        replace(unit, enable=False) if unit.unit_name == "codev-mcp-codegraph.service" else unit
        for unit in full_manifest.units
    )
    manifest = module.SystemdInstallManifest(
        units,
        runtime_revision=full_manifest.runtime_revision,
    )
    events: list[object] = []
    ports, _files, _states, _initial_files, _initial_states = _端口(
        module,
        manifest,
        events,
    )

    with pytest.raises(module.SystemdInstallTransactionError, match="生命周期声明"):
        module.install_systemd_maintenance_stage_transaction(
            manifest,
            ports=ports,
            platform_name="linux",
            effective_user_id=lambda: 0,
        )

    assert not any(isinstance(event, tuple) and event[0] == "写入" for event in events)


def test_安装策略mode被篡改为另一合法枚举后清单重验失败关闭(
    tmp_path: Path,
) -> None:
    from codev_platform import mcp_systemd_install_transaction as module
    from codev_platform.mcp_systemd_install_policy import (
        SystemdInstallMode,
        SystemdInstallPolicy,
    )

    policy = SystemdInstallPolicy(SystemdInstallMode.NORMAL)
    object.__setattr__(policy, "mode", SystemdInstallMode.MAINTENANCE_STAGE)

    with pytest.raises(module.SystemdInstallTransactionError, match="策略无效"):
        policy.require_manifest(_维护清单(module, tmp_path))


def test_安装策略mode被篡改为字符串后拒绝非精确类型(tmp_path: Path) -> None:
    from codev_platform import mcp_systemd_install_transaction as module
    from codev_platform.mcp_systemd_install_policy import (
        MAINTENANCE_DEFERRED_UNITS,
        SystemdInstallMode,
        SystemdInstallPolicy,
    )

    policy = SystemdInstallPolicy(
        SystemdInstallMode.MAINTENANCE_STAGE,
        MAINTENANCE_DEFERRED_UNITS,
    )
    object.__setattr__(policy, "mode", SystemdInstallMode.MAINTENANCE_STAGE.value)

    with pytest.raises(module.SystemdInstallTransactionError, match="策略无效"):
        policy.require_manifest(_维护清单(module, tmp_path))


def test_安装策略拒绝派生类型绕过精确合同() -> None:
    from codev_platform.mcp_systemd_install_contract import (
        SystemdInstallTransactionError,
    )
    from codev_platform.mcp_systemd_install_policy import (
        SystemdInstallMode,
        SystemdInstallPolicy,
    )

    class 派生策略(SystemdInstallPolicy):
        pass

    with pytest.raises(SystemdInstallTransactionError, match="策略无效"):
        派生策略(SystemdInstallMode.NORMAL)


@pytest.mark.parametrize(
    "tampered_deferred",
    [
        pytest.param((), id="缺少受保护单元"),
        pytest.param(
            ["codev-reindex.service", "codev-mcp-codegraph.service"],
            id="非精确元组类型",
        ),
    ],
)
def test_延迟激活集合被篡改后不得执行任何enable或restart(
    tmp_path: Path,
    tampered_deferred: object,
) -> None:
    from codev_platform import mcp_systemd_install_transaction as module
    from codev_platform.mcp_systemd_install_policy import (
        MAINTENANCE_DEFERRED_UNITS,
        SystemdInstallMode,
        SystemdInstallPolicy,
    )

    policy = SystemdInstallPolicy(
        SystemdInstallMode.MAINTENANCE_STAGE,
        MAINTENANCE_DEFERRED_UNITS,
    )
    object.__setattr__(policy, "deferred_units", tampered_deferred)
    commands: list[tuple[str, ...]] = []

    with pytest.raises(module.SystemdInstallTransactionError, match="延迟激活策略无效"):
        module._run_install_lifecycle_commands(
            _维护清单(module, tmp_path),
            policy,
            commands.append,
        )

    assert commands == []


def test_维护态命令入口显式选择stage并报告延迟激活(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    from codev_platform import mcp_systemd_install_transaction as module

    captured: list[Path] = []

    def stage(path: Path, **_kwargs) -> module.SystemdInstallReport:
        captured.append(path)
        return module.SystemdInstallReport(
            deferred_activation_units=(
                "codev-reindex.service",
                "codev-mcp-codegraph.service",
                "codev-webhook.service",
            )
        )

    monkeypatch.setattr(
        module,
        "install_systemd_maintenance_stage_from_manifest_path",
        stage,
    )

    assert module.main(["--manifest", "/trusted/install-manifest.json", "--maintenance-stage"]) == 0

    assert captured == [Path("/trusted/install-manifest.json")]
    output = capsys.readouterr().out
    assert "维护态载荷安装完成" in output
    assert "未由本事务重启" in output
    assert "codev-reindex.service" in output
    assert "codev-mcp-codegraph.service" in output
    assert "codev-webhook.service" in output


def test_维护态提交前终态证明失败会补偿全部载荷与普通服务状态(
    tmp_path: Path,
) -> None:
    from codev_platform import mcp_systemd_install_transaction as module

    manifest = _维护清单(module, tmp_path)
    events: list[object] = []
    ports, files, states, initial_files, initial_states = _端口(
        module,
        manifest,
        events,
    )
    calls = 0

    def 证明维护边界() -> None:
        nonlocal calls
        calls += 1
        if calls == 2:
            raise module.SystemdInstallTransactionError("提交前维护终态失败")

    ports = replace(ports, verify_install_boundary=证明维护边界)

    with pytest.raises(module.SystemdInstallTransactionError, match="已回滚"):
        module.install_systemd_maintenance_stage_transaction(
            manifest,
            ports=ports,
            platform_name="linux",
            effective_user_id=lambda: 0,
        )

    assert calls == 3
    assert files == initial_files
    assert states == initial_states


def test_维护态补偿后的完整状态仍无法证明时不得误报已回滚(
    tmp_path: Path,
) -> None:
    from codev_platform import mcp_systemd_install_transaction as module

    manifest = _维护清单(module, tmp_path)
    events: list[object] = []
    ports, _files, _states, _initial_files, _initial_states = _端口(
        module,
        manifest,
        events,
    )
    calls = 0

    def 证明维护边界() -> None:
        nonlocal calls
        calls += 1
        if calls >= 2:
            raise module.SystemdInstallTransactionError("维护终态无法证明")

    ports = replace(ports, verify_install_boundary=证明维护边界)

    with pytest.raises(module.SystemdInstallTransactionError, match="安全状态未证明"):
        module.install_systemd_maintenance_stage_transaction(
            manifest,
            ports=ports,
            platform_name="linux",
            effective_user_id=lambda: 0,
        )

    assert calls == 3
