"""root-fd worker 的 user systemd cgroup 启动与 descriptor 交接。"""

from __future__ import annotations

import array
import math
import os
import posixpath
import re
import select
import socket
import stat
import struct
import subprocess
import sys
import tempfile
import time
import uuid
from pathlib import Path
from collections.abc import Mapping

from codev_platform.core.process_tree import kill_process_tree, popen_tree
from codev_platform.core.systemd_process_identity import (
    SystemdProcessIdentityError,
    cgroup_belongs_to_unit,
)
from codev_platform.runtime_worker_pidfd import (
    RuntimeWorkerPidfdError,
    open_process_pidfd,
    require_pidfd_descriptor,
    require_pidfd_not_ready,
)
from codev_platform.runtime_worker_scope_control import (
    ENV as _ENV,
    SYSTEMCTL as _SYSTEMCTL,
    SYSTEMD_RUN as _SYSTEMD_RUN,
    RuntimeWorkerScopeControlError,
    kill_scope as _kill_scope,
    require_systemd_client_binaries as _require_systemd_client_binaries,
    terminate_scope_main as _terminate_scope_main,
    user_systemd_client_environment as _systemd_user_client_environment,
    verify_scope_control as _verify_scope_control,
)
from codev_platform.runtime_process import isolated_process_environment


_WORKER_UNIT = re.compile(r"codev-rootfd-[a-z0-9-]{8,48}\.service\Z")
_WORKER_OPERATION = re.compile(r"[a-z][a-z0-9-]{0,63}\Z")
_MAX_SOCKET_PATH_BYTES = 100
_MAX_CGROUP_BYTES = 16 * 1024
_HANDOFF_MAX_SEC = 5.0
_CLEANUP_TIMEOUT_SEC = 2.0


class RuntimeWorkerScopeError(RuntimeError):
    """user systemd scope 或 root descriptor 交接无法安全证明。"""


def new_worker_scope_unit() -> str:
    """生成一次性 transient service 名称，避免历史 unit 身份复用。"""
    return f"codev-rootfd-{uuid.uuid4().hex}.service"


def build_worker_scope_command(
    *,
    unit: str,
    handoff_socket: str,
    operation: str,
    timeout_sec: float,
    bootstrap_path: Path,
    interpreter: str,
    environment: Mapping[str, str],
) -> tuple[str, ...]:
    """构造不可由业务 payload 覆盖的 user transient service 命令。"""
    _require_worker_unit(unit)
    socket_path = _require_handoff_socket_path(handoff_socket)
    _require_worker_operation(operation)
    runtime_seconds = _runtime_seconds(timeout_sec)
    bootstrap = _require_bootstrap_path(bootstrap_path)
    executable = _require_interpreter(interpreter)
    assignments = _environment_assignments(environment)
    return (
        _SYSTEMD_RUN,
        "--user",
        f"--unit={unit}",
        "--quiet",
        "--wait",
        "--pipe",
        "--collect",
        "--service-type=exec",
        "--property=KillMode=control-group",
        "--property=ExitType=main",
        "--property=KillSignal=SIGKILL",
        f"--property=RuntimeMaxSec={runtime_seconds}s",
        "--property=TimeoutStopSec=1s",
        "--property=SendSIGKILL=yes",
        "--property=Restart=no",
        "--property=RemainAfterExit=no",
        "--property=NoNewPrivileges=yes",
        "--property=ProtectControlGroups=yes",
        "--property=UMask=0077",
        "--property=WorkingDirectory=/",
        "--",
        _ENV,
        "-i",
        *assignments,
        executable,
        "-B",
        "-I",
        str(bootstrap),
        "--scope",
        "--handoff-socket",
        str(socket_path),
        "--expected-unit",
        unit,
        "--operation",
        operation,
    )


