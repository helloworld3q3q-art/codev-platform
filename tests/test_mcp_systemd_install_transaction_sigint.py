"""systemd 安装事务的 SIGINT 延后与补偿边界测试。"""

from __future__ import annotations

import signal
from dataclasses import replace
from pathlib import Path

import pytest

from tests.mcp_systemd_install_transaction_support import _清单, _端口


def test_补偿中再次中断仍耗尽恢复并报告安全状态未证明(tmp_path: Path) -> None:
    """P1：第二次 Ctrl+C 不得截断剩余 unit、状态恢复与复证。"""
    from codev_platform import mcp_systemd_install_transaction as module

    events: list[object] = []
    manifest = _清单(module, tmp_path)
    ports, _files, _states, _initial_files, _initial_states = _端口(
        module,
        manifest,
        events,
        fail_command="restart",
    )
    interrupted = False

    def 首次恢复文件时中断(name: str, snapshot) -> None:
        nonlocal interrupted
        events.append(("恢复文件", name, snapshot))
        if not interrupted:
            interrupted = True
            raise KeyboardInterrupt()

    ports = replace(ports, restore_installed_unit=首次恢复文件时中断)
    with pytest.raises(module.SystemdInstallTransactionError, match="安全状态未证明"):
        module.install_systemd_transaction(
            manifest,
            ports=ports,
            platform_name="linux",
            effective_user_id=lambda: 0,
        )

    assert [item[1] for item in events if isinstance(item, tuple) and item[0] == "恢复文件"] == [
        unit.unit_name for unit in reversed(manifest.units)
    ]
    assert events.count(("systemctl", ("systemctl", "daemon-reload"))) == 2
    assert [
        item[1] for item in events if isinstance(item, tuple) and item[0] == "恢复启用态"
    ] == list(manifest.stateful_units)
    assert [
        item[1] for item in events if isinstance(item, tuple) and item[0] == "恢复活动态"
    ] == list(manifest.stateful_units)
    assert [
        item[1] for item in events if isinstance(item, tuple) and item[0] == "读取状态"
    ] == list(manifest.stateful_units) * 2


def test_补偿中连续SIGINT仍耗尽恢复并在证明后延后中断(tmp_path: Path) -> None:
    """P1：真实连续 Ctrl+C 只能被记录，不能截断 unit、状态恢复和复证。"""
    from codev_platform import mcp_systemd_install_transaction as module

    events: list[object] = []
    manifest = _清单(module, tmp_path)
    ports, files, states, initial_files, initial_states = _端口(
        module,
        manifest,
        events,
        fail_command="restart",
    )
    previous_sigint_handler = signal.getsignal(signal.SIGINT)
    sent = False

    def 首次恢复文件发送连续中断(name: str, snapshot) -> None:
        nonlocal sent
        events.append(("恢复文件", name, snapshot))
        if not sent:
            sent = True
            signal.raise_signal(signal.SIGINT)
            signal.raise_signal(signal.SIGINT)
            signal.raise_signal(signal.SIGINT)
        files[name] = snapshot

    ports = replace(ports, restore_installed_unit=首次恢复文件发送连续中断)
    with pytest.raises(KeyboardInterrupt):
        module.install_systemd_transaction(
            manifest,
            ports=ports,
            platform_name="linux",
            effective_user_id=lambda: 0,
        )

    assert files == initial_files
    assert states == initial_states
    assert [item[1] for item in events if isinstance(item, tuple) and item[0] == "恢复文件"] == [
        unit.unit_name for unit in reversed(manifest.units)
    ]
    assert events.count(("systemctl", ("systemctl", "daemon-reload"))) == 2
    assert [
        item[1] for item in events if isinstance(item, tuple) and item[0] == "恢复启用态"
    ] == list(manifest.stateful_units)
    assert [
        item[1] for item in events if isinstance(item, tuple) and item[0] == "恢复活动态"
    ] == list(manifest.stateful_units)
    assert [
        item[1] for item in events if isinstance(item, tuple) and item[0] == "读取状态"
    ] == list(manifest.stateful_units) * 2
    assert signal.getsignal(signal.SIGINT) == previous_sigint_handler


