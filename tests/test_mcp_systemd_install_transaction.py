"""systemd 安装事务的常规编排与访问门禁测试。"""

from __future__ import annotations

import signal
from dataclasses import replace
from pathlib import Path

import pytest

from tests.mcp_systemd_install_transaction_support import (
    _CONTENT,
    _清单,
    _维护清单,
    _端口,
    _规格,
)


def test_安装事务在独占安装锁内读取快照写入并验证运行态(tmp_path: Path) -> None:
    """安装只执行可逆动作，源内容在独占锁内预读并绑定到事务。"""
    from codev_platform import mcp_systemd_install_transaction as module

    events: list[object] = []
    manifest = _清单(module, tmp_path)
    ports, _files, states, _initial_files, _initial_states = _端口(module, manifest, events)
    previous_sigint_handler = signal.getsignal(signal.SIGINT)

    report = module.install_systemd_transaction(
        manifest,
        ports=ports,
        platform_name="linux",
        effective_user_id=lambda: 0,
    )

    assert ("systemctl", ("systemctl", "reset-failed", *manifest.restart_units)) not in events
    assert [states[name] for name in manifest.restart_units] == [
        module.SystemdUnitState("enabled", "active") for _name in manifest.restart_units
    ]
    assert report.deferred_activation_units == ()
    assert events.index("进入安装锁") < events.index(("读取源", manifest.units[0].source))
    assert signal.getsignal(signal.SIGINT) == previous_sigint_handler


def test_install_only复用事务但不改变任何服务活动态(tmp_path: Path) -> None:
    from codev_platform import mcp_systemd_install_transaction as module

    events: list[object] = []
    manifest = _清单(module, tmp_path)
    ports, _files, states, _initial_files, initial_states = _端口(
        module,
        manifest,
        events,
    )

    report = module.install_systemd_install_only_transaction(
        manifest,
        ports=ports,
        platform_name="linux",
        effective_user_id=lambda: 0,
    )

    commands = [
        event[1] for event in events if isinstance(event, tuple) and event[0] == "systemctl"
    ]
    assert commands == [
        ("systemctl", "daemon-reload"),
        ("systemctl", "enable", *manifest.enable_units),
    ]
    assert all(states[name].active_state == initial_states[name].active_state for name in states)
    assert report.deferred_activation_units == manifest.restart_units


def test_启用态复证不受瞬时活动态影响(tmp_path: Path) -> None:
    """daemon-reload 后的 activating 不能掩盖已落地的持久启用链接。"""
    from codev_platform import mcp_systemd_install_transaction as module

    events: list[object] = []
    manifest = _清单(module, tmp_path)
    ports, _files, states, _initial_files, _initial_states = _端口(module, manifest, events)

    def 瞬时活动态读取(name: str):
        if any(
            isinstance(event, tuple) and event[0] == "systemctl" and event[1][1] == "enable"
            for event in events
        ):
            raise module.SystemdInstallTransactionError("systemd unit 活动状态不可逆")
        return states[name]

    ports = replace(ports, read_unit_state=瞬时活动态读取)

    report = module.install_systemd_transaction(
        manifest,
        ports=ports,
        platform_name="linux",
        effective_user_id=lambda: 0,
    )

    assert report.deferred_activation_units == ()
    assert ("systemctl", ("systemctl", "enable", *manifest.enable_units)) in events


def test_启用态复证拒绝非enabled持久状态() -> None:
    from codev_platform.mcp_systemd_install_contract import SystemdInstallTransactionError
    from codev_platform.mcp_systemd_install_verification import verify_enabled_units

    with pytest.raises(SystemdInstallTransactionError, match="启用状态未生效"):
        verify_enabled_units(("codev-web.service",), lambda _name: "disabled")