def run_in_worker_scope(
    *,
    root_descriptor: int,
    parent_pidfd: int,
    operation: str,
    payload: bytes,
    timeout: float,
    bootstrap_path: Path,
    source_root: Path,
) -> subprocess.CompletedProcess[bytes]:
    """启动 user service、完成一次 descriptor 交接并等待整个 cgroup 收口。"""
    _require_descriptor(root_descriptor, "根")
    _require_pidfd(parent_pidfd, "父进程")
    _require_worker_operation(operation)
    if type(payload) is not bytes:
        raise RuntimeWorkerScopeError("root-fd worker 请求载荷无效")
    _require_source_root(source_root)
    environment = isolated_process_environment()
    unit = new_worker_scope_unit()
    deadline = time.monotonic() + _require_positive_timeout(timeout)
    process: subprocess.Popen[bytes] | None = None
    scope_main_pidfd: int | None = None
    try:
        with _DescriptorHandoffServer(
            unit=unit,
            root_descriptor=root_descriptor,
            parent_pidfd=parent_pidfd,
        ) as handoff:
            command = build_worker_scope_command(
                unit=unit,
                handoff_socket=handoff.path,
                operation=operation,
                timeout_sec=_remaining(deadline),
                bootstrap_path=bootstrap_path,
                interpreter=sys.executable,
                environment=environment,
            )
            process = _start_scope_client(command, source_root)
            scope_main_pidfd = handoff.send_to_scope(process, deadline)
            completed = _communicate_scope(process, command, payload, deadline)
            process = None
            return completed
    except (KeyboardInterrupt, SystemExit, MemoryError):
        _abort_worker_scope(unit, process, scope_main_pidfd)
        raise
    except Exception:
        _abort_worker_scope(unit, process, scope_main_pidfd)
        raise
    finally:
        if scope_main_pidfd is not None:
            _close_quietly(scope_main_pidfd)


def require_current_worker_scope(unit: str) -> None:
    """scope bootstrap 只接受自己确实位于预期 transient unit 内。"""
    _require_worker_unit(unit)
    raw = _read_cgroup(os.getpid())
    try:
        belongs = cgroup_belongs_to_unit(raw, unit)
    except SystemdProcessIdentityError as error:
        raise RuntimeWorkerScopeError("root-fd worker cgroup 身份无效") from error
    if not belongs:
        raise RuntimeWorkerScopeError("root-fd worker 不属于预期 systemd cgroup")


def verify_worker_scope_control(unit: str) -> None:
    """在启动业务操作前证明 MainPID 与关键 cgroup 收口属性完全匹配。"""
    _require_worker_unit(unit)
    try:
        _verify_scope_control(unit, os.getpid())
    except RuntimeWorkerScopeControlError as error:
        raise RuntimeWorkerScopeError("root-fd worker systemd 控制面不可用") from error


def kill_worker_scope(unit: str) -> bool:
    """请求 manager 强杀整个 scope cgroup；调用方本身也会随 unit 回收。"""
    _require_worker_unit(unit)
    return _kill_scope(unit)


def terminate_worker_scope_main(pidfd: int) -> bool:
    """用不可复用 pidfd 中止 bootstrap 主进程，触发 ExitType=main 收口。"""
    return _terminate_scope_main(pidfd)


def _start_scope_client(
    command: tuple[str, ...],
    source_root: Path,
) -> subprocess.Popen[bytes]:
    try:
        _require_systemd_client_binaries(_SYSTEMD_RUN, _SYSTEMCTL, _ENV)
        return popen_tree(
            command,
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            close_fds=True,
            cwd=source_root,
            env=_systemd_user_client_environment(),
            bufsize=0,
        )
    except (OSError, subprocess.SubprocessError, RuntimeWorkerScopeControlError) as error:
        raise RuntimeWorkerScopeError("root-fd worker 无法启动 user systemd scope") from error


