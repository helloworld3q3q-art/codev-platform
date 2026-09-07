"""在 user systemd scope 内接收 root fd 并启动受限 worker。"""

from __future__ import annotations

import argparse
import array
import os
import select
import socket
import stat
import subprocess
import sys
from pathlib import Path


_FAILURE_EXIT_CODE = 125
_READY_BYTE = b"1"
_NORMAL_COMPLETION = b"0"
_READY_TIMEOUT_SEC = 2.0
_BOOTSTRAP_PATH = Path(__file__).resolve(strict=True)
_SOURCE_ROOT = _BOOTSTRAP_PATH.parent.parent
_WATCHDOG_PATH = _BOOTSTRAP_PATH.with_name("runtime_bound_worker_watchdog.py")


def main(argv: list[str] | None = None) -> int:
    """先证明 cgroup 与控制面，再交接 fd、就绪 watchdog、运行固定 operation。"""
    parsed = _arguments(sys.argv[1:] if argv is None else argv)
    if parsed is None:
        return _FAILURE_EXIT_CODE
    handoff_socket, expected_unit, operation = parsed
    root_descriptor: int | None = None
    parent_pidfd: int | None = None
    bootstrap_pidfd: int | None = None
    completion_writer: int | None = None
    try:
        worker, scope, pidfd = _load_trusted_modules()
        scope.require_current_worker_scope(expected_unit)
        scope.verify_worker_scope_control(expected_unit)
        root_descriptor, parent_pidfd = _receive_descriptors(handoff_socket)
        pidfd.require_pidfd_descriptor(parent_pidfd)
        bootstrap_pidfd = pidfd.open_current_pidfd()
        completion_writer = _start_watchdog(parent_pidfd, bootstrap_pidfd, expected_unit)
        parent_pidfd = None
        bootstrap_pidfd = None
        result = worker._scope_child_main(root_descriptor, operation)
        root_descriptor = None
        if result != 0:
            return result
        _send_normal_completion(completion_writer)
        completion_writer = None
        return 0
    except (KeyboardInterrupt, SystemExit, MemoryError):
        raise
    except Exception:
        return _FAILURE_EXIT_CODE
    finally:
        _close_quietly(root_descriptor)
        _close_quietly(parent_pidfd)
        _close_quietly(bootstrap_pidfd)
        _close_quietly(completion_writer)


def _arguments(argv: list[str]) -> tuple[str, str, str] | None:
    parser = argparse.ArgumentParser(add_help=False)
    parser.add_argument("--scope", action="store_true")
    parser.add_argument("--handoff-socket", required=True)
    parser.add_argument("--expected-unit", required=True)
    parser.add_argument("--operation", required=True)
    try:
        parsed = parser.parse_args(argv)
    except SystemExit:
        return None
    if (
        not parsed.scope
        or type(parsed.handoff_socket) is not str
        or not parsed.handoff_socket.startswith("/")
        or type(parsed.expected_unit) is not str
        or type(parsed.operation) is not str
        or not parsed.operation
    ):
        return None
    if not _WATCHDOG_PATH.is_file():
        return None
    return parsed.handoff_socket, parsed.expected_unit, parsed.operation


def _load_trusted_modules() -> tuple[object, object, object]:
    worker_file = _SOURCE_ROOT / "codev_platform" / "runtime_bound_worker.py"
    scope_file = _SOURCE_ROOT / "codev_platform" / "runtime_worker_scope.py"
    pidfd_file = _SOURCE_ROOT / "codev_platform" / "runtime_worker_pidfd.py"
    if not worker_file.is_file() or not scope_file.is_file() or not pidfd_file.is_file():
        raise RuntimeError("root-fd worker 受信源码不完整")
    sys.path.insert(0, str(_SOURCE_ROOT))
    from codev_platform import runtime_bound_worker as worker
    from codev_platform import runtime_worker_scope as scope
    from codev_platform import runtime_worker_pidfd as pidfd

    return worker, scope, pidfd