def test_install_only失败补偿不得启动或停止原有服务(tmp_path: Path) -> None:
    from codev_platform import mcp_systemd_install_transaction as module

    events: list[object] = []
    manifest = _清单(module, tmp_path)
    ports, _files, states, _initial_files, initial_states = _端口(
        module,
        manifest,
        events,
        fail_command="enable",
    )

    with pytest.raises(module.SystemdInstallTransactionError, match="已回滚"):
        module.install_systemd_install_only_transaction(
            manifest,
            ports=ports,
            platform_name="linux",
            effective_user_id=lambda: 0,
        )

    assert not any(isinstance(event, tuple) and event[0] == "恢复活动态" for event in events)
    assert all(states[name].active_state == initial_states[name].active_state for name in states)


def test_install_only_detects_pid_or_invocation_drift_as_unproven(tmp_path: Path) -> None:
    from codev_platform import mcp_systemd_install_transaction as module

    events: list[object] = []
    manifest = _清单(module, tmp_path)
    ports, _files, _states, _initial_files, _initial_states = _端口(
        module,
        manifest,
        events,
    )
    reads = 0

    def drifting_process_state(name: str):
        nonlocal reads
        reads += 1
        if reads > len(manifest.units) and name == manifest.units[0].unit_name:
            return module.SystemdUnitProcessState("active", 1234, "a" * 32)
        return module.SystemdUnitProcessState("inactive", 0, "")

    ports = replace(ports, read_unit_process_state=drifting_process_state)

    with pytest.raises(module.SystemdInstallTransactionError, match="安全状态未证明"):
        module.install_systemd_install_only_transaction(
            manifest,
            ports=ports,
            platform_name="linux",
            effective_user_id=lambda: 0,
        )

    assert not any(isinstance(event, tuple) and event[0] == "恢复活动态" for event in events)


def test_主unit与legacy_shadow在同一事务中提交且最终复证(
    tmp_path: Path,
) -> None:
    """shadow 必须在主 unit 写完后、reload 前退役，并在全部启动完成后复证。"""
    from codev_platform import mcp_systemd_install_transaction as module

    events: list[object] = []
    manifest = _清单(module, tmp_path)
    shadow_states = {unit.unit_name: "active" for unit in manifest.units}
    ports, _files, _states, _initial_files, _initial_states = _端口(
        module,
        manifest,
        events,
        shadow_states=shadow_states,
    )

    module.install_systemd_transaction(
        manifest,
        ports=ports,
        platform_name="linux",
        effective_user_id=lambda: 0,
    )

    names = tuple(unit.unit_name for unit in manifest.units)
    assert set(shadow_states.values()) == {"retired"}
    assert events.index(("读取shadow原像", names)) < events.index(
        ("写入", manifest.units[0].unit_name, _CONTENT)
    )
    assert events.index(("写入", manifest.units[-1].unit_name, _CONTENT)) < events.index(
        ("退役shadow", names)
    )
    assert events.index(("退役shadow", names)) < events.index(
        ("systemctl", ("systemctl", "daemon-reload"))
    )
    assert events.index(("systemctl", ("systemctl", "daemon-reload"))) < events.index(
        ("验证有效载荷", names)
    )
    assert events.index(("验证有效载荷", names)) < events.index(
        ("systemctl", ("systemctl", "enable", *manifest.enable_units))
    )
    assert events.index(("验证运行", manifest.immediate_restart_units)) < events.index(
        ("验证shadow退役", names)
    )


def test_有效载荷证明失败时禁止启停并补偿全部原像(tmp_path: Path) -> None:
    """reload 后的闭环证明是生命周期动作前的失败关闭门禁。"""
    from codev_platform import mcp_systemd_install_transaction as module

    events: list[object] = []
    manifest = _清单(module, tmp_path)
    names = tuple(unit.unit_name for unit in manifest.units)
    shadow_states = {name: "active" for name in names}
    ports, files, states, initial_files, initial_states = _端口(
        module,
        manifest,
        events,
        shadow_states=shadow_states,
        effective_payload_failure=True,
    )

    with pytest.raises(module.SystemdInstallTransactionError, match="已回滚"):
        module.install_systemd_transaction(
            manifest,
            ports=ports,
            platform_name="linux",
            effective_user_id=lambda: 0,
        )

    assert files == initial_files
    assert states == initial_states
    assert set(shadow_states.values()) == {"active"}
    assert ("systemctl", ("systemctl", "daemon-reload")) in events
    assert not any(
        isinstance(item, tuple) and item[0] == "systemctl" and item[1][1] in {"enable", "restart"}
        for item in events
    )