def _communicate_scope(
    process: subprocess.Popen[bytes],
    command: tuple[str, ...],
    payload: bytes,
    deadline: float,
) -> subprocess.CompletedProcess[bytes]:
    if _remaining(deadline) <= 0:
        raise RuntimeWorkerScopeError("root-fd worker scope 执行超时")
    try:
        stdout, stderr = process.communicate(input=payload, timeout=_remaining(deadline))
    except subprocess.TimeoutExpired:
        raise RuntimeWorkerScopeError("root-fd worker scope 执行超时") from None
    except (OSError, subprocess.SubprocessError) as error:
        raise RuntimeWorkerScopeError("root-fd worker scope 通信失败") from error
    if type(stdout) is not bytes or type(stderr) is not bytes:
        raise RuntimeWorkerScopeError("root-fd worker scope 输出类型无效")
    if _remaining(deadline) <= 0:
        raise RuntimeWorkerScopeError("root-fd worker scope 执行超时")
    return subprocess.CompletedProcess(command, process.returncode, stdout, stderr)


def _abort_worker_scope(
    unit: str,
    process: subprocess.Popen[bytes] | None,
    scope_main_pidfd: int | None,
) -> None:
    if scope_main_pidfd is not None:
        terminate_worker_scope_main(scope_main_pidfd)
    kill_worker_scope(unit)
    _abort_scope_client(process)


def _abort_scope_client(process: subprocess.Popen[bytes] | None) -> None:
    if process is None:
        return
    try:
        if process.poll() is None:
            kill_process_tree(process, timeout=_CLEANUP_TIMEOUT_SEC)
        process.communicate(timeout=_CLEANUP_TIMEOUT_SEC)
    except (OSError, subprocess.SubprocessError):
        return
    finally:
        for stream in (process.stdin, process.stdout, process.stderr):
            if stream is None:
                continue
            try:
                stream.close()
            except OSError:
                pass


class _DescriptorHandoffServer:
    """只向经 peer credential 与 cgroup 双重证明的 scope 发送根 fd 和父 pidfd。"""

    def __init__(
        self,
        *,
        unit: str,
        root_descriptor: int,
        parent_pidfd: int,
    ) -> None:
        self._unit = unit
        self._root_descriptor = root_descriptor
        self._parent_pidfd = parent_pidfd
        self._temporary_directory: tempfile.TemporaryDirectory[str] | None = None
        self._listener: socket.socket | None = None
        self.path = ""

    def __enter__(self) -> _DescriptorHandoffServer:
        temporary_directory = tempfile.TemporaryDirectory(prefix="codev-rootfd-")
        socket_path = Path(temporary_directory.name) / "handoff.sock"
        listener: socket.socket | None = None
        try:
            _require_private_directory(Path(temporary_directory.name))
            _require_handoff_socket_path(str(socket_path))
            listener = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
            listener.bind(str(socket_path))
            listener.listen(1)
        except Exception:
            if listener is not None:
                listener.close()
            temporary_directory.cleanup()
            raise
        self._temporary_directory = temporary_directory
        self._listener = listener
        self.path = str(socket_path)
        return self

    def __exit__(self, _type: object, _value: object, _traceback: object) -> None:
        if self._listener is not None:
            self._listener.close()
            self._listener = None
        if self._temporary_directory is not None:
            self._temporary_directory.cleanup()
            self._temporary_directory = None

    def send_to_scope(self, process: subprocess.Popen[bytes], deadline: float) -> int:
        listener = self._listener
        if listener is None:
            raise RuntimeWorkerScopeError("root-fd worker descriptor 交接未初始化")
        while True:
            if process.poll() is not None:
                raise RuntimeWorkerScopeError("root-fd worker scope 在 descriptor 交接前退出")
            remaining = _remaining(deadline)
            if remaining <= 0:
                raise RuntimeWorkerScopeError("root-fd worker descriptor 交接超时")
            readable, _writable, _exceptional = select.select(
                (listener,),
                (),
                (),
                min(remaining, _HANDOFF_MAX_SEC),
            )
            if not readable:
                raise RuntimeWorkerScopeError("root-fd worker descriptor 交接超时")
            connection, _address = listener.accept()
            with connection:
                scope_main_pidfd = _require_scope_peer(connection, self._unit)
                try:
                    _send_descriptors(connection, self._root_descriptor, self._parent_pidfd)
                    require_pidfd_not_ready(scope_main_pidfd, "scope peer")
                    return scope_main_pidfd
                except RuntimeWorkerPidfdError as error:
                    _close_quietly(scope_main_pidfd)
                    raise RuntimeWorkerScopeError("root-fd worker scope peer 已退出") from error
                except RuntimeWorkerScopeError:
                    _close_quietly(scope_main_pidfd)
                    raise


