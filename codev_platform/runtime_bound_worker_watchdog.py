"""root-fd worker scope 的 pidfd 父死亡与完成 ACK watchdog。"""

from __future__ import annotations

import argparse
import os
import select
import signal
import stat
import sys
from pathlib import Path


_FAILURE_EXIT_CODE = 125
_READY_BYTE = b"1"
_NORMAL_COMPLETION = b"0"
_WATCHDOG_PATH = Path(__file__).resolve(strict=True)
_SOURCE_ROOT = _WATCHDOG_PATH.parent.parent


def main(argv: list[str] | None = None) -> int:
    """ACK 后撤防；父/MainPID 退出或任意异常均先中止 bootstrap 主进程。"""
    parsed = _arguments(sys.argv[1:] if argv is None else argv)
    if parsed is None:
        return _FAILURE_EXIT_CODE
    parent_pidfd, bootstrap_pidfd, completion_descriptor, unit, ready_descriptor = parsed
    scope: object | None = None
    pidfd: object | None = None
    try:
        pidfd = _load_pidfd_module()
        _require_pidfd_capability(pidfd, parent_pidfd, bootstrap_pidfd)
        scope = _load_scope_module()
        scope.require_current_worker_scope(unit)
        _detach_and_confirm_ready(
            ready_descriptor,
            parent_pidfd,
            bootstrap_pidfd,
            completion_descriptor,
            unit,
            scope,
            pidfd,
        )
        if _wait_for_normal_completion(parent_pidfd, bootstrap_pidfd, completion_descriptor):
            return 0
    except (KeyboardInterrupt, MemoryError):
        _terminate_scope(scope, unit, bootstrap_pidfd, pidfd)
        raise
    except Exception:
        _terminate_scope(scope, unit, bootstrap_pidfd, pidfd)
        return _FAILURE_EXIT_CODE
    _terminate_scope(scope, unit, bootstrap_pidfd, pidfd)
    return _FAILURE_EXIT_CODE


def _arguments(argv: list[str]) -> tuple[int, int, int, str, int] | None:
    parser = argparse.ArgumentParser(add_help=False)
    parser.add_argument("--parent-pidfd", type=int, required=True)
    parser.add_argument("--bootstrap-main-pidfd", type=int, required=True)
    parser.add_argument("--completion-fd", type=int, required=True)
    parser.add_argument("--expected-unit", required=True)
    parser.add_argument("--ready-fd", type=int, required=True)
    try:
        parsed = parser.parse_args(argv)
    except SystemExit:
        return None
    if (
        type(parsed.parent_pidfd) is not int
        or type(parsed.bootstrap_main_pidfd) is not int
        or type(parsed.completion_fd) is not int
        or type(parsed.expected_unit) is not str
        or type(parsed.ready_fd) is not int
    ):
        return None
    if not _is_pidfd_descriptor(parsed.parent_pidfd) or not _is_pidfd_descriptor(
        parsed.bootstrap_main_pidfd
    ):
        return None
    if not all(
        _is_pipe_descriptor(descriptor) for descriptor in (parsed.completion_fd, parsed.ready_fd)
    ):
        return None
    for descriptor in (
        parsed.parent_pidfd,
        parsed.bootstrap_main_pidfd,
        parsed.completion_fd,
        parsed.ready_fd,
    ):
        try:
            os.set_inheritable(descriptor, False)
        except OSError:
            return None
    return (
        parsed.parent_pidfd,
        parsed.bootstrap_main_pidfd,
        parsed.completion_fd,
        parsed.expected_unit,
        parsed.ready_fd,
    )


def _load_scope_module() -> object:
    scope_file = _SOURCE_ROOT / "codev_platform" / "runtime_worker_scope.py"
    if not scope_file.is_file():
        raise RuntimeError("root-fd worker scope 控制模块不可用")
    sys.path.insert(0, str(_SOURCE_ROOT))
    from codev_platform import runtime_worker_scope as scope

    return scope


def _load_pidfd_module() -> object:
    pidfd_file = _SOURCE_ROOT / "codev_platform" / "runtime_worker_pidfd.py"
    if not pidfd_file.is_file():
        raise RuntimeError("root-fd worker pidfd 模块不可用")
    sys.path.insert(0, str(_SOURCE_ROOT))
    from codev_platform import runtime_worker_pidfd as pidfd

    return pidfd


def _require_pidfd_capability(pidfd: object, parent_pidfd: int, bootstrap_pidfd: int) -> None:
    capability = getattr(pidfd, "require_pidfd_capability", None)
    require_descriptor = getattr(pidfd, "require_pidfd_descriptor", None)
    if not callable(capability) or not callable(require_descriptor):
        raise RuntimeError("root-fd worker pidfd 控制接口无效")
    capability()
    require_descriptor(parent_pidfd)
    require_descriptor(bootstrap_pidfd)


def _is_pidfd_descriptor(descriptor: int) -> bool:
    if descriptor < 3:
        return False
    try:
        return os.readlink(f"/proc/self/fd/{descriptor}") == "anon_inode:[pidfd]"
    except OSError:
        return False