def test_连续SIGINT且补偿未证明时仍以安全错误结束(tmp_path: Path) -> None:
    """安全状态未证明必须压过延后的 Ctrl+C，并保留其为异常原因。"""
    from codev_platform import mcp_systemd_install_transaction as module

    events: list[object] = []
    manifest = _清单(module, tmp_path)
    ports, _files, _states, _initial_files, _initial_states = _端口(
        module,
        manifest,
        events,
        fail_command="restart",
    )
    sent = False

    def 恢复文件时发送中断并失败(name: str, snapshot) -> None:
        nonlocal sent
        events.append(("恢复文件", name, snapshot))
        if not sent:
            sent = True
            signal.raise_signal(signal.SIGINT)
            signal.raise_signal(signal.SIGINT)
        raise RuntimeError("恢复文件失败")

    ports = replace(ports, restore_installed_unit=恢复文件时发送中断并失败)
    with pytest.raises(module.SystemdInstallTransactionError, match="安全状态未证明") as captured:
        module.install_systemd_transaction(
            manifest,
            ports=ports,
            platform_name="linux",
            effective_user_id=lambda: 0,
        )

    assert isinstance(captured.value.__cause__, KeyboardInterrupt)
    assert [item[1] for item in events if isinstance(item, tuple) and item[0] == "恢复文件"] == [
        unit.unit_name for unit in reversed(manifest.units)
    ]
    assert [
        item[1] for item in events if isinstance(item, tuple) and item[0] == "恢复活动态"
    ] == list(manifest.stateful_units)