def _require_scope_peer(connection: socket.socket, unit: str) -> int:
    try:
        raw = connection.getsockopt(socket.SOL_SOCKET, socket.SO_PEERCRED, struct.calcsize("3i"))
        process_id, user_id, _group_id = struct.unpack("3i", raw)
    except (AttributeError, OSError, struct.error) as error:
        raise RuntimeWorkerScopeError("root-fd worker 无法读取 scope peer 身份") from error
    if process_id <= 0 or user_id != os.geteuid():
        raise RuntimeWorkerScopeError("root-fd worker scope peer 身份不匹配")
    scope_main_pidfd = -1
    try:
        scope_main_pidfd = open_process_pidfd(process_id, "scope peer")
        require_pidfd_not_ready(scope_main_pidfd, "scope peer")
        belongs = cgroup_belongs_to_unit(_read_cgroup(process_id), unit)
        if not belongs:
            raise RuntimeWorkerScopeError("root-fd worker scope peer 不属于预期 cgroup")
        require_pidfd_not_ready(scope_main_pidfd, "scope peer")
        return scope_main_pidfd
    except RuntimeWorkerScopeError:
        _close_quietly(scope_main_pidfd)
        raise
    except (RuntimeWorkerPidfdError, SystemdProcessIdentityError, OSError) as error:
        _close_quietly(scope_main_pidfd)
        raise RuntimeWorkerScopeError("root-fd worker scope peer cgroup 无法证明") from error


def _send_descriptors(connection: socket.socket, root_descriptor: int, parent_pidfd: int) -> None:
    descriptors = array.array("i", (root_descriptor, parent_pidfd))
    try:
        sent = connection.sendmsg(
            (b"R",),
            ((socket.SOL_SOCKET, socket.SCM_RIGHTS, descriptors),),
        )
    except OSError as error:
        raise RuntimeWorkerScopeError("root-fd worker descriptor 发送失败") from error
    if sent != 1:
        raise RuntimeWorkerScopeError("root-fd worker descriptor 发送不完整")


def _read_cgroup(process_id: int) -> bytes:
    if type(process_id) is not int or process_id <= 0:
        raise RuntimeWorkerScopeError("root-fd worker cgroup PID 无效")
    descriptor = -1
    try:
        descriptor = os.open(
            f"/proc/{process_id}/cgroup",
            os.O_RDONLY | getattr(os, "O_CLOEXEC", 0),
        )
        raw = os.read(descriptor, _MAX_CGROUP_BYTES + 1)
    except OSError as error:
        raise RuntimeWorkerScopeError("root-fd worker cgroup 无法读取") from error
    finally:
        _close_quietly(descriptor)
    if not raw or len(raw) > _MAX_CGROUP_BYTES:
        raise RuntimeWorkerScopeError("root-fd worker cgroup 原像无效")
    return raw


def _require_private_directory(path: Path) -> None:
    try:
        metadata = path.stat()
    except OSError as error:
        raise RuntimeWorkerScopeError("root-fd worker 交接目录不可用") from error
    if (
        not stat.S_ISDIR(metadata.st_mode)
        or metadata.st_uid != os.geteuid()
        or stat.S_IMODE(metadata.st_mode) & (stat.S_IRWXG | stat.S_IRWXO)
    ):
        raise RuntimeWorkerScopeError("root-fd worker 交接目录不私有")