def test_runtime_binding_lock_covers_preflight_mutation_and_compensation(tmp_path: Path) -> None:
    """current 共享锁必须直到失败补偿和复证完成后才释放。"""
    from codev_platform import mcp_systemd_install_transaction as module

    events: list[object] = []
    manifest = _清单(module, tmp_path)
    ports, _files, _states, _initial_files, _initial_states = _端口(
        module,
        manifest,
        events,
        effective_payload_failure=True,
    )

    with pytest.raises(module.SystemdInstallTransactionError, match="已回滚"):
        module.install_systemd_transaction(
            manifest,
            ports=ports,
            platform_name="linux",
            effective_user_id=lambda: 0,
        )

    assert events.index("进入安装锁") < events.index("进入运行时绑定锁")
    assert events.index("进入运行时绑定锁") < events.index("验证运行时绑定")
    assert events.index("验证运行时绑定") < events.index("目标用户预检")
    last_restore = max(
        index
        for index, event in enumerate(events)
        if isinstance(event, tuple) and event[0].startswith("恢复")
    )
    assert last_restore < events.index("退出运行时绑定锁")
    assert events.index("退出运行时绑定锁") < events.index("退出安装锁")


def test_第N个shadow退役失败时恢复全部文件shadow与服务状态(
    tmp_path: Path,
) -> None:
    """前序已移动的 shadow 也属于同一补偿边界，后续补偿分量不得遗漏。"""
    from codev_platform import mcp_systemd_install_transaction as module

    events: list[object] = []
    manifest = _清单(module, tmp_path)
    shadow_states = {unit.unit_name: "active" for unit in manifest.units}
    ports, files, states, initial_files, initial_states = _端口(
        module,
        manifest,
        events,
        shadow_states=shadow_states,
        shadow_retire_fail_at=2,
    )

    with pytest.raises(module.SystemdInstallTransactionError, match="已回滚"):
        module.install_systemd_transaction(
            manifest,
            ports=ports,
            platform_name="linux",
            effective_user_id=lambda: 0,
        )

    assert files == initial_files
    assert states == initial_states
    assert set(shadow_states.values()) == {"active"}
    restored = [
        item[1][0] for item in events if isinstance(item, tuple) and item[0] == "恢复shadow"
    ]
    assert restored == list(reversed(tuple(unit.unit_name for unit in manifest.units)))


def test_shadow最终证明失败时补偿而非保留半迁移状态(tmp_path: Path) -> None:
    """restart 全成功后证明失败仍必须恢复旧主 unit、shadow 和精确状态。"""
    from codev_platform import mcp_systemd_install_transaction as module

    events: list[object] = []
    manifest = _清单(module, tmp_path)
    shadow_states = {unit.unit_name: "active" for unit in manifest.units}
    ports, files, states, initial_files, initial_states = _端口(
        module,
        manifest,
        events,
        shadow_states=shadow_states,
        shadow_verify_failure=True,
    )

    with pytest.raises(module.SystemdInstallTransactionError, match="已回滚"):
        module.install_systemd_transaction(
            manifest,
            ports=ports,
            platform_name="linux",
            effective_user_id=lambda: 0,
        )

    assert files == initial_files
    assert states == initial_states
    assert set(shadow_states.values()) == {"active"}


