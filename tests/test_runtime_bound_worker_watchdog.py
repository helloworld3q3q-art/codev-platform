"""root-fd worker cgroup watchdog 的窄契约回归。"""

from __future__ import annotations

import os
import signal
import sys

import pytest

import codev_platform.runtime_bound_worker_watchdog as watchdog


_LINUX = sys.platform.startswith("linux")


@pytest.mark.skipif(not _LINUX, reason="需要 Linux pipe/select 语义")
def testwatchdog正常完成确认优先于同时到达的父端EOF() -> None:
    """成功 ACK 与父 pidfd 同时就绪时，watchdog 必须撤防而非回收已成功 unit。"""
    parent_pid = os.fork()
    if parent_pid == 0:  # pragma: no cover - 子进程只制造已退出的父 pidfd。
        os._exit(0)
    parent_pidfd = os.pidfd_open(parent_pid)
    _wait_for_exit(parent_pid)
    bootstrap_pidfd = os.pidfd_open(os.getpid())
    completion_reader, completion_writer = os.pipe()
    try:
        os.write(completion_writer, b"0")

        assert watchdog._wait_for_normal_completion(
            parent_pidfd,
            bootstrap_pidfd,
            completion_reader,
        )
    finally:
        _close_quietly(parent_pidfd)
        _close_quietly(bootstrap_pidfd)
        _close_quietly(completion_reader)
        _close_quietly(completion_writer)


@pytest.mark.skipif(not _LINUX, reason="需要 Linux pipe/select 语义")
def testwatchdog完成通道异常关闭会要求回收() -> None:
    """bootstrap 非正常退出只会关闭 completion 写端，不能被误判为成功 ACK。"""
    parent_pidfd = os.pidfd_open(os.getpid())
    bootstrap_pidfd = os.pidfd_open(os.getpid())
    completion_reader, completion_writer = os.pipe()
    try:
        os.close(completion_writer)
        completion_writer = -1

        assert not watchdog._wait_for_normal_completion(
            parent_pidfd,
            bootstrap_pidfd,
            completion_reader,
        )
    finally:
        _close_quietly(parent_pidfd)
        _close_quietly(bootstrap_pidfd)
        _close_quietly(completion_reader)
        _close_quietly(completion_writer)


@pytest.mark.skipif(
    not _LINUX or not hasattr(os, "pidfd_open"),
    reason="需要 Linux pidfd/select 语义",
)
def testwatchdogbootstrap退出优先于fork保留的完成描述符() -> None:
    """completion 写端被 operation 的 fork 子进程保留时，主进程退出仍须立即收口。"""
    parent_pidfd = os.pidfd_open(os.getpid())
    completion_reader, completion_writer = os.pipe()
    holder_pid = os.fork()
    if holder_pid == 0:  # pragma: no cover - 子进程只制造真实 fd 泄漏窗口。
        _close_quietly(completion_reader)
        __import__("time").sleep(2)
        os._exit(0)
    bootstrap_pid = os.fork()
    if bootstrap_pid == 0:  # pragma: no cover - 子进程只制造已退出的 MainPID。
        os._exit(0)
    bootstrap_pidfd = os.pidfd_open(bootstrap_pid)
    _wait_for_exit(bootstrap_pid)
    _close_quietly(completion_writer)
    completion_writer = -1
    try:
        started = __import__("time").monotonic()
        assert not watchdog._wait_for_normal_completion(
            parent_pidfd,
            bootstrap_pidfd,
            completion_reader,
        )
        assert __import__("time").monotonic() - started < 0.5
    finally:
        _stop_child(holder_pid)
        _close_quietly(parent_pidfd)
        _close_quietly(bootstrap_pidfd)
        _close_quietly(completion_reader)
        _close_quietly(completion_writer)


@pytest.mark.skipif(
    not _LINUX or not hasattr(signal, "pthread_sigmask"),
    reason="需要 Linux pthread_sigmask",
)
def testwatchdog显式解除继承终止信号屏蔽(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """常规 SIGTERM/SIGHUP 回收 handler 不得受上游信号掩码失效。"""
    observed: list[tuple[object, set[int]]] = []

    def unblock(how: object, signals: set[int]) -> None:
        observed.append((how, signals))

    monkeypatch.setattr(watchdog.signal, "pthread_sigmask", unblock)
    watchdog._unblock_termination_signals()

    assert observed == [
        (signal.SIG_UNBLOCK, {signal.SIGHUP, signal.SIGTERM}),
    ]


def testwatchdog回收先中止bootstrap再请求精确scope控制器() -> None:
    """父死收口不能等待 D-Bus 返回，也不能回退到数字 PGID。"""
    observed: list[tuple[str, object]] = []

    class Scope:
        @staticmethod
        def terminate_worker_scope_main(descriptor: int) -> bool:
            observed.append(("bootstrap", descriptor))
            return True

        @staticmethod
        def kill_worker_scope(unit: str) -> bool:
            observed.append(("scope", unit))
            return True

    watchdog._terminate_scope(Scope(), "codev-rootfd-0123456789ab.service", 91)

    assert observed == [
        ("bootstrap", 91),
        ("scope", "codev-rootfd-0123456789ab.service"),
    ]


def testwatchdog控制面失败仍中止bootstrap主进程() -> None:
    """manager 瞬时不可达时，pidfd 主进程终止仍触发 ExitType=main 收口。"""
    observed: list[int] = []

    class Scope:
        @staticmethod
        def terminate_worker_scope_main(descriptor: int) -> bool:
            observed.append(descriptor)
            return True

        @staticmethod
        def kill_worker_scope(_unit: str) -> bool:
            return False

    watchdog._terminate_scope(Scope(), "codev-rootfd-0123456789ab.service", 92)

    assert observed == [92]


def _close_quietly(descriptor: int) -> None:
    if descriptor < 0:
        return
    try:
        os.close(descriptor)
    except OSError:
        pass


def _wait_for_exit(process_id: int) -> None:
    while True:
        try:
            observed, _status = os.waitpid(process_id, 0)
        except InterruptedError:
            continue
        if observed == process_id:
            return


def _stop_child(process_id: int) -> None:
    try:
        os.kill(process_id, signal.SIGKILL)
    except ProcessLookupError:
        pass
    _wait_for_exit(process_id)
