"""systemd 安装事务提交前的启用态、进程态与边界证明。"""

from __future__ import annotations

from collections.abc import Callable

from codev_platform.mcp_systemd_install_contract import (
    SystemdInstallTransactionError,
    SystemdUnitProcessState,
)
from codev_platform.mcp_systemd_install_signal import TERMINATION_EXCEPTIONS


UnitEnablementReader = Callable[[str], str]
ProcessStateReader = Callable[[str], SystemdUnitProcessState]


def verify_enabled_units(names: tuple[str, ...], reader: UnitEnablementReader) -> None:
    """逐项证明持久启用链接，避免把瞬时活动态混入启用态校验。"""
    if type(names) is not tuple or not callable(reader):
        raise SystemdInstallTransactionError("systemd unit 启用状态证明输入无效")
    for name in names:
        try:
            state = reader(name)
        except TERMINATION_EXCEPTIONS:
            raise
        except SystemdInstallTransactionError:
            raise
        except Exception as error:
            raise SystemdInstallTransactionError("systemd unit 启用状态无法复证") from error
        if type(state) is not str:
            raise SystemdInstallTransactionError("systemd unit 启用状态无法复证")
        if state != "enabled":
            raise SystemdInstallTransactionError("systemd unit 启用状态未生效")


def verify_running_units(
    names: tuple[str, ...],
    verifier: Callable[[tuple[str, ...]], None],
) -> None:
    if not names:
        return
    try:
        verifier(names)
    except TERMINATION_EXCEPTIONS:
        raise
    except SystemdInstallTransactionError:
        raise
    except Exception as error:
        raise SystemdInstallTransactionError("systemd unit 运行状态未证明") from error


def verify_process_states(
    states: tuple[tuple[str, SystemdUnitProcessState], ...],
    reader: ProcessStateReader,
) -> None:
    """install-only 类策略成功前复证活动态、PID 与 invocation 均未变化。"""
    for name, expected in states:
        try:
            if reader(name) != expected:
                raise SystemdInstallTransactionError("install-only 改变了 systemd unit 进程状态")
        except TERMINATION_EXCEPTIONS:
            raise
        except SystemdInstallTransactionError:
            raise
        except Exception as error:
            raise SystemdInstallTransactionError("systemd unit 进程状态无法复证") from error


def verify_install_boundary(proof: Callable[[], None]) -> None:
    try:
        proof()
    except TERMINATION_EXCEPTIONS:
        raise
    except SystemdInstallTransactionError:
        raise
    except Exception as error:
        raise SystemdInstallTransactionError("systemd 安装边界状态无法证明") from error


__all__ = [
    "verify_enabled_units",
    "verify_install_boundary",
    "verify_process_states",
    "verify_running_units",
]