def test_单个shadow补偿失败仍尝试其余原像且不得误报安全(
    tmp_path: Path,
) -> None:
    """每个 shadow 恢复是独立补偿分量，一项失败不能截断后续恢复。"""
    from codev_platform import mcp_systemd_install_transaction as module

    events: list[object] = []
    manifest = _清单(module, tmp_path)
    names = tuple(unit.unit_name for unit in manifest.units)
    shadow_states = {name: "active" for name in names}
    ports, _files, _states, _initial_files, _initial_states = _端口(
        module,
        manifest,
        events,
        fail_command="restart",
        shadow_states=shadow_states,
        shadow_restore_failure=names[1],
    )

    with pytest.raises(module.SystemdInstallTransactionError, match="安全状态未证明"):
        module.install_systemd_transaction(
            manifest,
            ports=ports,
            platform_name="linux",
            effective_user_id=lambda: 0,
        )

    restored = [
        item[1][0] for item in events if isinstance(item, tuple) and item[0] == "恢复shadow"
    ]
    assert restored == list(reversed(names))
    assert shadow_states[names[1]] == "retired"
    assert all(shadow_states[name] == "active" for name in (names[0], names[2]))


def test_manifest外shadow冻结失败时零修改退出(tmp_path: Path) -> None:
    """manifest 外残留属于准备期错误，不能先写任意主 unit 再发现。"""
    from codev_platform import mcp_systemd_install_transaction as module

    events: list[object] = []
    manifest = _清单(module, tmp_path)
    ports, _files, _states, _initial_files, _initial_states = _端口(
        module,
        manifest,
        events,
    )

    def 拒绝manifest外(_names: tuple[str, ...]):
        events.append("拒绝manifest外shadow")
        raise module.SystemdInstallTransactionError("发现 manifest 外 shadow")

    ports = replace(ports, snapshot_legacy_release_dropins=拒绝manifest外)
    with pytest.raises(module.SystemdInstallTransactionError, match="manifest 外"):
        module.install_systemd_transaction(
            manifest,
            ports=ports,
            platform_name="linux",
            effective_user_id=lambda: 0,
        )

    assert "拒绝manifest外shadow" in events
    assert not any(isinstance(item, tuple) and item[0] == "写入" for item in events)
    assert not any(isinstance(item, tuple) and item[0].startswith("恢复") for item in events)


def test_runtime_mask拒绝发生在shadow冻结和所有修改之前(tmp_path: Path) -> None:
    """安装锁内的 runtime mask 复核失败时，shadow 也不得被读取或移动。"""
    from codev_platform import mcp_systemd_install_transaction as module

    events: list[object] = []
    manifest = _清单(module, tmp_path)
    ports, _files, _states, _initial_files, _initial_states = _端口(
        module,
        manifest,
        events,
    )

    def 拒绝mask() -> None:
        events.append("拒绝runtime mask")
        raise module.SystemdInstallTransactionError("runtime mask 生效")

    ports = replace(ports, verify_install_boundary=拒绝mask)
    with pytest.raises(module.SystemdInstallTransactionError, match="runtime mask"):
        module.install_systemd_transaction(
            manifest,
            ports=ports,
            platform_name="linux",
            effective_user_id=lambda: 0,
        )

    assert "拒绝runtime mask" in events
    assert not any(
        isinstance(item, tuple) and item[0] in {"读取shadow原像", "写入", "退役shadow"}
        for item in events
    )


def test_已归档shadow的二次安装保持幂等(tmp_path: Path) -> None:
    """首次成功留下的固定归档可以再次冻结、退役和证明，不重新激活旧覆盖。"""
    from codev_platform import mcp_systemd_install_transaction as module

    manifest = _清单(module, tmp_path)
    shadow_states = {unit.unit_name: "retired" for unit in manifest.units}
    for _attempt in range(2):
        events: list[object] = []
        ports, _files, _states, _initial_files, _initial_states = _端口(
            module,
            manifest,
            events,
            shadow_states=shadow_states,
        )
        module.install_systemd_transaction(
            manifest,
            ports=ports,
            platform_name="linux",
            effective_user_id=lambda: 0,
        )

    assert set(shadow_states.values()) == {"retired"}