def _is_pipe_descriptor(descriptor: int) -> bool:
    if descriptor < 3:
        return False
    try:
        return stat.S_ISFIFO(os.fstat(descriptor).st_mode)
    except OSError:
        return False


def _detach_and_confirm_ready(
    ready_descriptor: int,
    parent_pidfd: int,
    bootstrap_pidfd: int,
    completion_descriptor: int,
    unit: str,
    scope: object,
    pidfd: object,
) -> None:
    try:
        child_process = os.fork()
    except OSError:
        _close_quietly(ready_descriptor)
        raise
    if child_process > 0:
        os._exit(0)
    try:
        os.setsid()
        _redirect_standard_streams()
        _unblock_termination_signals()
        _install_termination_guard(unit, scope, bootstrap_pidfd, pidfd)
        if _pidfd_ready(pidfd, parent_pidfd):
            _terminate_scope(scope, unit, bootstrap_pidfd, pidfd)
            os._exit(_FAILURE_EXIT_CODE)
        os.write(ready_descriptor, _READY_BYTE)
    except OSError:
        os._exit(_FAILURE_EXIT_CODE)
    finally:
        _close_quietly(ready_descriptor)


def _redirect_standard_streams() -> None:
    null_descriptor = os.open(os.devnull, os.O_RDWR)
    try:
        for descriptor in (0, 1, 2):
            os.dup2(null_descriptor, descriptor)
    finally:
        if null_descriptor > 2:
            _close_quietly(null_descriptor)


def _unblock_termination_signals() -> None:
    if not hasattr(signal, "pthread_sigmask"):
        raise OSError("缺少 pthread_sigmask")
    signal.pthread_sigmask(signal.SIG_UNBLOCK, {signal.SIGHUP, signal.SIGTERM})


def _install_termination_guard(
    unit: str,
    scope: object,
    bootstrap_pidfd: int,
    pidfd: object,
) -> None:
    handler = _termination_handler(unit, scope, bootstrap_pidfd, pidfd)
    signal.signal(signal.SIGHUP, handler)
    signal.signal(signal.SIGTERM, handler)


def _termination_handler(unit: str, scope: object, bootstrap_pidfd: int, pidfd: object):
    def handler(_signum: int, _frame: object) -> None:
        _terminate_scope(scope, unit, bootstrap_pidfd, pidfd)
        os._exit(_FAILURE_EXIT_CODE)

    return handler


def _pidfd_ready(pidfd: object, descriptor: int) -> bool:
    readiness = getattr(pidfd, "pidfd_is_ready", None)
    if not callable(readiness):
        raise RuntimeError("root-fd worker pidfd 状态接口无效")
    return readiness(descriptor) is True


def _wait_for_normal_completion(
    parent_pidfd: int,
    bootstrap_pidfd: int,
    completion_descriptor: int,
) -> bool:
    """ACK 优先；否则父或 MainPID 退出、完成 pipe 异常都必须触发收口。"""
    while True:
        readable, _writable, _exceptional = select.select(
            (parent_pidfd, bootstrap_pidfd, completion_descriptor),
            (),
            (),
        )
        if completion_descriptor in readable:
            return os.read(completion_descriptor, 1) == _NORMAL_COMPLETION
        if parent_pidfd in readable or bootstrap_pidfd in readable:
            return False


def _terminate_scope(
    scope: object | None,
    unit: str,
    bootstrap_pidfd: int,
    pidfd: object | None = None,
) -> None:
    """先杀精确 MainPID，再尽力请求 manager 收口；两条路径互不依赖。"""
    terminated = _terminate_with_scope(scope, bootstrap_pidfd)
    if not terminated:
        _terminate_with_pidfd_module(pidfd, bootstrap_pidfd)
    _request_scope_kill(scope, unit)


def _terminate_with_scope(scope: object | None, bootstrap_pidfd: int) -> bool:
    if scope is None:
        return False
    terminate = getattr(scope, "terminate_worker_scope_main", None)
    if not callable(terminate):
        return False
    try:
        return terminate(bootstrap_pidfd) is True
    except Exception:
        return False


def _terminate_with_pidfd_module(pidfd: object | None, bootstrap_pidfd: int) -> bool:
    if pidfd is None:
        return False
    terminate = getattr(pidfd, "terminate_pidfd", None)
    if not callable(terminate):
        return False
    try:
        return terminate(bootstrap_pidfd) is True
    except Exception:
        return False


def _request_scope_kill(scope: object | None, unit: str) -> None:
    if scope is None:
        return
    kill = getattr(scope, "kill_worker_scope", None)
    if not callable(kill):
        return
    try:
        kill(unit)
    except Exception:
        return


def _close_quietly(descriptor: int) -> None:
    try:
        os.close(descriptor)
    except OSError:
        pass


if __name__ == "__main__":  # pragma: no cover - 仅由 scope bootstrap 启动。
    raise SystemExit(main())