def test_恢复旧SIGINT处理器时的中断不得掩盖安全状态(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """旧处理器恢复后的 Ctrl+C 仍须让安全状态错误保持在最外层。"""
    from codev_platform import mcp_systemd_install_signal as signal_module
    from codev_platform import mcp_systemd_install_transaction as module

    events: list[object] = []
    manifest = _清单(module, tmp_path)
    ports, _files, _states, _initial_files, _initial_states = _端口(
        module,
        manifest,
        events,
        fail_command="restart",
    )
    previous_sigint_handler = signal.getsignal(signal.SIGINT)

    def 恢复文件失败(name: str, snapshot) -> None:
        events.append(("恢复文件", name, snapshot))
        raise RuntimeError("恢复文件失败")

    原始注册_SIGINT = signal_module.signal.signal
    注册次数 = 0

    def 恢复旧处理器后发送中断(signum: int, handler: object):
        nonlocal 注册次数
        result = 原始注册_SIGINT(signum, handler)
        注册次数 += 1
        if 注册次数 == 2:
            signal.raise_signal(signal.SIGINT)
        return result

    monkeypatch.setattr(signal_module.signal, "signal", 恢复旧处理器后发送中断)
    ports = replace(ports, restore_installed_unit=恢复文件失败)

    with pytest.raises(module.SystemdInstallTransactionError, match="安全状态未证明") as captured:
        module.install_systemd_transaction(
            manifest,
            ports=ports,
            platform_name="linux",
            effective_user_id=lambda: 0,
        )

    assert 注册次数 == 2
    assert isinstance(captured.value.__cause__, KeyboardInterrupt)
    assert signal.getsignal(signal.SIGINT) == previous_sigint_handler
    assert [item[1] for item in events if isinstance(item, tuple) and item[0] == "恢复文件"] == [
        unit.unit_name for unit in reversed(manifest.units)
    ]
    assert [item[1] for item in events if isinstance(item, tuple) and item[0] == "读取状态"] == (
        list(manifest.stateful_units) * 2
    )


def test_恢复旧SIGINT处理器时的SystemExit不得掩盖安全状态(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """调用方自定义 Ctrl+C 为 SystemExit 时，安全结论仍必须优先。"""
    from codev_platform import mcp_systemd_install_signal as signal_module
    from codev_platform import mcp_systemd_install_transaction as module

    events: list[object] = []
    manifest = _清单(module, tmp_path)
    ports, _files, _states, _initial_files, _initial_states = _端口(
        module,
        manifest,
        events,
        fail_command="restart",
    )
    原始注册_SIGINT = signal_module.signal.signal
    previous_sigint_handler = signal.getsignal(signal.SIGINT)

    def 自定义终止处理器(_signum: int, _frame: object) -> None:
        raise SystemExit(130)

    def 恢复文件失败(name: str, snapshot) -> None:
        events.append(("恢复文件", name, snapshot))
        raise RuntimeError("恢复文件失败")

    原始注册_SIGINT(signal.SIGINT, 自定义终止处理器)
    注册次数 = 0

    def 恢复旧处理器后发送中断(signum: int, handler: object):
        nonlocal 注册次数
        result = 原始注册_SIGINT(signum, handler)
        注册次数 += 1
        if 注册次数 == 2:
            signal.raise_signal(signal.SIGINT)
        return result

    monkeypatch.setattr(signal_module.signal, "signal", 恢复旧处理器后发送中断)
    ports = replace(ports, restore_installed_unit=恢复文件失败)
    try:
        with pytest.raises(
            module.SystemdInstallTransactionError, match="安全状态未证明"
        ) as captured:
            module.install_systemd_transaction(
                manifest,
                ports=ports,
                platform_name="linux",
                effective_user_id=lambda: 0,
            )
    finally:
        原始注册_SIGINT(signal.SIGINT, previous_sigint_handler)

    assert 注册次数 == 2
    assert isinstance(captured.value.__cause__, SystemExit)
    assert signal.getsignal(signal.SIGINT) == previous_sigint_handler
    assert [item[1] for item in events if isinstance(item, tuple) and item[0] == "恢复文件"] == [
        unit.unit_name for unit in reversed(manifest.units)
    ]
    assert [item[1] for item in events if isinstance(item, tuple) and item[0] == "读取状态"] == (
        list(manifest.stateful_units) * 2
    )


def test_安装锁退出连续SIGINT时安全状态结论优先(tmp_path: Path) -> None:
    """锁退出异常时不得重取锁补偿，延后 Ctrl+C 也不能掩盖安全错误。"""
    from codev_platform import mcp_systemd_install_transaction as module

    events: list[object] = []
    manifest = _清单(module, tmp_path)

    class 退出异常安装锁:
        def __enter__(self) -> None:
            events.append("进入安装锁")
            return None

        def __exit__(self, *_args: object) -> bool:
            events.append("退出安装锁")
            signal.raise_signal(signal.SIGINT)
            signal.raise_signal(signal.SIGINT)
            raise RuntimeError("安装锁退出失败")

    ports, _files, _states, _initial_files, _initial_states = _端口(
        module,
        manifest,
        events,
        installer_lock_factory=lambda: 退出异常安装锁(),
    )

    with pytest.raises(
        module.SystemdInstallTransactionError, match="安全状态未证明"
    ) as captured:
        module.install_systemd_transaction(
            manifest,
            ports=ports,
            platform_name="linux",
            effective_user_id=lambda: 0,
        )

    assert isinstance(captured.value.__cause__, KeyboardInterrupt)
    assert not any(
        isinstance(item, tuple) and item[0].startswith("恢复") for item in events
    )


def test_SIGINT守卫建立失败时首次写入前失败关闭(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """守卫无法建立时不得继续快照、写入或碰触 unit 状态。"""
    from codev_platform import mcp_systemd_install_signal as signal_module
    from codev_platform import mcp_systemd_install_transaction as module

    events: list[object] = []
    manifest = _清单(module, tmp_path)
    ports, _files, _states, _initial_files, _initial_states = _端口(module, manifest, events)

    def 拒绝注册_SIGINT(_signum, _handler) -> None:
        raise ValueError("当前线程不能注册信号")

    monkeypatch.setattr(signal_module.signal, "signal", 拒绝注册_SIGINT)
    with pytest.raises(module.SystemdInstallTransactionError, match="无法建立 SIGINT 延后守卫"):
        module.install_systemd_transaction(
            manifest,
            ports=ports,
            platform_name="linux",
            effective_user_id=lambda: 0,
        )

    assert not any(
        isinstance(item, tuple) and item[0] in {"读取原像", "写入", "读取状态"} for item in events
    )


def test_准备快照期间SIGINT不写入也不执行补偿(tmp_path: Path) -> None:
    """守卫建立后但首次写入前收到 Ctrl+C，只能取消，不能反向改动 unit。"""
    from codev_platform import mcp_systemd_install_transaction as module

    events: list[object] = []
    manifest = _清单(module, tmp_path)
    ports, files, states, initial_files, initial_states = _端口(module, manifest, events)
    sent = False

    def 原像读取时发送中断(name: str):
        nonlocal sent
        events.append(("读取原像", name))
        if not sent:
            sent = True
            signal.raise_signal(signal.SIGINT)
        return files[name]

    ports = replace(ports, snapshot_installed_unit=原像读取时发送中断)
    with pytest.raises(KeyboardInterrupt):
        module.install_systemd_transaction(
            manifest,
            ports=ports,
            platform_name="linux",
            effective_user_id=lambda: 0,
        )

    assert files == initial_files
    assert states == initial_states
    assert not any(
        isinstance(item, tuple)
        and item[0] in {"写入", "恢复文件", "恢复启用态", "恢复活动态", "systemctl"}
        for item in events
    )