def test_普通安装事务拒绝包含两个写服务的清单(tmp_path: Path) -> None:
    """写服务只能由维护状态机延迟激活，禁止恢复排他锁内 bootstrap。"""
    from codev_platform import mcp_systemd_install_transaction as module

    events: list[object] = []
    manifest = _维护清单(module, tmp_path)
    ports, _files, _states, _initial_files, _initial_states = _端口(
        module,
        manifest,
        events,
    )

    with pytest.raises(module.SystemdInstallTransactionError, match="maintenance-stage"):
        module.install_systemd_transaction(
            manifest,
            ports=ports,
            platform_name="linux",
            effective_user_id=lambda: 0,
        )

    assert not any(isinstance(item, tuple) and item[0] == "写入" for item in events)


def test_事务二次拒绝被篡改的unit激活所有者(tmp_path: Path) -> None:
    """即使对象绕过构造期校验，事务入口也不能改变状态机所有权。"""
    from codev_platform import mcp_systemd_install_transaction as module

    events: list[object] = []
    unit = _规格(module, tmp_path, "codev-mcp-codegraph.service")
    manifest = module.SystemdInstallManifest(
        units=(unit,),
        runtime_revision="1" * 40,
    )
    object.__setattr__(
        unit,
        "activation_mode",
        module.SystemdUnitActivationMode.REINDEX_STATE_MACHINE,
    )
    ports, _files, _states, _initial_files, _initial_states = _端口(module, manifest, events)

    with pytest.raises(module.SystemdInstallTransactionError, match="激活所有者"):
        module.install_systemd_transaction(
            manifest,
            ports=ports,
            platform_name="linux",
            effective_user_id=lambda: 0,
        )

    assert events == []


def test_生产路径入口仅在独占安装锁内读取受信输入(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """manifest/source 绑定读取不能早于安装锁与 runtime mask 复核。"""
    from codev_platform import mcp_systemd_install_transaction as module

    events: list[object] = []
    manifest = _清单(module, tmp_path)
    ports, _files, _states, _initial_files, _initial_states = _端口(module, manifest, events)
    install_input = module.VerifiedInstallInput(
        manifest=manifest,
        payloads=tuple(module.SystemdUnitPayload(unit, _CONTENT) for unit in manifest.units),
    )
    manifest_path = (tmp_path / "install-manifest.json").resolve()
    monkeypatch.setattr(
        module,
        "load_verified_install_input",
        lambda path: events.append(("读取受信输入", path)) or install_input,
    )

    module.install_systemd_from_manifest_path(
        manifest_path,
        ports=ports,
        platform_name="linux",
        effective_user_id=lambda: 0,
    )

    assert events.index("进入安装锁") < events.index(("读取受信输入", manifest_path))
    assert events.index("复核mask") < events.index(("读取受信输入", manifest_path))


def test_事务拒绝非root调用且不取得安装锁(tmp_path: Path) -> None:
    """身份不满足时必须在任何门禁或文件副作用前失败关闭。"""
    from codev_platform import mcp_systemd_install_transaction as module

    events: list[object] = []
    manifest = _清单(module, tmp_path)
    ports, _files, _states, _initial_files, _initial_states = _端口(module, manifest, events)

    with pytest.raises(module.SystemdInstallTransactionError, match="root"):
        module.install_systemd_transaction(
            manifest,
            ports=ports,
            platform_name="linux",
            effective_user_id=lambda: 1000,
        )

    assert events == []


def test_身份读取MemoryError必须原样终止且不取得安装锁(tmp_path: Path) -> None:
    """不可恢复终止异常不能被身份检查包装为普通事务失败。"""
    from codev_platform import mcp_systemd_install_transaction as module

    events: list[object] = []
    manifest = _清单(module, tmp_path)
    ports, _files, _states, _initial_files, _initial_states = _端口(module, manifest, events)

    def 读取身份失败() -> int:
        raise MemoryError("内存不足")

    with pytest.raises(MemoryError, match="内存不足"):
        module.install_systemd_transaction(
            manifest,
            ports=ports,
            platform_name="linux",
            effective_user_id=读取身份失败,
        )

    assert events == []