def _receive_descriptors(handoff_socket: str) -> tuple[int, int]:
    connection = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    descriptors: list[int] = []
    try:
        connection.connect(handoff_socket)
        payload, ancillary, _flags, _address = connection.recvmsg(
            1,
            socket.CMSG_SPACE(2 * array.array("i").itemsize),
        )
        if payload != b"R" or len(ancillary) != 1:
            raise RuntimeError("root-fd worker descriptor 消息无效")
        level, kind, raw = ancillary[0]
        if level != socket.SOL_SOCKET or kind != socket.SCM_RIGHTS:
            raise RuntimeError("root-fd worker descriptor 消息无效")
        received = array.array("i")
        received.frombytes(raw[: len(raw) - (len(raw) % received.itemsize)])
        descriptors = list(received)
        if len(descriptors) != 2:
            raise RuntimeError("root-fd worker descriptor 数量无效")
        root_descriptor, parent_pidfd = descriptors
        _require_root_descriptor(root_descriptor)
        _require_descriptor_number(parent_pidfd, "父 pidfd")
        os.set_inheritable(root_descriptor, False)
        os.set_inheritable(parent_pidfd, False)
        return root_descriptor, parent_pidfd
    except Exception:
        for descriptor in descriptors:
            _close_quietly(descriptor)
        raise
    finally:
        connection.close()


def _require_root_descriptor(descriptor: int) -> None:
    if type(descriptor) is not int or descriptor < 3:
        raise RuntimeError("root-fd worker 根 descriptor 无效")
    metadata = os.fstat(descriptor)
    if not stat.S_ISDIR(metadata.st_mode):
        raise RuntimeError("root-fd worker 根 descriptor 不是目录")


def _require_descriptor_number(descriptor: int, label: str) -> None:
    if type(descriptor) is not int or descriptor < 3:
        raise RuntimeError(f"root-fd worker {label} descriptor 无效")


def _start_watchdog(parent_pidfd: int, bootstrap_pidfd: int, unit: str) -> int:
    """完成就绪握手后才执行 operation；返回成功 ACK 的唯一写端。"""
    ready_reader: int | None = None
    ready_writer: int | None = None
    completion_reader: int | None = None
    completion_writer: int | None = None
    try:
        ready_reader, ready_writer = os.pipe()
        completion_reader, completion_writer = os.pipe()
        for descriptor in (ready_reader, ready_writer, completion_reader, completion_writer):
            os.set_inheritable(descriptor, False)
        command = (
            sys.executable,
            "-B",
            "-I",
            "-S",
            str(_WATCHDOG_PATH),
            "--parent-pidfd",
            str(parent_pidfd),
            "--bootstrap-main-pidfd",
            str(bootstrap_pidfd),
            "--completion-fd",
            str(completion_reader),
            "--expected-unit",
            unit,
            "--ready-fd",
            str(ready_writer),
        )
        subprocess.Popen(
            command,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            close_fds=True,
            pass_fds=(parent_pidfd, bootstrap_pidfd, completion_reader, ready_writer),
            start_new_session=True,
        )
        _close_quietly(parent_pidfd)
        _close_quietly(bootstrap_pidfd)
        _close_quietly(completion_reader)
        _close_quietly(ready_writer)
        parent_pidfd = -1
        bootstrap_pidfd = -1
        completion_reader = -1
        ready_writer = -1
        if not _wait_for_watchdog_ready(ready_reader):
            raise RuntimeError("root-fd worker watchdog 未确认就绪")
        _close_quietly(ready_reader)
        ready_reader = -1
        return completion_writer
    except Exception:
        _close_quietly(completion_writer)
        raise
    finally:
        _close_quietly(parent_pidfd)
        _close_quietly(bootstrap_pidfd)
        _close_quietly(ready_reader)
        _close_quietly(ready_writer)
        _close_quietly(completion_reader)


def _wait_for_watchdog_ready(ready_reader: int) -> bool:
    try:
        readable, _writable, _exceptional = select.select(
            (ready_reader,),
            (),
            (),
            _READY_TIMEOUT_SEC,
        )
        return bool(readable) and os.read(ready_reader, 1) == _READY_BYTE
    except OSError:
        return False


def _send_normal_completion(descriptor: int) -> None:
    if os.write(descriptor, _NORMAL_COMPLETION) != len(_NORMAL_COMPLETION):
        raise RuntimeError("root-fd worker watchdog 正常完成确认失败")
    _close_quietly(descriptor)


def _close_quietly(descriptor: int | None) -> None:
    if descriptor is None or descriptor < 0:
        return
    try:
        os.close(descriptor)
    except OSError:
        pass


if __name__ == "__main__":  # pragma: no cover - 仅由固定 systemd service 启动。
    raise SystemExit(main())
