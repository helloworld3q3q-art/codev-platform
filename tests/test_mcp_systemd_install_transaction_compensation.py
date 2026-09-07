"""systemd 安装补偿、锁退出与最终证明顺序测试。"""

from __future__ import annotations

from dataclasses import replace
from pathlib import Path

import pytest

from tests.mcp_systemd_install_transaction_support import _清单, _端口


def test_安装命令失败时恢复文件权限属主与精确状态(tmp_path: Path) -> None:
    from codev_platform import mcp_systemd_install_transaction as module

    events: list[object] = []
    manifest = _清单(module, tmp_path)
    ports, files, states, initial_files, initial_states = _端口(
        module,
        manifest,
        events,
        fail_command="restart",
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
    restored = [
        item[1]
        for item in events
        if isinstance(item, tuple) and item[0] == "恢复文件"
    ]
    assert restored == [unit.unit_name for unit in reversed(manifest.units)]


def test_启用态恢复失败仍尝试恢复每个unit的活动态(tmp_path: Path) -> None:
    from codev_platform import mcp_systemd_install_transaction as module

    events: list[object] = []
    manifest = _清单(module, tmp_path)
    ports, _files, _states, _initial_files, _initial_states = _端口(
        module,
        manifest,
        events,
        fail_command="restart",
    )

    def 启用态恢复失败(name: str, _state) -> None:
        events.append(("恢复启用态", name))
        raise RuntimeError("disable 失败")

    ports = replace(ports, restore_unit_file_state=启用态恢复失败)
    with pytest.raises(module.SystemdInstallTransactionError, match="安全状态未证明"):
        module.install_systemd_transaction(
            manifest,
            ports=ports,
            platform_name="linux",
            effective_user_id=lambda: 0,
        )

    restored = [
        item[1]
        for item in events
        if isinstance(item, tuple) and item[0] == "恢复活动态"
    ]
    assert restored == list(manifest.stateful_units)


def test_安装锁抑制体内异常时仍以补偿后的失败结束(tmp_path: Path) -> None:
    from codev_platform import mcp_systemd_install_transaction as module

    events: list[object] = []
    manifest = _清单(module, tmp_path)

    class 抑制异常安装锁:
        def __enter__(self) -> None:
            events.append("进入安装锁")
            return None

        def __exit__(self, *_args: object) -> bool:
            events.append("退出安装锁")
            return True

    ports, files, states, initial_files, initial_states = _端口(
        module,
        manifest,
        events,
        fail_command="restart",
        installer_lock_factory=lambda: 抑制异常安装锁(),
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


class _退出失败绑定锁:
    def __init__(self, events: list[object]) -> None:
        self._events = events

    def __enter__(self) -> object:
        self._events.append("进入运行时绑定锁")
        return object()

    def __exit__(self, *_args: object) -> None:
        self._events.append("运行时绑定锁退出失败")
        raise RuntimeError("注入绑定锁退出失败")


def test_运行时绑定锁退出失败在安装锁内补偿且最终失败关闭(tmp_path: Path) -> None:
    from codev_platform import mcp_systemd_install_transaction as module

    events: list[object] = []
    manifest = _清单(module, tmp_path)
    ports, files, states, initial_files, initial_states = _端口(module, manifest, events)
    ports = replace(
        ports,
        runtime_binding_lock=lambda _manifest: _退出失败绑定锁(events),
    )

    with pytest.raises(module.SystemdInstallTransactionError, match="绑定锁状态未证明"):
        module.install_systemd_transaction(
            manifest,
            ports=ports,
            platform_name="linux",
            effective_user_id=lambda: 0,
        )

    assert files == initial_files
    assert states == initial_states
    exit_index = events.index("运行时绑定锁退出失败")
    restore_indices = [
        index
        for index, event in enumerate(events)
        if isinstance(event, tuple) and event[0].startswith("恢复")
    ]
    assert exit_index < min(restore_indices)
    assert max(restore_indices) < events.index("退出安装锁")


def test_体内已补偿且绑定退出又失败时补偿最多一次(tmp_path: Path) -> None:
    from codev_platform import mcp_systemd_install_transaction as module

    events: list[object] = []
    manifest = _清单(module, tmp_path)
    ports, files, _states, initial_files, _initial_states = _端口(
        module,
        manifest,
        events,
        effective_payload_failure=True,
    )
    ports = replace(
        ports,
        runtime_binding_lock=lambda _manifest: _退出失败绑定锁(events),
    )

    with pytest.raises(module.SystemdInstallTransactionError, match="绑定锁状态未证明"):
        module.install_systemd_transaction(
            manifest,
            ports=ports,
            platform_name="linux",
            effective_user_id=lambda: 0,
        )

    assert files == initial_files
    restored_files = [
        event for event in events if isinstance(event, tuple) and event[0] == "恢复文件"
    ]
    assert len(restored_files) == len(manifest.units)


def test_补偿进程身份证明在所有恢复和其他证明之后执行(tmp_path: Path) -> None:
    from codev_platform import mcp_systemd_install_transaction as module

    events: list[object] = []
    manifest = _清单(module, tmp_path)
    ports, _files, _states, _initial_files, _initial_states = _端口(
        module,
        manifest,
        events,
        fail_command="enable",
    )
    process_state = module.SystemdUnitProcessState("inactive", 0, "")

    def read_process(name: str):
        events.append(("读取进程", name))
        return process_state

    ports = replace(ports, read_unit_process_state=read_process)

    with pytest.raises(module.SystemdInstallTransactionError, match="已回滚"):
        module.install_systemd_install_only_transaction(
            manifest,
            ports=ports,
            platform_name="linux",
            effective_user_id=lambda: 0,
        )

    process_reads = [
        index
        for index, event in enumerate(events)
        if isinstance(event, tuple) and event[0] == "读取进程"
    ]
    assert len(process_reads) == len(manifest.units) * 2
    final_process_reads = process_reads[len(manifest.units) :]
    last_state_proof = max(
        index
        for index, event in enumerate(events)
        if isinstance(event, tuple) and event[0] == "读取状态"
    )
    assert last_state_proof < min(final_process_reads)