def _require_handoff_socket_path(value: str) -> str:
    if type(value) is not str or not value:
        raise RuntimeWorkerScopeError("root-fd worker 交接 socket 路径无效")
    if not posixpath.isabs(value) or len(os.fsencode(value)) > _MAX_SOCKET_PATH_BYTES:
        raise RuntimeWorkerScopeError("root-fd worker 交接 socket 路径无效")
    return value


def _require_worker_unit(unit: str) -> None:
    if type(unit) is not str or _WORKER_UNIT.fullmatch(unit) is None:
        raise RuntimeWorkerScopeError("root-fd worker systemd unit 无效")


def _require_worker_operation(operation: str) -> None:
    if type(operation) is not str or _WORKER_OPERATION.fullmatch(operation) is None:
        raise RuntimeWorkerScopeError("root-fd worker 操作名无效")


def _require_bootstrap_path(value: Path) -> Path:
    path = Path(value)
    try:
        resolved = path.resolve(strict=True)
    except OSError as error:
        raise RuntimeWorkerScopeError("root-fd worker 隔离启动门不可用") from error
    if not resolved.is_absolute() or not resolved.is_file():
        raise RuntimeWorkerScopeError("root-fd worker 隔离启动门不可用")
    return resolved


def _require_interpreter(value: str) -> str:
    if type(value) is not str or not value or not Path(value).is_absolute():
        raise RuntimeWorkerScopeError("root-fd worker 解释器无效")
    return value


def _environment_assignments(environment: Mapping[str, str]) -> tuple[str, ...]:
    if not isinstance(environment, Mapping):
        raise RuntimeWorkerScopeError("root-fd worker 隔离环境无效")
    assignments: list[str] = []
    for key in sorted(environment):
        value = environment[key]
        if (
            type(key) is not str
            or type(value) is not str
            or re.fullmatch(r"[A-Z_][A-Z0-9_]*", key) is None
            or "\x00" in value
        ):
            raise RuntimeWorkerScopeError("root-fd worker 隔离环境无效")
        assignments.append(f"{key}={value}")
    return tuple(assignments)


def _require_source_root(value: Path) -> None:
    path = Path(value)
    if not path.is_absolute() or not path.is_dir():
        raise RuntimeWorkerScopeError("root-fd worker 受信源码根无效")


def _require_descriptor(descriptor: int, label: str) -> None:
    if type(descriptor) is not int or descriptor < 3:
        raise RuntimeWorkerScopeError(f"root-fd worker {label} descriptor 无效")


def _require_pidfd(descriptor: int, label: str) -> None:
    try:
        require_pidfd_descriptor(descriptor)
    except RuntimeWorkerPidfdError as error:
        raise RuntimeWorkerScopeError(f"root-fd worker {label} pidfd 无效") from error


def _require_positive_timeout(value: float) -> float:
    if type(value) not in {int, float} or not math.isfinite(value) or value <= 0:
        raise RuntimeWorkerScopeError("root-fd worker scope 超时无效")
    return float(value)


def _runtime_seconds(value: float) -> int:
    return max(1, math.ceil(_require_positive_timeout(value)))


def _remaining(deadline: float) -> float:
    return max(0.0, deadline - time.monotonic())


def _close_quietly(descriptor: int) -> None:
    if descriptor < 0:
        return
    try:
        os.close(descriptor)
    except OSError:
        pass


__all__ = [
    "RuntimeWorkerScopeError",
    "build_worker_scope_command",
    "kill_worker_scope",
    "new_worker_scope_unit",
    "require_current_worker_scope",
    "run_in_worker_scope",
    "terminate_worker_scope_main",
    "verify_worker_scope_control",
]
